"""Rule diff utility — compare two rule package versions via NSFocus vendor portal descriptions.

This module is read-only with respect to the local DB:
- /api/diff/fetch:  live-fetches HTML from vendor portal with PHPSESSID,
                    parses ALL <table> blocks (vs NsfocusCollector._extract_table_items
                    which reads only the first one — green-alliance rule-history pages
                    have one <table> per historical upgrade package, often 100+).
- /api/diff/compare: parse rule added/updated blocks from two description strings,
                     return added / updated / persisted-added diff.

Both algorithms were dry-run validated on DB samples before being committed:
  2.0.0.45102 → 2.0.0.45209 → 35 new + 27 updated, matching user expectation.
"""

import hashlib
import json
import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup


# === Parser for "新增规则：" / "更新规则：" blocks inside a rule package description ===
# Each line looks like "1. 攻击[42792]:SpectralViper后门 C2通信" (zh) or
# "1.  threat[42792]:..." (en). English is parsed too as a cross-check but the
# frontend only displays the Chinese names (per design decision 3a).
_RULE_LINE_RE = re.compile(
    r'^\s*(\d+)\.\s+(?:attack|threat|攻击)\[(\d+)\][：:](.+)$',
    re.MULTILINE,
)


def _slice_block(text: str, start_marker: str, end_markers: List[str]) -> str:
    """Slice a sub-block from `text` starting at `start_marker`, ending before
    the first occurrence of any string in `end_markers`. Returns the slice body
    (without the start marker itself), or '' if start_marker is missing."""
    idx = text.find(start_marker)
    if idx < 0:
        return ''
    sub = text[idx + len(start_marker):]
    for end in end_markers:
        e = sub.find(end)
        if e > 0:
            sub = sub[:e]
            break
    return sub


def parse_rules(description_raw: str) -> Dict[str, List[Tuple[int, str]]]:
    """Extract added/updated rule lists from a single IPS rule package description.

    Returns:
        {
          'added':   [(rule_id: int, name: str), ...],   # "新增规则：" block
          'updated': [(rule_id: int, name: str), ...],   # "更新规则：" block
        }

    English "new rules:" / "update rules:" blocks are discarded (kept only as a
    sanity check against future vendor changes; not exposed to the UI).
    """
    if not description_raw:
        return {'added': [], 'updated': []}

    # 2026-07-12 vendor marker 后缀符号兼容:
    #   实测 vendor 在「新增规则」「更新规则」后面跟了多种符号:
    #     ":\n" ":." "：" "：\n" "。" "  " "\t" 都有
    #   归一策略: 扫整个文本,marker 后紧跟的 1+ 个分隔符 (全/半角冒号 + 中文/英文句号
    #   + 空白 + 控制符) → 全部规整为 "：\n"
    #   后续如 vendor 把「新增规则」改成别的字(「新增的规则」等),改 _MARKERS 列表即可
    _MARKERS = ('新增规则', '更新规则')
    _SEP_CHARS = r':：\s\n\r\t。.'  # raw: \s \n \r \t 都要视作分隔符
    _NORMED_SUFFIX = '：\n'
    _norm = description_raw
    for marker in _MARKERS:
        idx = 0
        while True:
            i = _norm.find(marker, idx)
            if i < 0: break
            j = i + len(marker)
            # marker 之后到下一个「非分隔符」(字母/数字/中文)之间的字符全部替换
            k = j
            while k < len(_norm) and _norm[k] in _SEP_CHARS:
                k += 1
            _norm = _norm[:j] + _NORMED_SUFFIX + _norm[k:]
            idx = j + len(_NORMED_SUFFIX)
    zh_added_block = _slice_block(
        _norm, '新增规则：',
        ['更新规则：', '注意事项：', 'new rules:'],
    )
    zh_updated_block = _slice_block(
        _norm, '更新规则：',
        ['注意事项：', 'new rules:'],
    )
    return {
        'added':   [(int(rid), name.strip()) for _, rid, name in _RULE_LINE_RE.findall(zh_added_block)],
        'updated': [(int(rid), name.strip()) for _, rid, name in _RULE_LINE_RE.findall(zh_updated_block)],
    }


# WAF 描述行的格式跟 IPS 完全不同,有 2 种形态:
#   (3 段式) <7位ID> <英文key> <中文描述>  ← ID + name + 中间有空格
#       例: 27007155 nine_and_on_pichy_sqli 防护NINE_AND_ON相关漏洞
#   (2 段式) <7位ID> <name直接中文>         ← ID + name_desc 中间没空格
#       例: 28612273 path_travel_file_include防护路径穿越漏洞
# 段标题: 一、新增规则 / 二、修改规则 / 三、删除规则 / 四、其他修改 / 五、升级建议
# 行前缀只有 ID 数字(7 位起),无 "攻击[id]" 这种前缀
#
# 处理方式: 抓 ID 之后,剩下整行 = name。name 内的中英文空格用户接受。
_WAF_RULE_LINE_RE = re.compile(
    r'^\s*(\d{7,9})\s+(.+?)\s*$',
    re.MULTILINE,
)


def parse_rules_waf(description_raw: str) -> Dict[str, List[Tuple[int, str]]]:
    """Extract added/updated/deleted rule lists from a single WAF rule package
    description.

    WAF pages have an EXPLICIT "三、删除规则" section — we use it directly,
    unlike IPS where we derive "deleted" via set-difference.

    Returns:
      {
        'added':   [(rule_id: int, name: str), ...],   # "新增规则" block
        'updated': [(rule_id: int, name: str), ...],   # "修改规则" block
        'deleted': [(rule_id: int, name: str), ...],   # "删除规则" block (if any)
      }
    "其他修改" / "升级建议" sections are ignored — they contain prose, not rules.

    2026-07-12 切块策略升级 (用户提):
      旧版依赖「一、新增规则」「二、修改规则」等带「中文数字、」前缀的 marker,
      如果 vendor 哪天改成「一.新增规则」「1. 新增规则」甚至漏写编号,旧版直接 0 条。
      新版以**纯关键字** (新增规则/修改规则/删除规则/其他修改/升级建议) 为准,
      关键字前面可有可无中文/英文/标点编号前缀 (一、 / 一. / 1. / 1、 / (一) / （一）),
      也可全无编号。段定位更鲁棒,跟 IPS 后缀归一思路一致。
    """
    if not description_raw:
        return {'added': [], 'updated': [], 'deleted': []}

    KEYWORDS = ('新增规则', '修改规则', '删除规则', '其他修改', '升级建议')
    # 行首匹配: 可选 (中文/英文数字 + 标点 / 圆括号) + 关键字 + 行尾
    # 兼容: 「一、新增规则」「一.新增规则」「1. 新增规则」「(一)新增规则」「（1）新增规则」「新增规则」 都应匹配
    # 行尾: 空白 + 换行 或 文本末尾
    prefix_alt = r'(?:[一二三四五六七八九十0-9]+[、.)]|\([一二三四五六七八九十0-9]+\)|（[一二三四五六七八九十0-9]+）)?[ \t]*'
    KW_HEADER_RE = re.compile(
        r'^[ \t]*' + prefix_alt + '(' + '|'.join(KEYWORDS) + r')(?=[ \t]*(?:\r?\n|$))',
        re.MULTILINE,
    )

    # 收集段头 (位置, 关键字索引, 关键字文本)
    section_positions = []
    for m in KW_HEADER_RE.finditer(description_raw):
        kw_text = m.group(1)
        for i, k in enumerate(KEYWORDS):
            if kw_text == k:
                section_positions.append((m.start(), i, k))
                break
    section_positions.sort()

    def _extract(kw_idx: int) -> List[Tuple[int, str]]:
        target_pos = None
        target_idx_in_list = None
        for idx, (pos, kw_i, k) in enumerate(section_positions):
            if kw_i == kw_idx:
                target_pos = pos
                target_idx_in_list = idx
                break
        if target_pos is None:
            return []
        # 切到下一个段头 (按出现顺序)
        if target_idx_in_list + 1 < len(section_positions):
            block = description_raw[target_pos:section_positions[target_idx_in_list + 1][0]]
        else:
            block = description_raw[target_pos:]
        # 「无」占位 (实测 vendor「三、删除规则\n无\n------」这种) → 跳过
        # 不再用 len(cleaned) < 10 这个粗判: 段头已精确匹配,block 内容可能短(只有 1 条规则)
        # 直接剥段头第一行后看规则行数,0 条 → 跳过
        nl = block.find('\n')
        body = block[nl + 1:] if nl > 0 else ''
        if not body.strip() or body.strip() in ('无', '无\n', '无\r\n'):
            return []
        out = []
        for rid, name in _WAF_RULE_LINE_RE.findall(body):
            # The page uses "-------..." as a section separator line at the end
            # of every rule block. Strip that trailing divider from the name.
            name = re.sub(r'-{3,}\s*$', '', name).rstrip()
            if not name:
                continue
            out.append((int(rid), name))
        return out

    return {
        'added':   _extract(0),
        'updated': _extract(1),
        'deleted': _extract(2),
    }


def diff_rules(
    from_ver: str,
    to_ver: str,
    from_parsed: Dict,
    to_parsed: Dict,
    intermediates: Optional[List[Tuple[str, Dict]]] = None,
) -> Dict:
    """Diff two rule packages, optionally with intermediate versions in between.

    Key insight: NSFocus rule descriptions list rules ADDED/UPDATED *relative to
    the immediate previous upgrade package*, not relative to a fixed baseline.
    So FROM → TO net change = union of all (intermediates + TO) added/updated
    MINUS FROM's added/updated (which already shipped to customers).

    Args:
        from_ver: baseline version, e.g. "2.0.0.45102"
        to_ver: target version, e.g. "2.0.0.45209"
        from_parsed: parse_rules(from.description_raw)
        to_parsed: parse_rules(to.description_raw)
        intermediates: list of (version_str, parsed_dict) sorted ASC by
                       published_at, between from_ver and to_ver.

    Returns: dict with stats, pure_new (rule IDs), pure_updated (rule IDs),
    pure_deleted (rule IDs that were in from but in NONE of the intermediate/TO
    added/updated blocks — i.e. genuinely dropped out of the rule set).
    """
    intermediates = intermediates or []

    # from IDs
    from_added_ids   = {rid for rid, _ in from_parsed['added']}
    from_updated_ids = {rid for rid, _ in from_parsed['updated']}
    from_ids         = from_added_ids | from_updated_ids
    # Build name lookup for from (so we can show deleted rule names)
    from_name_by_id: Dict[int, str] = {}
    for rid, name in from_parsed['added']:
        from_name_by_id[rid] = name
    for rid, name in from_parsed['updated']:
        from_name_by_id[rid] = name

    # All IDs that appear in any intermediate or TO (added + updated)
    successor_ids: set = set()
    for _, parsed in intermediates:
        successor_ids |= {rid for rid, _ in parsed['added']}
        successor_ids |= {rid for rid, _ in parsed['updated']}
    successor_ids |= {rid for rid, _ in to_parsed['added']}
    successor_ids |= {rid for rid, _ in to_parsed['updated']}

    # Pure deleted = in from, NOT in any successor
    deleted_ids = from_ids - successor_ids

    # New rules: each intermediate/TO upgrade may add its own batch. Take the
    # FIRST version in which an ID appears (chronological order of intermediates).
    pure_new_map: Dict[int, Dict] = {}
    # Updated rules: collect every version in which each ID was updated.
    updated_versions_map: Dict[int, List[str]] = {}
    updated_name_map: Dict[int, str] = {}
    new_name_map: Dict[int, str] = {}

    all_upgrades = [(v, p) for v, p in intermediates] + [(to_ver, to_parsed)]
    for ver, parsed in all_upgrades:
        for rid, name in parsed['added']:
            if rid not in pure_new_map:
                pure_new_map[rid] = {'id': rid, 'name': name, 'first_added_in': ver}
            new_name_map[rid] = name
        for rid, name in parsed['updated']:
            updated_versions_map.setdefault(rid, []).append(ver)
            updated_name_map[rid] = name

    pure_new = [
        pure_new_map[rid] for rid in sorted(pure_new_map) if rid not in from_ids
    ]
    pure_updated = [
        {
            'id': rid,
            'name': updated_name_map.get(rid, new_name_map.get(rid, '')),
            'updated_in_versions': sorted(set(versions)),
        }
        for rid, versions in updated_versions_map.items()
        if rid not in from_ids
    ]
    pure_deleted = [
        {'id': rid, 'name': from_name_by_id.get(rid, '')}
        for rid in sorted(deleted_ids)
    ]

    return {
        'from_version': from_ver,
        'to_version': to_ver,
        'stats': {
            'from_added_count': len(from_parsed['added']),
            'from_updated_count': len(from_parsed['updated']),
            'intermediate_versions': [v for v, _ in intermediates],
            'to_added_count': len(to_parsed['added']),
            'to_updated_count': len(to_parsed['updated']),
            'pure_new_count': len(pure_new),
            'pure_updated_count': len(pure_updated),
            'pure_deleted_count': len(pure_deleted),
        },
        'pure_new': pure_new,
        'pure_updated': pure_updated,
        'pure_deleted': pure_deleted,
    }


# === Parse ALL <table> blocks of a vendor portal HTML page ===
#
# NSFocusCollector._extract_table_items only reads the first <table> per page.
# But green-alliance rule-history pages (e.g. /update/listNewipsDetail/v/rule5.6.11_v2)
# contain ONE <table> PER historical upgrade package, often 100+. We need all of them.

def parse_rule_packages_from_html(html: str, page_url: str) -> List[Dict]:
    """Parse every <table> block on a vendor portal detail page into a list of
    rule package dicts suitable for the diff frontend.

    Each returned dict has:
        package_version: str (e.g. "2.0.0.45209")
        file_name: str    (e.g. "eoi.unify.allrulepatch.ips.2.0.0.45209.rule")
        md5_hash: str
        file_size_raw: str (e.g. "95.43M")  — raw text; consumer can parse if needed
        published_at: str (raw CST string from page; consumer converts to UTC)
        description_raw: str (full text from 描述： block, may include 中文 + 英文)
        description_zh: str (Chinese section only, between 描述： and the English marker)
        download_url: str (relative path, e.g. /update/downloads/id/190001)
        table_index: int (0-based index in the page, useful for debugging)
        page_url: str
    """
    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    packages: List[Dict] = []

    for idx, table in enumerate(tables):
        rows = table.find_all('tr')
        if not rows:
            continue

        current_item: Dict = {}
        download_url = ''

        for row in rows:
            cells = row.find_all(['td', 'th'])
            cell_texts: List[str] = []
            download_url_in_cell = ''

            for cell in cells:
                a_tag = cell.find('a', href=True)
                if a_tag and (
                    '/update/downloads/id/' in a_tag['href']
                    or '/update/downloadsVm/id/' in a_tag['href']
                ):
                    download_url_in_cell = a_tag['href']
                    cell_texts.append(cell.get_text(' ', strip=True))
                else:
                    raw_text = cell.get_text(' ', strip=True)
                    if '描述：' in raw_text or '描述:' in raw_text:
                        # Preserve newlines so the kv-row parser can split on them
                        raw_text = cell.get_text('\n', strip=True)
                    cell_texts.append(raw_text)

            if download_url_in_cell:
                download_url = download_url_in_cell

            row_text = ' '.join(c for c in cell_texts if c).strip()
            if not row_text:
                continue

            is_desc_row = any('描述：' in c or '描述:' in c for c in cell_texts)

            # Same key-row detection as NsfocusCollector._extract_table_items
            if any(
                kw in row_text
                for kw in ['名称：', '版本：', 'MD5：', '大小：', '描述：', '文件名', '发布时间']
            ):
                for full_text in cell_texts:
                    full_text = full_text.strip()
                    if not full_text:
                        continue
                    parsed = _parse_kv_row(full_text)
                    if parsed:
                        current_item.update(parsed)
                continue

            # Standalone 名称： row → finalize the previous item before starting a new one
            if re.search(r'名称[：:]', row_text):
                if current_item.get('file_name') and current_item.get('md5_hash'):
                    packages.append(_build_pkg(current_item, download_url, idx, page_url))
                current_item = {}

            parsed = _parse_kv_row(row_text)
            if parsed:
                current_item.update(parsed)

        # Finalize last item in this table
        if current_item.get('file_name') and current_item.get('md5_hash'):
            packages.append(_build_pkg(current_item, download_url, idx, page_url))

    # Dedup by (file_name, md5_hash) — vendor pages sometimes repeat a row
    seen = set()
    deduped: List[Dict] = []
    for pkg in packages:
        key = (pkg.get('file_name', ''), pkg.get('md5_hash', ''))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(pkg)
    return deduped


def _build_pkg(current_item: Dict, download_url: str, table_index: int, page_url: str) -> Dict:
    desc_raw = current_item.get('description_raw', '') or ''
    # Split Chinese vs English: English starts at first "This upgrade" / "new rules:"
    en_idx = -1
    for marker in ('This upgrade', 'this upgrade', 'new rules:', 'Rules updated pack'):
        i = desc_raw.find(marker)
        if i >= 0 and (en_idx < 0 or i < en_idx):
            en_idx = i
    desc_zh = desc_raw[:en_idx].strip() if en_idx >= 0 else desc_raw.strip()
    return {
        'package_version': current_item.get('package_version', ''),
        'file_name':       current_item.get('file_name', ''),
        'md5_hash':        current_item.get('md5_hash', ''),
        'file_size_raw':   current_item.get('file_size_raw', ''),
        'published_at':    current_item.get('published_at', ''),
        'description_raw': desc_raw,
        'description_zh':  desc_zh,
        'download_url':    download_url,
        'table_index':     table_index,
        'page_url':        page_url,
    }


# === Reused from NsfocusCollector._parse_kv_row (kept local to avoid coupling
# with private collector internals; signature-compatible) ===

_KV_PATTERNS = [
    (r'名称[：:]\s*(.+?)(?=\s*(?:版本|MD5|大小|描述|发布|名称|$))', 'file_name'),
    (r'版本[：:]\s*(.+?)(?=\s*(?:MD5|大小|描述|发布|名称|$))', 'package_version'),
    (r'MD5[：:]\s*([a-fA-F0-9]{32})', 'md5_hash'),
    (r'大小[：:]\s*([\d.]+[KMGT]?B?)', 'file_size_raw'),
    (r'描述[：:](.*)', 'description_raw', re.DOTALL),
    (r'发布时间[：:]\s*(.+?)$', 'published_at'),
]


def _parse_kv_row(text: str) -> Dict:
    result: Dict = {}
    for item in _KV_PATTERNS:
        pattern = item[0]
        key = item[1]
        flags = item[2] if len(item) > 2 else 0
        m = re.search(pattern, text, flags)
        if m:
            val = m.group(1).strip()
            if key == 'md5_hash':
                val = val.lower()
            if key == 'description_raw':
                # Strip trailing whitespace/newlines so block-slice indices are stable
                val = val.rstrip()
            result[key] = val
    return result


# === Helpers used by /api/diff/export ===

def diff_to_csv_rows(diff_result: Dict) -> List[Dict]:
    """Flatten a diff result into CSV-friendly rows.

    Each row = {'类型', '规则号', '规则名称', '首次新增于', '更新于'}.
    One row per (rule, version) for updated rules (so multi-version updates expand).
    """
    rows: List[Dict] = []
    for entry in diff_result.get('pure_new', []):
        rows.append({
            '类型': '新增',
            '规则号': entry['id'],
            '规则名称': entry['name'],
            '首次新增于': entry.get('first_added_in', ''),
            '更新于': '',
        })
    for entry in diff_result.get('pure_updated', []):
        versions = entry.get('updated_in_versions', [])
        if not versions:
            rows.append({
                '类型': '更新',
                '规则号': entry['id'],
                '规则名称': entry['name'],
                '首次新增于': '',
                '更新于': '',
            })
        else:
            for v in versions:
                rows.append({
                    '类型': '更新',
                    '规则号': entry['id'],
                    '规则名称': entry['name'],
                    '首次新增于': '',
                    '更新于': v,
                })
    for entry in diff_result.get('pure_deleted', []):
        rows.append({
            '类型': '删除',
            '规则号': entry['id'],
            '规则名称': entry['name'],
            '首次新增于': '',
            '更新于': '',
        })
    return rows


def compare_versions(a: str, b: str) -> int:
    """Compare two version strings like '2.0.0.45209' vs '2.0.0.45102'.

    Splits on '.', converts each segment to int (falls back to string compare).
    Returns -1 / 0 / 1 (a < b / a == b / a > b).
    """
    def _parts(v: str) -> List:
        out = []
        for seg in (v or '').split('.'):
            try:
                out.append((0, int(seg)))
            except ValueError:
                out.append((1, seg))
        return out

    pa, pb = _parts(a), _parts(b)
    n = max(len(pa), len(pb))
    while len(pa) < n:
        pa.append((0, 0))
    while len(pb) < n:
        pb.append((0, 0))
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0