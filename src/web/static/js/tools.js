// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// ── Tools page (顶级菜单「🛠️ 其他工具」的入口) ──
async function toolsPage(){
  const app=document.getElementById('app');
  app.innerHTML=`
    <div class="card">
      <div class="card-hd">🛠️ 其他工具</div>
      <div class="card-bd">
        <div class="tools-grid">
          <div class="tool-card" data-on-click="toolDiffRuleOpen">
            <div class="tool-ico">🔍</div>
            <div class="tool-title">IDPS 规则库 Diff</div>
            <div class="tool-desc">输入 vendor portal 规则库链接 (限 IPS / IDS 产品), 实时拉取所有升级包, 选择任意 2 个版本对比新增 / 修改 / 删除的规则, 支持 CSV / xlsx 导出。</div>
            <span class="tool-tag">IDPS · 规则库</span>
          </div>
          <div class="tool-card" data-on-click="toolDiffWafOpen">
            <div class="tool-ico">🛡️</div>
            <div class="tool-title">WAF 规则库 Diff</div>
            <div class="tool-desc">输入 vendor portal 规则库链接 (限 WAF 产品), 实时拉取所有升级包, 选择任意 2 个版本对比新增 / 修改 / 删除的规则, 支持 CSV / xlsx 导出。</div>
            <span class="tool-tag">WAF · 规则库</span>
          </div>
        </div>
      </div>
    </div>
  `;
}

// ── Rule Diff 子页 ──
// 共用 IDPS / WAF 两套入口;后端按 _rd.sourceKind 决定解析器
//   sourceKind='ips' → parse_rules (攻击[id]: 名称)
//   sourceKind='waf' → parse_rules_waf (一、新增规则 / 二、修改规则 / 三、删除规则)
// DIFF 工具前端白名单:跟后端 diff_routes._validate_diff_url 对齐
// IDPS 入口仅展示 IPS/IDS;WAF 入口仅展示 WAF (IDS-ICS/HWAF 仍被后端接受,仅前端不主动列出)
const _DIFF_WHITELIST = {
  ips: new Set(['网络入侵防护系统(IPS)','网络入侵检测系统(IDS)']),
  waf: new Set(['WEB应用防护系统(WAF)']),
};
const _DIFF_KIND_LABEL = { ips: 'IDPS', waf: 'WAF' };
// picker 弹层缓存:{[expectedKind]: productsArray}
const _toolDiffProductsCache = {};
const _rd={
  packages: [],
  url: '',
  sourceKind: 'ips',   // 子页打开时由 toolDiffRuleOpen / toolDiffWafOpen 设定
  selected: [],        // package_version 数组, 最多 2 个
  diffResult: null,
  filterVersion: null, // 当前过滤的版本号 (null = 不过滤)
};

// 根据 URL 嗅探 sourceKind (rdUrlValidate / rdCompare 容错用)
// 后端 /api/diff/fetch 已返回 source_kind,优先用后端的
function _rdSniffKind(url){
  if (!url) return 'ips';
  if (url.indexOf('/listWafV') >= 0 && url.indexOf('Detail') >= 0) return 'waf';
  return 'ips';
}

const _rdKindLabel = { ips: 'IDPS', waf: 'WAF' };

// 2026-07-12 重构: 入口只决定预期 kind,不再设 _rd.sourceKind 初值
// 真实 kind 由后端 fetch 返 source_kind 决定 (line 6566 处 _rd.sourceKind = j.data.source_kind)
// 防止「IDPS 入口粘 WAF URL」,在 rdUrlValidate / rdFetch 里校验强制
async function toolDiffRuleOpen(){
  return _toolDiffOpenImpl('🔍 IDPS 规则库 Diff',
    'https://update.nsfocus.com/update/listNewipsDetail/v/rule5.6.11_v2',
    'ips');
}

async function toolDiffWafOpen(){
  return _toolDiffOpenImpl('🛡️ WAF 规则库 Diff',
    'https://update.nsfocus.com/update/listWafV69Detail/v/rule',
    'waf');
}

async function _toolDiffOpenImpl(title, placeholder, expectedKind){
  // expectedKind: 'ips' | 'waf' — 入口的预期 kind
  // 实际 kind 还得看 URL sniff,前端双校验避免用户粘错
  _rd.expectedKind = expectedKind;
  const app=document.getElementById('app');
  app.innerHTML=`
    <div class="card">
      <div class="card-hd">
        ${title}
        <button class="btn btn-outline btn-sm ml-auto" data-on-click="toolsPage">← 返回工具列表</button>
      </div>
      <div class="card-bd">
        <div class="rd-toolbar">
          <button class="btn btn-outline" id="rdPickerBtn" data-on-click="toolDiffOpenPicker" data-args='${_dataArgs(expectedKind)}' title="从已有产品目录选择路径, 自动填 URL">📂 选择产品路径</button>
          <input type="text" id="rdUrl" placeholder="${placeholder}" title="可手动编辑 URL,或点「选择产品路径」自动填充">
          <button class="btn btn-primary" id="rdFetchBtn" data-on-click="rdFetch" data-args='${_dataArgs(false)}'>🚀 提取规则信息</button>
          <button class="btn btn-primary btn-sm" id="rdForceBtn" data-on-click="rdFetch" data-args='${_dataArgs(true)}' title="跳过本地缓存, 重新从 vendor portal 拉取">🔄 强制刷新</button>
          <span class="rd-status" id="rdStatus"></span>
        </div>
        <div id="rdUrlHint" data-css="display:none;font-size:12px;margin:-8px 0 14px;padding:0 4px;color:var(--muted)"></div>

        <div id="rdStep1">
          <div id="rdStep1Header" data-css="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
            <div data-css="font-size:13px;font-weight:600;color:var(--text)">
              ① 选择 2 个版本(只能选 2 个, 第 3 次勾选会自动取消最早选的)
            </div>
            <button class="btn btn-outline btn-sm" id="rdReselectBtn" data-css="display:none" data-on-click="rdReselect">✏️ 修改选择</button>
          </div>
          <div class="rd-table-container" id="rdPkgTableContainer">
            <table class="rd-table" id="rdPkgTable">
              <thead><tr>
                <th class="w36"></th><th class="w100">版本号</th><th class="w230">文件名</th><th class="w150">发布时间 (CST)</th><th class="w60">大小</th><th class="w260">MD5</th>
              </tr></thead>
              <tbody></tbody>
            </table>
          </div>
          <div id="rdSelectedBar" data-css="display:none;align-items:center;gap:10px;padding:10px 14px;background:var(--card);border:1px solid var(--border);border-radius:10px;margin-bottom:18px">
            <span id="rdSelectedText" data-css="font-size:13px;color:var(--text);flex:1"></span>
            <button class="btn btn-primary btn-sm" id="rdCompareBtn" data-on-click="rdCompare">🔄 生成对比结果</button>
          </div>
        </div>

        <div id="rdStep2" data-css="display:none">
          <div data-css="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text)">
            ② 对比结果
          </div>
          <div id="rdSummary"></div>
          <div id="rdFilterBar" data-css="display:none;margin-bottom:14px;font-size:12px;color:var(--muted)">
            当前过滤: <code id="rdFilterLabel" data-css="background:var(--primary-light);padding:2px 6px;border-radius:4px;color:var(--primary)"></code>
            <button class="btn btn-outline btn-xs ml8" data-on-click="rdClearFilter">清除过滤</button>
          </div>
          <div id="rdResultGroups"></div>
          <div data-css="display:flex;gap:8px;align-items:center;margin-top:14px">
            <button class="btn btn-primary" data-on-click="rdExport" data-args='${_dataArgs('xlsx')}'>📥 导出 xlsx</button>
            <button class="btn btn-success" data-on-click="rdExport" data-args='${_dataArgs('csv')}'>📥 导出 CSV</button>
            <button class="btn btn-outline" data-on-click="rdExport" data-args='${_dataArgs('csv', true)}'>📋 复制 CSV 到剪贴板</button>
            <span class="rd-status" id="rdExportStatus"></span>
          </div>
        </div>
      </div>
    </div>
  `;
  // 恢复上次输入的 URL + 实时校验
  // 按 kind 分 key (ips/waf 各自记忆,避免 IPS 选了 URL 后切到 WAF 还显示 IPS 的 URL)
  const lastKey = 'rd_last_url_' + expectedKind;
  const last = localStorage.getItem(lastKey);
  if (last) {
    document.getElementById('rdUrl').value = last;
    // 延迟一帧触发 input 事件以显示 hint
    setTimeout(() => document.getElementById('rdUrl').dispatchEvent(new Event('input')), 0);
  }
  document.getElementById('rdUrl').addEventListener('input', rdUrlValidate);
}

function rdToast(msg, type='info', timeout=4000){
  const t = document.getElementById('rdToast');
  if (!t) { alert(msg); return; }
  t.textContent = msg;
  t.className = 'rd-toast ' + type;
  t.style.display = 'block';
  clearTimeout(t._tm);
  t._tm = setTimeout(()=>{ t.style.display='none'; }, timeout);
}

// ── DIFF 工具:产品路径 picker ───────────────────────────────
// 打开 picker 弹层:从 /api/products 拉 active 产品,按白名单过滤,按 chain 末项分组
async function toolDiffOpenPicker(expectedKind){
  // 已有弹层则先关掉
  const existing = document.getElementById('rdPickerOverlay');
  if (existing) existing.remove();
  const whitelist = _DIFF_WHITELIST[expectedKind];
  if (!whitelist) { rdToast('未知工具类型: ' + expectedKind, 'error'); return; }
  const kindLabel = _DIFF_KIND_LABEL[expectedKind] || expectedKind;

  const overlay = document.createElement('div');
  overlay.id = 'rdPickerOverlay';
  overlay.className = 'rd-picker-overlay';
  overlay.onclick = (ev) => { if (ev.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div class="rd-picker-modal" >
      <div class="rd-picker-hd">
        <span class="rd-picker-title">📂 选择产品路径 · ${kindLabel}</span>
        <button class="btn btn-xs btn-outline" data-on-click="_removeById" data-args='${_dataArgs('rdPickerOverlay')}'>✕ 关闭</button>
      </div>
      <div class="rd-picker-bd">
        <div class="rd-picker-loading">加载产品目录中...</div>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  // 加载 (有缓存直接用)
  let products;
  try {
    if (_toolDiffProductsCache[expectedKind]) {
      products = _toolDiffProductsCache[expectedKind];
    } else {
      const r = await api('/system/products');
      if (r.code !== 0) throw new Error(r.message || '加载失败');
      // /api/system/products 返回 {code, data:{products:[], total, ...}}, data 是包装对象
      const dataObj = r.data || {};
      const all = Array.isArray(dataObj) ? dataObj : (dataObj.products || []);
      products = all.filter(p => p.is_active && whitelist.has(p.name));
      _toolDiffProductsCache[expectedKind] = products;
    }
  } catch (e) {
    const bd = overlay.querySelector('.rd-picker-bd');
    bd.innerHTML = `<div class="rd-picker-error">⚠️ 加载失败: ${escHtml(e.message || String(e))}<br><span class="muted-11">请手动输入 URL 或重试</span></div>`;
    // 保留弹层,用户可手动关闭或重试
    return;
  }

  const bd = overlay.querySelector('.rd-picker-bd');
  if (products.length === 0) {
    bd.innerHTML = `<div class="rd-picker-error">⚠️ 当前没有可用的 ${kindLabel} 产品 (白名单: ${[...whitelist].join(' / ')})<br><span class="muted-11">请先到「产品」页启用相应产品并完成包路径发现</span></div>`;
    return;
  }

  // 渲染:每个白名单产品一张卡片,卡片 body 用 buildDataTreeHtml 渲染规则库路径树
  // (跟采集数据左侧产品目录同款树形,只是过滤后只显示"规则升级包/规则库升级包"路径)
  const rdpKind = 'rdp_' + expectedKind;
  bd.innerHTML = products.map((p, idx) => {
    const allPaths = (p.package_type_discovered && p.package_type_discovered.paths) || [];
    // DIFF 工具只关心规则库对比 → 过滤 chain 任一段含"规则升级"或"规则库升级"
    // 兼容三种命名:规则升级包(标准) / 规则库升级包(IDPS) / 规则升级(信创海光,无"包"字)
    const ruleLibPaths = allPaths.filter(pth => {
      const chain = pth.chain || [];
      return chain.some(seg => /规则(?:库)?升级(?:包)?/.test(seg));
    });
    const cardId = `${rdpKind}_card_${idx}`;
    const bodyId = cardId + '_body';
    let treeHtml;
    if (allPaths.length === 0) {
      treeHtml = `<div class="muted-12 p8x12">该产品尚未完成包路径发现,请先到「产品」页执行「发现」</div>`;
    } else if (ruleLibPaths.length === 0) {
      treeHtml = `<div class="muted-12 p8x12">该产品下没有规则库路径 (共 ${allPaths.length} 条 path, 无匹配)</div>`;
    } else {
      // 复用采集数据左侧目录的 buildDataTreeHtml 渲染树形结构
      // sid 用独立 namespace 避免跟正常采集数据页冲突; snapshotsMap 传空 (DIFF 不需要快照)
      treeHtml = buildDataTreeHtml({paths: ruleLibPaths}, {}, typeCN, {}, '', `${rdpKind}_${idx}`);
    }
    return `<div class="rd-picker-product" id="${cardId}">
      <div class="rd-picker-prod-hd" data-on-click="toolDiffToggleProduct" data-args='${_dataArgs('$el')}'>
        <span class="rd-picker-arrow">▶</span>
        <span>${escHtml(p.name)}</span>
        <span class="rd-picker-count">${ruleLibPaths.length} / ${allPaths.length} paths</span>
      </div>
      <div class="rd-picker-paths-wrap" id="${bodyId}" data-css="display:none">${treeHtml}</div>
    </div>`;
  }).join('');

  // 给所有 tree leaf 节点追加一个明显的「✅ 选取」按钮(推到右侧)
  // buildDataTreeHtml 内置的 leaf 只有 span 文字,视觉上不可点;加按钮让选择行为明确
  bd.querySelectorAll('.tree-node[data-isleaf="1"]').forEach(leaf => {
    // 防止重复注入 (再次打开弹层时)
    if (leaf.querySelector('.rd-picker-select-btn')) return;
    const url = leaf.getAttribute('data-url') || '';
    const chainJson = leaf.getAttribute('data-chain') || '[]';
    const btn = document.createElement('button');
    btn.className = 'rd-picker-select-btn btn btn-xs btn-primary';
    btn.textContent = '✅ 选取';
    btn.style.marginLeft = 'auto';
    btn.style.flexShrink = '0';
    btn.onclick = (ev) => {
      ev.stopPropagation();  // 阻止 leaf div 上的 click 委托
      let chainStr = '';
      try { chainStr = JSON.parse(chainJson).join(' › '); } catch (e) {}
      toolDiffSelectPathByUrl(expectedKind, url, chainStr);
    };
    leaf.appendChild(btn);
  });

  // 拦截 tree leaf 节点的 click (默认行为是弹右边面板/pktbl_*),改为"选这条路径"
  // 用事件委托绑到弹层容器 (点 leaf 文字部分触发,点按钮不会被触发因为 stopPropagation)
  overlay.addEventListener('click', function(ev){
    // 忽略按钮自身的 click (它已经 stopPropagation)
    if (ev.target.closest('.rd-picker-select-btn')) return;
    const leafNode = ev.target.closest('.tree-node[data-isleaf="1"]');
    if (!leafNode) return;
    ev.preventDefault();
    const url = leafNode.getAttribute('data-url') || '';
    const chainJson = leafNode.getAttribute('data-chain') || '[]';
    let chainStr = '';
    try { chainStr = JSON.parse(chainJson).join(' › '); } catch (e) {}
    toolDiffSelectPathByUrl(expectedKind, url, chainStr);
  });
}

// 选取路径后:写 URL + 触发 fetch (新版本从 tree-node data-url 直接拿)
function toolDiffSelectPathByUrl(expectedKind, url, chainStr){
  if (!url) { rdToast('该路径没有 URL,无法提取', 'error'); return; }
  const fullUrl = url.startsWith('http') ? url : ('https://update.nsfocus.com' + (url.startsWith('/') ? url : '/' + url));
  // input 一直可见,直接写 URL 即可 (用户可继续手动编辑)
  document.getElementById('rdUrl').value = fullUrl;
  // 关弹层
  const overlay = document.getElementById('rdPickerOverlay');
  if (overlay) overlay.remove();
  // 触发 fetch (用户主动选择路径,直接进入提取流程)
  rdFetch(false);
  // 顺便刷新一下链路反查 hint (rdFetch 不显示链路,rdUrlValidate 会异步覆盖)
  setTimeout(() => { if (typeof rdUrlValidate === 'function') rdUrlValidate(); }, 50);
}

// 旧版 from cache:保留作兼容,新 picker 走 tree 事件委托不再调它
function toolDiffSelectPath(expectedKind, prodIdx, pathIdx){
  const products = _toolDiffProductsCache[expectedKind] || [];
  const p = products[prodIdx];
  if (!p) return;
  const paths = (p.package_type_discovered && p.package_type_discovered.paths) || [];
  const pth = paths[pathIdx];
  if (!pth) return;
  const url = pth.url || '';
  const fullUrl = url.startsWith('http') ? url : ('https://update.nsfocus.com' + (url.startsWith('/') ? url : '/' + url));
  document.getElementById('rdUrl').value = fullUrl;
  // 关弹层 + 触发 fetch
  const overlay = document.getElementById('rdPickerOverlay');
  if (overlay) overlay.remove();
  rdFetch(false);
}

// 展开/折叠单个产品的 paths 列表
function toolDiffToggleProduct(hdEl){
  hdEl.classList.toggle('open');
  const product = hdEl.parentElement;
  const wrap = product && product.querySelector('.rd-picker-paths-wrap');
  if (wrap) wrap.style.display = wrap.style.display === 'none' ? '' : 'none';
}

async function rdFetch(force){
  force = !!force;
  const url = document.getElementById('rdUrl').value.trim();
  if (!url) { rdToast('请输入 vendor portal 链接', 'error'); return; }
  // 2026-07-12 入口校验: 用户在 IDPS 入口粘 WAF URL (或反之) → 拒绝,引导切工具
  // 跟 rdUrlValidate 对称:嗅探 URL 路径决定 sniffKind
  const _isIpsPath = url.indexOf('/listNewipsDetail') >= 0;
  const _isWafPath = url.indexOf('/listWafV') >= 0 && url.indexOf('Detail') >= 0;
  const sniffKind = _isIpsPath ? 'ips' : _isWafPath ? 'waf' : '';
  if (sniffKind && sniffKind !== _rd.expectedKind) {
    rdToast('URL 是 ' + _rdKindLabel[sniffKind] + ' 链接, 当前是 ' + _rdKindLabel[_rd.expectedKind] + ' 工具,请切到对应入口或改正 URL', 'error', 6000);
    status.textContent = '';
    btn.disabled = false; fbtn.disabled = false;
    btn.textContent = '🚀 提取规则信息';
    return;
  }
  localStorage.setItem('rd_last_url_' + _rd.expectedKind, url);

  const btn = document.getElementById('rdFetchBtn');
  const fbtn = document.getElementById('rdForceBtn');
  const status = document.getElementById('rdStatus');
  btn.disabled = true; fbtn.disabled = true;
  btn.textContent = '⏳ 提取中...';
  status.textContent = '';
  _rd.packages = []; _rd.selected = []; _rd.diffResult = null; _rd.filterVersion = null;
  document.getElementById('rdStep1').style.display = 'none';
  document.getElementById('rdStep2').style.display = 'none';

  const t0 = Date.now();
  try {
    const qs = '?url=' + encodeURIComponent(url) + (force ? '&force=1' : '');
    const j = await api('/diff/fetch' + qs);
    if (j.code !== 0) {
      rdToast('提取失败: ' + (j.message || '网络错误'), 'error', 6000);
      status.textContent = '';
      return;
    }
    _rd.packages = j.data.packages || [];
    _rd.url = j.data.url;
    // 后端 source_kind 是权威值(基于 URL 白名单匹配);优先用它覆盖 _rd.sourceKind
    // 这样即使子页打开时设了 ips 但用户粘了 WAF 链接,后续 compare 也会走 waf parser
    if (j.data.source_kind === 'waf' || j.data.source_kind === 'ips') {
      _rd.sourceKind = j.data.source_kind;
    }
    const cacheTag = j.data.cache === 'hit'
      ? `<span data-css="color:var(--success);font-weight:500">📦 来自缓存</span> (${j.data.fetched_at || ''} CST, ${((Date.now()-t0)/1000).toFixed(1)}s)`
      : `<span data-css="color:var(--primary);font-weight:500">🌐 新拉取</span> (${((Date.now()-t0)/1000).toFixed(1)}s)`;
    status.innerHTML = `✓ 共 ${_rd.packages.length} 个升级包 ${cacheTag}`;
    rdRenderPkgTable();
    document.getElementById('rdStep1').style.display = 'block';
  } catch (e) {
    rdToast('提取失败: ' + e.message, 'error', 6000);
    status.textContent = '✗ ' + e.message;
  } finally {
    btn.disabled = false; fbtn.disabled = false;
    btn.textContent = '🚀 提取规则信息';
  }
}

// 实时校验 URL 是否在 IDPS / WAF 白名单,给用户友好提示(不阻塞输入)
// 子页打开时根据 _rd.sourceKind 决定默认接受哪种 URL:
//   ips → listNewipsDetail/(IPS/IDS/IDS-ICS)
//   waf → listWafV...Detail/(WAF/HWAF)
// 也接受用户切换 URL(混合嗅探):只要后端 _validate_diff_url 能识别就放行
async function rdUrlValidate(){
  const url = document.getElementById('rdUrl').value.trim();
  const hint = document.getElementById('rdUrlHint');
  if (!url) { hint.style.display = 'none'; return; }

  // 后端只接受 update.nsfocus.com 域名 + 两种已知 detail 路径
  const isVendorDomain = url.startsWith('https://update.nsfocus.com/');
  const isIpsPath = url.indexOf('/listNewipsDetail') >= 0;
  const isWafPath = url.indexOf('/listWafV') >= 0 && url.indexOf('Detail') >= 0;

  if (!isVendorDomain || (!isIpsPath && !isWafPath)) {
    hint.style.display = 'block';
    hint.innerHTML = '<span class="text-danger">⚠️ 仅支持 update.nsfocus.com 域名的 listNewipsDetail/ (IPS / IDS) 或 listWafV...Detail/ (WAF) 详情页</span>';
    return;
  }

  // 嗅探用户实际粘的 URL 类型 (跟入口 expectedKind 对比校验)
  const sniffKind = isWafPath ? 'waf' : 'ips';
  const expected = _rd.expectedKind;
  const mismatch = expected && sniffKind !== expected;
  const fetchBtn = document.getElementById('rdFetchBtn');
  const forceBtn = document.getElementById('rdForceBtn');
  if (mismatch) {
    // 入口跟 URL 不匹配:置灰按钮 + 红色警告
    if (fetchBtn) fetchBtn.disabled = true;
    if (forceBtn) forceBtn.disabled = true;
    hint.style.display = 'block';
    hint.innerHTML = `<span class="danger-600">🚫 入口跟 URL 不匹配</span> · 当前是 <b>${_rdKindLabel[expected]}</b> 工具,但 URL 是 <b>${_rdKindLabel[sniffKind]}</b> 链接。请点 ← 返回切到 <b>${_rdKindLabel[sniffKind]}</b> 工具,或改正 URL`;
    return;
  } else {
    if (fetchBtn) fetchBtn.disabled = false;
    if (forceBtn) forceBtn.disabled = false;
  }
  const mismatchTip = '';

  // 看缓存里有没有现成的 cache-status 可查
  hint.style.display = 'block';
  hint.innerHTML = '<span class="text-muted">🔍 校验中...</span>';

  // 2026-07-14: 反推 URL 对应的 chain 路径(从 _dataAll 里的 package_type.paths 查)
  // _dataAll 未加载时主动 loadDataAll() 一次(in flight 用 _rdChainLoading 防重复请求)
  const chainPromise = (async () => {
    if (!_dataAll || !Object.keys(_dataAll).length) {
      if (!_rd._chainLoading) {
        _rd._chainLoading = loadDataAll();
      }
      await _rd._chainLoading;
      _rd._chainLoading = null;
    }
    try {
      const u = new URL(url);
      // 2026-07-14: _srcUrlChainMap 同 path 会被后写覆盖(只能存 1 条 chain),
      // 但 vendor portal 经常把同一 URL 挂在多个子分类下(比如 IPS rule5.6.11_v2
      // 出现在 5 个子分类:标准/飞腾/海光/海光P/海光U)。所以这里**直接遍历 _dataAll**
      // 找全部 chain,绕过 map 覆盖问题。
      const stripped = (u.pathname || '').replace(/\/+$/, '').replace(/^\/+/, '') || '';
      const matches = [];
      const seenChain = new Set();
      const seenSid = new Set();
      for (const [sid, src] of Object.entries(_dataAll || {})) {
        if (seenSid.has(sid)) continue;
        seenSid.add(sid);
        const paths = (src.package_type && src.package_type.paths) || [];
        for (const p of paths) {
          if (!p || !p.url || !p.chain || !p.chain.length) continue;
          // 容错匹配:单/双/无前导斜杠都试
          const pu = String(p.url).replace(/\/+$/, '');
          if (pu !== stripped && '/' + pu !== stripped && pu.replace(/^\//, '') !== stripped) continue;
          const key = p.chain.join('||');
          if (seenChain.has(key)) continue;
          seenChain.add(key);
          matches.push({
            chain: p.chain,
            srcName: src.display_name || src.name || ''
          });
        }
      }
      return matches;
    } catch (_) { /* invalid URL — ignore */ }
    return [];
  })();

  try {
    const j = await api('/diff/cache-status?url=' + encodeURIComponent(url));
    let baseHint;
    if (j.code === 0 && j.data && j.data.exists) {
      const age = j.data.age_seconds || 0;
      const ageStr = age < 60 ? `${age}秒前` : age < 3600 ? `${Math.floor(age/60)}分钟前` : `${Math.floor(age/3600)}小时前`;
      baseHint = `<span class="text-success">📦 有 ${ageStr} 的本地缓存,点「提取」秒级返回;如需最新数据点「强制刷新」覆盖</span>`;
    } else {
      baseHint = `<span class="text-muted">🌐 无缓存,提取时将实时拉取 vendor portal (约 2-5 秒)</span>`;
    }
    // 链路径(可能有 0 条 = URL 不在已采集产品里,或多条 = 跨产品共用 URL)
    const matches = await chainPromise;
    let chainHtml = '';
    if (matches && matches.length) {
      // 纯文本格式(贴工单/微信友好),转 > 替换 ›
      const plainText = matches.map((m, i) =>
        `${i + 1}. ` + m.chain.join(' > ') + (m.srcName ? `  [${m.srcName}]` : '')
      ).join('\n');
      const headerText = `链路 (${matches.length} 条匹配):`;
      chainHtml = '<div data-css="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">';
      chainHtml += `<div data-css="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">`;
      chainHtml += `<span data-css="color:var(--muted);font-weight:600">📍 ${headerText}</span>`;
      chainHtml += `<button class="btn btn-xs btn-outline" data-on-click="rdCopyChains" data-args='${_dataArgs(headerText + '\n' + plainText)}' title="复制为纯文本(可贴工单/微信)">📋 复制链路</button>`;
      chainHtml += `</div>`;
      matches.forEach((m, i) => {
        const segments = m.chain.map(s => `<span class="text-primary">${escHtml(s)}</span>`).join(` <span class="text-muted">›</span> `);
        const srcLabel = m.srcName ? `<span class="muted-11"> [${escHtml(m.srcName)}]</span>` : '';
        chainHtml += `<div data-css="margin-top:2px;padding-left:14px">${i + 1}. ${segments}${srcLabel}</div>`;
      });
      chainHtml += '</div>';
    }
    hint.innerHTML = baseHint + mismatchTip + chainHtml;
  } catch (e) {
    hint.style.display = 'none';
  }
}

// 复制链路到剪贴板(rdUrlValidate 「📋 复制链路」按钮调用)
// text = 转义后的字符串,JS 里 \\n 是换行,' \\' 是单引号,需要还原
function rdCopyChains(text){
  text = String(text == null ? '' : text);
  const done = () => rdToast('链路已复制到剪贴板 (' + text.split('\n').length + ' 行)', 'success', 2000);
  const fail = (e) => rdToast('复制失败: ' + e.message, 'error', 4000);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(fail);
  } else {
    // 旧浏览器 fallback:用临时 textarea + execCommand
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); }
    catch (e) { fail(e); }
    finally { document.body.removeChild(ta); }
  }
}

function rdRenderPkgTable(){
  const tbody = document.querySelector('#rdPkgTable tbody');
  if (!_rd.packages.length) {
    tbody.innerHTML = '<tr><td class="ta-center p30 text-muted" colspan="6">无数据</td></tr>';
    return;
  }
  // 升序展示(老的在上, 新的在下)
  tbody.innerHTML = _rd.packages.map(p => {
    const isSel = _rd.selected.includes(p.package_version);
    const pubCST = p.published_at ? p.published_at.replace(' ', ' ').slice(0, 19) : '—';
    return `<tr class="${isSel ? 'rd-pkg-selected' : ''}" data-ver="${escHtml(p.package_version)}">
      <td><input type="checkbox" class="rd-pkg-cb" data-ver="${escHtml(p.package_version)}" ${isSel ? 'checked' : ''} data-on-change="rdToggleSel" data-args='${_dataArgs(p.package_version)}'></td>
      <td class="font-mono">${escHtml(p.package_version)}</td>
      <td data-css="font-size:12px;color:var(--muted);max-width:230px;word-break:break-all;line-height:1.4" title="${escHtml(p.file_name)}">${escHtml(p.file_name)}</td>
      <td class="fs12">${pubCST}</td>
      <td class="fs12">${p.file_size_raw || '—'}</td>
      <td data-css="font-size:11px;font-family:monospace;color:var(--muted);word-break:break-all" title="${escHtml(p.md5_hash||'')}">${p.md5_hash || '<span class="text-muted">—</span>'}</td>
    </tr>`;
  }).join('');
}

function rdToggleSel(ver){
  const i = _rd.selected.indexOf(ver);
  if (i >= 0) {
    _rd.selected.splice(i, 1);
  } else {
    if (_rd.selected.length >= 2) {
      // 自动取消最早选的 (决策 6a)
      _rd.selected.shift();
    }
    _rd.selected.push(ver);
  }
  // 2 个版本选满后,收起 Step1 表格 + 显示 selected bar
  const table = document.getElementById('rdPkgTableContainer');
  const bar = document.getElementById('rdSelectedBar');
  const reselectBtn = document.getElementById('rdReselectBtn');
  if (_rd.selected.length >= 2) {
    table.style.display = 'none';
    bar.style.display = 'flex';
    reselectBtn.style.display = 'inline-flex';
    document.getElementById('rdSelectedText').innerHTML =
      `已选: <b class="font-mono">${_rd.selected[0]}</b>  →  <b class="font-mono">${_rd.selected[1]}</b>`;
  } else {
    rdRenderPkgTable();  // 展开状态需要刷新表格来更新选中样式
    table.style.display = 'block';
    bar.style.display = 'none';
    reselectBtn.style.display = 'none';
    document.getElementById('rdSelectedText').textContent =
      _rd.selected.length === 0 ? '未选择' : `已选: ${_rd.selected[0]} (还需选 1 个)`;
  }
}

function rdReselect(){
  // 重新展开表格 + 清空已选 + 隐藏 Step2
  _rd.selected = [];
  _rd.diffResult = null;
  document.getElementById('rdStep2').style.display = 'none';
  document.getElementById('rdPkgTableContainer').style.display = 'block';
  document.getElementById('rdSelectedBar').style.display = 'none';
  document.getElementById('rdReselectBtn').style.display = 'none';
  rdRenderPkgTable();
  rdClearFilter();
}

async function rdCompare(){
  if (_rd.selected.length !== 2) return;
  const btn = document.getElementById('rdCompareBtn');
  btn.disabled = true; btn.textContent = '⏳ 对比中...';
  document.getElementById('rdStep2').style.display = 'none';
  try {
    const j = await api('/diff/compare', {
      method: 'POST',
      body: JSON.stringify({
        from_version: _rd.selected[0],
        to_version:   _rd.selected[1],
        packages:     _rd.packages,
        source_kind:  _rd.sourceKind,
      }),
    });
    if (j.code !== 0) { rdToast('对比失败: ' + (j.message || '网络错误'), 'error', 6000); return; }
    _rd.diffResult = j.data;
    rdRenderResult();
    document.getElementById('rdStep2').style.display = 'block';
  } catch (e) {
    rdToast('网络错误: ' + e.message, 'error', 6000);
  } finally {
    btn.disabled = false; btn.textContent = '🔄 生成对比结果';
  }
}

function rdRenderResult(){
  const r = _rd.diffResult;
  if (!r) return;
  const stats = r.stats || {};
  const intermediates = stats.intermediate_versions || [];
  const fromV = r.from_version, toV = r.to_version;
  const clickableVersions = [...intermediates, toV];

  // 摘要: 5 项 (FROM/TO/中间数/净新增/净更新/净删除) + 可点击版本区域
  document.getElementById('rdSummary').innerHTML = `
    <div class="rd-summary-bar">
      <div class="rd-summary-item"><span class="label">FROM 版本</span><span class="val">${fromV}</span></div>
      <div class="rd-summary-item"><span class="label">TO 版本</span><span class="val">${toV}</span></div>
      <div class="rd-summary-item"><span class="label">中间版本数</span><span class="val">${intermediates.length}</span></div>
      <div class="rd-summary-item text-success"><span class="label">新增</span><span class="val">${(r.pure_new||[]).length}</span></div>
      <div class="rd-summary-item text-primary"><span class="label">修改</span><span class="val">${(r.pure_updated||[]).length}</span></div>
      <div class="rd-summary-item text-danger"><span class="label">删除</span><span class="val">${(r.pure_deleted||[]).length}</span></div>
    </div>
    ${clickableVersions.length > 0
      ? `<div data-css="font-size:12px;color:var(--muted);margin-bottom:14px">
          📍 点击版本过滤结果:
          ${intermediates.map(v => `<a href="#" data-prevent class="rd-version-chip" data-ver="${escHtml(v)}" data-on-click="rdApplyFilter" data-args='${_dataArgs(v)}'><code>${v}</code></a>`).join(' ')}
          <a href="#" data-prevent class="rd-version-chip rd-version-latest" data-ver="${escHtml(toV)}" data-on-click="rdApplyFilter" data-args='${_dataArgs(toV)}'><code>最新 ${toV}</code></a>
        </div>`
      : ''}
    ${!stats.from_has_rule_blocks
      ? `<div class="info-bar warn-box">⚠️ FROM 版本 (${fromV}) 描述中未找到「新增规则/更新规则」清单, 可能是较老的版本格式. 净新增/修改/删除数字可能不准确.</div>`
      : ''}
    ${!stats.to_has_rule_blocks
      ? `<div class="info-bar warn-box">⚠️ TO 版本 (${toV}) 描述中未找到「新增规则/更新规则」清单, 较老的版本格式. 净新增/修改/删除数字可能不准确.</div>`
      : ''}
  `;
  rdRenderGroups();
}

// 辅助: 把 "2.0.0.45209" 拆成 [2, 0, 0, 45209] 用于排序
function _rdVerParts(v){ return (v||'').split('.').map(s => /^\d+$/.test(s) ? parseInt(s, 10) : s); }
function _rdVerCmp(a, b){
  const pa = _rdVerParts(a), pb = _rdVerParts(b);
  const n = Math.max(pa.length, pb.length);
  for (let i = 0; i < n; i++) {
    const x = pa[i] !== undefined ? pa[i] : 0;
    const y = pb[i] !== undefined ? pb[i] : 0;
    if (typeof x === 'number' && typeof y === 'number') { if (x !== y) return x - y; }
    else { const s = String(x).localeCompare(String(y)); if (s !== 0) return s; }
  }
  return 0;
}
// 降序
function _rdVerCmpDesc(a, b){ return _rdVerCmp(b, a); }

function rdRenderGroups(){
  const r = _rd.diffResult;
  if (!r) return;
  const filterVer = _rd.filterVersion;  // null 或 "2.0.0.45151" 之类
  const container = document.getElementById('rdResultGroups');
  const filterBar = document.getElementById('rdFilterBar');
  if (filterVer) {
    filterBar.style.display = 'block';
    document.getElementById('rdFilterLabel').textContent = filterVer;
  } else {
    filterBar.style.display = 'none';
  }

  // 1) 建立所有出现过的版本号 → 全局 rank (用于排序 + 过滤)
  //    rank 按版本号升序: rank 越大 = 版本越新
  const allVersions = [...new Set([
    ...(r.pure_new || []).map(x => x.first_added_in),
    ...(r.pure_updated || []).flatMap(x => x.updated_in_versions || []),
    r.from_version, r.to_version,
  ].filter(Boolean))].sort(_rdVerCmp);
  const rankOf = v => allVersions.indexOf(v);

  // 2) 过滤 + 排序
  //    新增组: 按 first_added_in 降序 (最新版本在最上)
  const newItems = (r.pure_new || []).filter(x =>
    !filterVer || x.first_added_in === filterVer
  );
  newItems.sort((a, b) => rankOf(b.first_added_in) - rankOf(a.first_added_in));

  //    更新组: 组内按"该规则被更新的最高版本"降序,同版本按 id 升序
  const updItems = (r.pure_updated || []).filter(x =>
    !filterVer || (x.updated_in_versions || []).includes(filterVer)
  );
  updItems.sort((a, b) => {
    const aMax = Math.max(...(a.updated_in_versions || []).map(v => rankOf(v)));
    const bMax = Math.max(...(b.updated_in_versions || []).map(v => rankOf(v)));
    if (aMax !== bMax) return bMax - aMax;
    return a.id - b.id;
  });

  //    删除组: 不依赖版本号,过滤时如果选了具体版本 = 隐藏(因为"删除"是相对 from 的整体观察)
  //    按规则号升序展示
  const delItems = (filterVer ? [] : (r.pure_deleted || [])).slice();
  delItems.sort((a, b) => a.id - b.id);

  // 3) 渲染 3 个分组
  const renderGroup = (title, icon, color, count, items, typeKey) => {
    const emptyNote = filterVer ? `在 <code>${filterVer}</code> 中无${typeKey === 'new' ? '新增' : typeKey === 'updated' ? '更新' : '删除'}规则` : null;
    if (items.length === 0 && emptyNote) {
      return `<div class="rd-group" data-css="margin-bottom:12px;color:var(--muted);font-size:13px">${icon} ${title}: ${emptyNote}</div>`;
    }
    if (items.length === 0) return '';  // 未过滤时该组 0 条,不渲染标题
    const headers = {
      new:     ['规则号', '规则名称', '首次新增于'],
      updated: ['规则号', '规则名称', '更新于'],
      deleted: ['规则号', '规则名称', 'FROM 中归属'],
    }[typeKey];
    const widths = {
      new:     '140px',
      updated: '200px',
      deleted: '100px',
    }[typeKey];
    return `
      <div class="rd-group" data-css="margin-bottom:18px">
        <div data-css="font-size:13px;font-weight:600;margin-bottom:8px;color:${color};display:flex;align-items:center;gap:8px">
          ${icon} ${title}
          <span data-css="font-size:11px;font-weight:normal;color:var(--muted)">(${items.length} 条${filterVer ? ` · 过滤自 ${count} 条` : ''})</span>
        </div>
        <div class="rd-table-container" data-css="max-height:none">
          <table class="rd-table">
            <thead><tr>
              <th class="w90">${headers[0]}</th>
              <th>${headers[1]}</th>
              <th data-css="width:${widths}">${headers[2]}</th>
            </tr></thead>
            <tbody>${items.map(x => {
              if (typeKey === 'new') {
                return `<tr>
                  <td><code class="rd-cve">[${x.id}]</code></td>
                  <td>${escHtml(x.name)}</td>
                  <td><a href="#" data-prevent class="rd-version-chip" data-on-click="rdApplyFilter" data-args='${_dataArgs(x.first_added_in)}'><code>${x.first_added_in || ''}</code></a></td>
                </tr>`;
              } else if (typeKey === 'updated') {
                const versions = (x.updated_in_versions || []).slice().sort(_rdVerCmpDesc);
                return `<tr>
                  <td><code class="rd-cve">[${x.id}]</code></td>
                  <td>${escHtml(x.name)}</td>
                  <td class="rd-versions">${versions.map(v => `<a href="#" data-prevent class="rd-version-chip rd-version-inline" data-on-click="rdApplyFilter" data-args='${_dataArgs(v)}'><code>${v}</code></a>`).join(' ')}</td>
                </tr>`;
              } else {
                // deleted
                return `<tr>
                  <td><code class="rd-cve">[${x.id}]</code></td>
                  <td>${escHtml(x.name)}</td>
                  <td data-css="font-size:12px;color:var(--muted)">${fromVer || ''}</td>
                </tr>`;
              }
            }).join('')}</tbody>
          </table>
        </div>
      </div>
    `;
  };

  const fromVer = r.from_version;
  container.innerHTML =
    renderGroup('新增规则', '🟢', 'var(--success)', (r.pure_new || []).length, newItems, 'new') +
    renderGroup('修改规则', '🔵', 'var(--primary)', (r.pure_updated || []).length, updItems, 'updated') +
    renderGroup('删除规则', '🔴', 'var(--danger)', (r.pure_deleted || []).length, delItems, 'deleted') ||
    '<div class="ta-center p30 text-muted">无规则数据</div>';
}

function rdApplyFilter(ver){
  _rd.filterVersion = ver;
  rdRenderGroups();
  // 滚动到结果区域
  document.getElementById('rdStep2').scrollIntoView({behavior:'smooth', block:'start'});
}

function rdClearFilter(){
  _rd.filterVersion = null;
  rdRenderGroups();
}

async function rdExport(fmt, copy=false){
  if (!_rd.diffResult) return;
  const from = _rd.diffResult.from_version, to = _rd.diffResult.to_version;
  const filename = `rule_diff_${from}_to_${to}`.replace(/\./g, '_');
  if (copy) {
    // 直接生成 CSV 文本给剪贴板
    const r = await fetch(API + '/diff/export', {
      method: 'POST',
      headers: _authHeaders(),
      body: JSON.stringify({ diff_result: _rd.diffResult, format: 'csv', filename }),
    });
    const txt = await r.text();
    try {
      await navigator.clipboard.writeText(txt);
      rdToast('已复制 CSV 到剪贴板 (含 BOM, Excel 直接粘贴可识别)', 'success', 3000);
    } catch (e) {
      rdToast('复制失败: ' + e.message + ' — 请改用「导出 CSV」下载', 'error');
    }
    return;
  }
  // 走 download 流程
  const r = await fetch(API + '/diff/export', {
    method: 'POST',
    headers: _authHeaders(),
    body: JSON.stringify({ diff_result: _rd.diffResult, format: fmt, filename }),
  });
  if (!r.ok) { rdToast('导出失败: HTTP ' + r.status, 'error'); return; }
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename + '.' + fmt;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
  rdToast(`已下载 ${filename}.${fmt}`, 'success', 2500);
}
