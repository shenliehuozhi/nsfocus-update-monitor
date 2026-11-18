"""客户资产台账 UI 回归测试。"""
from tests.frontend_source import WEB, js_source

CSS = (WEB / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')


def test_customer_page_no_standalone_upgrade_advice():
    source = js_source()
    assert "api('/customers/upgrade-advice')" not in source
    assert '📦 资产升级建议' not in source
    assert '_adviceHtml' not in source


def test_customer_add_button_matches_session_and_rule_layout():
    """「+ 添加客户」放在卡片头部右侧，与 Session、订阅规则页一致。"""
    source = js_source()
    assert ('👥 客户 <button class="btn btn-primary ml-auto px5-14 fs12" '
            'data-on-click="openCModal">+ 添加客户</button>') in source
    assert 'data-on-click="openCModal">+ 添加客户</button><table class="mt16">' not in source


def test_customer_list_shows_push_stats_and_drills_down():
    """客户列表增加近 30 天推送统计列，点击进入该客户推送历史。"""
    source = js_source()
    assert "api('/customers/push-summary?days=30')" in source
    assert '推送 / 成功率（近30天）' in source
    assert 'function _custPushCell' in source
    assert 'if(_custPushStats===null)' in source
    assert "data-on-click=\"_gotoHistoryDays\" data-args='${_dataArgs(30,cid)}'" in source
    assert '近30天无推送' in source
    assert 'colspan="8"' in source


def test_customer_modal_uses_path_chips():
    """客户资产与订阅规则一致：一个「+ 添加路径」入口，已选路径以标签展示。"""
    source = js_source()
    assert '<div id="cvars" class="cvar-list"></div>' in source
    assert 'data-on-click="openCVarPicker">+ 添加产品</button>' in source
    assert 'function renderCVars' in source
    assert 'function removeCVar' in source
    assert '_cvarRows.map(r=>({product:r.product,version:r.version||\'\'}))' in source
    assert 'cprod-row' not in source
    assert 'addCProd' not in source


def test_customer_list_shows_product_and_version():
    source = js_source()
    assert 'function _assetLabel' in source
    assert '产品 / 版本' in source
    assert "(c.owned_products||[]).map(_assetLabel)" in source
    assert "(c.owned_products||[]).join(', ')" not in source


def test_customer_modal_args_pass_structured_assets():
    source = js_source()
    assert 'c.owned_products||[]' in source
    assert "typeof a==='string'" in source


def test_customer_service_report_button_and_modal():
    source = js_source()
    assert 'data-on-click="openSvcReport"' in source
    assert 'async function _svcLoad' in source
    assert 'function openSvcReport' in source
    assert 'function _svcShift' in source
    assert '/service-report?month=' in source
    assert 'svcReportWrap' in source
    assert 'svcBody' in source
    assert '本月推送明细' in source
    assert '资产升级建议' not in source


def test_service_report_month_nav_guards_future():
    source = js_source()
    assert 'function _cstMonth' in source
    assert '_cstMonth()' in source
    assert "month>=_cstMonth()?' disabled':''" in source
    assert 'Date.UTC(y,m-1+delta,1)' in source


def test_customer_modal_is_wide_enough_for_path_chips():
    source = js_source()
    assert 'modal-card-760' in source
    assert '.modal-card-760{width:760px' in CSS
    assert '.cvar-list{display:flex;flex-wrap:wrap;gap:6px;min-height:28px;align-items:center}' in CSS
    assert '.cvar-chip{' in CSS and '.cvar-label{' in CSS
    assert '.cprod-row{' not in CSS
    assert '.cver-pop{' not in CSS


def test_customer_assets_keep_legacy_and_dedup_by_product_version():
    source = js_source()
    assert 'function _cvarNormalize' in source
    assert "typeof a==='string'" in source
    assert "const [pp,vv]=a.split(':')" in source
    # 同一产品允许多个版本：按 (product, version) 去重
    assert "const key=p+'\\u0000'+v;" in source
    assert 'if(seen.has(key))return;' in source
    assert 'const versioned=new Set(out.filter(a=>a.version).map(a=>a.product));' in source
    assert '_cvarRows.findIndex(r=>r.product===product&&(r.version||\'\')===(version||\'\'))' in source
    assert 'if(i>=0)_cvarRows[i]=row;else _cvarRows.push(row);' in source


def test_customer_path_picker_reuses_subscription_tree():
    """客户「+ 添加路径」直接复用订阅的链选择弹窗：plain 模式 + 确认回调。"""
    source = js_source()
    assert 'function openCVarPicker' in source
    assert "openChainSelectorModal([], {" in source
    assert "title:'选择客户产品'" in source
    assert 'plain:true' in source
    assert 'onConfirm:chains=>' in source
    assert 'const product=String(chain[0]||\'\').trim();' in source
    assert 'const version=cverVersionFromChain(chain);' in source
    # 弹窗按模式调整标题/提示/右侧列表，并执行确认回调
    assert 'window._chainSelConfirm = opt.onConfirm || null;' in source
    assert 'window._chainSelPlain = !!opt.plain;' in source
    assert 'if (cb) cb(chains);' in source
    assert 'renderChainSelList(chains, window._chainSelPlain)' in source
    assert "可多选；选中路径即确定产品与当前版本，同一产品可添加多个版本" in source
    # plain 模式复用 subscription 的多选与父子冲突逻辑，不再按产品替换
    assert 'c.chain[0] !== root' not in source


def test_new_rule_defaults_to_customer_products():
    """新建订阅规则选中客户后，订阅路径默认沿用客户档案里的产品与版本。"""
    source = js_source()
    assert 'function customerAssetChains' in source
    assert 'function applyCustomerPaths' in source
    assert 'if(!c||!window._custAutoFill)return;' in source
    assert 'window._custAutoFill=!id;' in source
    assert 'if(c)applyCustomerPaths(c);' in source
    assert 'window._condChains=derived;' in source
    assert 'chains.length&&!window._condChainsAuto' in source
    # 编辑规则保留已保存条件，不被客户档案覆盖
    assert 'window._condChainsAuto=false;' in source
    # 「添加路径」带上当前条件，取消不会清空
    assert 'function openRuleChainSelector' in source
    assert 'data-on-click="openRuleChainSelector">+ 添加路径</button>' in source
    # 客户版本精确落到版本分支，匹配方式随完整路径长度决定
    assert 'cverVersionFromChain(chain)===version' in source
    assert "match:chosen.length<full.length?'subtree':'leaf'" in source
    # 同产品多版本各自成链；重复路径只生成一次
    assert 'const key=JSON.stringify(o.chain);' in source


def test_cver_version_from_chain_uses_path_label():
    source = js_source()
    assert 'function cverVersionFromChain' in source
    # 选中的路径节点标签就是版本；升级包/列表只是容器，不作为版本
    assert 'const isContainer=s=>/升级包|升级|列表$|包$/.test(s);' in source
    assert 'if(!isContainer(segs[i])&&re.test(segs[i]))return segs[i];' in source

