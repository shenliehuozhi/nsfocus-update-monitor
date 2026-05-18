# ROLLBACK MARKER — strategy field cleanup
# Before this change, the following code existed but was dead (never called by any active path):
#   - NsfocusCollector.collect()       (lines ~117-133, read strategy field)
#   - NsfocusCollector.collect_by_id() (lines ~135-155, read strategy field)
#   - _get_source_config()             (lines ~93-96, used only by the two methods above)
# The strategy field (standard/recursive) was read by collect_by_id but that method was never invoked.
# Actual collection uses: _collect_quick (delta/full scheduler), discover_package_types (refresh).
# If rolling back, restore the two methods above from git history commit ~957494c.
# ROLLBACK MARKER — strategy field cleanup

"""NSFOCUS update site collector.

Handles all 6 products: WAF, IPS, IDS, RSAS, NF, UTS.
WAF/IPS/IDS/UTS: version → package type → table (3 levels)
RSAS/NF: variable depth recursion (up to 4 levels)
"""

import re
import json
import hashlib
import time
import random
from typing import Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from src.core.logger import get_logger
from src.collectors.base import BaseCollector, UnifiedContentItem, CollectorHealth

logger = get_logger('nsfocus')

BASE_URL = 'https://update.nsfocus.com'
def _get_delay(key, default):
    try:
        from src.core.scheduler import _get_setting
        return float(_get_setting(key, str(default)))
    except Exception:
        return default


def _cst_to_utc(raw_time: str) -> str:
    """Convert CST (China Standard Time, UTC+8) string to UTC ISO format.

    Handles:
      '2026-05-12 17:05:51' -> '2026-05-12T09:05:51'
      '2026-05-12 17:05'    -> '2026-05-12T09:05:00'
      '2026-05-12'           -> '2026-05-12T00:00:00'
    Returns empty string if unparseable.
    """
    if not raw_time or not raw_time.strip():
        return ''
    try:
        from datetime import datetime, timedelta, timezone
        cst = timezone(timedelta(hours=8))
        # Try multiple formats
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(raw_time.strip(), fmt)
                dt = dt.replace(tzinfo=cst)
                return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
            except ValueError:
                continue
        # Fallback: return as-is (formats like '2024年12月')
        return raw_time.strip()
    except Exception:
        return raw_time.strip() if raw_time else ''


REQUEST_DELAY_MIN = _get_delay('collect_delay_min', 0.3)
REQUEST_DELAY_MAX = _get_delay('collect_delay_max', 0.5)
TIMEOUT = int(_get_delay('collect_timeout', 30))
MAX_RETRIES = 2

PRODUCTS = {
    'WAF':  '/update/wafIndex',
    'IPS':  '/update/listIps',
    'IDS':  '/update/listIds',
    'RSAS': '/update/listAuroraIndex',
    'NF':   '/update/ListNf',
    'UTS':  '/update/bsaUtsIndex',
}

# Legacy alias — all code imports PRODUCTS directly.
# Replace with _get_products() from snapshot model for dynamic DB-driven lookup.
def _get_products() -> dict:
    """Return {name: entry_url} for all active nsfocus products from DB."""
    from src.models.snapshot import list_sources
    result = {}
    for src in list_sources('nsfocus'):
        if src.get('is_active') and src.get('entry_url'):
            result[src['name']] = src['entry_url']
    return result


def _get_products_full() -> list[dict]:
    """Return list of product dicts with all DB fields (id, name, entry_url, strategy, ...)."""
    from src.models.snapshot import list_sources
    return list_sources('nsfocus')


class NsfocusCollector(BaseCollector):
    source_type = 'nsfocus'

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        # Separate session for discover (may hit /upLic redirects — must not pollute collect session)
        self._discover_session = requests.Session()
        self._discover_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })

    # ROLLBACK MARKER — strategy field cleanup
    # If rolling back, restore the full method body from git history commit ~957494c.
    # ROLLBACK MARKER — strategy field cleanup
    def collect(self, source_id: int, session_cookie: str):
        """DEPRECATED: not used by any active collection path. Use scheduler delta/full modes."""
        raise NotImplementedError('use scheduler delta/full modes instead')

    def discover_package_types(self, source_id: int, session_cookie: str, log_fn=None, progress_fn=None) -> dict:
        """Fully recursive directory-tree traversal for package type discovery.

        progress_fn: optional callback(phase: str) called when starting each top-level
                      version branch, so the caller can update UI progress.
        """
        from src.models.snapshot import get_source
        src = get_source(source_id)
        if not src:
            return {}
        name = src['name']
        url = src['entry_url']
        self._set_discover_cookie(session_cookie)
        _log = lambda msg: log_fn(msg) if log_fn else None

        all_types = []    # union list
        paths = []        # list of {chain, types}
        visited = set()  # prevent loops

        try:
            _log(f'入口: {url}')
            html = self._fetch_discover(url)

            # Extract section titles from entry page (e.g. "WEB应用防护系统(WAF)列表")
            # and map each top-level link to its containing section.
            section_titles = {}
            import re as _re
            for sec_match in _re.finditer(r"ser_c_b_tit['\">]\s*([^<]+?)\s*</div>", html):
                sec_title = sec_match.group(1).strip()
                if sec_title:
                    # Find all links within this section (until next ser_c_b_tit or end)
                    sec_start = sec_match.end()
                    next_sec = html.find("ser_c_b_tit", sec_start)
                    sec_html = html[sec_start:next_sec if next_sec > 0 else len(html)]
                    for link_match in _re.finditer(r'<a href=["\']([^"\']+)["\']\s*>', sec_html):
                        link_url = link_match.group(1).strip()
                        if link_url and not link_url.startswith('#'):
                            section_titles[link_url] = sec_title

            # Top-level links exclude sidebar + stopped links
            top_links = self._extract_content_links(html)
            top_links = [(t.strip(), u) for t, u in top_links
                         if not self._is_sidebar_link(u)
                         and not self._is_stopped(u, html)]

            if not top_links:
                # Single-page product (no sub-links at top level)
                items = self._extract_table_items(html, source_id, name, '', '', page_url=f'{BASE_URL}{url}')
                if items:
                    type_name = self._clean_version(name) or name
                    all_types.append(type_name)
                    paths.append({'chain': [name], 'types': [type_name], 'url': url})
                _log(f'完成，共 {len(all_types)} 种包类型，{len(paths)} 条路径')
                return {'types': sorted(all_types), 'paths': paths, 'modes': {t: 'auto' for t in all_types}}

            def recurse(page_url: str, chain: list, depth: int):
                if depth > 6 or page_url in visited:
                    return
                visited.add(page_url)

                _log('  ' * (depth + 1) + f'{"└── " if chain else ""}{chain[-1] if chain else url} ({page_url})')
                try:
                    page_html = self._fetch_discover(page_url)
                except RedirectToLicenseError:
                    _log('  ' * (depth + 2) + f'跳过（/upLic 跳转，session 上下文污染）')
                    return
                except Exception as e:
                    _log('  ' * (depth + 2) + f'访问失败: {e}')
                    return

                # Check if this is a final package page (has table/download items)
                table_items = self._extract_table_items(page_html, source_id, name, '', '', page_url=f'{BASE_URL}{page_url}')
                if table_items:
                    type_name = chain[-1] if chain else self._clean_version(name) or name
                    if type_name not in all_types:
                        all_types.append(type_name)
                    paths.append({'chain': [name] + list(chain), 'types': [type_name], 'url': page_url})
                    _log('  ' * (depth + 2) + f'  ► 最终页，包类型={type_name}，{len(table_items)} 条记录')
                    return

                # Not final — keep recursing into sub-links
                sub_links = self._extract_content_links(page_html)
                sub_links = [(t.strip(), u) for t, u in sub_links
                              if not self._is_sidebar_link(u)
                              and not self._is_stopped(u, page_html)]

                if not sub_links:
                    type_name = chain[-1] if chain else self._clean_version(name) or name
                    if type_name not in all_types:
                        all_types.append(type_name)
                    paths.append({'chain': [name] + list(chain), 'types': [type_name], 'url': page_url})
                    return

                for sub_text, sub_url in sub_links:
                    new_chain = list(chain) + [sub_text]
                    recurse(sub_url, new_chain, depth + 1)

            # Start recursion from top-level links
            total = len(top_links)
            for idx, (top_text, top_url) in enumerate(top_links):
                # Notify progress: which version we're starting
                if progress_fn:
                    progress_fn(f'fetching_ver', idx + 1, total)
                # section_title is extracted from entry page ser_c_b_tit markers
                sec_title = section_titles.get(top_url, '')
                initial_chain = [sec_title, top_text] if sec_title else [top_text]
                recurse(top_url, initial_chain, depth=1)

            _log(f'完成，共 {len(all_types)} 种包类型，{len(paths)} 条最终路径')
            logger.debug(f'[{source_id}] {name}: discovered types={all_types}, paths={paths}')
            return {'types': sorted(all_types), 'paths': paths, 'modes': {t: 'auto' for t in all_types}}

        except Exception as e:
            _log(f'错误: {e}')
            logger.error(f'[{source_id}] {name} discover: {e}')
            return {'types': [], 'paths': [], 'modes': {}}

    @staticmethod
    def diff_package_types(current: dict, discovered: dict) -> dict:
        """
        Compare current stored package_type JSON with newly discovered result.

        Returns change structure:
        {
            'added_paths':    [{chain, types, url}, ...],   # new paths
            'deleted_paths':  [{chain, types, url}, ...],   # removed paths
            'modified_paths': [{chain, types, url, old_types, new_types}, ...],
        }
        All comparison by URL as primary key.
        """
        current_paths = current.get('paths', []) if current else []
        discovered_paths = discovered.get('paths', []) if discovered else []

        # Use chain+types as identity key — 'url' is just the page URL where it was
        # discovered and may differ between old/new data for the same actual path.
        def path_key(p):
            return '|'.join(p.get('chain', []) + p.get('types', []))

        cur_map = {path_key(p): p for p in current_paths}
        disc_map = {path_key(p): p for p in discovered_paths}

        all_keys = set(cur_map.keys()) | set(disc_map.keys())

        added, deleted, modified = [], [], []
        for key in all_keys:
            cur_p = cur_map.get(key)
            disc_p = disc_map.get(key)
            if disc_p and not cur_p:
                added.append(disc_p)
            elif cur_p and not disc_p:
                deleted.append(cur_p)
            else:
                # Both exist — compare types
                old_types = set(cur_p.get('types', []))
                new_types = set(disc_p.get('types', []))
                if old_types != new_types:
                    modified.append({
                        **disc_p,
                        'old_types': sorted(old_types),
                        'new_types': sorted(new_types),
                    })

        return {
            'added_paths': added,
            'deleted_paths': deleted,
            'modified_paths': modified,
        }

    def check_health(self, session_cookie: str) -> CollectorHealth:
        start = time.time()
        errors = []
        self._set_cookie(session_cookie)
        try:
            html = self._fetch('/update/wafIndex')
            if 'ser_c_b_con' not in html:
                errors.append('WAF index structure changed')
        except Exception as e:
            errors.append(f'Health check failed: {e}')
        return CollectorHealth(healthy=len(errors)==0, errors=errors,
                               duration_ms=int((time.time()-start)*1000))

    # ── Quick: HEAD-check known pages, only GET changed ones ──

    def _collect_quick(self, source_id: int, product_name: str) -> list:
        """Quick collection: revisit known package-page URLs, HEAD-check for changes.

        Primary source: content_sources.package_type_discovered.paths (has final-page URLs).
        Fallback: snapshots.source_url (legacy, for old data without URL in paths).

        Falls back gracefully:
        - No known URLs → empty list (caller should run full scan first)
        - HEAD not supported → GET with stream to check Last-Modified
        - Page unchanged → skip
        - Page changed → full GET + extract new/updated items
        """
        import json as _json
        from src.models.database import query as snap_query
        from src.models.database import execute as snap_exec
        items = []
        seen_ids = set()  # track snapshot IDs for rollback detection (Bug #006)

        # ── Step 1: get final-page URLs from content_sources.package_type_discovered ──
        src_rows = snap_query(
            "SELECT package_type_discovered FROM content_sources WHERE id = ?",
            (source_id,)
        )
        paths_urls = []   # [(url, version_branch, package_type), ...]
        if src_rows and src_rows[0].get('package_type_discovered'):
            try:
                pkg_data = _json.loads(src_rows[0]['package_type_discovered'])
                for p in pkg_data.get('paths', []):
                    url = p.get('url')
                    if not url:
                        continue
                    chain = p.get('chain', [])
                    # version is second-to-last in chain (last is the package type name)
                    ver = chain[-2] if len(chain) >= 2 else ''
                    # package type is the last chain element
                    pkg_type = chain[-1] if chain else ''
                    paths_urls.append((url, ver, pkg_type))
            except Exception:
                pass

        # ── Step 2: fallback — also check snapshots.source_url for legacy data ──
        snap_urls = []
        rows = snap_query(
            """SELECT DISTINCT source_url, version_branch, package_type
               FROM snapshots
               WHERE source_id = ? AND source_url != ''
                 AND status IN ('active', 'rollback', 'rollback_pending')
               ORDER BY source_url""",
            (source_id,)
        )
        if rows:
            snap_urls = [(r['source_url'], r['version_branch'], r['package_type']) for r in rows]

        # Use paths_urls if available, otherwise fall back to snap_urls
        if paths_urls:
            urls = paths_urls
        elif snap_urls:
            urls = snap_urls
        else:
            logger.info(f'Quick {product_name}: no known URLs, run full scan first')
            return items
        total = len(urls)
        checked = 0
        changed = 0

        for url, ver, pkg in urls:
            checked += 1
            # Prepend BASE_URL if url is a relative path (from paths.url)
            full_url = f'{BASE_URL}{url}' if url.startswith('/') else url
            try:
                # Try HEAD with If-Modified-Since
                self._delay()
                head_resp = self.session.head(full_url, timeout=15, allow_redirects=True)
                if head_resp.status_code == 200:
                    # Server supports HEAD — but HEAD doesn't give us the body
                    # We still need GET to check if content actually changed
                    # Fall through to GET below
                    pass
            except Exception:
                pass  # HEAD failed, try GET instead

            try:
                # Use GET with stream to read headers without full body
                self._delay()
                resp = self.session.get(full_url, timeout=TIMEOUT, stream=True)
                content_length = resp.headers.get('Content-Length', '')
                last_modified = resp.headers.get('Last-Modified', '')

                # Detect login-page redirect (session valid for some products but not this one)
                if '/portal/' in resp.url or '/login' in resp.url.lower() or '登录' in resp.text[:200]:
                    raise SessionExpiredError(f'Session invalid for {product_name} (login page)')

                # Quick check: if page is small enough and has last_modified,
                # we can compute a hash from headers alone
                # For now: always do a light GET to read page hash
                html = resp.text[:50000]  # Read up to 50KB
                resp.close()

                # Also verify this isn't a login page by title
                if '<title>' in html:
                    title = html[html.find('<title>')+7:html.find('</title>')]
                    if '登录' in title:
                        raise SessionExpiredError(f'Session invalid for {product_name} (login title: {title[:30]})')

                # Compute page hash for comparison
                import hashlib
                page_hash = hashlib.md5(html.encode()).hexdigest()
                _stored = snap_query("SELECT page_hash, prev_page_hash FROM snapshots WHERE source_id=? AND source_url=? LIMIT 1", (source_id, full_url))
                stored_hash = _stored[0]['page_hash'] if _stored else None
                prev_hash = _stored[0]['prev_page_hash'] if _stored else None

                # ── Log hash change: prev → current ─────────────────────
                # Display URL uses the clean original url (from DB paths, no corruption)
                display_url = url[-50:]  # last 50 chars of clean path
                if stored_hash is None:
                    logger.info(f'【{product_name}】NEW  {display_url}  无 → {page_hash}')
                elif page_hash == stored_hash:
                    prev = prev_hash or '无'
                    logger.info(f'【{product_name}】SAME {display_url}  {prev} → {page_hash}')
                else:
                    prev = prev_hash or '无'
                    logger.info(f'【{product_name}】CHANGE {display_url}  {prev} → {page_hash}')
                existing = snap_query(
                    """SELECT id FROM snapshots
                       WHERE source_id = ? AND source_url = ? AND page_hash = ?
                       LIMIT 1""",
                    (source_id, full_url, page_hash)
                )
                if existing:
                    # Hash matched — but verify the page is actually the real data page,
                    # not a login page that accidentally collides with the stored hash.
                    # Strategy: if this URL has known snapshots with MD5 values,
                    # check whether any of those MD5s appear in the current page text.
                    # If none found → treat as changed (force re-extract).
                    known_md5s = snap_query(
                        """SELECT md5_hash FROM snapshots
                           WHERE source_id=? AND source_url=? AND md5_hash != ''""",
                        (source_id, full_url)
                    )
                    force_changed = False
                    if known_md5s:
                        md5_found = any(m['md5_hash'] in html for m in known_md5s)
                        if not md5_found:
                            logger.info(f'Quick [{product_name}] hash matched but content differs (login page collision?), re-extracting')
                            force_changed = True

                    if not force_changed:
                        # Page truly unchanged — reconstruct existing snapshots as items
                        # so they are included in seen_ids for rollback detection
                        existing_snaps = snap_query(
                            """SELECT * FROM snapshots
                               WHERE source_id = ? AND source_url = ? AND status = 'active'""",
                            (source_id, full_url)
                        )
                        for s in existing_snaps:
                            desc_parsed = s.get('description_parsed', '{}')
                            if isinstance(desc_parsed, str):
                                try: desc_parsed = json.loads(desc_parsed)
                                except: desc_parsed = {}
                            item = UnifiedContentItem(
                                source_id=source_id, source_type='nsfocus',
                                product_name=s['product_name'], version_branch=s['version_branch'],
                                package_type=s['package_type'], file_name=s['file_name'],
                                package_version=s.get('package_version', ''),
                                md5_hash=s['md5_hash'], file_size=s.get('file_size', 0),
                                description_raw=s.get('description_raw', ''),
                                description_parsed=desc_parsed,
                                min_sys_version=s.get('min_sys_version', ''),
                                restart_required=bool(s.get('restart_required', 0)),
                                urgency=s.get('urgency', 'normal'),
                                download_id=s.get('download_id', 0),
                                published_at=s.get('published_at', ''),
                                page_hash=page_hash, source_url=full_url,
                            )
                            items.append(item)
                            # BUG #006 FIX: also add to seen_ids so mark_rollback_pending
                            # doesn't re-mark these as rollback_pending (they were already active)
                            seen_ids.add(s['id'])
                        # Update prev_page_hash to track the change trail
                        # NOTE: do NOT update page_hash itself here — that would erase
                        # the "changed" signal for the next quick run (Bug #008 fix)
                        from src.models.database import execute
                        execute("UPDATE snapshots SET prev_page_hash=page_hash WHERE source_id=? AND source_url=?",
                                (source_id, full_url))
                        continue

                # --- Page changed (or login-collision forced re-extract) ---
                changed += 1
                old_items_count = len(items)
                logger.info(f'Quick {product_name}: changed {full_url[-60:]} ({checked}/{total})')

                # Extract items from the page
                table_items = self._extract_table_items(
                    html, source_id, product_name, ver, pkg, page_url=full_url)

                # ── Log package-level diff ───────────────────────────────
                # Query existing active snapshots for this URL as the "before" state
                old_snaps = snap_query(
                    """SELECT file_name, md5_hash, package_version, package_type, file_size
                       FROM snapshots
                       WHERE source_id=? AND source_url=? AND status='active'""",
                    (source_id, full_url))
                old_map = {(s['file_name'], s['package_type']): s for s in old_snaps}

                if table_items:
                    for ti in table_items:
                        key = (ti.file_name, ti.package_type)
                        old_s = old_map.get(key)
                        if old_s is None:
                            status = '► NEW  '
                            old_info = '  (none)'
                        else:
                            status = '► CHANGE'
                            old_ver = old_s['package_version'] or ''
                            old_md5 = old_s['md5_hash'] or ''
                            old_info = f'{old_s["file_name"]} md5={old_md5[:12]}... → {ti.md5_hash[:12]}...'
                        ver_str = f' v{ti.package_version}' if ti.package_version else ''
                        size_str = f' ({ti.file_size} bytes)' if ti.file_size else ''
                        logger.info(f'  {status} {ti.file_name}{ver_str}{size_str}')
                        if old_s is None:
                            logger.info(f'    {ti.package_type}  md5={ti.md5_hash[:12]}...')
                        else:
                            logger.info(f'    {old_info}')
                            logger.info(f'    {ti.package_type}')

                    # Detect removed packages (in old but not in new)
                    new_keys = {(ti.file_name, ti.package_type) for ti in table_items}
                    for (fname, ptype), old_s in old_map.items():
                        if (fname, ptype) not in new_keys:
                            old_md5 = old_s['md5_hash'] or ''
                            logger.info(f'  ◄ REMOVED {fname} ({old_s["file_size"] or 0} bytes)')
                            logger.info(f'    type={ptype}  md5={old_md5[:12]}...')

                items.extend(table_items)
                # Also update page_hash on ALL snapshots for this URL,
                # so the "unchanged path" works even if items don't match
                # existing snapshots (due to md5 changes on the page).
                from src.models.database import execute
                execute("""UPDATE snapshots SET prev_page_hash = page_hash,
                    page_hash = ? WHERE source_id=? AND source_url=?""",
                        (page_hash, source_id, full_url))

            except SessionExpiredError:
                raise
            except Exception as e:
                logger.warning(f'Quick {product_name}: {full_url[-60:]}: {e}')

        logger.info(f'Quick {product_name}: {changed}/{total} pages changed, {len(items)} items')
        return items

    # ── Internal ──────────────────────────────────────────────

    def _set_cookie(self, cookie: str):
        self.session.cookies.set('PHPSESSID', cookie, domain='update.nsfocus.com')

    def _set_discover_cookie(self, cookie: str):
        """Set cookie on the dedicated discover session (separate from collect session)."""
        self._discover_session.cookies.set('PHPSESSID', cookie, domain='update.nsfocus.com')

    def _fetch_discover(self, path: str) -> str:
        """Fetch using the dedicated discover session. Raises RedirectToLicenseError on /upLic."""
        url = f'{BASE_URL}{path}'
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt == 0:
                self._delay()
            else:
                backoff = 2 ** attempt
                logger.warning(f'[discover] Retry {attempt}/{MAX_RETRIES} for {path} (backoff {backoff}s)')
                time.sleep(backoff)
            try:
                logger.debug(f'[discover] GET {url}')
                resp = self._discover_session.get(url, timeout=TIMEOUT)
                resp.raise_for_status()
                if '/portal/index' in resp.url:
                    raise SessionExpiredError('Session expired')
                if '/update/upLic' in resp.url:
                    raise RedirectToLicenseError('Redirected to /update/upLic — session context switched')
                return resp.text
            except SessionExpiredError:
                raise
            except RedirectToLicenseError:
                raise
            except requests.Timeout:
                last_error = f'Timeout after {TIMEOUT}s'
                logger.warning(f'[discover] {path}: {last_error} (attempt {attempt+1}/{MAX_RETRIES+1})')
            except requests.ConnectionError as e:
                if 'Name or service not known' in str(e):
                    raise
                last_error = str(e)[:120]
                logger.warning(f'[discover] {path}: {last_error} (attempt {attempt+1}/{MAX_RETRIES+1})')
            except Exception as e:
                last_error = str(e)[:120]
                logger.warning(f'[discover] {path}: {last_error}')
                break
        raise Exception(last_error or f'[discover] Failed after {MAX_RETRIES+1} attempts')

    def verify_session(self, health_url: str = None) -> bool:
        """Check if session is still valid. health_url defaults to /update/listBvsV6/v/bvssys."""
        url = health_url or '/update/listBvsV6/v/bvssys'
        try:
            resp = self.session.get(f'{BASE_URL}{url}', timeout=15, allow_redirects=True)
            # Login redirect: any nsfocus portal path means expired
            if '/portal/' in resp.url or '/login' in resp.url:
                return False
            # Also catch the license redirect (session context switch = wrong mode)
            if '/update/upLic' in resp.url:
                return False
            return resp.status_code == 200
        except Exception:
            return False

    def _delay(self, skip: bool = False):
        if skip:
            return
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

    def _fetch(self, path: str) -> str:
        url = f'{BASE_URL}{path}'
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt == 0:
                self._delay()  # normal delay before first attempt
            else:
                backoff = 2 ** attempt  # 2s, 4s backoff
                logger.warning(f'Retry {attempt}/{MAX_RETRIES} for {path} (backoff {backoff}s)')
                time.sleep(backoff)
            try:
                logger.debug(f'GET {url}')
                resp = self.session.get(url, timeout=TIMEOUT)
                resp.raise_for_status()
                if '/portal/index' in resp.url:
                    raise SessionExpiredError('Session expired')
                if '/update/upLic' in resp.url:
                    raise RedirectToLicenseError('Redirected to /update/upLic — session context switched')
                return resp.text
            except SessionExpiredError:
                raise
            except RedirectToLicenseError:
                raise
            except requests.Timeout:
                last_error = f'Timeout after {TIMEOUT}s'
                logger.warning(f'{path}: {last_error} (attempt {attempt+1}/{MAX_RETRIES+1})')
            except requests.ConnectionError as e:
                # Don't retry DNS failures
                if 'Name or service not known' in str(e):
                    raise
                last_error = str(e)[:120]
                logger.warning(f'{path}: {last_error} (attempt {attempt+1}/{MAX_RETRIES+1})')
            except Exception as e:
                last_error = str(e)[:120]
                logger.warning(f'{path}: {last_error}')
                break  # non-retryable error
        raise Exception(last_error or f'Failed after {MAX_RETRIES+1} attempts')

    def _extract_content_links(self, html: str) -> list[tuple[str, str]]:
        soup = BeautifulSoup(html, 'html.parser')
        links = []
        for block in soup.select('.ser_c_b_con'):
            for a_tag in block.find_all('a', href=True):
                href = a_tag['href']
                if '/update/' not in href:
                    continue
                text = a_tag.get_text(strip=True)
                if text:
                    links.append((text, href))
        return links

    def _extract_table_items(self, html: str, source_id: int, product_name: str,
                             version_branch: str, package_type: str,
                             page_url: str = '') -> list:
        import hashlib
        page_hash = hashlib.md5(html[:50000].encode()).hexdigest()
        items = []
        soup = BeautifulSoup(html, 'html.parser')
        table = soup.find('table')
        if not table:
            return items

        rows = table.find_all('tr')
        current_item = {}
        download_id = 0

        for row in rows:
            cells = row.find_all(['td', 'th'])
            cell_texts = []
            for cell in cells:
                a_tag = cell.find('a', href=True)
                if a_tag and ('/update/downloads/id/' in a_tag['href'] or '/update/downloadsVm/id/' in a_tag['href']):
                    download_id = self._extract_download_id(a_tag['href'])
                    cell_texts.append(cell.get_text(' ', strip=True))
                else:
                    # Preserve newlines for description cells
                    raw_text = cell.get_text(' ', strip=True)
                    if '描述：' in raw_text or '描述:' in raw_text:
                        raw_text = cell.get_text('\n', strip=True)
                    cell_texts.append(raw_text)

            row_text = ' '.join(c for c in cell_texts if c).strip()
            if not row_text:
                continue

            # For description rows, preserve newlines for parsing
            is_desc_row = any('描述：' in c or '描述:' in c for c in cell_texts)

            if any(kw in row_text for kw in ['名称：', '版本：', 'MD5：', '大小：', '描述：', '文件名', '发布时间']):
                for full_text in cell_texts:
                    full_text = full_text.strip()
                    if not full_text:
                        continue
                    parsed = self._parse_kv_row(full_text)
                    if parsed:
                        current_item.update(parsed)
                continue

            if current_item.get('file_name') and current_item.get('md5_hash'):
                item = self._build_item(current_item, source_id, product_name,
                                        version_branch, package_type, download_id,
                                        source_url=page_url,
                                        path_id=hashlib.md5(page_url.encode()).hexdigest()[:12])
                items.append(item)
                current_item = {}
                download_id = 0

            if re.search(r'名称[：:]', row_text):
                if current_item.get('file_name'):
                    item = self._build_item(current_item, source_id, product_name,
                                            version_branch, package_type, download_id,
                                            source_url=page_url,
                                            path_id=hashlib.md5(page_url.encode()).hexdigest()[:12])
                    items.append(item)
                current_item = {}

            parsed = self._parse_kv_row(row_text)
            if parsed:
                current_item.update(parsed)

        if current_item.get('file_name') and current_item.get('md5_hash'):
            item = self._build_item(current_item, source_id, product_name,
                                    version_branch, package_type, download_id,
                                    source_url=page_url,
                                    path_id=hashlib.md5(page_url.encode()).hexdigest()[:12])
            items.append(item)

        for it in items:
            it.page_hash = page_hash
        return items

    def _parse_kv_row(self, text: str) -> Optional[dict]:
        result = {}
        patterns = [
            (r'名称[：:]\s*(.+?)(?=\s*(?:版本|MD5|大小|描述|发布|$))', 'file_name'),
            (r'版本[：:]\s*(.+?)(?=\s*(?:MD5|大小|描述|发布|名称|$))', 'package_version'),
            (r'MD5[：:]\s*([a-fA-F0-9]{32})', 'md5_hash'),
            (r'大小[：:]\s*([\d.]+[KMGT]?B?)', 'file_size_raw'),
            (r'描述[：:](.*)', 'description_raw', re.DOTALL),
            (r'发布时间[：:]\s*(.+?)$', 'published_at'),
        ]
        for item in patterns:
            pattern = item[0]
            key = item[1]
            flags = item[2] if len(item) > 2 else 0
            m = re.search(pattern, text, flags)
            if m:
                val = m.group(1).strip()
                result[key] = val
        return result if result else None

    def _build_item(self, raw: dict, source_id: int, product_name: str,
                    version_branch: str, package_type: str,
                    download_id: int, source_url: str = '',
                    path_id: str = '') -> UnifiedContentItem:
        description_raw = raw.get('description_raw', '')
        description_parsed = parse_description(description_raw)
        urgency = 'normal'
        desc_lower = description_raw.lower()
        if any(kw in desc_lower for kw in ['高危', '严重', 'critical', '远程代码执行', '紧急']):
            urgency = 'critical'
        elif any(kw in desc_lower for kw in ['中危', 'high', '漏洞', '绕过']):
            urgency = 'high'
        file_size = self._parse_size(raw.get('file_size_raw', ''))
        return UnifiedContentItem(
            source_id=source_id, source_type='nsfocus',
            product_name=product_name, version_branch=version_branch,
            package_type=package_type, file_name=raw.get('file_name', ''),
            package_version=raw.get('package_version', ''),
            md5_hash=raw.get('md5_hash', ''), file_size=file_size,
            description_raw=description_raw, description_parsed=description_parsed,
            min_sys_version=description_parsed.get('min_sys_version', ''),
            restart_required=description_parsed.get('restart_required', False),
            urgency=urgency, download_id=download_id,
            published_at=_cst_to_utc(raw.get('published_at', '')),
            source_url=source_url,
            path_id=path_id,
        )

    @staticmethod
    def _parse_size(raw: str) -> int:
        raw = raw.upper().replace('B', '').strip()
        try:
            if 'M' in raw: return int(float(raw.replace('M','')) * 1024 * 1024)
            if 'K' in raw: return int(float(raw.replace('K','')) * 1024)
            if 'G' in raw: return int(float(raw.replace('G','')) * 1024 * 1024 * 1024)
            return int(float(raw))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _extract_download_id(url: str) -> int:
        m = re.search(r'/downloads/id/(\d+)', url)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _is_sidebar_link(url: str) -> bool:
        patterns = [
            r'/bmgIndex$', r'/cdgIndex$', r'/bsaIndex$', r'/bsaUtsIndex$',
            r'/listEspcL$', r'/listDms$', r'/DsitIndex$', r'/DsdbIndex$',
            r'/DsesIndex', r'/ertIndex', r'/nespIndex', r'/isopRaIndex',
            r'/isopIndex$', r'/isopHIndex', r'/uesIndex', r'/basIndex',
            r'/csspIndex', r'/isgIndex', r'/esphIndex', r'/ncssi',
            r'/cnspIndex', r'/nissIndex', r'/tsaIndex', r'/tatIndex',
            r'/listIds$', r'/listIps$', r'/listTac', r'/listScm',
            r'/listTac$', r'/listScm$', r'/listSas$', r'/idrIndex$',
            r'/listSasL$', r'/listSasICS$', r'/listIdsICS$', r'/listDas$',
            r'/listSash$', r'/wafIndex$',
            r'/listHwaf$', r'/listNfSse$', r'/ListNf$', r'/ListNfVpn$',
            r'/ListNfWan$', r'/ListNfOEM$', r'/ListAIUtm$', r'/sgIndex$',
            r'/DsgIndex$', r'/listAuroraIndex$', r'/tvmIndex$', r'/listICSScan$',
            r'/iscatIndex$', r'/bvsIndex$', r'/listWsms$', r'/listWvss$',
            r'/listApiScan$', r'/websafeIndex$', r'/uipIndex$', r'/sagIndex$',
            r'/CSSIndex$', r'/adsIndex$', r'/adsmIndex$', r'/AdbosIndex$',
            r'/n taIndex$', r'/mfIndex$', r'/listEspc$', r'/listEspcM$',
            r'/listMatrix$', r'/listEps$', r'/apolloIndex$', r'/saswIndex$',
            r'/iotapIndex$', r'/tdcIndex$', r'/inspIndex$', r'/sdaIndex$',
            r'/listLas$', r'/mdpsIndex$', r'/rsasmIndex$', r'/sgecIndex$',
            r'/siesIndex$', r'/isidIndex$', r'/naptIndex$',
        ]
        return any(re.search(p, url) for p in patterns)

    @staticmethod
    def _is_stopped(url: str, html: str) -> bool:
        pos = html.find(url)
        if pos < 0:
            return False
        context = html[max(0, pos-100):pos+len(url)+50]
        return 'default' in context

    @staticmethod
    def _clean_version(text: str) -> str:
        for prefix in ['WEB应用防护系统 ', '网络入侵防护系统 ', '网络入侵检测系统 ',
                       'WAF ', 'RSAS ', '下一代防火墙']:
            if text.startswith(prefix):
                text = text[len(prefix):]
        return text.strip()[:50]

    @staticmethod
    def _clean_package_type(text: str) -> str:
        type_map = {
            '系统升级包': 'sys', '引擎升级包': 'sys',
            '规则升级包': 'rule', '规则库升级包': 'rule', '规则升级包 ': 'rule',
            '入侵检测规则升级包': 'rule', 'WEB应用规则升级包': 'rule',
            '威胁情报升级包': 'nti', 'NTI威胁情报升级包': 'nti',
            '病毒特征库升级包': 'av',
            '应用规则库升级包': 'apprule',
            'URL分类库升级包': 'url',
            '恶意站点库升级包': 'wcs',
            '研判规则库升级包': 'judge',
            '地理库升级包': 'geo',
            '接口升级包': 'interface',
            '特殊升级包': 'special',
            '其他升级包': 'other',
            '合并升级包': 'merge',
            '客户端': 'client',
            '流式病毒库升级包': 'av_stream',
            'URL分类库': 'url', 'url分类': 'url',
        }
        for cn, en in type_map.items():
            cn_lower = cn.lower()
            if cn_lower in text.lower():
                return en
        return text.strip()[:20]


def parse_description(desc: str) -> dict:
    result = {'added':[], 'modified':[], 'deleted':[], 'other':'',
              'min_sys_version':'', 'restart_required':False}
    if not desc:
        return result
    sections = re.split(r'[一二三四五]、', desc)
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if '新增' in section[:10]:
            result['added'] = re.findall(r'(\d{8,})\s+\w+', section)
        elif '修改' in section[:10]:
            result['modified'] = re.findall(r'(\d{8,})\s+\w+', section)
        elif '删除' in section[:10]:
            result['deleted'] = re.findall(r'(\d{8,})\s+\w+', section)
        elif '其他' in section[:10]:
            result['other'] = section[:300]
        elif '升级建议' in section[:10]:
            vm = re.search(r'版本\s*([\d.VRCF]+(?:[a-z]*\d+)?)', section)
            if vm: result['min_sys_version'] = vm.group(1)
            result['restart_required'] = '重启' in section and '无需重启' not in section
    pre = re.search(r'前置版本[：:]\s*(.+)', desc)
    if pre and not result['min_sys_version']:
        result['min_sys_version'] = pre.group(1).strip()
    return result


class SessionExpiredError(Exception):
    pass


class RedirectToLicenseError(Exception):
    """Raised when a page redirects to /update/upLic (session context switched to virtualisation)."""
    pass
