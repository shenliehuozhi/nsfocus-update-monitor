"""CNVD / CNNVD 中文漏洞信息导入。

CNVD 公开站点（``www.cnvd.org.cn``）对非浏览器请求返回 HTTP 521 的 JS
挑战，且批量数据仅向注册用户开放；CNVD 全量接口只对技术组成员/兼容厂商
开放。因此本模块不抓取网页，只读取管理员放入本地目录的官方导出文件：

- CNVD「统计查询 → 共享数据」每周 XML（注册用户可下载）；
- CNNVD 公开 XML 数据包（可选，用于补充中文名称）；
- 接口导出的 JSON / CSV（字段名兼容中英文）。

导入只补充 ``cve_meta`` 的中文字段，不修改 NVD 评分；CVE 编号仍是主键。
同一 CVE 命中多条记录时保留 CNVD 编号更新、名称更完整的一条。
"""

import csv
import gzip
import io
import json
import os
import re
import xml.etree.ElementTree as ET

from src.core.logger import get_logger
from src.core.timeutil import utc_iso
from src.models.database import execute, executemany, query
logger = get_logger('cnvd_import')

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_NAME = 300
MAX_DESCRIPTION = 2000
MAX_SOURCE_TEXT = 4000

_CVE_RE = re.compile(r'CVE-\d{4}-\d{4,}', re.I)
_CNVD_RE = re.compile(r'CNVD-\d{4}-\d+', re.I)
_CNNVD_RE = re.compile(r'CNNVD-\d{6}-\d+', re.I)

_SUFFIXES = ('.xml', '.json', '.csv')
_RECORD_TAGS = {
    'entry', 'item', 'record', 'vulnerability', 'vulnerability_info',
    'vul', 'vuln', 'vuln_info', 'vulinfo', 'flaw', '漏洞', '漏洞信息',
}

_NAME_KEYS = (
    'name', 'title', 'vul_name', 'vulname', 'vuln_name', 'vulnname',
    'vulnerability_name', 'vulnerabilityname', '漏洞名称', '名称',
)
_DESC_KEYS = (
    'description', 'desc', 'vuln_descript', 'vulndescript',
    'vuln_description', 'vulndescription', 'vulnerability_description',
    'vulnerabilitydescription', 'detail', 'summary', '漏洞描述', '描述',
)
_CNVD_KEYS = (
    'cnvd', 'cnvd_id', 'cnvdid', 'cnvd_no', 'cnvdno', 'cnvd_code',
    'cnvdcode', 'cnvd编号', '编号',
)
_REF_KEYS = (
    'reference', 'references', 'ref', 'refs', 'related', 'related_ids',
    'relatedids', 'cve', 'cve_id', 'cveid', '引用编号', '相关编号',
)
_SKIP_NAME_TOKENS = (
    'product', 'vendor', 'company', 'supplier', 'file', 'package',
    'patch', 'solution', 'cve', 'cnvd', 'cnnvd', 'url', 'link',
    'product_name', 'app_name', 'system_name', '产品',
)
_SKIP_DESC_TOKENS = (
    'patch', 'solution', 'fix', 'product', 'vendor', 'company',
    '补丁', '解决', '厂商',
)

_UPSERT_SQL = """
INSERT INTO cve_meta (
    cve_id, name_zh, description_zh, cnvd_id, cnvd_url, cnvd_updated_at
) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(cve_id) DO UPDATE SET
    name_zh = CASE WHEN excluded.name_zh != '' THEN excluded.name_zh
                   ELSE cve_meta.name_zh END,
    description_zh = CASE WHEN excluded.description_zh != '' THEN excluded.description_zh
                          ELSE cve_meta.description_zh END,
    cnvd_id = CASE WHEN excluded.cnvd_id != '' THEN excluded.cnvd_id
                   ELSE cve_meta.cnvd_id END,
    cnvd_url = CASE WHEN excluded.cnvd_url != '' THEN excluded.cnvd_url
                    ELSE cve_meta.cnvd_url END,
    cnvd_updated_at = CASE WHEN excluded.cnvd_updated_at != '' THEN excluded.cnvd_updated_at
                           ELSE cve_meta.cnvd_updated_at END
"""


def default_import_dir() -> str:
    """``MONITOR_CNVD_DIR`` 优先，否则使用数据库同级的 ``cnvd/`` 目录。"""
    env_dir = (os.getenv('MONITOR_CNVD_DIR') or '').strip()
    if env_dir:
        return os.path.abspath(env_dir)
    import src.models.database as db_mod
    if not db_mod.DB_PATH:
        db_mod.init_db()
    return os.path.join(os.path.dirname(db_mod.DB_PATH), 'cnvd')


def import_cnvd_files(directory: str = None, force: bool = False) -> dict:
    """扫描目录并导入新增/变更的中文漏洞文件。"""
    directory = os.path.abspath(directory or default_import_dir())
    result = {
        'directory': directory,
        'files': 0,
        'imported_files': 0,
        'skipped_files': 0,
        'records': 0,
        'cves': 0,
        'unmatched': 0,
        'errors': 0,
    }
    if not os.path.isdir(directory):
        result['reason'] = 'dir_missing'
        return result

    best = {}
    states = _load_states(directory)
    for path in _iter_files(directory):
        result['files'] += 1
        try:
            stat = os.stat(path)
        except OSError as exc:
            logger.warning('[CNVD] stat failed for %s: %s', path, exc)
            result['errors'] += 1
            continue
        key = os.path.relpath(path, directory)
        previous = states.get(key)
        if (not force and previous
                and int(previous['size'] or 0) == stat.st_size
                and abs(float(previous['mtime'] or 0) - stat.st_mtime) < 1e-6):
            result['skipped_files'] += 1
            continue
        try:
            records = _parse_file(path)
        except Exception as exc:
            logger.warning('[CNVD] parse failed for %s: %s', path, exc)
            result['errors'] += 1
            continue

        file_cves = set()
        for record in records:
            result['records'] += 1
            if not record['cve_ids']:
                result['unmatched'] += 1
                continue
            for cve_id in record['cve_ids']:
                file_cves.add(cve_id)
                current = best.get(cve_id)
                if current is None or _quality(record) > _quality(current):
                    best[cve_id] = record
        _save_state(key, stat, len(records), len(file_cves))
        result['imported_files'] += 1

    if best:
        executemany(_UPSERT_SQL, [
            (
                cve_id,
                record['name_zh'],
                record['description_zh'],
                record['cnvd_id'],
                _cnvd_url(record['cnvd_id']),
                utc_iso(),
            )
            for cve_id, record in best.items()
        ])
    result['cves'] = len(best)
    if result['imported_files']:
        logger.info('[CNVD] imported files=%d records=%d cves=%d unmatched=%d errors=%d',
                    result['imported_files'], result['records'], result['cves'],
                    result['unmatched'], result['errors'])
    return result


def _iter_files(directory: str):
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for name in sorted(files):
            if name.startswith('.'):
                continue
            lower = name.lower()
            if not (lower.endswith(_SUFFIXES) or lower.endswith('.gz')):
                continue
            if lower.endswith('.gz') and not lower[:-3].endswith(_SUFFIXES):
                continue
            yield os.path.join(root, name)


def _load_states(directory: str) -> dict:
    rows = query('SELECT path, size, mtime FROM cnvd_import_state')
    states = {}
    for row in rows:
        path = row['path']
        if os.path.exists(os.path.join(directory, path)):
            states[path] = row
    return states


def _save_state(path: str, stat, records: int, cves: int):
    execute(
        "INSERT INTO cnvd_import_state (path, size, mtime, records, cves, imported_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET size = excluded.size, "
        "mtime = excluded.mtime, records = excluded.records, "
        "cves = excluded.cves, imported_at = excluded.imported_at",
        (path, stat.st_size, stat.st_mtime, records, cves, utc_iso()),
    )


def _parse_file(path: str) -> list:
    data = _read_bytes(path)
    lower = path.lower()
    if lower.endswith('.csv') or lower.endswith('.csv.gz'):
        return _parse_csv(data)
    if lower.endswith('.json') or lower.endswith('.json.gz'):
        return _parse_json(data)
    return _parse_xml(data)


def _read_bytes(path: str) -> bytes:
    opener = gzip.open if path.lower().endswith('.gz') else open
    with opener(path, 'rb') as fh:
        data = fh.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f'file exceeds {MAX_FILE_BYTES} bytes')
    return data


def _parse_xml(data: bytes) -> list:
    if b'<!doctype' in data[:4096].lower() or b'<!entity' in data.lower():
        raise ValueError('DTD/ENTITY is not allowed')
    root = ET.fromstring(data)
    records = []
    for element in root.iter():
        if _local(element.tag) not in _RECORD_TAGS:
            continue
        record = _record_from_fields(_deep_fields(element))
        if record:
            records.append(record)
    if records:
        return records

    # 未知 schema：兜底把每个直接包含引用编号的元素当作一条记录
    for element in root.iter():
        if element is root:
            continue
        record = _record_from_fields(_direct_fields(element))
        if record:
            records.append(record)
    return records


def _parse_json(data: bytes) -> list:
    payload = json.loads(data.decode('utf-8-sig'))
    items = _json_items(payload)
    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = _record_from_fields(_flatten_fields(item))
        if record:
            records.append(record)
    return records


def _json_items(payload) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return [payload] if payload else []
    for key in ('items', 'records', 'data', 'list', 'results',
                'vulnerabilities', 'vul_list', 'vulList'):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return _json_items(value)
    return [payload]


def _flatten_fields(value, prefix: str = '', out: dict = None) -> dict:
    out = out if out is not None else {}
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten_fields(item, f'{prefix}.{key}' if prefix else str(key), out)
    elif isinstance(value, list):
        for item in value:
            _flatten_fields(item, prefix, out)
    elif value is not None and not isinstance(value, bool):
        text = _clean(value)
        if text:
            out.setdefault(prefix or 'value', []).append(text)
    return out


def _parse_csv(data: bytes) -> list:
    text = _decode_text(data)
    reader = csv.DictReader(io.StringIO(text))
    records = []
    for row in reader:
        fields = {}
        for key, value in row.items():
            if key is None or value is None:
                continue
            value = _clean(value)
            if value:
                fields.setdefault(_norm_key(key), []).append(value)
        record = _record_from_fields(fields)
        if record:
            records.append(record)
    return records


def _decode_text(data: bytes) -> str:
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def _direct_fields(element) -> dict:
    fields = {}
    for key, value in element.attrib.items():
        value = _clean(value)
        if value:
            fields.setdefault(_norm_key(key), []).append(value)
    for child in list(element):
        text = _clean(' '.join(child.itertext()))
        if text:
            fields.setdefault(_norm_key(_local(child.tag)), []).append(text)
    return fields


def _deep_fields(element, fields: dict = None) -> dict:
    """展开记录内的分组节点（如国标的「标识项」「描述项」）。"""
    fields = fields if fields is not None else {}
    known = set(_NAME_KEYS) | set(_DESC_KEYS) | set(_CNVD_KEYS) | set(_REF_KEYS)
    for key, value in element.attrib.items():
        value = _clean(value)
        if value:
            fields.setdefault(_norm_key(key), []).append(value)
    key = _norm_key(_local(element.tag))
    has_children = len(element) > 0
    if key in known or not has_children:
        text = _clean(' '.join(element.itertext()))
        if text:
            fields.setdefault(key, []).append(text)
    for child in list(element):
        _deep_fields(child, fields)
    return fields


def _record_from_fields(fields: dict) -> dict | None:
    all_values = [value for values in fields.values() for value in values]
    cnvd_id = _pick(fields, all_values, _CNVD_KEYS, _CNVD_RE)
    cve_ids = _pick_cves(fields, all_values)
    if not cve_ids and not cnvd_id:
        return None

    name = _pick(fields, [], _NAME_KEYS, None)
    if not name:
        name = _pick_loose(fields, 'name', _SKIP_NAME_TOKENS, MAX_NAME)
    description = _pick(fields, [], _DESC_KEYS, None)
    if not description:
        description = _longest(fields, _SKIP_DESC_TOKENS)
    return {
        'cnvd_id': (cnvd_id or '').upper(),
        'cve_ids': cve_ids,
        'name_zh': _clip(name, MAX_NAME),
        'description_zh': _clip(description, MAX_DESCRIPTION),
    }


def _pick(fields: dict, all_values: list, keys: tuple, regex) -> str:
    for key in keys:
        normalized = _norm_key(key)
        for candidate, values in fields.items():
            if candidate == normalized:
                for value in values:
                    found = _first_match(value, regex) if regex else _clean(value)
                    if found:
                        return found
    if regex:
        for value in all_values:
            found = _first_match(value, regex)
            if found:
                return found
    return ''


def _pick_loose(fields: dict, token: str, skip: tuple, limit: int) -> str:
    best = ''
    for key, values in fields.items():
        if token not in key or any(part in key for part in skip):
            continue
        for value in values:
            if 0 < len(value) <= limit and len(value) > len(best):
                best = value
    return best


def _longest(fields: dict, skip: tuple) -> str:
    best = ''
    for key, values in fields.items():
        if any(part in key for part in skip):
            continue
        for value in values:
            if 20 <= len(value) <= MAX_SOURCE_TEXT and len(value) > len(best):
                best = value
    return best


def _pick_cves(fields: dict, all_values: list) -> list:
    refs = []
    for key, values in fields.items():
        if key in _REF_KEYS or 'cve' in key or 'ref' in key or '引用' in key or '相关' in key:
            for value in values:
                refs.extend(_CVE_RE.findall(value))
    if not refs:
        for value in all_values:
            refs.extend(_CVE_RE.findall(value))
    return sorted({ref.upper() for ref in refs})


def _first_match(value: str, regex):
    found = regex.search(value or '')
    return found.group(0).upper() if found else ''


def _quality(record: dict) -> tuple:
    return (
        1 if record['cnvd_id'] else 0,
        _cnvd_rank(record['cnvd_id']),
        len(record['name_zh']),
        min(len(record['description_zh']), 500),
    )


def _cnvd_rank(cnvd_id: str) -> tuple:
    found = re.fullmatch(r'CNVD-(\d{4})-(\d+)', cnvd_id or '', re.I)
    if not found:
        return (0, 0)
    return (int(found.group(1)), int(found.group(2)))


def _cnvd_url(cnvd_id: str) -> str:
    return f'https://www.cnvd.org.cn/flaw/show/{cnvd_id}' if cnvd_id else ''


def _local(tag) -> str:
    tag = str(tag or '')
    if '}' in tag:
        tag = tag.rsplit('}', 1)[-1]
    return tag.strip().lower()


def _norm_key(key) -> str:
    return str(key or '').strip().lower().replace('-', '_').replace(' ', '_')


def _clean(value) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _clip(value: str, limit: int) -> str:
    return (value or '').strip()[:limit]
