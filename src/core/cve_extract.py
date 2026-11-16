"""从升级包描述中提取 CVE 编号并落库。

解析只依赖 ``snapshots.description_raw``（采集阶段已保存），不改采集器、
不改数据页。扫描以快照为单位，用 ``md5(description_raw)`` 判断是否需要
重扫；``cve_scan_state.desc_len`` 和 ``parser_version`` 用于在 SQL 层先
过滤，避免每轮把所有描述正文读进内存。

注意：``description_raw`` 里的 CVE 可能是规则包的历史防护覆盖，不代表
本次发布修复了该漏洞；对外文案统一用「关联 CVE」。
"""

import hashlib
import re

from src.core.logger import get_logger

logger = get_logger('cve_extract')

# 解析器版本：规则变化时递增，扫描会重算已入库快照
PARSER_VERSION = 'v1'

# CVE-YYYY-NNNN（编号 4–7 位）；前后不能紧邻字母数字，避免 XCVE- 误匹配
CVE_RE = re.compile(r'(?<![A-Za-z0-9])CVE-(\d{4})-(\d{4,7})(?!\d)', re.IGNORECASE)

CONTEXT_RADIUS = 80
MAX_EVIDENCE = 300

POSITIVE_KEYWORDS = (
    '修复', '漏洞', '防护', '远程', '执行', '攻击', '注入', '绕过', '溢出',
    '拒绝服务', '提权', '信息泄露',
    'vulnerability', 'remote', 'execute', 'injection', 'bypass', 'overflow',
    'denial of service', 'privilege',
)
# 明确排除的上下文：命中后降权，避免「不受影响/不涉及」被算成关联漏洞
NEGATIVE_KEYWORDS = ('不受影响', '不涉及', '无关', '未受')

BASE_CONFIDENCE = 0.6
KEYWORD_BONUS = 0.2
LIST_BONUS = 0.1
NEGATIVE_PENALTY = 0.3
MIN_CONFIDENCE = 0.1
MAX_CONFIDENCE = 0.95
LOW_CONFIDENCE = 0.6


def extract_cves(text: str) -> list:
    """Extract unique CVE IDs with evidence snippet and confidence.

    Returns a deterministic list (sorted by CVE ID) of
    ``{'cve_id', 'evidence', 'confidence'}``. Duplicate mentions keep the
    occurrence with the highest confidence.
    """
    if not text or not isinstance(text, str):
        return []

    found = {}
    for match in CVE_RE.finditer(text):
        cve_id = match.group(0).upper()
        evidence = _evidence(text, match.start(), match.end())
        confidence = _confidence(evidence)
        current = found.get(cve_id)
        if current is None or confidence > current['confidence']:
            found[cve_id] = {
                'cve_id': cve_id,
                'evidence': evidence,
                'confidence': confidence,
            }
    return [found[key] for key in sorted(found)]


def desc_hash(text: str) -> str:
    return hashlib.md5((text or '').encode('utf-8', 'replace')).hexdigest()


def scan_pending(limit: int = None, force: bool = False) -> dict:
    """Scan snapshots whose description is new or changed.

    Args:
        limit: max snapshots to process this round (None = all).
        force: ignore the length/parser prefilter and re-hash every snapshot.
            Used by the daily full pass so same-length edits are still caught.

    Returns:
        ``{'candidates', 'scanned', 'cves', 'errors'}``.
    """
    from src.models.database import execute, executemany, query

    rows = _candidate_rows(query, limit, force)
    stats = {'candidates': len(rows), 'scanned': 0, 'cves': 0, 'errors': 0}

    for row in rows:
        snapshot_id = row['id']
        text = row['description_raw'] or ''
        digest = desc_hash(text)
        if not force and row['desc_hash'] == digest and row['parser_version'] == PARSER_VERSION:
            continue
        try:
            items = extract_cves(text)
            execute("DELETE FROM snapshot_cve WHERE snapshot_id = ?", (snapshot_id,))
            if items:
                executemany(
                    "INSERT OR REPLACE INTO snapshot_cve "
                    "(snapshot_id, cve_id, evidence, confidence, parser_version) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(snapshot_id, it['cve_id'], it['evidence'], it['confidence'], PARSER_VERSION)
                     for it in items],
                )
            execute(
                "INSERT INTO cve_scan_state "
                "(snapshot_id, desc_hash, desc_len, cve_count, parser_version, scanned_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(snapshot_id) DO UPDATE SET "
                "desc_hash = excluded.desc_hash, desc_len = excluded.desc_len, "
                "cve_count = excluded.cve_count, parser_version = excluded.parser_version, "
                "scanned_at = excluded.scanned_at",
                (snapshot_id, digest, len(text), len(items), PARSER_VERSION),
            )
        except Exception as exc:
            stats['errors'] += 1
            logger.warning(f'scan snapshot {snapshot_id} failed: {exc}')
            continue
        stats['scanned'] += 1
        stats['cves'] += len(items)

    if stats['scanned']:
        logger.info(
            f'CVE scan: {stats["scanned"]}/{stats["candidates"]} snapshot(s), '
            f'{stats["cves"]} link(s), {stats["errors"]} error(s)'
        )
    return stats


def _candidate_rows(query, limit: int, force: bool) -> list:
    base = (
        "SELECT s.id, s.description_raw, st.desc_hash, st.parser_version "
        "FROM snapshots s "
        "LEFT JOIN cve_scan_state st ON st.snapshot_id = s.id "
        "WHERE s.status != 'withdrawn' AND COALESCE(s.description_raw, '') != ''"
    )
    params = ()
    if not force:
        base += (
            " AND (st.snapshot_id IS NULL OR st.desc_len != length(s.description_raw)"
            " OR st.parser_version != ?)"
        )
        params = (PARSER_VERSION,)
    base += " ORDER BY s.id"
    if limit:
        base += " LIMIT ?"
        params = params + (int(limit),)
    return query(base, params)


def _evidence(text: str, start: int, end: int) -> str:
    left = max(0, start - CONTEXT_RADIUS)
    right = min(len(text), end + CONTEXT_RADIUS)
    snippet = re.sub(r'\s+', ' ', text[left:right]).strip()
    return snippet[:MAX_EVIDENCE]


def _confidence(evidence: str) -> float:
    lowered = evidence.lower()
    score = BASE_CONFIDENCE
    if any(keyword in lowered for keyword in POSITIVE_KEYWORDS):
        score += KEYWORD_BONUS
    if any(sep in evidence for sep in '、,，/;；'):
        score += LIST_BONUS
    if any(keyword in evidence for keyword in NEGATIVE_KEYWORDS):
        score -= NEGATIVE_PENALTY
    return round(max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, score)), 2)
