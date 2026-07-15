# ROLLBACK MARKER — strategy field cleanup (git commit cbfaea6)
# Before this change, the following existed but was dead or strategy-based:
#   - NsfocusCollector.collect()            (raised NotImplementedError)
#   - NsfocusCollector.collect_by_id()      (DELETED)
#   - _collect_standard / _collect_recursive (DELETED)
#   - _collect_full read strategy field     (REFACTORED to use paths URLs)
# To rollback: git show cbfaea6^:src/collectors/nsfocus.py > /tmp/nsfocus_old.py
#              git show cbfaea6^:src/core/scheduler.py > /tmp/scheduler_old.py
#
# Related fix (git 5b83e8c): empty pages now record url; /upLic paths get url:null, vm:true
#   - Old behavior: skipped empty pages entirely (no url recorded)
#   - New behavior: empty pages record url; /upLic 302 gets url:null + vm:true marker
#   - _collect_quick skips url:None paths automatically
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
from dataclasses import replace

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
TIMEOUT = int(_get_delay('collect_timeout', 5))
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
    """Return list of product dicts with all DB fields (id, name, entry_url, ...)."""
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
        import warnings
        warnings.warn('NsfocusCollector.collect() is deprecated; use scheduler delta/full modes', DeprecationWarning, stacklevel=2)
        return []

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
        # Visited keyed by (url, chain_tuple) so that the same URL reached via
        # different ancestors (e.g. WAF V6.0.8 规则 reached via 普通 WAF V6.0.8
        # vs via 海光系列 V6.0.8) is treated as a separate path. NSFocus uses
        # the same detail URL for both branches, so pure URL dedup collapses
        # them and the 海光 branch loses its chain context.
        visited = set()  # set[(url, chain_tuple_str)]  prevent loops while preserving chain context

        try:
            _log(f'入口: {url}')
            html = self._fetch_discover(url)

# Extract section titles from entry page (e.g. "WEB应用防护系统(WAF)列表")
            # and map each top-level link to its containing section.
            section_titles = self._extract_ser_c_b_sections(html)

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
                # Visit-key includes the current chain so that the same URL reached
                # via different ancestors is treated as a separate discovery branch.
                # E.g. NSFocus's 海光 V6.0.8 规则 uses the same detail URL as 普通
                # WAF V6.0.8 规则; without the chain-key they would collide.
                visit_key = (page_url, tuple(chain))
                if depth > 6 or visit_key in visited:
                    return
                visited.add(visit_key)

                _log('  ' * (depth + 1) + f'{"└── " if chain else ""}{chain[-1] if chain else url} ({page_url})')
                try:
                    page_html = self._fetch_discover(page_url)
                except RedirectToLicenseError:
                    # Record the type as VM-specific even though we can't get the URL.
                    # _collect_quick will skip VM paths (no url), and future collectors
                    # with proper VM context can re-discover and populate the real url.
                    _log('  ' * (depth + 2) + f'  ► /upLic 重定向，标记为 VM 类型')
                    type_name = chain[-1] if chain else self._clean_version(name) or name
                    if type_name not in all_types:
                        all_types.append(type_name)
                    paths.append({'chain': [name] + list(chain), 'types': [type_name], 'url': None, 'vm': True})
                    return
                except Exception as e:
                    _log('  ' * (depth + 2) + f'访问失败: {e}')
                    return

                # Check if this is a final package page (has table/download items)
                table_items = self._extract_table_items(page_html, source_id, name, '', '', page_url=f'{BASE_URL}{page_url}', current_chain=list(chain))
                if table_items:
                    type_name = chain[-1] if chain else self._clean_version(name) or name
                    if type_name not in all_types:
                        all_types.append(type_name)
                    paths.append({'chain': [name] + list(chain), 'types': [type_name], 'url': page_url})
                    _log('  ' * (depth + 2) + f'  ► 最终页，包类型={type_name}，{len(table_items)} 条记录')
                    return

                # Record current page only if it has no sub-links (true leaf).
                # Pages with sub-links will be recorded when their children are visited,
                # so we avoid creating spurious "intermediate" paths with only the
                # version name as the type (e.g. "WAF V6.0.9" instead of
                # "WAF V6.0.9系统升级包").
                sub_links = self._extract_content_links(page_html)
                sub_links = [(t.strip(), u) for t, u in sub_links
                              if not self._is_sidebar_link(u)
                              and not self._is_stopped(u, page_html)]

                if not sub_links:
                    _log('  ' * (depth + 2) + f'  ► 空页（无子链接），记录: {page_url}')
                    type_name = chain[-1] if chain else self._clean_version(name) or name
                    if type_name not in all_types:
                        all_types.append(type_name)
                    paths.append({'chain': [name] + list(chain), 'types': [type_name], 'url': page_url})
                    return

                # Tag each sub-link with the ser_c_b_tit section it belongs to on
                # THIS page (not the entry page). Required for version pages that
                # have multiple ser_c_b_tit blocks (e.g. IPS /update/ipsIndex/v/5.6.10
                # has both 标准系列升级包列表 and 10000系列升级包列表). Without this,
                # all sub-links inherit the entry-page section title and branches
                # that share detail URLs (e.g. IPS listNewipsDetail/v/engine is
                # used by BOTH 标准系列 and 10000系列) are silently collapsed.
                sub_section_titles = self._extract_ser_c_b_sections(page_html)
                for sub_text, sub_url in sub_links:
                    sec = sub_section_titles.get(sub_url, '')
                    new_chain = list(chain) + ([sec, sub_text] if sec else [sub_text])
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
    def diff_package_types(current: dict, discovered: dict, _debug: bool = False) -> dict:
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
        # chain elements are stripped to handle whitespace inconsistencies between
        # runs (e.g. section titles extracted with or without leading space).
        def path_key(p):
            return '|'.join([s.strip() for s in p.get('chain', [])] + p.get('types', []))

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

        if _debug and (added or deleted):
            logger.warning(f'[diff_package_types] DEBUG: current keys ({len(cur_map)}): {sorted(cur_map.keys())}')
            logger.warning(f'[diff_package_types] DEBUG: discovered keys ({len(disc_map)}): {sorted(disc_map.keys())}')
            logger.warning(f'[diff_package_types] DEBUG: diff added={len(added)} deleted={len(added)}')

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

        Note: historical `skip_page_hash` parameter was a dead switch (no comparison
        logic against prev_page_hash ever existed in this function). Removed 2026-06-23.
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
        paths_urls = []   # [(url, version_branch, package_type, chain), ...]
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
                    # Carry the full chain so we can compute per-chain path_id
                    # in _extract_table_items (used by new UNIQUE index).
                    paths_urls.append((url, ver, pkg_type, chain))
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
            # Legacy fallback rows have no stored chain — use empty chain (path_id
            # will collapse to MD5(url)[:12], same as old behavior).
            snap_urls = [(r['source_url'], r['version_branch'], r['package_type'], []) for r in rows]

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

        # ── URL dedup cache: same URL across multiple chains fetched only once ──
        # NSFocus paths often map N chains to 1 URL (e.g. UTS /wcl2.0.0 has 13 chains).
        # We fetch the URL once, parse once, then attribute the same items to each chain.
        # Note: NSFocus's _extract_table_items only reads the first <table> per page,
        # so all chains sharing a URL get identical items — safe to dedup.
        # Cached value is a list of (file_name, md5_hash, package_version, file_size,
        # description_raw, ...) primitives — i.e. we DON'T cache UnifiedContentItem
        # objects directly because mutating them (ti.version_branch = ...) would corrupt
        # the cached copy. Each chain rebuilds its own items from the cached template.
        url_cache: dict = {}  # url -> list of dicts (raw fields from items)
        zero_hashes: dict = {}  # url -> page_hash (0 items 诊断用,不重算)

        for url, ver, pkg, chain in urls:
            checked += 1
            # Prepend BASE_URL if url is a relative path; if url already has scheme (// or http://) use as-is
            if url.startswith('//') or url.startswith('http://') or url.startswith('https://'):
                full_url = url
            elif url.startswith('/'):
                full_url = f'{BASE_URL}{url}'
            else:
                full_url = f'{BASE_URL}/{url}'

            try:
                # ── URL dedup: fetch + parse only on first encounter ──
                if full_url not in url_cache:
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

                    # ── Log hash change: prev → current ─────────────────────
                    # Display URL uses the clean original url (from DB paths, no corruption)
                    display_url = url[-50:]  # last 50 chars of clean path
                    logger.info(f'【{product_name}】FETCH {display_url}  hash={page_hash}')

                    # Record page hash for 0-items diagnostic (only first time we see this URL)
                    if full_url not in zero_hashes:
                        zero_hashes[full_url] = page_hash

                    # Extract items from the page
                    table_items = self._extract_table_items(
                        html, source_id, product_name, ver, pkg,
                        page_url=full_url, current_chain=chain)

                    # ── Clone items for cache (avoid shared-reference mutation) ──
                    # _extract_table_items returns the SAME item list shared across all
                    # chains that dedup-hit this URL. If we cache the live objects and
                    # later mutate ti.version_branch = ... the cached copy gets clobbered
                    # with the LAST chain's ver/pkg, not the first. Clone via dataclass
                    # replace() so each chain works on its own copy.
                    from dataclasses import replace
                    cached_items = [replace(ti) for ti in table_items]

                    # ── Log package-level diff (only on first fetch per URL) ──
                    # Query existing active snapshots for this URL + path_id as the
                    # "before" state. Path_id isolation prevents cross-chain false
                    # NEW/OLD reports when multiple chains share one URL
                    # (e.g. NSFocus 海光 + 标准版 both pointing at /apprule6.0.60).
                    # path_id comes from the first extracted item — it was computed
                    # inside _extract_table_items as MD5(page_url + json(chain))[:12],
                    # identical to the algorithm in src/core/scheduler.py:_compute_path_id.
                    chain_path_id = table_items[0].path_id if table_items else ''
                    # md5 是文件身份的最终标识 — 把 md5_hash 也纳入 key 维度
                    # (file_name, path_id, md5_hash) 任一不同都视为不同文件身份。
                    # 老 DB 有同 (filename, path_id) 但不同 md5 → 视为 NEW (撤回重发 / 文件被替换)
                    # 老 DB 有同 (filename, path_id, md5)  → 视为已存在,走 UNCHANGED 分支
                    # key 用 ti.md5_hash or '':两边都为空时 key 仍能匹配,当作 UNCHANGED (无法判定保守处理)
                    # SQL 仍按 path_id 捞所有 (filename, path_id) 的 active 行 (覆盖所有历史 md5),
                    # 由 key 三元组自然区分 — 这样多 ti 时每条独立判定,不用 SQL 只对第一个 ti 的 md5。
                    old_snaps = snap_query(
                        """SELECT id, file_name, md5_hash, package_version, package_type, file_size, path_id, published_at
                           FROM snapshots
                           WHERE source_id=? AND source_url=? AND path_id=? AND status='active'""",
                        (source_id, full_url, chain_path_id))
                    old_map = {(s['file_name'], s['path_id'], s['md5_hash'] or ''): s for s in old_snaps}

                    if cached_items:
                        for ti in cached_items:
                            key = (ti.file_name, ti.path_id, ti.md5_hash or '')
                            old_s = old_map.get(key)
                            if old_s is None:
                                # DB 没有 (filename, path_id, md5_hash) 这个组合 → 文件身份不同 → NEW
                                status = '► NEW  '
                                old_info = '  (none)'
                            else:
                                # 三元组命中 → 必是 md5 一致 → UNCHANGED (含 md5 双空兜底)
                                status = '► UNCHANGED'
                                old_md5 = old_s['md5_hash'] or ''
                                old_info = f'md5={old_md5[:12]}... (unchanged)'
                            ver_str = f' v{ti.package_version}' if ti.package_version else ''
                            size_str = f' ({ti.file_size} bytes)' if ti.file_size else ''
                            logger.info(f'  {status} {ti.file_name}{ver_str}{size_str}')
                            if old_s is None:
                                logger.info(f'    {ti.package_type}  md5={ti.md5_hash[:12]}...')
                            else:
                                logger.info(f'    {old_info}')
                                logger.info(f'    {ti.package_type}')

                        # Detect removed packages (in old but not in new)
                        # 第二遍用三元组 (file_name, path_id, md5_hash) 做 key 跟第一遍对称 —
                        # 老行 md5 跟本次抓到任一新包都不同 = 真正被取代或撤回,单凭 (file_name, path_id)
                        # 会把"撤回重发"场景的老行错认为还在。
                        new_keys = {(ti.file_name, ti.path_id, ti.md5_hash or '') for ti in cached_items}
                        # 拿本次所有新包 published_at,用于判定 OLD vs WITHDRAWN:
                        #   老行 pub 早于任一新包 → 被新版取代 → OLD
                        #   老行 pub 不早于任一新包 → 绿盟主动撤回/下架 → WITHDRAWN
                        #   缺 pub 数据 → 保守 OLD
                        new_pubs = [ti.published_at for ti in cached_items if ti.published_at]
                        for (fname, pid, old_md5), old_s in old_map.items():
                            if (fname, pid, old_md5) not in new_keys:
                                old_pub = old_s.get('published_at', '')
                                md5_short = (old_s['md5_hash'] or '')[:12]
                                type_str = old_s['package_type']
                                size = old_s['file_size'] or 0
                                if not old_pub or not new_pubs:
                                    # 缺 pub 数据,保守按被取代
                                    logger.info(f'  ◄ OLD {fname} ({size} bytes)')
                                    logger.info(f'    type={type_str}  md5={md5_short}...')
                                    snap_exec("UPDATE snapshots SET status='superseded' WHERE id=?",
                                              (old_s['id'],))
                                elif any(np > old_pub for np in new_pubs):
                                    # 老行 pub 早于至少一个新包 → 被新版取代
                                    logger.info(f'  ◄ OLD {fname} ({size} bytes)')
                                    logger.info(f'    type={type_str}  md5={md5_short}...')
                                    snap_exec("UPDATE snapshots SET status='superseded' WHERE id=?",
                                              (old_s['id'],))
                                else:
                                    # 老行 pub 不早于任一新包 → 绿盟主动撤回/下架
                                    logger.info(f'  ◄ WITHDRAWN {fname} ({size} bytes)')
                                    logger.info(f'    type={type_str}  md5={md5_short}...')
                                    snap_exec("UPDATE snapshots SET status='withdrawn' WHERE id=?",
                                              (old_s['id'],))

                    # Cache result for other chains that share this URL
                    url_cache[full_url] = cached_items
                else:
                    # URL already fetched — reuse cached items
                    cached_items = url_cache[full_url]

                # ── Attribute cached items to current chain (ver, pkg, path_id) ──
                # _extract_table_items hard-coded (ver, pkg, path_id) into each item
                # for the FIRST chain. For subsequent chains sharing this URL, rewrite
                # these fields to match the current chain. Item count (file_name, md5,
                # etc.) is identical because NSFocus returns the same content for the
                # same URL regardless of chain.
                #
                # CRITICAL: We must clone via replace() BEFORE extending into items.
                # Otherwise the in-place mutation (ti.path_id = ...) corrupts the cache
                # AND any items already extended into the outer items list from prior
                # chains (all of which share the same dataclass instances). The fix:
                # build a per-chain list of NEW instances, extend that, and let the
                # cache hold its own reference (untouched).
                chain_path_id = hashlib.md5(
                    (full_url + json.dumps(chain, ensure_ascii=False)).encode()
                ).hexdigest()[:12]
                per_chain_items = []
                for ti in cached_items:
                    new_ti = replace(ti,
                                     version_branch=ver,
                                     package_type=pkg,
                                     path_id=chain_path_id)
                    per_chain_items.append(new_ti)
                items.extend(per_chain_items)

            except SessionExpiredError:
                raise
            except Exception as e:
                logger.warning(f'Quick {product_name}: {full_url}: {e}')

        logger.info(f'Quick {product_name}: {len(items)} items extracted from {total} URLs (deduped to {len(url_cache)} unique URLs)')

        # 0 items 时返回 url→hash 映射(诊断用);有 items 时返回空 dict
        return items, ({} if items else zero_hashes)

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

    @staticmethod
    def _extract_ser_c_b_sections(html: str) -> dict:
        """Map each <a href> in a page to its containing ser_c_b_tit section title.

        Vendor pages (e.g. IPS /update/ipsIndex/v/5.6.10) can have MULTIPLE
        ser_c_b_tit blocks on a single page: one for 标准系列升级包列表 and
        another for 10000系列升级包列表. Without section annotation, recurse()
        would tag every sub-link with the inherited entry-page section,
        collapsing branches that share detail URLs (e.g. IPS listNewipsDetail/v/engine
        is used by BOTH 标准系列 and 10000系列).

        Returns: dict[href -> section_title]. Only hrefs containing '/update/' are
        recorded (sidebar/nav links excluded). Section titles are stripped and
        leading '>' is removed.
        """
        import re as _re
        sections = {}
        for sec_match in _re.finditer(r"ser_c_b_tit['\">]\s*([^<]+?)\s*</div>", html):
            sec_title = sec_match.group(1).strip().lstrip('>')
            if not sec_title:
                continue
            sec_start = sec_match.end()
            next_sec = html.find("ser_c_b_tit", sec_start)
            sec_html = html[sec_start:next_sec if next_sec > 0 else len(html)]
            for link_match in _re.finditer(r'<a href=[\"\']([^\"\']+)[\"\']\s*>', sec_html):
                link_url = link_match.group(1).strip()
                if link_url and '/update/' in link_url and not link_url.startswith('#'):
                    sections[link_url] = sec_title
        return sections

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
                             page_url: str = '', current_chain: list = None) -> list:
        import hashlib
        import json as _json
        page_hash = hashlib.md5(html[:50000].encode()).hexdigest()
        # path_id = MD5(page_url + JSON(chain))[:12] — encodes BOTH url and the
        # chain text so that multiple chains sharing one URL get distinct path_ids.
        # Used as part of UNIQUE index (source_id, path_id, file_name, md5_hash).
        if current_chain is None:
            current_chain = []
        path_id = hashlib.md5(
            (page_url + _json.dumps(current_chain, ensure_ascii=False)).encode()
        ).hexdigest()[:12]
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
                                        path_id=path_id)
                items.append(item)
                current_item = {}
                download_id = 0

            if re.search(r'名称[：:]', row_text):
                if current_item.get('file_name'):
                    item = self._build_item(current_item, source_id, product_name,
                                            version_branch, package_type, download_id,
                                            source_url=page_url,
                                            path_id=path_id)
                    items.append(item)
                current_item = {}

            parsed = self._parse_kv_row(row_text)
            if parsed:
                current_item.update(parsed)

        if current_item.get('file_name') and current_item.get('md5_hash'):
            item = self._build_item(current_item, source_id, product_name,
                                    version_branch, package_type, download_id,
                                    source_url=page_url,
                                    path_id=path_id)
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
