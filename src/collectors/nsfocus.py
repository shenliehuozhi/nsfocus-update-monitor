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


REQUEST_DELAY_MIN = _get_delay('collect_delay_min', 1.0)
REQUEST_DELAY_MAX = _get_delay('collect_delay_max', 3.0)
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


class NsfocusCollector(BaseCollector):
    source_type = 'nsfocus'

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })

    def collect(self, source_id: int, session_cookie: str) -> list[UnifiedContentItem]:
        items = []
        self._set_cookie(session_cookie)
        for name, url in PRODUCTS.items():
            try:
                if name in ('RSAS', 'NF'):
                    p_items = self._collect_recursive(source_id, name, url, max_depth=4)
                else:
                    p_items = self._collect_standard(source_id, name, url)
                items.extend(p_items)
                logger.info(f'{name}: {len(p_items)} items')
            except Exception as e:
                logger.error(f'{name}: {e}')
        return items

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

    # ── Standard: version → package type → table ────────────

    def _collect_standard(self, source_id: int, product_name: str,
                          start_url: str) -> list:
        items = []
        html = self._fetch(start_url)
        ver_links = self._extract_content_links(html)
        for v_text, v_url in ver_links:
            if self._is_sidebar_link(v_url):
                continue
            if self._is_stopped(v_url, html):
                continue
            try:
                ver_html = self._fetch(v_url)
                pkg_links = self._extract_content_links(ver_html)
                for p_text, p_url in pkg_links:
                    if self._is_sidebar_link(p_url):
                        continue
                    try:
                        pkg_html = self._fetch(p_url)
                        table_items = self._extract_table_items(
                            pkg_html, source_id, product_name,
                            self._clean_version(v_text),
                            self._clean_package_type(p_text),
                            page_url=f'{BASE_URL}{p_url}')
                        items.extend(table_items)
                    except SessionExpiredError:
                        raise
                    except Exception as e:
                        logger.warning(f'{product_name} {v_text} {p_text}: {e}')
            except SessionExpiredError:
                raise
            except Exception as e:
                logger.warning(f'{product_name} {v_text}: {e}')
        return items

    # ── Recursive: for variable-depth products ──────────────

    def _collect_recursive(self, source_id: int, product_name: str,
                           start_url: str, max_depth: int = 4) -> list:
        items = []
        visited = set()

        def recurse(url: str, ver: str, pkg: str, depth: int):
            if depth > max_depth or url in visited:
                return
            visited.add(url)
            try:
                html = self._fetch(url)
            except SessionExpiredError:
                raise
            except Exception:
                return

            table_items = self._extract_table_items(html, source_id, product_name, ver, pkg,
                                                   page_url=f'{BASE_URL}{url}')
            if table_items:
                items.extend(table_items)
                return

            links = self._extract_content_links(html)
            for text, link_url in links:
                if self._is_sidebar_link(link_url):
                    continue
                if self._is_stopped(link_url, html):
                    continue
                text = text.strip()
                nv = self._clean_version(text) if re.search(r'\d', text) else ver
                np = self._clean_package_type(text) if not re.search(r'\d', text) else pkg
                recurse(link_url, nv or ver, np or pkg, depth + 1)

        recurse(start_url, '', 'sys', 0)
        return items

    # ── Quick: HEAD-check known pages, only GET changed ones ──

    def _collect_quick(self, source_id: int, product_name: str) -> list:
        """Quick collection: revisit known snapshot URLs, HEAD-check for changes.

        Falls back gracefully:
        - No known URLs → empty list (caller should run full scan first)
        - HEAD not supported → GET with stream to check Last-Modified
        - Page unchanged → skip
        - Page changed → full GET + extract new/updated items
        """
        from src.models.database import query as snap_query
        items = []

        # Get distinct page URLs for this source's snapshots (all statuses).
        rows = snap_query(
            """SELECT DISTINCT source_url, version_branch, package_type
               FROM snapshots
               WHERE source_id = ? AND source_url != ''
                 AND status IN ('active', 'rollback', 'rollback_pending')
               ORDER BY source_url""",
            (source_id,)
        )
        if not rows:
            logger.info(f'Quick {product_name}: no known URLs, run full scan first')
            return items

        urls = [(r['source_url'], r['version_branch'], r['package_type']) for r in rows]
        total = len(urls)
        checked = 0
        changed = 0

        for url, ver, pkg in urls:
            checked += 1
            try:
                # Try HEAD with If-Modified-Since
                self._delay()
                head_resp = self.session.head(url, timeout=15, allow_redirects=True)
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
                resp = self.session.get(url, timeout=TIMEOUT, stream=True)
                content_length = resp.headers.get('Content-Length', '')
                last_modified = resp.headers.get('Last-Modified', '')

                # Quick check: if page is small enough and has last_modified,
                # we can compute a hash from headers alone
                # For now: always do a light GET to read page hash
                html = resp.text[:50000]  # Read up to 50KB
                resp.close()

                # Compute page hash for comparison
                import hashlib
                page_hash = hashlib.md5(html.encode()).hexdigest()

                # Check if this hash matches any existing snapshot's page_hash
                existing = snap_query(
                    """SELECT id FROM snapshots
                       WHERE source_id = ? AND source_url = ? AND page_hash = ?
                       LIMIT 1""",
                    (source_id, url, page_hash)
                )
                if existing:
                    # Page unchanged — reconstruct existing snapshots as items
                    # so they are included in seen_ids for rollback detection
                    existing_snaps = snap_query(
                        """SELECT * FROM snapshots
                           WHERE source_id = ? AND source_url = ? AND status = 'active'""",
                        (source_id, url)
                    )
                    for s in existing_snaps:
                        desc_parsed = s.get('description_parsed', '{}')
                        if isinstance(desc_parsed, str):
                            try: desc_parsed = json.loads(desc_parsed)
                            except: desc_parsed = {}
                        item = UnifiedContentItem(
                            source_id=source_id, source_type='nsfocus',
                            product_name=s['product_name'],
                            version_branch=s['version_branch'],
                            package_type=s['package_type'],
                            file_name=s['file_name'],
                            package_version=s.get('package_version', ''),
                            md5_hash=s['md5_hash'],
                            file_size=s.get('file_size', 0),
                            description_raw=s.get('description_raw', ''),
                            description_parsed=desc_parsed,
                            min_sys_version=s.get('min_sys_version', ''),
                            restart_required=bool(s.get('restart_required', 0)),
                            urgency=s.get('urgency', 'normal'),
                            download_id=s.get('download_id', 0),
                            published_at=s.get('published_at', ''),
                            page_hash=page_hash,
                            source_url=url,
                        )
                        items.append(item)
                    # Update prev_page_hash even for unchanged pages (user wants "旧→新" always)
                    from src.models.database import execute
                    execute("UPDATE snapshots SET prev_page_hash=page_hash, page_hash=? WHERE source_id=? AND source_url=?",
                            (page_hash, source_id, url))
                    continue

                changed += 1
                logger.info(f'Quick {product_name}: changed {url[-60:]} ({checked}/{total})')

                # Extract items from the page
                table_items = self._extract_table_items(
                    html, source_id, product_name, ver, pkg, page_url=url)
                items.extend(table_items)
                # Also update page_hash on ALL snapshots for this URL,
                # so the "unchanged path" works even if items don't match
                # existing snapshots (due to md5 changes on the page).
                from src.models.database import execute
                execute("""UPDATE snapshots SET prev_page_hash = page_hash,
                    page_hash = ? WHERE source_id=? AND source_url=?""",
                        (page_hash, source_id, url))

            except SessionExpiredError:
                raise
            except Exception as e:
                logger.warning(f'Quick {product_name}: {url[-60:]}: {e}')

        logger.info(f'Quick {product_name}: {changed}/{total} pages changed, {len(items)} items')
        return items

    # ── Internal ──────────────────────────────────────────────

    def _set_cookie(self, cookie: str):
        self.session.cookies.set('PHPSESSID', cookie, domain='update.nsfocus.com')

    def verify_session(self) -> bool:
        try:
            resp = self.session.get(f'{BASE_URL}/update/wafIndex', timeout=15, allow_redirects=True)
            return '/portal/index' not in resp.url and resp.status_code == 200
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
                return resp.text
            except SessionExpiredError:
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
                if a_tag and '/update/downloads/id/' in a_tag['href']:
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
                                        source_url=page_url)
                items.append(item)
                current_item = {}
                download_id = 0

            if re.search(r'名称[：:]', row_text):
                if current_item.get('file_name'):
                    item = self._build_item(current_item, source_id, product_name,
                                            version_branch, package_type, download_id,
                                            source_url=page_url)
                    items.append(item)
                current_item = {}

            parsed = self._parse_kv_row(row_text)
            if parsed:
                current_item.update(parsed)

        if current_item.get('file_name') and current_item.get('md5_hash'):
            item = self._build_item(current_item, source_id, product_name,
                                    version_branch, package_type, download_id,
                                    source_url=page_url)
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
                    download_id: int, source_url: str = '') -> UnifiedContentItem:
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
            r'/listEspcL', r'/listDms', r'/DsitIndex', r'/DsdbIndex',
            r'/DsesIndex', r'/ertIndex', r'/nespIndex', r'/isopRaIndex',
            r'/isopIndex$', r'/isopHIndex', r'/uesIndex', r'/basIndex',
            r'/csspIndex', r'/isgIndex', r'/esphIndex', r'/ncssi',
            r'/cnspIndex', r'/nissIndex', r'/tsaIndex', r'/tatIndex',
            r'/listIds$', r'/listIps$', r'/listTac', r'/listScm',
            r'/listSas', r'/idrIndex', r'/listSasL', r'/listSasICS',
            r'/listIdsICS', r'/listDas', r'/listSash', r'/wafIndex$',
            r'/listHwaf', r'/listNfSse', r'/ListNf$', r'/ListNfVpn',
            r'/ListNfWan', r'/ListNfOEM', r'/ListAIUtm', r'/sgIndex',
            r'/DsgIndex', r'/listAuroraIndex$', r'/tvmIndex', r'/listICSScan',
            r'/iscatIndex', r'/bvsIndex', r'/listWsms', r'/listWvss',
            r'/listApiScan', r'/websafeIndex', r'/uipIndex', r'/sagIndex',
            r'/CSSIndex', r'/adsIndex', r'/adsmIndex', r'/AdbosIndex',
            r'/ntaIndex', r'/mfIndex', r'/listEspc', r'/listEspcM',
            r'/listMatrix', r'/listEps', r'/apolloIndex', r'/saswIndex',
            r'/iotapIndex', r'/tdcIndex', r'/inspIndex', r'/sdaIndex',
            r'/listLas', r'/mdpsIndex', r'/rsasmIndex', r'/sgecIndex',
            r'/siesIndex', r'/isidIndex', r'/naptIndex',
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
