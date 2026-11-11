// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// 产品发现/刷新/确认三处轮询的取消入口；登出时由 core._clearAllTimers 调用
let _productsPollCleanup=null;
function stopProductsPolls(){
  if(!_productsPollCleanup)return;
  try{_productsPollCleanup();}catch(e){}
  _productsPollCleanup=null;
}
// ── 缩写→中文映射（同时支持 raw key 和完整中文名）────────────────────────────
const PKGMAP = {
  // 缩写 key
  sys:'系统', rule:'规则', nti:'威胁情报', av:'病毒库', apprule:'应用规则',
  url:'URL库', wcs:'恶意站点', judge:'研判', geo:'地理库', interface:'接口',
  special:'特殊', other:'其他', merge:'合并', client:'客户端',
  av_stream:'流式病毒库', test:'测试', engine:'引擎',
  // 完整中文名（WAF 等产品的 chain[-1] 格式）
  '系统升级包':'系统', '规则升级包':'规则', '威胁情报升级包':'威胁情报',
  '病毒库升级包':'病毒库', '应用规则库升级包':'应用规则', 'URL分类库升级包':'URL库',
  '恶意站点库升级包':'恶意站点', '研判规则库升级包':'研判', '地理库升级包':'地理库',
  '接口升级包':'接口', '特殊升级包':'特殊', '其他升级包':'其他',
  '合并升级包':'合并', '客户端':'客户端', '流式病毒库升级包':'流式病毒库',
  '测试升级包':'测试', '引擎升级包':'引擎',
};

function _mapType(t) {
  // 先精确匹配
  if (t in PKGMAP) return PKGMAP[t];
  // 后缀匹配（处理 WAF V6.0.9系统升级包 → 系统）
  for (const [suffix, label] of Object.entries(PKGMAP)) {
    if (t.endsWith(suffix)) return label;
  }
  // 前缀+关键词归约（处理 WAF规则包、海光系列 V6.0.9系统升级等）
  if (t.includes('规则')) return '规则';
  if (t.includes('系统升级') || t.endsWith('系统')) return '系统';
  if (t.includes('引擎升级') || t.includes('引擎')) return '引擎';
  if (t.includes('威胁情报')) return '威胁情报';
  if (t.includes('合并升级') || t.includes('合并')) return '合并';
  // 纯版本号类（如 WAF V6.0.3）归为其他
  if (/V?\d+\.\d+/.test(t) && !t.includes('升级')) return '其他';
  return t;
}

// 旧格式 raw key 列表
const _rawKeys = new Set(['sys','rule','nti','av','apprule','url','wcs','judge','geo','interface','special','other','merge','client','av_stream','test','engine']);

// 从 typesObj.paths 归约统计
// 核心：直接从 paths.types 计数，用 PKGMAP 归约分组
function _groupPkgTypes(typesObj) {
  const {types, paths} = typesObj;
  if (!paths || !paths.length) return types ? types.map(_mapType).join(', ') : '';

  // 统计 paths.types 中每个 type 的出现次数
  const cnt = {};
  paths.forEach(p => { (p.types || []).forEach(t => { cnt[t] = (cnt[t]||0) + 1; }); });

  // 判断是 raw key 模式还是完整中文名模式
  const rawKeysInCnt = Object.keys(cnt).filter(k => _rawKeys.has(k));
  if (rawKeysInCnt.length > 0) {
    // raw key 模式（如 sys/rule/nti）
    const sorted = rawKeysInCnt.sort((a,b) => (cnt[b]||0) - (cnt[a]||0));
    return sorted.map(k => `${_mapType(k)}(${cnt[k]})`).join(', ');
  }

  // 完整中文名模式：用 PKGMAP 归约分组
  const merged = {};
  Object.keys(cnt).forEach(t => {
    const base = _mapType(t); // "WAF V6.0.9系统升级包" → "系统"
    merged[base] = (merged[base]||0) + cnt[t];
  });

  const sorted = Object.keys(merged).sort((a,b) => merged[b]-merged[a]);
  return sorted.map(k => `${k}(${merged[k]})`).join(', ');
}

function _fmtOldTypes(arr) {
  return arr.map(t => _mapType(t)).join(', ');
}

const PKG_TYPE_OPTIONS=[
  {code:'sys',     label:'系统升级包'},
  {code:'rule',    label:'规则升级包'},
  {code:'nti',     label:'威胁情报升级包'},
  {code:'av',      label:'病毒特征库升级包'},
  {code:'apprule', label:'应用规则库升级包'},
  {code:'url',     label:'URL分类库升级包'},
  {code:'wcs',     label:'恶意站点库升级包'},
  {code:'judge',   label:'研判规则库升级包'},
  {code:'geo',     label:'地理库升级包'},
  {code:'interface',label:'接口升级包'},
  {code:'special', label:'特殊升级包'},
  {code:'other',   label:'其他升级包'},
  {code:'merge',   label:'合并升级包'},
  {code:'client',  label:'客户端'},
  {code:'av_stream',label:'流式病毒库升级包'},
  {code:'test',    label:'测试包'},
];


// 从 paths 数组构建可折叠目录树 HTML
function buildTreeHtml(pt, MAP) {
  const paths = pt.paths || [];
  if (paths.length === 0) return '<span data-css="color:var(--gray-disabled)">无路径数据</span>';

  const root = {};
  paths.forEach(p => {
    const chain = p.chain || [];
    if (chain.length === 0) return;
    let node = root;
    chain.forEach((name, idx) => {
      if (!node._children) node._children = {};
      if (!node._children[name]) node._children[name] = { _children: {}, _types: [], _isPkg: false };
      node = node._children[name];
      if (idx === chain.length - 1) { node._isPkg = true; node._types = p.types || []; }
    });
  });

  function renderNode(node, name, depth) {
    const indent = depth * 16;
    const children = node._children ? Object.entries(node._children) : [];
    const hasChildren = children.length > 0;
    const isPkg = node._isPkg;
    const types = node._types || [];

    let html = `<div class="tree-node" data-css="padding-left:${indent}px;display:flex;align-items:center;gap:4px;padding-top:4px;padding-bottom:4px;min-height:28px">`;

    if (hasChildren) {
      html += `<span class="tree-toggle" data-on-click="treeToggle" data-args='${_dataArgs('$el')}' data-css="cursor:pointer;color:var(--gray-disabled);font-size:14px;margin-right:4px;user-select:none;flex-shrink:0">&#9658;</span>`;
    } else {
      html += `<span data-css="width:18px;flex-shrink:0"></span>`;
    }

    const icon = isPkg ? '&#128196;' : (hasChildren ? '&#128193;' : '&#128196;');
    const label = MAP[name] || name;
    const childCount = hasChildren ? ` <span data-css="color:var(--gray-disabled);font-size:11px">(${children.length})</span>` : '';
    html += `<span data-css="font-size:13px;color:var(--gray-muted);flex-shrink:0">${icon} ${escHtml(label)}${childCount}</span>`;

    if (isPkg && types.length > 0) {
      const badges = types.map(t => `<span data-css="background:var(--snap-pkg-type-badge);color:var(--snap-pkg-type-color);font-size:10px;padding:1px 6px;border-radius:8px;margin-left:2px;flex-shrink:0">${MAP[t]||escHtml(t)}</span>`).join('');
      html += badges;
      const modeKey = types[0];
      const mode = (pt.modes && pt.modes[modeKey]) || 'auto';
      html += `<select class="sel-sm dd" data-type="${escHtml(modeKey)}" data-on-change="_setPtMode" data-args='${_dataArgs(modeKey, '$value')}' data-css="margin-left:auto;flex:0 0 80px">
        <option value="auto" ${mode==='auto'?'selected':''}>自动</option>
        <option value="full" ${mode==='full'?'selected':''}>全量</option>
        <option value="incremental" ${mode==='incremental'?'selected':''}>增量</option>
      </select>`;
    } else if (!hasChildren) {
      html += `<span data-css="margin-left:auto;color:var(--gray-disabled);font-size:11px;flex-shrink:0">—</span>`;
    } else {
      html += `<span data-css="margin-left:auto;color:var(--gray-disabled);font-size:11px;flex-shrink:0">&#9658;</span>`;
    }
    html += `</div>`;

    if (hasChildren) {
      html += `<div class="tree-children" data-css="display:none">`;
      children.forEach(([childName, childNode]) => { html += renderNode(childNode, childName, depth + 1); });
      html += `</div>`;
    }
    return html;
  }

  let treeHtml = '<div data-css="font-family:system-ui">';
  const rootChildren = root._children ? Object.entries(root._children).sort(([a],[b]) => {
    const aV = /\d/.test(a); const bV = /\d/.test(b);
    if (aV && !bV) return -1; if (!aV && bV) return 1;
    return a.localeCompare(b, 'zh');
  }) : [];
  rootChildren.forEach(([name, node]) => { treeHtml += renderNode(node, name, 0); });
  treeHtml += '</div>';
  return treeHtml;
}

// Build data tree: renders snapshots using paths chain as directory structure.
// snapshotsMap: { "version_branch|short_type": [snapshot, ...], ... }
// MAP: type code -> Chinese label map
// verTypeMap: { "ver|shortType": dispType } for snapshot type display
// alias: product alias to strip from version (e.g., "WAF")
// sid: source id for click delegation
function buildDataTreeHtml(pt, snapshotsMap, MAP, verTypeMap, alias, sid) {
  const paths = pt.paths || [];
  if (paths.length === 0) return '<span data-css="color:var(--gray-disabled)">无路径数据</span>';

  // Build tree structure from paths
  const root = {};
  paths.forEach((p, pathIdx) => {
    const chain = p.chain || [];
    if (chain.length < 2) return;
    let node = root;
    // Track accumulated chain for intermediate nodes
    let accChain = [];
    chain.forEach((name, idx) => {
      if (!node.__children) node.__children = {};
      if (!node.__children[name]) node.__children[name] = { __children: {}, __isPkg: false, __pkgKey: null, __snapshots: [], __pathId: '', __chain: [], __pathIdx: -1 };
      node = node.__children[name];
      // Set __chain for ALL nodes (not just leaves) so intermediate clicks work
      node.__chain = accChain.concat([name]);
      node.__pathIdx = pathIdx;
      if (idx === chain.length - 1) {
        node.__isPkg = true;
        const rawType = (p.types || [])[0] || '';
        node.__pkgKey = chain[chain.length - 1] || (ver + '|' + rawType);
        node.__pathId = p.url || '';
        // Store the backend-computed path_id on the leaf node so tree clicks can
        // pass it directly to _dataSelectedPathId (avoids the verHint/pkgHint
        // ambiguity when several chains share one URL — see propagateHints).
        if (p.path_id) node.__backendPathId = p.path_id;
      }
      accChain = node.__chain;
    });
  });

  // Attach snapshots to matching leaf nodes
  // snapshotsMap key = source_url (relative path, stripped of BASE_URL)
  // tree node __pathId = p.url (relative path from product management)
  // Direct URL comparison — no version/type matching needed
  Object.entries(snapshotsMap || {}).forEach(([urlKey, snaps]) => {
function attach(node) {
      if (node.__isPkg && node.__pathId) {
        // Normalize both URLs: strip protocol/host, ensure single leading /
        // node.__pathId may or may not have leading /, urlKey may or may not
        // Normalize to: no leading /, no trailing /
        const stripSlashes = s => s.replace(/^\/+|\/+$/g, '');
        const normNode = '/' + stripSlashes(node.__pathId);
        const normKey  = '/' + stripSlashes(urlKey);
        if (normNode === normKey) {
          // Several chains share this URL (NSFocus real structure). Don't
          // `return true` — keep walking so every chain's leaf gets a chance
          // to match. Only attach snaps to the leaf whose __backendPathId is
          // in the snap array; other leaves leave __snapshots as [].
          // Note: with one URL key, attach is called once and walks the tree.
          // Match ALL snap rows for this leaf's path_id (cross-status: active+superseded+withdrawn),
          // not just the first one. Vendor URLs list all historical packages on the same page;
          // the tree-leaf "1包" badge must reflect total count, not the latest.
          const leafPid = node.__backendPathId;
          const matchedSnaps = leafPid ? snaps.filter(s => s.path_id === leafPid) : [];
          if (matchedSnaps.length > 0) {
            // Keep stable order: latest published first
            node.__snapshots = matchedSnaps.slice().sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''));
          } else if (!leafPid) {
            // No path_id on the leaf (legacy) — fall back to first matching
            // (ver, pkg) snap in the array.
            const matchedByVer = snaps.find(s => {
              const last2 = (node.__chain || []).slice(-2);
              return last2[0] === s.version_branch && last2[1] === s.package_type;
            });
            node.__snapshots = matchedByVer ? [matchedByVer] : [];
          } else {
            // path_id exists but no snap matches — leave empty (the right
            // panel dataBuildFeed uses snap.path_id to display correctly).
            node.__snapshots = [];
          }
          // Do NOT return — keep walking so sibling leaves can try.
        }
      }
      if (node.__children) {
        for (const [, child] of Object.entries(node.__children)) {
          attach(child);  // no early return — every leaf gets a chance
        }
      }
    }
    attach(root);
  });

  // Phase 2: Propagate version_branch, package_type hints and freshness from leaves up.
  function propagateHints(node) {
    const nodeChain = node.__chain || [];
    const nodeDepth = nodeChain.length;

    if (node.__isPkg) {
      // 2026-08-08: leaf 的 hint 用 node.__chain 派生,不读 snaps[0].version_branch
      // / package_type(URL 去重后 snap 行被互相覆盖,数据脏)。
      // node.__chain 在 buildDataTreeHtml 阶段从 paths[].chain 取,稳定。
      const verHint = (nodeChain.length >= 2) ? nodeChain[nodeChain.length - 2] : '';
      const pkgHint = nodeChain.length > 0 ? nodeChain[nodeChain.length - 1] : '';
      const now = Date.now();
      const latestPublished = (node.__snapshots || []).reduce((max, s) => {
        if (!s.published_at) return max;
        const t = new Date(s.published_at).getTime();
        return t > max ? t : max;
      }, 0);
      let freshness = 'idle';
      if (latestPublished > 0) {
        const days = (now - latestPublished) / 86400000;
        if (days <= 30) freshness = 'new';
        else if (days <= 365) freshness = 'lowfreq';
      }
      node.__freshness = freshness;
      return { ver: verHint, pkg: pkgHint, freshness };
    }
    if (!node.__children) return null;

    // 2026-07-29: 重写 hint 推导逻辑
    // 之前从 children snap 取 ver/pkg → 但 children 是子节点名 (e.g. "5.6.8 升级包列表"),
    //   snap.version_branch 又是基础字段 ("网络入侵检测系统 5.6.3") → 跟节点层级名不对应,
    //   导致 chain 2 层以上时 verHint 经常是错的值。
    // 新规则 (用节点名 = chain[nodeDepth-1] 作 hint):
    //   - 中间节点 verHint = chain 末段(节点自己的名字)
    //     原因: snap.version_branch 字段常包含节点名 substring
    //           (例如 5.6.8 节点的子叶子会有 ver="网络入侵检测系统 5.6.8" 包含 "5.6.8")。
    //           节点级过滤 = "本节点 + 本节点所有 descendants" 收敛。
    //   - pkgHint = children 第一个 pkg 子节点名 (e.g. "URL分类升级包"),
    //     仅在所有 children pkg 一致时设,否则空(避免错过滤)
    //   - 仍收集 children freshness
    const verHint = nodeDepth >= 1 ? nodeChain[nodeDepth - 1] : '';

    // pkgHint: 仅当 children 子节点 pkg 名全一致时设
    const childPkgNames = Object.entries(node.__children)
      .filter(([, c]) => c.__isPkg)
      .map(([n]) => n);
    const uniquePkgName = childPkgNames.length > 0 && childPkgNames.every(n => n === childPkgNames[0])
      ? childPkgNames[0] : '';
    const pkgHint = uniquePkgName;

    let childFreshness = 'idle';
    for (const [, child] of Object.entries(node.__children)) {
      const childHints = propagateHints(child);
      // Don't recurse through children's snap-derived verHint, but USE its freshness
      if (!childHints) continue;
      if (childHints.freshness === 'new') childFreshness = 'new';
      else if (childHints.freshness === 'lowfreq' && childFreshness !== 'new') childFreshness = 'lowfreq';
    }
    node.__verHint = verHint;
    node.__pkgHint = pkgHint;
    node.__freshness = childFreshness;
    return { ver: verHint, pkg: pkgHint, freshness: childFreshness };
  }
  propagateHints(root);

  // Render the tree
  function renderNode(node, name, depth) {
    const indent = depth * 16;
    const children = node.__children ? Object.entries(node.__children) : [];
    const hasChildren = children.length > 0;
    const isPkg = node.__isPkg;
    const snaps = node.__snapshots || [];

    // Determine target div to toggle (both empty and non-empty pkg nodes need IDs)
    // Use __pathId (URL) for targetId to ensure uniqueness — same leaf name across different
    // versions (e.g. "引擎升级包" x16) must not share the same ID. __pathId is unique per path.
    // For paths with empty URLs (NF-B/NF-D, etc.), include __pathIdx in the ID to distinguish
    // duplicate chains that share the same empty URL (e.g. NF-B "引擎库升级包" ×3).
    let targetId = null;
    if (isPkg) {
      const rawId = node.__pathId
        ? node.__pathId
        : ('|' + (node.__chain || []).join('|') + '|idx=' + node.__pathIdx);
      targetId = 'pktbl_' + rawId
          .replace(/[^\x00-\x7F]/g, function(m) { return '_' + m.charCodeAt(0).toString(16) + '_'; })
          .replace(/\W/g, '_');
    }

    // Clicking any node: filter right panel to that product/sub-category/version
    // Clicking a leaf node: also expand the corresponding row in the right panel
    // data attributes for event delegation
    const chainJson = JSON.stringify(node.__chain || []).replace(/"/g, '&quot;');
    const nodeUrl = node.__pathId || '';
    const isLeaf = isPkg ? '1' : '0';
    // For leaf nodes, collect snapshot IDs for right-panel expansion
    const leafSnapIds = isPkg ? (snaps.map(s => s.id).join(',')) : '';
    // Hints for filtering: version_branch and package_type shared by all descendants
    const verHint = node.__verHint || '';
    const pkgHint = node.__pkgHint || '';
    // Backend-computed path_id (MD5(BASE_URL+url+chain)[:12]) — preferred over
    // verHint/pkgHint when the URL is shared by multiple chains (NSFocus
    // NSFocus chains can map to the same URL with different ver segments).
    // Empty when this is an intermediate (non-leaf) node or the path has no
    // backend path_id yet.
    const nodePathId = node.__backendPathId || '';

    const icon = isPkg ? '&#128196;' : (hasChildren ? '&#128193;' : '&#128196;');
    const label = isPkg ? name : (MAP[name] || name);
    const childCount = hasChildren ? ` <span data-css="color:var(--gray-disabled);font-size:11px">(${children.length})</span>` : '';
    const snapCount = isPkg ? (
      snaps.length > 0
        ? ` <span data-css="background:var(--primary-light);color:var(--primary-text);font-size:10px;padding:1px 5px;border-radius:8px;margin-left:2px">${snaps.length}包</span>`
        : ` <span data-css="background:var(--danger-light);color:var(--danger);font-size:10px;padding:1px 5px;border-radius:8px;margin-left:2px">空</span>`
    ) : '';
    const freshBadge = (node.__freshness === 'new') ? ` <span data-css="background:var(--success);color:#fff;font-size:10px;padding:1px 5px;border-radius:8px;margin-left:2px">活跃</span>` : (node.__freshness === 'lowfreq' ? ` <span data-css="background:var(--warn);color:#fff;font-size:10px;padding:1px 5px;border-radius:8px;margin-left:2px">低频</span>` : (node.__freshness === 'idle' ? ` <span data-css="background:var(--gray-disabled);color:#fff;font-size:10px;padding:1px 5px;border-radius:8px;margin-left:2px">沉寂</span>` : ''));
    // Arrow: toggle children visibility via data-action="treeToggle" (handled by event delegation)
    // Uses a generated children ID stored in data-target on the arrow
    const childDivId = hasChildren ? ('tcd_' + sid + '_' + depth + '_' + name.replace(/\W/g, '_')) : null;
    const arrow = hasChildren || isPkg
      ? `<span data-on-click="treeToggleById" data-stop data-args='${_dataArgs(childDivId, '$el')}' data-css="cursor:pointer;color:var(--gray-disabled);font-size:13px;user-select:none;flex-shrink:0;margin-right:4px">&#9658;</span>`
      : `<span data-css="width:13px;flex-shrink:0;margin-right:4px"></span>`;
    let html = `<div class="tree-node" data-action="treeNode" data-sid="${sid}" data-chain="${chainJson}" data-url="${nodeUrl}" data-isleaf="${isLeaf}" data-snapids="${leafSnapIds}" data-verhint="${escHtml(verHint)}" data-pkghint="${escHtml(pkgHint)}" data-pathid="${escHtml(nodePathId)}" data-css="padding-left:${indent}px;display:flex;align-items:center;padding-top:4px;padding-bottom:4px;min-height:28px;cursor:pointer">`;
    html += arrow;
    html += `<span data-css="font-size:13px;color:var(--gray-muted);flex-shrink:0">${icon} ${escHtml(label)}${childCount}${snapCount}${freshBadge}</span>`;
    html += `</div>`;

    if (hasChildren) {
      html += `<div id="${childDivId}" class="tree-children" data-target="${childDivId}" data-css="display:none">`;
      children.forEach(([childName, childNode]) => { html += renderNode(childNode, childName, depth + 1); });
      html += `</div>`;
    }

    // Snapshot table for leaf nodes — NO LONGER rendered here.
    // Clicking a leaf node now filters the right panel to that URL and expands the row there.
    // Empty leaves still show the placeholder div so tree height is consistent.
    if (isPkg) {
      if (snaps.length === 0) {
        // Snapshot content is rendered into the right panel on click, not into the tree.
        // Keep a minimal placeholder so tree height is stable — uses a fixed non-conflicting id.
        html += `<div class="tree-children" data-css="display:none;padding-left:${indent + 16}px;margin-bottom:8px">
          <div data-css="color:var(--muted);font-size:12px;padding:8px 12px;background:var(--gray-bg);border-radius:4px">
            暂无采集数据<div data-css="font-size:11px;margin-top:2px">页面可能返回登录页或无数据包</div>
          </div></div>`;
      }
      // When snaps.length > 0: no placeholder div needed — clicking the leaf node
      // directly populates the right panel via dataBuildFeed().
    }

    return html;
  }

  let treeHtml = '<div data-css="font-family:system-ui">';
  const rootChildren = root.__children ? Object.entries(root.__children).sort(([a],[b]) => {
    const aV = /\d/.test(a); const bV = /\d/.test(b);
    if (aV && !bV) return -1; if (!aV && bV) return 1;
    return a.localeCompare(b, 'zh');
  }) : [];
  // Skip the top-level product name node if it's the only child (duplicates card title)
  if (rootChildren.length === 1) {
    const [topName, topNode] = rootChildren[0];
    const topChildren = topNode.__children ? Object.entries(topNode.__children).sort(([a],[b]) => {
      const aV = /\d/.test(a); const bV = /\d/.test(b);
      if (aV && !bV) return -1; if (!aV && bV) return 1;
      return a.localeCompare(b, 'zh');
    }) : [];
    topChildren.forEach(([childName, childNode]) => { treeHtml += renderNode(childNode, childName, 0); });
  } else {
    rootChildren.forEach(([name, node]) => { treeHtml += renderNode(node, name, 0); });
  }
  treeHtml += '</div>';
  return treeHtml;
}

function pkgTableToggle(el) {
  var targetId = el.getAttribute('data-target');
  if (!targetId) return;
  var target = document.getElementById(targetId);
  if (!target) return;
  var isOpen = target.style.display !== 'none';
  target.style.display = isOpen ? 'none' : 'block';
  el.innerHTML = isOpen ? '&#9658;' : '&#9660;';
}

function pkgTableToggleById(targetId) {
  if (!targetId) return;
  var target = document.getElementById(targetId);
  if (!target) return;
  var isOpen = target.style.display !== 'none';
  target.style.display = isOpen ? 'none' : 'block';
  // Also toggle the arrow in the parent tree-node
  var node = target.previousElementSibling;
  if (node && node.classList.contains('tree-node')) {
    var arrow = node.querySelector('.tree-arrow');
    if (arrow) arrow.innerHTML = isOpen ? '&#9658;' : '&#9660;';
  }
}

function treeToggle(el, extraTargetId) {
  var node = el.closest ? el.closest('.tree-node') : el;
  // .tree-children is the next sibling of .tree-node
  var ch = node.nextElementSibling;
  if (!ch || !ch.classList.contains('tree-children')) return;
  var isOpen = ch.style.display !== 'none';
  ch.style.display = isOpen ? 'none' : 'block';
  // Toggle extra target (snapshot table) if provided
  if (extraTargetId) {
    var extra = document.getElementById(extraTargetId);
    if (extra) extra.style.display = isOpen ? 'none' : 'block';
  }
  // Update arrow inside this node
  var arrow = node.querySelector('.tree-arrow');
  if (arrow) arrow.innerHTML = isOpen ? '&#9658;' : '&#9660;';
}

function editPkgType(id){
  const prods=window._allProducts||[];
  const p=prods.find(x=>x.id===id);
  if(!p){toast('产品不存在','error');return;}

  const MAP={sys:'系统',rule:'规则',nti:'威胁情报',av:'病毒库',apprule:'应用规则',url:'URL库',wcs:'恶意站点',judge:'研判',geo:'地理库',interface:'接口',special:'特殊',other:'其他',merge:'合并',client:'客户端',av_stream:'流式病毒库',test:'测试'};

  // 解析 package_type（新格式 {types:[], paths:[], modes:{}} 或旧格式 [...]）
  let pt={types:[], paths:[], modes:{}};
  try{
    if(typeof p.package_type==='string'){
      const parsed=JSON.parse(p.package_type);
      if(parsed.types){pt=parsed;}
      else if(Array.isArray(parsed)){pt.types=parsed; pt.paths=[]; pt.modes={};} // 旧格式
    }
  }catch{}

  // 如果有未确认的包类型变化 → 显示 diff 确认弹窗
  if(p.package_type_changed==1 && p.package_type_discovered){
    let disc={types:[], paths:[], modes:{}};
    try{const r=JSON.parse(p.package_type_discovered);if(r.types){disc=r;}}catch{}
    const currentSet=new Set(pt.types);
    const discSet=new Set(disc.types);
    const added=[...discSet].filter(x=>!currentSet.has(x));
    const removed=[...currentSet].filter(x=>!discSet.has(x));

    let diffRows='';
    if(added.length){
      diffRows+=added.map(t=>`<span data-css="background:var(--success-light);color:var(--success-text);font-size:12px;padding:2px 8px;border-radius:10px;margin:2px;display:inline-block">+${MAP[t]||t}</span>`).join(' ');
    }
    if(removed.length){
      diffRows+=removed.map(t=>`<span data-css="background:var(--danger-light);color:var(--danger-text);font-size:12px;padding:2px 8px;border-radius:10px;margin:2px;display:inline-block;text-decoration:line-through">-${MAP[t]||t}</span>`).join(' ');
    }
    if(!added.length && !removed.length){
      diffRows=`<span data-css="color:var(--gray-disabled);font-size:13px">包类型集合无变化，但采集时检测到差异</span>`;
    }

    // diff 模式下的 modes 编辑
    let modeRows='';
    disc.types.forEach(t=>{
      const label=MAP[t]||t;
      const mode=disc.modes[t]||'auto';
      modeRows+=`<div data-css="display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:6px;background:#f9fafb;border:1px solid var(--gray-border);margin-bottom:4px">
        <span data-css="flex:1;font-size:13px;color:var(--gray-disabled)">${label}</span>
        <select class="sel-sm dd" data-type="${escHtml(t)}" data-css="flex:0 0 90px">
          <option value="auto" ${mode==='auto'?'selected':''}>自动</option>
          <option value="full" ${mode==='full'?'selected':''}>全量</option>
          <option value="incremental" ${mode==='incremental'?'selected':''}>增量</option>
        </select>
      </div>`;
    });

    // 目录树展示 disc.paths（复用新的树形渲染）
    let pathTreeHtml = '';
    if (disc.paths && disc.paths.length) {
      // 用 disc 的 modes 渲染树
      const discPt = { types: disc.types, paths: disc.paths, modes: disc.modes || {} };
      const discTreeHtml = buildTreeHtml(discPt, MAP);
      pathTreeHtml = `<div data-css="margin-top:12px;font-size:11px;color:var(--gray-muted);margin-bottom:6px">📂 采集到的目录结构</div><div data-css="border:1px solid var(--gray-border);border-radius:8px;max-height:240px;overflow-y:auto;padding:8px 4px">${discTreeHtml}</div>`;
    }

    // 智能警告
    let rulesWarning='';
    if(removed.length && disc.affected_rules && disc.affected_rules.length){
      const ruleItems=disc.affected_rules.map(r=>
        `<div data-css="margin-top:4px">
          <span data-css="color:#7c3aed;font-weight:600">${escHtml(r.name)}</span>
          <span data-css="color:var(--danger-text);font-size:12px;margin-left:6px">
            ${(r.removed_types||[]).map(t=>MAP[t]||t).join(', ')}
          </span>
        </div>`
      ).join('');
      rulesWarning=`<div data-css="background:var(--danger-light);border:1px solid #fca5a5;border-radius:8px;padding:14px;margin-top:12px">
        <div data-css="font-size:12px;color:var(--danger-text);font-weight:600;margin-bottom:6px">⚠️ 以下订阅规则引用了将被删除的包类型，采纳后这些规则对此产品将不再推送：</div>
        ${ruleItems}
      </div>`;
    }

    _showModal('pkgtype');
    modalBody('pkgtype',`<div data-css="padding:24px;max-height:80vh;overflow-y:auto">
      <div data-css="font-size:15px;font-weight:600;margin-bottom:16px">⚠️ 包类型检测到变化 <span data-css="font-weight:400;font-size:12px;color:var(--gray-muted)">「${escHtml(p.name)}」</span></div>
      <div data-css="background:#fffbeb;border:1px solid #fbbf24;border-radius:8px;padding:14px;margin-bottom:16px">
        <div data-css="font-size:12px;color:#92400e;font-weight:600;margin-bottom:8px">📊 变更明细</div>
        <div>${diffRows}</div>
      </div>
      ${rulesWarning}
      ${pathTreeHtml}
      <div data-css="display:flex;gap:8px;margin-top:16px">
        <button class="btn btn-primary" data-on-click="confirmPkgTypeChange" data-args='${_dataArgs(id, 'apply')}'>✅ 采纳变化</button>
        <button class="btn" data-on-click="confirmPkgTypeChange" data-args='${_dataArgs(id, 'keep')}'>保留原样</button>
        <button class="btn" data-css="margin-left:auto" data-on-click="closeModal" data-args='${_dataArgs('pkgtype')}'>取消</button>
      </div>
    </div>`);
    window._ptModes={};
    disc.types.forEach(t=>{ window._ptModes[t]=disc.modes[t]||'auto'; });
    window._ptDiscovered=disc;
    return;
  }

  // ── 正常编辑模式 ────────────────────────────────────────────
  if(pt.types.length===0){
    _showModal('pkgtype');
    modalBody('pkgtype',`<div data-css="padding:24px">
      <div data-css="font-size:15px;font-weight:600;margin-bottom:16px">📦 包类型设置 <span data-css="font-weight:400;font-size:12px;color:var(--gray-muted)">「${escHtml(p.name)}」</span></div>
      <div data-css="color:var(--gray-disabled);font-size:13px;text-align:center;padding:12px">
        尚未发现包类型，请先对该产品进行<span data-css="color:#7c3aed;cursor:pointer" data-on-click="_applyAndRefreshPkgType" data-args='${_dataArgs(id)}'>全量采集</span>
      </div>
      <div data-css="display:flex;gap:8px;margin-top:20px">
        <button class="btn" data-on-click="closeModal" data-args='${_dataArgs('pkgtype')}'>关闭</button>
      </div>
    </div>`);
    return;
  }

  // 计算每种类型的路径覆盖数
  const pathCount={};
  pt.types.forEach(t=>{ pathCount[t]=0; });
  (pt.paths||[]).forEach(p=>{
    (p.types||[]).forEach(t=>{ if(t in pathCount) pathCount[t]++; });
  });
  const sortedTypes=[...pt.types].sort((a,b)=>(pathCount[b]||0)-(pathCount[a]||0));

  // 构建目录树（使用独立函数）
  const treeHtml = buildTreeHtml(pt, MAP);

  // 弹窗内容：目录树 + mode 选择 + 保存/取消
  _showModal('pkgtype');
  modalBody('pkgtype',`<div data-css="padding:24px;max-height:80vh;overflow-y:auto">
    <div data-css="font-size:15px;font-weight:600;margin-bottom:16px">📦 包类型设置 <span data-css="font-weight:400;font-size:12px;color:var(--gray-muted)">「${escHtml(p.name)}」</span></div>
    <div data-css="margin-bottom:14px;font-size:12px;color:#7c3aed;background:#f5f3ff;padding:8px 12px;border-radius:6px">
      共 ${pt.types.length} 种包类型，${(pt.paths||[]).length} 条路径
    </div>
    <div data-css="border:1px solid var(--gray-border);border-radius:8px;max-height:320px;overflow-y:auto;padding:8px 4px">
      ${treeHtml}
    </div>
    <div data-css="display:flex;gap:8px;margin-top:16px">
      <button class="btn btn-primary" data-on-click="savePkgType" data-args='${_dataArgs(id)}'>💾 保存</button>
      <button class="btn" data-on-click="closeModal" data-args='${_dataArgs('pkgtype')}'>取消</button>
    </div>
  </div>`);

  // 初始化 modes
  window._ptModes = {};
  window._ptPaths = pt.paths || [];
  pt.types.forEach(t => { window._ptModes[t] = pt.modes && pt.modes[t] || 'auto'; });
}

async function savePkgType(id){
  const modes = window._ptModes || {};
  // types 来自 modes 的 key（由树形控件的 mode selector 更新）
  const types = Object.keys(modes);

  // 构建新格式: {types:[...], paths:[...], modes:{...}}
  const finalModes = {};
  types.forEach(t => {
    const m = modes[t] || 'auto';
    if (m !== 'auto') finalModes[t] = m;
  });
  const pkg_type = { types, paths: window._ptPaths || [], modes: finalModes };

  const r = await api(`/system/products/${id}`,{method:'PUT',body:JSON.stringify({package_type:JSON.stringify(pkg_type)})});
  if(r.code===0){
    toast('包类型已保存','success');
    closeModal('pkgtype');
    await products();
  }else{
    toast(r.message||'保存失败','error');
  }
}

async function confirmPkgTypeChange(id, action){
  if(action==='apply'){
    const selEls=document.querySelectorAll('#pt-type-list select[data-type]');
    const disc=window._ptDiscovered||{types:[],paths:[],modes:{}};
    const updatedModes={};
    selEls.forEach(sel=>{ updatedModes[sel.dataset.type]=sel.value; });
    const finalPkg={types:disc.types, paths:disc.paths||[], modes:updatedModes};
    await api(`/system/products/${id}`,{method:'PUT',body:JSON.stringify({
      package_type:JSON.stringify(finalPkg),
      package_type_discovered:'',
      package_type_changed:0
    })});
    toast('已采纳新包类型','success');
  }else if(action==='keep'){
    await api(`/system/products/${id}`,{method:'PUT',body:JSON.stringify({
      package_type_discovered:'',
      package_type_changed:0
    })});
    toast('已保留原包类型','success');
  }
  closeModal('pkgtype');
  await products();
}

function _setPtMode(key,val){window._ptModes[key]=val;}
function _applyAndRefreshPkgType(id){closeModal('pkgtype');refreshPkgType(id);}

async function refreshPkgType(id){
  // 触发刷新 → POST → SSE 轮询 → 圆形进度环 + 日志浮动窗口
  const btn=document.querySelector(`[data-refresh-pkg="${id}"]`);
  if(!btn) return;

  // 触发后台任务
  const r=await api(`/system/products/${id}/refresh-pkg-type`,{method:'POST'});
  if(r.code!==0){
    toast(r.message||'启动失败','error');
    return;
  }

  // 按钮变成进度环
  const progId=`rprog-${id}`;
  btn.style.display='none';
  const ring=document.createElement('span');
  ring.id=progId;
  ring.style.cssText='display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;cursor:pointer';
  ring.innerHTML=`<svg width="28" height="28" viewBox="0 0 28 28" data-on-click="showRefreshLog" data-args='${_dataArgs(id)}'><circle cx="14" cy="14" r="11" fill="none" stroke="var(--gray-border)" stroke-width="2.5"/><circle id="${progId}-arc" cx="14" cy="14" r="11" fill="none" stroke="#7c3aed" stroke-width="2.5" stroke-dasharray="69.1" stroke-dashoffset="69.1" stroke-linecap="round" transform="rotate(-90 14 14)"/><text id="${progId}-txt" x="14" y="19" text-anchor="middle" font-size="9" fill="#7c3aed" font-weight="700">·</text></svg>`;
  btn.parentNode.insertBefore(ring,btn);

  // 预建立日志窗口（隐藏）
  const logId=`rlog-${id}`;
  if(!document.getElementById(logId)){
    const logDiv=_createFloatLog(logId, '📋 包类型刷新日志', {bodyClass: 'log-body'});
    document.body.appendChild(logDiv);
  }

  // 轮询方式获取进度（每 2 秒一次，兼容长任务）
  let done=false;
  let pollTimer=null;
  _productsPollCleanup=()=>{done=true;if(pollTimer){clearInterval(pollTimer);pollTimer=null;}};
  function updateRing(state){
    if(!state) return;
    const arc=document.getElementById(`${progId}-arc`);
    const txt=document.getElementById(`${progId}-txt`);
    if(arc&&txt){
      // Phase format: 'fetching_home' | 'fetching_ver (3/7)' | 'done' | 'error'
      const rawPhase = state.phase || '';
      let pct = 0;
      const verMatch = rawPhase.match(/fetching_ver \((\d+)\/(\d+)\)/);
      if (verMatch) {
        const cur = parseInt(verMatch[1]);
        const tot = parseInt(verMatch[2]);
        pct = 25 + Math.round((cur / tot) * 70);
      } else if (rawPhase === 'fetching_home') {
        pct = 10;
      } else if (rawPhase === 'done') {
        pct = 100;
      } else if (rawPhase === 'error') {
        pct = 100;
      }
      arc.setAttribute('stroke-dashoffset', 69.1*(1-pct/100));
      txt.textContent = pct === 100 ? (rawPhase === 'error' ? '✗' : '✓') : `${pct}`;
    }
    if(state.log_lines&&state.log_lines.length){
      const win=document.getElementById(logId);
      if(win){
        win.style.display='block';
        win.querySelector('.log-body').innerHTML=state.log_lines.map(l=>`<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border)">${escHtml(l)}</div>`).join('');
        win.querySelector('.log-body').scrollTop=win.querySelector('.log-body').scrollHeight;
      }
    }
    if(state.phase==='done'||state.phase==='error'||!state.active){
      done=true;
      if(pollTimer){clearInterval(pollTimer);pollTimer=null;}
      const isError = state.phase==='error';
      // 终态视觉:成功保留绿色 ✓,失败切红色 ✗ + 添加 tooltip
      if(isError && arc && txt){
        arc.setAttribute('stroke', 'var(--danger)');
        txt.setAttribute('fill', 'var(--danger)');
      }
      // 提取最后一条错误日志做 tooltip(失败时);成功时用路径数
      const lastLog = (state.log_lines && state.log_lines.length)
        ? state.log_lines[state.log_lines.length-1] : '';
      ring.title = isError
        ? `✗ 刷新失败:${lastLog||'未知错误'}`
        : `✓ 刷新成功,点击查看日志`;
      // 失败时显示 toast(单行简短提示)
      if(isError){
        toast('包类型刷新失败:'+(lastLog||'未知错误'),'error');
      }
      // 保留 3s 让用户看到结果,然后移除 + 刷新产品表
      setTimeout(async()=>{
        ring.remove();
        btn.style.display='';
        await products();
      },3000);
    }
  }

  // 触发后立即查询一次初始状态，然后每 2 秒轮询
  async function poll(){
    if(done) return;
    const r=await api(`/system/products/${id}/refresh-pkg-status`);
    if(r.code===0){
      updateRing(r.data);
    }
  }

  poll(); // 立即查一次
  pollTimer=setInterval(poll, 2000); // 每 2 秒轮询
  // 最多轮询 120 秒（60次 × 2s），防止无限等待
  setTimeout(()=>{if(!done){done=true;if(pollTimer){clearInterval(pollTimer);}ring.remove();btn.style.display='';}},120000);
}

function showRefreshLog(id){
  const w=document.getElementById(`rlog-${id}`);
  if(w){ w.style.display=w.style.display==='none'?'block':'none'; }
}

function _renderProductRows(prods){
  if(!prods.length) return `<tr><td colspan="8" data-css="text-align:center;color:var(--gray-disabled);padding:32px 0">暂无产品，请点击「自动发现」或「手动添加」</td></tr>`;
  let h='';
  for(const p of prods){
    const last=p.last_collected_at?fmtTZ(p.last_collected_at,true):'—';
    const hlth=p.health_status||'unknown';
    const hlthColor={'正常':'var(--success-text)','异常':'var(--danger)','未知':'var(--gray-muted)'}[_hlthLabel(hlth)]||'var(--gray-muted)';
    const rowClass=p.is_active?'':'opacity:0.6';
    h+=`<tr id="pr-${p.id}" data-css="${rowClass}">
      <td>${p.id}</td>
      <td><input type="checkbox" class="prod-cb" value="${p.id}" data-css="cursor:pointer"></td>
      <td>
        <span class="active-badge" data-css="font-size:11px;color:${p.is_active?'var(--success-text)':'var(--danger)'};cursor:pointer" data-on-click="confirmToggleActive" data-args='${_dataArgs(p.id, !p.is_active, 'pr-'+p.id)}'>${p.is_active?'● 启用':'● 禁用'}</span>
      </td>
      <td><strong>${escHtml(p.name)}</strong></td>
      <td><span class="mono" data-css="font-size:12px;color:var(--gray-muted)">${escHtml(p.entry_url||'—')}</span></td>
      <td><a href="#" data-prevent data-on-click="editPkgType" data-args='${_dataArgs(p.id)}' title="点击编辑包类型" data-css="color:#7c3aed;font-size:12px;text-decoration:none">${p.package_type_changed?'⚠️ ':''}${fmtPkgTypes(p.package_type)||'未设置'}</a></td>
      <td><span class="hlth-badge" data-css="color:${p.is_active?(hlthColor):'var(--gray-disabled)'};font-size:12px;white-space:nowrap">${p.is_active?'● '+_hlthLabel(hlth):'已禁用'}</span></td>
      <td><span data-css="font-size:12px;color:var(--gray-muted);white-space:nowrap">${last}</span></td>
      <td data-css="white-space:nowrap">
        <button class="btn btn-sm" ${p.is_active?'':'disabled'} data-on-click="editProduct" data-args='${_dataArgs(p.id)}' title="编辑" data-css="${p.is_active?'':'opacity:0.35;cursor:not-allowed'}">✏️</button>
        <button class="btn btn-sm" ${p.is_active?'':'disabled'} data-refresh-pkg="${p.id}" data-on-click="refreshPkgType" data-args='${_dataArgs(p.id)}' title="刷新包类型" data-css="${p.is_active?'':'opacity:0.35;cursor:not-allowed'}">🔄</button>
      </td>
    </tr>`;
  }
  return h;
}

function filterProducts(){
  const search=(document.getElementById('prod-search')?.value||'').toLowerCase();
  const filter=document.getElementById('prod-filter')?.value||'all';
  const all=window._allProducts||[];
  const filtered=all.filter(p=>{
    const matchSearch=!search || p.name.toLowerCase().includes(search) || (p.display_name||'').toLowerCase().includes(search) || (p.entry_url||'').toLowerCase().includes(search);
    const matchFilter=(filter==='all')||(filter==='enabled'&&p.is_active)||(filter==='disabled'&&!p.is_active);
    return matchSearch&&matchFilter;
  });
  document.getElementById('prod-tbody').innerHTML=_renderProductRows(filtered);
}

async function _handleDiscBtn(){
  if(window._pendingDiscResult){
    showDiscoverResult(window._pendingDiscResult, !!window._pendingDiscStale);
    return;
  }
  // 内存没有,实时查API(stale 恢复场景)
  try {
    const s=await api('/system/products/discover/status');
    if(s.code===0 && s.data?.phase==='done' && s.data?.result && !s.data?.active){
      window._pendingDiscResult=s.data.result;
      window._pendingDiscStale=!!s.data.is_stale;
      updateNavPendingBadge();
      showDiscoverResult(window._pendingDiscResult, window._pendingDiscStale);
      return;
    }
  } catch(e){}
  // 二次确认文案:用 .info-bar(跟手动添加表单一致)+ 简短下方说明,
  // 视觉统一两个流程的提示风格。弹窗用 discConfirmAct (580px 紫边框),
  // 跟手动添加弹窗宽度一致。
  discConfirmAct(
    '即将开始自动发现',
    async ()=>{ discoverProductsBanner(); },
    '🗂️', '启动发现',
    `<div class="info-bar">
       <div data-css="display:flex;align-items:flex-start;gap:8px">
         <span data-css="font-size:16px;line-height:1">🔍</span>
         <div data-css="flex:1">
           <div data-css="font-weight:600;margin-bottom:4px">什么时候用自动发现?</div>
           <div data-css="line-height:1.7">
             • <b>用途:</b>扫描绿盟更新站点,自动发现新增/下线产品 + 重新发现每个产品的包类型路径<br>
             • <b>场景:</b><br>
               &nbsp;&nbsp;◦ 首次接入系统初始化产品库<br>
               &nbsp;&nbsp;◦ 绿盟上线新产品,要纳入订阅<br>
               &nbsp;&nbsp;◦ <b>没找到自己想要关注的产品 / 包类型时</b> — 用「<span data-css="color:#7c3aed">🔍 自动发现</span>」全量扫描
                 或在产品列表点 <span data-css="color:#7c3aed">🔄 刷新</span> 重采单个产品<br>
             • <b>耗时:</b>长任务,后台跑,可中途停止<br>
             <small data-css="color:var(--gray-disabled);display:block;margin-top:6px">
               💡 后台每 4 小时自动轮询已发现产品(quick cycle,见「系统配置」可调)。<br>
               全量目录重爬(full scan)目前不自动跑 — 需要时手动点「🔍 自动发现」
             </small>
           </div>
         </div>
       </div>
     </div>`
  );
}

// discoverProductsBanner() — 不弹模态,直接走 banner 浮动条路径
// (banner 在产品页右上显示,mini-btn 在右下隐藏 banner 时出现)。
// 旧 discoverProducts() 弹模态(spinner + 进度条),但 banner 已经显示同样信息,
// 模态冗余砍掉。
async function discoverProductsBanner(){
  // 复用原 discoverProducts() 的启动逻辑(POST + 轮询),但砍掉模态
  const r=await api('/system/products/discover',{method:'POST'});
  if(r.code===409){
    // 已经在运行中(tab B 重复点 tab A 启动的)— 直接显示当前状态
    let active=false;
    for(let i=0;i<3 && !active;i++){
      const s=await api('/system/products/discover/status');
      if(s.code===0 && s.data?.active){active=true;break;}
      if(i<2) await new Promise(r=>setTimeout(r,200));
    }
    if(active){
      // 已经有任务 → 显示 banner,不重复启动
      _checkDiscBanner();
    } else {
      toast('没有正在运行的自动发现任务,请刷新页面','warn');
    }
    return;
  } else if(r.code!==0){
    toast(r.message||'启动失败','error');
    return;
  }
  // 任务已启动 → 切到产品页显示 banner 或检查 banner 是否已起
  // (用户点按钮时通常已在产品页;若不在,主动 showPage 让 banner 显示)
  if(!document.getElementById('disc-area')){
    showPage('products');
  }
  // 立即拉一次 status,触发 banner 显示
  _checkDiscBanner();
  toast('自动发现已启动,底部 banner 显示进度','success');
}

// ── 渲染带颜色的日志行(全局,discover 和 confirm 共用)────────────
// 兼容 2 种格式:旧 string(confirm 流程用)+ 新 entry{ts,level,product,msg}(discover 流程用)。
// 按 entry.level 着色,product 标记小角标。fallback 走 emoji 前缀推断(向后兼容 confirm)。
const _LOG_LEVEL_COLOR = {
  info: 'var(--gray-muted)',
  success: 'var(--success-text)',
  warn: '#f59e0b',
  error: 'var(--danger)',
};
function renderLogLine(l){
  // 兼容 entry 对象(后端结构化日志)
  if(l && typeof l === 'object' && 'msg' in l){
    const color = _LOG_LEVEL_COLOR[l.level] || 'var(--gray-muted)';
    const productBadge = l.product
      ? `<span data-css="background:#7c3aed20;color:#7c3aed;font-size:10px;padding:0 4px;border-radius:4px;margin-left:4px;vertical-align:middle">${escHtml(l.product)}</span>`
      : '';
    const ts = l.ts ? `<span data-css="color:var(--gray-disabled);font-size:10px;margin-right:4px">${escHtml(l.ts.split('T')[1]?.slice(0,8)||'')}</span>` : '';
    return `<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border);color:${color}${l.level==='error'||l.level==='success'?';font-weight:600':''}">${ts}${escHtml(l.msg)}${productBadge}</div>`;
  }
  // 兼容旧 string(confirm 流程暂未结构化)
  const txt = escHtml(String(l));
  if(txt.startsWith('🔍')) return `<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border);color:var(--primary)">${txt}</div>`;
  if(txt.startsWith('📦')&&txt.includes('发现包类型')) return `<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border);color:#7c3aed;font-weight:600">${txt}</div>`;
  if(txt.startsWith('  →')) return `<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border);color:#0891b2">${txt}</div>`;
  if(txt.startsWith('✅')||txt.startsWith('✔')) return `<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border);color:var(--success-text);font-weight:600">${txt}</div>`;
  if(txt.startsWith('❌')||txt.startsWith('⚠️')) return `<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border);color:var(--danger)">${txt}</div>`;
  if(txt.startsWith('[')) return `<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border);color:var(--gray-disabled)">${txt}</div>`;
  return `<div data-css="padding:1px 0;border-bottom:1px solid var(--gray-border);color:var(--gray-muted)">${txt}</div>`;
}

async function discoverProducts(){
  // 清除隐藏状态，新任务开始
  window._discBannerHidden=false;
  // 跨 tab 同步:监听 storage 事件,其他 tab 状态变化时刷新本 tab 的 UI
  // 单 tab 不会触发 storage 事件,所以不会有重复刷新
  if(!window._discStorageListener){
    window._discStorageListener = true;
    window.addEventListener('storage', e=>{
      if(e.key === 'nsm_disc_status_update' && e.newValue){
        // 其他 tab 状态变化(phase/result),强制刷一次 _checkDiscBanner
        _checkDiscBanner();
      }
    });
  }
  // 记录模态框启动时间(用于 ETA 估算)
  window._discModalStartTs = Date.now();
  // 预建立日志窗口（隐藏，点击按钮显示）
  const discLogId='disc-float-log';
  if(!document.getElementById(discLogId)){
    const logDiv=_createFloatLog(discLogId, '📋 自动发现日志');
    document.body.appendChild(logDiv);
    window.__discLogId=discLogId; // expose globally for confirmDiscoverResult
  }

  _showModal('disc');
  modalBody('disc',`<div data-css="padding:24px;text-align:center">
    <div class="spin"></div>
    <div id="disc-phase" data-css="margin-top:12px;color:var(--gray-muted);font-size:14px">正在启动自动发现…</div>
    <div data-css="margin:12px auto 0;width:80%;max-width:320px;height:6px;background:var(--gray-bg);border-radius:3px;overflow:hidden">
      <div id="disc-modal-bar" data-css="height:100%;background:#7c3aed;border-radius:3px;transition:width 0.4s;width:0%"></div>
    </div>
    <div data-css="margin-top:10px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
      <button data-on-click="_showById" data-args='${_dataArgs(discLogId)}' data-css="background:none;border:1px solid var(--gray-border);border-radius:6px;cursor:pointer;font-size:12px;color:#7c3aed;padding:4px 12px">📋 查看日志</button>
      <button data-on-click="_cancelDisc" data-css="background:none;border:1px solid var(--danger);border-radius:6px;cursor:pointer;font-size:12px;color:var(--danger);padding:4px 12px">⏹ 停止</button>
    </div>
  </div>`);

  const r=await api('/system/products/discover',{method:'POST'});
  if(r.code===409){
    // 已经在运行中——直接拉起进度 UI，不重复启动
    // tab B 重复点按钮时可能 tab A 刚启动后端 _auto_discover_state 还没完全初始化(竞态),
    // 短暂查 status 拿到 active=false → 走"似乎已结束"分支,体验差。最多重试 3 次 200ms 间隔。
    let active=false;
    for(let i=0;i<3 && !active;i++){
      const s=await api('/system/products/discover/status');
      if(s.code===0 && s.data?.active){active=true;break;}
      if(i<2) await new Promise(r=>setTimeout(r,200));
    }
    if(active){
      // 直接进入轮询渲染
    } else {
      modalBody('disc',`<div data-css="padding:24px;text-align:center">
        <div data-css="color:#f59e0b;font-size:14px;margin-bottom:8px">⚠️ 上次任务似乎已结束</div>
        <div data-css="color:var(--gray-muted);font-size:13px">请关闭后重新点击自动发现</div>
        <div data-css="margin-top:16px"><button class="btn" data-on-click="closeModal" data-args='${_dataArgs('disc')}'>关闭</button></div>
      </div>`);
      return;
    }
  } else if(r.code!==0){
    modalBody('disc',`<div data-css="padding:24px"><div data-css="color:var(--danger);font-weight:600;margin-bottom:8px">启动失败</div><div data-css="color:var(--gray-muted);font-size:13px">${escHtml(r.message||'')}</div><div data-css="margin-top:16px"><button class="btn" data-on-click="closeModal" data-args='${_dataArgs('disc')}'>关闭</button></div></div>`);
    return;
  }

  // 2. 轮询进度
  let done=false;
  let pollTimer=null;
  _productsPollCleanup=()=>{done=true;if(pollTimer){clearInterval(pollTimer);pollTimer=null;}};
  function renderProgress(state){
    const phaseEl=document.getElementById('disc-phase');
    // Phase text + ETA suffix (modal 进度条与 banner 一致)
    let phaseText = formatDiscPhase(state);
    if(state.phase==='discovering_pkg_types' && state.total && state.progress){
      const elapsedSec = (Date.now() - (window._discModalStartTs||Date.now())) / 1000;
      const ratio = state.progress / state.total;
      if(elapsedSec > 5 && ratio > 0.05){
        const remainSec = Math.round(elapsedSec * (1 - ratio) / ratio);
        if(remainSec > 0 && remainSec < 3600){
          phaseText += ` · 约剩 ${remainSec<60?remainSec+'秒':Math.round(remainSec/60)+'分钟'}`;
        }
      }
    }
    if(phaseEl) phaseEl.textContent=phaseText;
    // Progress bar (跟 banner 用同一公式)
    const barEl=document.getElementById('disc-modal-bar');
    if(barEl){
      const p=state.phase==='done'?100:
        state.phase==='scanning_products'?5:
        state.phase==='discovering_pkg_types' && state.total?
          5+Math.round((state.progress/state.total)*90):0;
      barEl.style.width=p+'%';
    }
    // Feed floating log window
    const logBody=document.getElementById(discLogId+'-body');
    if(logBody && state.log_lines?.length){
      logBody.innerHTML=state.log_lines.map(renderLogLine).join('');
      logBody.scrollTop=logBody.scrollHeight;
    }
    return state.phase==='done'||state.phase==='error'||state.phase==='cancelled'||!state.active;
  }

  async function poll(){
    if(done) return;
    const s=await api('/system/products/discover/status');
    if(s.code===0){
      done=renderProgress(s.data);
      if(done){
        clearInterval(pollTimer);
        if(s.data.phase==='done') showDiscoverResult(s.data.result);
        else if(s.data.phase==='cancelled'){toast('自动发现已取消','warn');closeModal('disc');}
      }
    }
  }
  poll();
  pollTimer=setInterval(poll,2000);
  setTimeout(()=>{if(!done){done=true;clearInterval(pollTimer);modalBody('disc',`<div data-css="padding:24px;text-align:center;color:var(--danger)">发现超时，请重试</div><div data-css="padding:8px"><button class="btn" data-on-click="closeModal" data-args='${_dataArgs('disc')}'>关闭</button></div>`);}},600000);
}

function showDiscoverResult(result, isStale){
  // 渲染两阶段变更确认
  const prod=result?.products||{};
  const pkgChanges=result?.pkg_changes||{};
  const added=prod.added||[];
  const removed=prod.removed||[];
  const unchanged=prod.unchanged||[];
  const pkgChangedProducts=Object.values(pkgChanges);
  const totalProdChanges=added.length+removed.length;
  const totalPkgChanges=pkgChangedProducts.reduce((n,c)=>n+(c.added_paths?.length||0)+(c.deleted_paths?.length||0)+(c.modified_paths?.length||0),0);

  const staleWarning=isStale?'<div data-css="background:var(--warn-light);border:1px solid #f59e0b;border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:13px;color:#92400e;display:flex;align-items:center;gap:8px"><span data-css="font-size:16px">⚠️</span><div><b>发现结果已从临时文件恢复</b>（服务重启前未执行确认），请检查以下变更后点击「确认应用变更」。</div></div>':'';

  let summaryHtml=`<div data-css="display:flex;gap:16px;margin-bottom:14px;font-size:13px">
    <span data-css="color:var(--success-text)">🆕 新增产品 <b>${added.length}</b></span>
    <span data-css="color:var(--danger)">⛔ 下线产品 <b>${removed.length}</b></span>
    <span data-css="color:var(--gray-disabled)">✔️ 未变 <b>${unchanged.length}</b></span>
    ${totalPkgChanges>0?`<span data-css="color:#7c3aed">📦 包类型变更 <b>${totalPkgChanges}</b> 项 (<b>${pkgChangedProducts.length}</b> 个产品)</span>`:''}
  </div>`;

  function toggleSection(key){
    const body=document.getElementById('sec-body-'+key);
    const icon=document.getElementById('sec-icon-'+key);
    if(!body) return;
    const open=body.style.display!=='none';
    body.style.display=open?'none':'block';
    if(icon) icon.textContent=open?'▸':'▾';
  }

  let sectionsHtml='';
  // 产品新增
  if(added.length){
    const rows=added.map(p=>`<tr><td>${escHtml(p.name)}</td><td><span class="mono" data-css="font-size:12px;color:var(--gray-muted)">${escHtml(p.entry_url||'—')}</span></td></tr>`).join('');
    sectionsHtml+=`<div data-css="margin-bottom:10px">
      <div data-css="font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid var(--gray-border);color:var(--success-text)" data-on-click="toggleSection" data-args='${_dataArgs('add-prod')}'>
        <span id="sec-icon-add-prod">▾</span>🆕 新增产品 <span data-css="background:var(--success-text)20;color:var(--success-text);padding:1px 7px;border-radius:10px;font-size:11px">${added.length}</span>
      </div>
      <div id="sec-body-add-prod" data-css="border:1px solid var(--gray-border);border-radius:8px;overflow:hidden;margin-top:4px">
        <table class="table" data-css="margin:0;font-size:13px"><thead><tr><th>产品名称</th><th>入口URL</th></tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>`;
  }
  // 产品删除
  if(removed.length){
    const rows=removed.map(p=>`<tr><td>${escHtml(p.name)}</td><td><span class="mono" data-css="font-size:12px;color:var(--gray-muted)">${escHtml(p.entry_url||'—')}</span></td></tr>`).join('');
    sectionsHtml+=`<div data-css="margin-bottom:10px">
      <div data-css="font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid var(--gray-border);color:var(--danger)" data-on-click="toggleSection" data-args='${_dataArgs('rem-prod')}'>
        <span id="sec-icon-rem-prod">▾</span>⛔ 下线产品 <span data-css="background:var(--danger)20;color:var(--danger);padding:1px 7px;border-radius:10px;font-size:11px">${removed.length}</span>
      </div>
      <div id="sec-body-rem-prod" data-css="border:1px solid var(--gray-border);border-radius:8px;overflow:hidden;margin-top:4px">
        <table class="table" data-css="margin:0;font-size:13px"><thead><tr><th>产品名称</th><th>入口URL</th></tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>`;
  }
  // 包类型变更
  if(pkgChangedProducts.length){
    let pkgRows='';
    for(const change of pkgChangedProducts){
      const addedCount=change.added_paths?.length||0;
      const delCount=change.deleted_paths?.length||0;
      const modCount=change.modified_paths?.length||0;
      const segs=[];
      if(addedCount) segs.push(`<span data-css="color:var(--success-text)">+${addedCount} 新增</span>`);
      if(delCount) segs.push(`<span data-css="color:var(--danger)">-${delCount} 删除</span>`);
      if(modCount) segs.push(`<span data-css="color:#f59e0b">±${modCount} 修改</span>`);
      pkgRows+=`<tr>
        <td data-css="font-weight:600">${escHtml(change.product_name||'')}</td>
        <td data-css="font-size:12px;color:var(--gray-muted)">${segs.join(' | ')}</td>
        <td data-css="text-align:right"><button class="btn btn-xs btn-outline" data-on-click="togglePkgDetail" data-args='${_dataArgs(change.entry_url||'')}'>详情</button></td>
      </tr>`;
      // Hidden detail rows
      pkgRows+=`<tr id="pkgdetail-${escHtml(change.entry_url||'')}" data-css="display:none;background:var(--gray-bg)">
        <td colspan="3" data-css="padding:8px 12px;font-size:12px">
          ${addedCount?`<div data-css="margin-bottom:4px"><span data-css="color:var(--success-text);font-weight:600">+ 新增路径</span><ul data-css="margin:2px 0 4px 16px;padding-left:0">${change.added_paths.map(p=>`<li data-css="word-break:break-all"><span class="mono" data-css="color:var(--gray-muted);font-size:11px">${escHtml(p.chain?.join(' → ')||'')}</span></li>`).join('')}</ul></div>`:''}
          ${delCount?`<div data-css="margin-bottom:4px"><span data-css="color:var(--danger);font-weight:600">- 删除路径</span><ul data-css="margin:2px 0 4px 16px;padding-left:0">${change.deleted_paths.map(p=>`<li data-css="word-break:break-all"><span class="mono" data-css="color:var(--gray-muted);font-size:11px">${escHtml(p.chain?.join(' → ')||'')}</span></li>`).join('')}</ul></div>`:''}
          ${modCount?`<div><span data-css="color:#f59e0b;font-weight:600">± 修改类型</span><ul data-css="margin:2px 0 4px 16px;padding-left:0">${change.modified_paths.map(p=>{
            // 多字段 diff:chain / url / types(后端目前只对比 types,所以 chain/url 通常不变)
            // 前端从 entry 自身推老值(chain 从 chain 数组隐含,url 从当前 entry)
            const urlChanged = p.url && p.old_url && p.url !== p.old_url;
            const typesChanged = JSON.stringify(p.old_types||[]) !== JSON.stringify(p.new_types||[]);
            const diffs = [];
            if(urlChanged) diffs.push(`URL: <span data-css="color:var(--danger)">${escHtml(p.old_url||'—')}</span> → <span data-css="color:var(--success-text)">${escHtml(p.url)}</span>`);
            if(typesChanged) diffs.push(`类型: <span data-css="color:var(--danger)">${escHtml((p.old_types||[]).join(','))}</span> → <span data-css="color:var(--success-text)">${escHtml((p.new_types||[]).join(','))}</span>`);
            // chain 长度变化(commit 8c41709 砍 sec 段后常见)
            const oldChainLen = p.old_chain_len;
            const chainLenChanged = oldChainLen != null && oldChainLen !== (p.chain?.length||0);
            if(chainLenChanged) diffs.push(`chain 长度: <span data-css="color:var(--danger)">${oldChainLen}</span> → <span data-css="color:var(--success-text)">${p.chain?.length||0}</span>`);
            if(diffs.length === 0) diffs.push(`类型: ${escHtml((p.old_types||[]).join(','))} → ${escHtml((p.new_types||[]).join(','))}`);
            const warn = urlChanged ? '<span data-css="color:var(--danger);font-weight:700" title="URL 变化属于重大变更,确认前请核对">⚠️</span> ' : '';
            return `<li data-css="word-break:break-all">${warn}<span class="mono" data-css="color:var(--gray-muted);font-size:11px">${escHtml(p.chain?.join(' → ')||'')}</span> — ${diffs.join('; ')}</li>`;
          }).join('')}</ul></div>`:''}
        </td>
      </tr>`;
    }
    sectionsHtml+=`<div data-css="margin-bottom:10px">
      <div data-css="font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid var(--gray-border);color:#7c3aed" data-on-click="toggleSection" data-args='${_dataArgs('pkg-chg')}'>
        <span id="sec-icon-pkg-chg">▾</span>📦 包类型变更 <span data-css="background:#7c3aed20;color:#7c3aed;padding:1px 7px;border-radius:10px;font-size:11px">${pkgChangedProducts.length} 个产品</span>
      </div>
      <div id="sec-body-pkg-chg" data-css="border:1px solid var(--gray-border);border-radius:8px;overflow:hidden;margin-top:4px">
        <table class="table" data-css="margin:0;font-size:13px"><thead><tr><th>产品</th><th>变更</th><th></th></tr></thead><tbody>${pkgRows}</tbody></table>
      </div>
    </div>`;
  }

  // 未变化产品
  if(unchanged.length){
    const visCount=Math.min(unchanged.length,5);
    const shown=unchanged.slice(0,visCount);
    const hidden=unchanged.slice(visCount);
    let rows=shown.map(p=>`<tr><td>${escHtml(p.name)}</td><td><span class="mono" data-css="font-size:12px;color:var(--gray-muted)">${escHtml(p.entry_url||'—')}</span></td></tr>`).join('');
    if(hidden.length){
      rows+=`<tr id="disc-unchanged-extra" data-css="display:none">${hidden.map(p=>`<tr><td>${escHtml(p.name)}</td><td><span class="mono" data-css="font-size:12px;color:var(--gray-muted)">${escHtml(p.entry_url||'—')}</span></td></tr>`).join('')}</tr>`;
    }
    sectionsHtml+=`<div data-css="margin-bottom:10px">
      <div data-css="font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid var(--gray-border);color:var(--gray-disabled)" data-on-click="toggleSection" data-args='${_dataArgs('unchanged')}'>
        <span id="sec-icon-unchanged">▸</span>✔️ 未变化产品 <span data-css="background:var(--gray-disabled)20;color:var(--gray-disabled);padding:1px 7px;border-radius:10px;font-size:11px">${unchanged.length}</span>
      </div>
      <div id="sec-body-unchanged" data-css="display:none;border:1px solid var(--gray-border);border-radius:8px;overflow:hidden;margin-top:4px">
        <table class="table" data-css="margin:0;font-size:13px"><thead><tr><th>产品名称</th><th>入口URL</th></tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>`;
  }

  let html=`<div data-css="padding:20px">
    <div data-css="font-size:15px;font-weight:600;margin-bottom:16px">🔍 自动发现结果</div>
    ${staleWarning}
    ${summaryHtml}
    <div data-css="max-height:420px;overflow-y:auto;border:1px solid var(--gray-border);border-radius:8px;padding:8px">
      ${sectionsHtml}
    </div>
    <div id="disc-confirm-phase" data-css="display:none;margin-top:10px;padding:8px 12px;background:#f5f3ff;border-radius:6px;font-size:13px;color:#7c3aed;text-align:center"></div>
    <div data-css="display:flex;gap:8px;align-items:center;margin-top:16px;padding-top:14px;border-top:1px solid var(--gray-border)">
      <button class="btn btn-primary" id="disc-confirm-btn" data-on-click="confirmDiscoverResult">💾 确认应用变更</button>
      <button class="btn" data-css="border-color:var(--danger);color:var(--danger)" data-on-click="discardDiscoverResult">丢弃</button>
      <span data-css="font-size:12px;color:var(--gray-disabled)">新增产品默认禁用，包类型变更将触发告警</span>
      <button class="btn" data-css="margin-left:auto" data-on-click="closeModal" data-args='${_dataArgs('disc')}'>关闭</button>
    </div>
  </div>`;
  _showModal('disc');
  modalBody('disc',html);
  window._discoverResult=result;
}

function togglePkgDetail(entryUrl){
  const el=document.getElementById('pkgdetail-'+entryUrl);
  if(el) el.style.display=el.style.display==='none'?'table-row':'';
}

async function confirmDiscoverResult(){
  const result=window._discoverResult;
  if(!result) return;
  const btn=document.getElementById('disc-confirm-btn');
  const phaseEl=document.getElementById('disc-confirm-phase');
  if(btn){btn.disabled=true;btn.textContent='处理中…';}
  if(phaseEl){phaseEl.style.display='block';phaseEl.textContent='💾 正在启动…';}

  const r=await api('/system/products/discover/confirm',{method:'POST',body:JSON.stringify(result)});
  if(r.code===0){
    // Reuse or create floating log window
    let logId=window.__discLogId||'disc-float-log';
    let logBody=document.getElementById(logId+'-body');
    if(!logBody){
      const logDiv=_createFloatLog(logId, '📋 应用日志');
      document.body.appendChild(logDiv);
      logBody=document.getElementById(logId+'-body');
      window.__discLogId=logId;
    }
    logBody.innerHTML='';
    document.getElementById(logId).style.display='block';

    let done=false;
    let pollTimer=null;
    let pollSSEAbort=null;  // SSE AbortController,失败兜底时主动关 SSE 流
    _productsPollCleanup=()=>{done=true;if(pollTimer){clearInterval(pollTimer);pollTimer=null;}if(pollSSEAbort){pollSSEAbort.abort();pollSSEAbort=null;}};

    // 终态收尾(单一收口) — 任何渠道发现 done/error 都走这里,确保按钮文字恢复一致
    function finalize(state){
      done=true;
      if(pollTimer)clearInterval(pollTimer);
      if(pollSSEAbort)pollSSEAbort.abort();
      if(state.phase==='done'){
        const d=state.result||{};
        // 成功 toast 含受影响订阅数(后端 confirm 已算好 affected_subscriptions),
        // 数字本身即告知用户"哪些订阅可能需要复查",不直接跳页避免越界改动订阅管理页。
        const aff=d.affected_subscriptions||[];
        const totalAffCount=aff.reduce((n,x)=>n+(x.count||0),0);
        const affMsg = totalAffCount>0
          ? `,${totalAffCount} 个订阅规则引用了被变更的产品(可能需要复查)`
          : '';
        showRichToast(`已应用:产品新增 ${d.saved?.length||0} 删除 ${d.deleted?.length||0},包类型更新 ${d.pkg_updated?.length||0}${affMsg}`, 'success');
        window._discoverResult=null;
        window._pendingDiscResult=null;
        window._pendingDiscStale=null;
        updateNavPendingBadge();
        closeModal('disc');
        products();
      }else if(state.phase==='error'){
        // 三重提示:toast(短暂) + modal 顶部错误卡(本次新增,持久) + 按钮恢复
        const errMsg=(state.error_message && state.error_category!=='session')
          ? state.error_message : (state.error||'未知错误');
        showRichToast(`应用失败: ${errMsg}`, 'error');
        toast(`应用失败: ${errMsg}`, 'error');
        if(btn){btn.disabled=false;btn.textContent='💾 重试应用变更';}
        // 打开浮动日志窗,让用户能看完整 log_lines(可能含 traceback)
        const logWin=document.getElementById(window.__discLogId||'disc-float-log');
        if(logWin)logWin.style.display='block';
      }else{
        // 非 done/error(超时/异常),按钮恢复 + 提示
        toast('应用未完成，请刷新页面查看状态','warn');
        if(btn){btn.disabled=false;btn.textContent='💾 确认应用变更';}
      }
    }

    function renderConfirmProgress(state){
      const phaseNames={
        'applying':'💾 正在应用变更…',
        'updating_pkg':`📦 更新包类型…`,
        'done':'✅ 应用完成',
        'error':'❌ 应用出错',
      };
      const msg=phaseNames[state.phase]||state.phase||'';
      if(phaseEl) phaseEl.textContent=msg;
      // 阶段色块:running 紫色,error 红色 + icon,让用户一眼区分
      if(phaseEl){
        if(state.phase==='error'){
          phaseEl.style.background='var(--danger-light)';
          phaseEl.style.color='var(--danger-text)';
          phaseEl.style.fontWeight='600';
        } else {
          phaseEl.style.background='#f5f3ff';
          phaseEl.style.color='#7c3aed';
          phaseEl.style.fontWeight='';
        }
      }
      // error 状态:在 modal 顶部加显眼红色错误卡(用户可能漏看 toast,模态内必须显眼)
      const modalBox=document.getElementById('disc-box');
      if(modalBox && state.phase==='error'){
        if(!document.getElementById('disc-confirm-error')){
          const errCard=document.createElement('div');
          errCard.id='disc-confirm-error';
          errCard.style.cssText='margin:0 0 14px;padding:12px 16px;background:var(--danger-light);border:1px solid var(--danger-border);border-radius:8px;color:var(--danger-text);font-size:13px;line-height:1.6';
          const errMsg=state.error_message||state.error||'未知错误,请查看日志';
          errCard.innerHTML=`<div data-css="font-weight:700;font-size:14px;margin-bottom:4px">❌ 应用失败</div><div>${escHtml(errMsg)}</div>`;
          const titleEl=modalBox.querySelector('div');
          if(titleEl)titleEl.after(errCard);
        }
      }
      if(logBody && state.log_lines?.length){
        logBody.innerHTML=state.log_lines.map(renderLogLine).join('');
        logBody.scrollTop=logBody.scrollHeight;
      }
      return state.phase==='done'||state.phase==='error'||!state.active;
    }

    // HTTP 轮询(主,SSE 是加速器) — fetch 加 timeout,超时即视为本轮失败
    async function pollConfirm(){
      if(done) return;
      try{
        const controller=new AbortController();
        const tid=setTimeout(()=>controller.abort(),8000);
        const s=await api('/system/products/discover/confirm/status',{signal:controller.signal});
        clearTimeout(tid);
        if(s.code===0){
          const isFinal=renderConfirmProgress(s.data);
          if(isFinal)finalize(s.data);
        }
      }catch(e){}
    }
    pollConfirm();  // first poll immediately
    pollTimer=setInterval(pollConfirm, 2000);

    // SSE 流(加速器,失败/超时主动 abort)
    async function pollSSE(){
      if(done) return;
      try{
        pollSSEAbort=new AbortController();
        // 会话走 HttpOnly Cookie，同源 fetch 自动携带，不再手工拼 Authorization
        const h={'Accept':'text/event-stream'};
        const r=await fetch(API+'/system/products/discover/confirm/status',{headers:h,cache:'no-store',signal:pollSSEAbort.signal});
        if(!r.ok){return;}  // fallback HTTP 轮询继续
        const reader=r.body.getReader();
        const dec=new TextDecoder();
        let buf='';
        while(!done){
          const {value, done: eos}=await reader.read();
          if(eos) break;
          buf+=dec.decode(value,{stream:true});
          const lines=buf.split('\n');
          buf=lines.pop()||'';
          for(const line of lines){
            const colonIdx=line.indexOf(':');
            if(colonIdx>=0){
              try{
                const s=JSON.parse(line.slice(colonIdx+1));
                renderConfirmProgress(s);
                if(s.phase==='done'||s.phase==='error'||!s.active){finalize(s);return;}
              }catch(e){}
            }
          }
        }
      }catch(e){}
      // SSE 正常关闭或失败 → 立刻 HTTP 兜底查一次状态,确保不漏掉终态
      if(!done){pollConfirm();}
    }
    setTimeout(pollSSE, 500);

    // 统一 watchdog:5min 后强制兜底查一次 + finalize,避免任一渠道卡住导致按钮永久 disabled
    setTimeout(async ()=>{
      if(done)return;
      try{
        const controller=new AbortController();
        const tid=setTimeout(()=>controller.abort(),5000);
        const s=await api('/system/products/discover/confirm/status',{signal:controller.signal});
        clearTimeout(tid);
        if(s.code===0 && (s.data.phase==='done'||s.data.phase==='error'||!s.data.active)){
          finalize(s.data);
        }else{
          finalize({phase:'unknown'});  // 触发 finalize 的"非 done/error"分支,按钮恢复
        }
      }catch(e){
        finalize({phase:'unknown'});
      }
    },300000);
  }else{
    toast('启动失败: '+r.message,'error');
    if(btn){btn.disabled=false;btn.textContent='💾 确认应用变更';}
    if(phaseEl){phaseEl.style.display='none';}
  }
}

async function discardDiscoverResult(){
  await confirmAct('确定丢弃当前发现结果？', async ()=>{window._pendingDiscResult=null;window._pendingDiscStale=null;window._discoverResult=null;updateNavPendingBadge();try{await api('/system/products/discover/confirm',{method:'DELETE'});}catch(e){}toast('已丢弃');}, '🗑️', '确认丢弃');
}

// 旧函数保留兼容（不再调用）
async function saveDiscoveredProducts(){ await confirmDiscoverResult(); }

function showProductForm(p){
  // p is optional: if provided, we're editing (for manual products); otherwise creating
  const isEdit=!!p;
  const id=p?.id||'';
  const name=p?.name||'';
  const url=p?.entry_url||'';
  const strat=p?.strategy||'standard';

  _showModal('pform');
  const isManual = p ? p.is_manual !== false : true; // default to true for new add
  modalBody('pform',`<div data-css="padding:24px">
    <div data-css="font-size:15px;font-weight:600;margin-bottom:20px">${isEdit?'✏️ 编辑产品':'➕ 手动添加产品'}</div>
    <div class="info-bar">
      <div data-css="display:flex;align-items:flex-start;gap:8px">
        <span data-css="font-size:16px;line-height:1">💡</span>
        <div data-css="flex:1">
          <div data-css="font-weight:600;margin-bottom:4px">什么时候用手动添加?</div>
          <div data-css="line-height:1.7">
            • <b>用途:</b>自动发现没扫到的产品(自定义 / 灰度 / 私有 / 内部测试)<br>
            • <b>建议:</b>绿盟标准产品 — 用「自动发现」批量加<br>
            • <b>不推荐:</b>已有同名产品(去重检查会失败)<br>
            • <b>下一步:</b>保存后弹窗问是否立即刷新包类型
          </div>
        </div>
      </div>
    </div>
    <div class="form-group" data-css="margin-top:16px"><label>产品名称 <span data-css="color:var(--danger)">*</span></label>
      <input id="pf-name" value="${escHtml(name)}" placeholder="如: WEB应用防护系统(WAF)" data-css="width:100%;padding:8px 12px;border:1px solid var(--gray-border);border-radius:8px;font-size:14px">
    </div>
    <div class="form-group" data-css="margin-top:14px"><label>入口URL <span data-css="color:var(--danger)">*</span></label>
      <input id="pf-url" value="${escHtml(url)}" placeholder="/update/wafIndex" data-css="width:100%;padding:8px 12px;border:1px solid var(--gray-border);border-radius:8px;font-size:14px">
      <div data-css="font-size:11px;color:var(--gray-disabled);margin-top:4px">绿盟升级站点相对路径，如 /update/wafIndex</div>
    </div>
    <div data-css="display:flex;gap:8px;margin-top:20px">
      <button class="btn btn-primary" data-on-click="saveProductForm" data-args='${_dataArgs(id)}'>💾 保存</button>
      <button class="btn" data-on-click="closeModal" data-args='${_dataArgs('pform')}'>取消</button>
    </div>
  </div>`);
}

async function saveProductForm(id){
  const name=document.getElementById('pf-name')?.value?.trim()||'';
  const entry_url=document.getElementById('pf-url')?.value?.trim()||'';
  if(!name||!entry_url){toast('名称和URL不能为空','error');return;}
  if(!entry_url.startsWith('/')){toast('URL必须以 / 开头','error');return;}
  const body={name,entry_url,strategy:'standard',display_name:name};
  const method=id?'PUT':'POST';
  const path=id?`/system/products/${id}`:'/system/products';
  const r=await api(path,{method,body:JSON.stringify(body)});
  if(r.code===0){
    toast(id?'已更新':'已添加','success');
    closeModal('pform');
    await products();
    if(!id && r.data?.id){
      await confirmAct('产品已添加，是否立即刷新包类型？', async ()=>{refreshPkgType(r.data.id);}, '🔄', '刷新包类型');
    }
  }else{
    toast(r.message||'保存失败','error');
  }
}

function cancelProductForm(){ closeModal('pform'); }

// ── Inline product edit (row-level) ──────────────────────────────────────────
let _editRowId = null; // currently editing row id

function editProduct(id){
  if(_editRowId && _editRowId !== id){
    cancelEditRow(_editRowId);
  }
  const row = document.getElementById('pr-' + id);
  if(!row) return;
  const products = window._allProducts || [];
  const p = products.find(x => x.id === id);
  if(!p) return;

  _editRowId = id;
  const isManual = p.is_manual;

  row.innerHTML = `<td>${p.id}</td>
    <td><span class="active-badge" data-css="font-size:11px;color:${p.is_active?'var(--success-text)':'var(--danger)'}">${p.is_active?'● 启用':'● 禁用'}</span></td>
    <td><span data-css="font-size:13px;color:var(--gray-disabled)">${escHtml(p.name)}</span></td>
    <td><input id="ef-url" value="${escHtml(p.entry_url||'')}" ${isManual?'':'readonly'} data-css="width:100%;padding:4px 6px;border:1px solid var(--gray-border);border-radius:6px;font-size:13px;background:${isManual?'#fff':'var(--gray-bg)'}"></td>
    <td><span class="hlth-badge" data-css="color:${p.is_active?(_hlthColor(_hlthLabel(p.health_status))):'var(--gray-disabled)'};font-size:12px">${p.is_active?'● '+_hlthLabel(p.health_status):'已禁用'}</span></td>
    <td><span data-css="font-size:12px;color:var(--gray-muted)">${fmtTZ(p.last_collected_at,true)||'—'}</span></td>
    <td>
      <button class="btn btn-sm btn-primary" data-on-click="saveEditRow" data-args='${_dataArgs(id)}'>💾</button>
      <button class="btn btn-sm" data-on-click="cancelEditRow" data-args='${_dataArgs(id)}'>✕</button>
    </td>`;
  row.dataset.editing = '1';
}

function cancelEditRow(id){
  _editRowId = null;
  products(); // re-render full list
}

async function saveEditRow(id){
  const entry_url = document.getElementById('ef-url')?.value?.trim() || '';
  if(!entry_url){ toast('URL不能为空','error'); return; }
  if(!entry_url.startsWith('/')){ toast('URL必须以 / 开头','error'); return; }

  const _prods = window._allProducts || [];
  const _p = _prods.find(x => x.id === id);
  const wasManual = _p?.is_manual;
  const oldUrl = _p?.entry_url || '';
  const urlChanged = oldUrl && oldUrl !== entry_url;

  const body = {};
  if(wasManual) body.name = _p.name;
  body.entry_url = entry_url;

  const r = await api(`/system/products/${id}`,{method:'PUT',body:JSON.stringify(body)});
  if(r.code === 0){
    toast('已更新','success');
    _editRowId = null;
    await products();
    if(urlChanged){await confirmAct('⚠️ 入口URL已变更，是否立即刷新包类型？', async ()=>{if(urlChanged) refreshPkgType(id);}, '🔄', '刷新');}
  } else {
    toast(r.message || '保存失败','error');
  }
}

// ── Settings ────────────────────────────────────────────────────────────────

async function delProduct(id,name){
  await confirmAct(`确定删除产品「${name}」？\n该操作将同时删除所有快照数据，且无法恢复。`, async ()=>{const r=await api(`/system/products/${id}`,{method:'DELETE'});if(r.code===0){toast('已删除');products();}else toast(r.message,'error');}, '🗑️', '确认删除', true);
}

async function collectProduct(id,mode){
  const r=await api(`/system/products/${id}/collect`,{method:'POST',body:JSON.stringify({mode})});
  toast(r.message||'已触发','success');
}

// Card-level trigger (data page): single product quick collection
async function dataCollectProduct(sid, name){
  // 2026-08-24: 触发前先查状态,避免无效请求 + 给用户明确反馈"已有任务在跑"
  const s = await api('/system/products/0/collect-status');
  if(s && s.code === 0 && s.data && s.data.active){
    toast('已有产品采集中,请稍后操作','warn');
    showSingleCollectBanner(s.data.source_id, s.data.source_name || name);
    return;
  }
  const r=await api(`/system/products/${sid}/collect`,{method:'POST',body:JSON.stringify({mode:'quick'})});
  if(r.code===0){toast(`「${name}」采集已触发`,'success');showSingleCollectBanner(sid, name);}
  else if(r.code===409){toast(r.message||'已有单产品采集中,请稍后操作','warn');}
  else{toast(r.message||'触发失败','error');}
}
