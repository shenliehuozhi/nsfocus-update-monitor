"""End-to-end test: Rule 1013 + 8703 → matched_chain = WAF V6.0.8.

Confirms commit d3a97f2 properly returns the subscription chain
(not snap-derived chain) as user_chain for push messages.

匹配数据取自生产库 rule 1013 / snap 8703 的真实取值(已内联成常量),但断言
走的是产品实现 src.detector.change._chain_matches —— 原先这个用例既 SELECT
生产库(绑死开发机、CI 上没文件、保留期清理一删 8703 就红),又在自己文件里
重写了一份 _chain_matches 来自测。
"""
import sys
import sqlite3 as _sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 生产 subscription_rules.id=1013(华兴银行黄灵昌)的 filter_conditions['chains']
RULE_1013_CHAINS = [
    {'chain': ['WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表',
               'WAF V6.0.8', 'WAF V6.0.8 规则升级包'], 'match': 'leaf'},
    {'chain': ['WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表',
               'WAF V6.0.7', 'WAF V6.0.7 规则升级包'], 'match': 'leaf'},
    {'chain': ['WEB应用防护系统(WAF)', '信息技术应用创新-WEB应用防护系统(WAF)列表',
               '海光系列HG', '海光系列 V6.0.9', '海光系列 V6.0.9规则升级'], 'match': 'leaf'},
]

# snap 8703 的物理 URL /listWafV68Detail/v/rule 被两条链共用,
# _get_chain 返回 list of chains —— 真实数据结构
SNAP_8703_CHAINS = [
    ['WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表', 'WAF V6.0.8', 'WAF V6.0.8 规则升级包'],
    ['WEB应用防护系统(WAF)', '信息技术应用创新-WEB应用防护系统(WAF)列表',
     '海光系列HG', '海光系列 V6.0.8', '海光系列 V6.0.8 规则升级'],
]


def test_rule_1013_8703_matched_to_waf_v68():
    """Rule 1013 (订阅 WAF V6.0.8/V6.0.7 规则升级包 + 海光 V6.0.9) + snap 8703
    (海光 V6.0.8 规则升级 active, 挂在共享 URL /listWafV68Detail/v/rule)
    → 按 change.py:291 的逐链匹配, WAF 那条 leaf 命中, 海光那条不命中。
    """
    from src.detector.change import _chain_matches

    matched_chain = None
    for sc in SNAP_8703_CHAINS:
        if _chain_matches(sc, RULE_1013_CHAINS):
            matched_chain = sc
            break

    assert matched_chain is not None, (
        'Expected one of snap 8703 chains to match Rule 1013 leaf subscription.'
    )
    assert matched_chain[-1] == 'WAF V6.0.8 规则升级包'
    assert matched_chain[-2] == 'WAF V6.0.8'

    # 海光 V6.0.8 5 层链 ≠ 规则里任何一条(规则订的是海光 V6.0.9)
    assert _chain_matches(SNAP_8703_CHAINS[1], RULE_1013_CHAINS) is False


def test_8703_push_message_uses_user_chain_not_snap_ver(monkeypatch, tmp_path):
    """from_snapshot(snap, user_chain=matched_chain) constructs push
    message from user_chain (content_source-derived), NOT snap.version_branch.

    Commit d3a97f2: NotificationMessage.from_snapshot uses user_chain
    when set, falls back to _build_chain(msg) (URL-only path_id first
    match, WAF) otherwise.
    """
    from src.notifiers.base import NotificationMessage

    waf_v68_chain = [
        'WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表',
        'WAF V6.0.8', 'WAF V6.0.8 规则升级包',
    ]
    haiguang_v68_chain = [
        'WEB应用防护系统(WAF)', '信息技术应用创新-WEB应用防护系统(WAF)列表',
        '海光系列HG', '海光系列 V6.0.8', '海光系列 V6.0.8 规则升级',
    ]

    snap = {
        'id': 8703,
        'source_id': 1,
        'product_name': 'WEB应用防护系统(WAF)',
        'version_branch': '',
        'package_type': '',
        'file_name': 'update_rule.v6.0.8.1.73229.wcl',
        'md5_hash': '2374ef70d5634775b44ba51499658100',
        'source_url': 'https://update.nsfocus.com/update/listWafV68Detail/v/rule',
        'description_parsed': {},
        'description_raw': '',
    }

    # Path 1 (pre-d3a97f2 fallback): user_chain=None → _build_chain 反查 chain。
    # 反查是否命中取决于 DB_PATH,原先用 if/else "两条路都接受",等于没断言。
    # 这里把 DB_PATH 钉在一个空的 content_sources 库上 → 必然查不到 →
    # 只断言 fallback 行为(生产路径永远传 user_chain,见 Path 2/3)。
    empty_db = str(tmp_path / 'no_sources.db')
    _sqlite3.connect(empty_db).close()
    monkeypatch.setattr('src.models.database.DB_PATH', empty_db)

    msg_no_chain = NotificationMessage.from_snapshot(snap)
    assert msg_no_chain.chain == [], f'空库不该反查到 chain: {msg_no_chain.chain}'
    assert msg_no_chain.version_branch == ''
    assert msg_no_chain.package_type == ''

    # Path 2 (post-d3a97f2): user_chain=海光 chain (snap 的真正归属)
    msg_haiguang = NotificationMessage.from_snapshot(snap, user_chain=haiguang_v68_chain)
    assert msg_haiguang.version_branch == '海光系列 V6.0.8'
    assert msg_haiguang.package_type == '海光系列 V6.0.8 规则升级'
    assert msg_haiguang.chain == haiguang_v68_chain

    # Path 3 (post-d3a97f2 + Rule 1013 matched WAF):
    # user_chain=matched WAF V6.0.8 chain (from subscription rule match)
    msg_waf_rule = NotificationMessage.from_snapshot(snap, user_chain=waf_v68_chain)
    assert msg_waf_rule.version_branch == 'WAF V6.0.8'
    assert msg_waf_rule.package_type == 'WAF V6.0.8 规则升级包'
    assert msg_waf_rule.chain == waf_v68_chain


def test_subscription_chain_match_leaf_strict():
    """产品 _chain_matches 的 leaf 严格相等:WAF 4 层 vs WAF 4 层命中,
    WAF 4 层 vs 海光 5 层不命中。

    原先这个用例在本文件里手写了一份 _chain_matches 副本自测 —— 改坏产品
    函数它照样绿。现在直接调实现。
    """
    from src.detector.change import _chain_matches

    waf = ['WAF', 'WAF列表', 'WAF V6.0.8', 'WAF V6.0.8 规则升级包']
    haiguang = ['WAF', '信创列表', '海光HG', '海光 V6.0.8', '海光 V6.0.8 规则升级']
    sub_waf_68 = {'chain': waf, 'match': 'leaf'}

    assert _chain_matches(waf, [sub_waf_68]) is True
    # 不同 chain、不同长度 → leaf 模式不匹配
    assert _chain_matches(haiguang, [sub_waf_68]) is False
    # subtree 模式:前缀相同即命中(订阅该节点下全部包)
    assert _chain_matches(waf, [{'chain': waf[:2], 'match': 'subtree'}]) is True


def test_multi_chain_subscription_matches_either_chain():
    """共享 URL 反查出多条 chain 时,逐条问 _chain_matches(change.py:291 的做法),
    任意一条命中就要推送 —— 原先这里又是本地副本,且副本签名和产品不一致。"""
    from src.detector.change import _chain_matches

    waf = ['WAF', 'WAF列表', 'WAF V6.0.8', 'WAF V6.0.8 规则升级包']
    haiguang = ['WAF', '信创列表', '海光HG', '海光 V6.0.8', '海光 V6.0.8 规则升级']
    sub_waf_68 = [{'chain': waf, 'match': 'leaf'}]

    # 海光那条不命中,但 WAF 那条命中 → 至少一条命中即触发推送
    assert any(_chain_matches(sc, sub_waf_68) for sc in [haiguang, waf]) is True
    # 一条都不命中时不能误推
    assert any(_chain_matches(sc, sub_waf_68) for sc in [haiguang]) is False