// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// ── Data ──

// ═══════════════════════════════════════════════════════════════
// TIMEZONE CONVENTION (DO NOT CHANGE):
//   DB stores UTC (datetime('now') in SQLite = UTC).
//   fmtTZ parses as UTC (t+'Z') and converts to LOCAL time.
//   Uses getHours()/getMinutes() — NOT toISOString() — by design.
//   toISOString() would show UTC (8h behind CST). See commit 884a835.
// ═══════════════════════════════════════════════════════════════
// 2026-08-31: 服务端时间(CST/UTC+8),避开浏览器时区差异
// 直接对 UTC 字符串加 8 小时,不依赖浏览器 getHours()
function serverTime(t, full){
  if(!t) return '';
  try{
    let s = String(t).replace('T', ' ').replace('Z', '').replace(/\..*$/, '');
    const dt = new Date(s + 'Z');  // 当作 UTC 解析
    if (isNaN(dt.getTime())) return full ? String(t).substring(0,16) : String(t).substring(0,10);
    // 加 8 小时转 CST
    const cst = new Date(dt.getTime() + 8 * 3600 * 1000);
    const y = cst.getUTCFullYear();
    const m = String(cst.getUTCMonth()+1).padStart(2, '0');
    const d = String(cst.getUTCDate()).padStart(2, '0');
    const hh = String(cst.getUTCHours()).padStart(2, '0');
    const mm = String(cst.getUTCMinutes()).padStart(2, '0');
    return full ? `${y}-${m}-${d} ${hh}:${mm}` : `${y}-${m}-${d}`;
  }catch(e){
    return full ? String(t).substring(0,16) : String(t).substring(0,10);
  }
}

// 2026-09-02: 与 serverTime 对齐 — 强制 +8h 输出 CST,不依赖浏览器时区
function fmtTZ(t,full){if(!t)return'';try{let s=String(t).replace('T',' ').replace('Z','').replace(/\..*$/,'');const dt=new Date(s+'Z');if(isNaN(dt.getTime()))return full?String(t).substring(0,16):String(t).substring(0,10);const cst=new Date(dt.getTime()+8*3600*1000);const y=cst.getUTCFullYear(),m=String(cst.getUTCMonth()+1).padStart(2,'0'),day=String(cst.getUTCDate()).padStart(2,'0'),h=String(cst.getUTCHours()).padStart(2,'0'),min=String(cst.getUTCMinutes()).padStart(2,'0');return full?`${y}-${m}-${day} ${h}:${min}`:`${y}-${m}-${day}`;}catch(e){return full?(t||'').substring(0,16):(t||'').substring(0,10);}}

// 渠道类型 chip:每种类型独立品牌色 + emoji,通知渠道表 / 订阅规则通知对象列共用
// 配色:浅背景 + 深文字 + 1px 同色淡边框,跟现有 .badge-ok/.badge-blue 同款风格
const CH_TYPE_STYLE = {
  email:    {bg:'#fef2f2', fg:'#dc2626', border:'#fecaca', icon:'M'},
  wecom:    {bg:'#ecfdf5', fg:'#0ca678', border:'#bbf7d0', icon:'CW'},
  dingtalk: {bg:'#e6f0ff', fg:'#1d4ed8', border:'#bfdbfe', icon:'DT'},
  feishu:   {bg:'#f5f3ff', fg:'#5b21b6', border:'#ddd6fe', icon:'FS'},
  apprise:  {bg:'#f8fafc', fg:'#475569', border:'#e2e7ee', icon:'AP'},
};
const CH_TYPE_CN = {email:'邮件',wecom:'企微',dingtalk:'钉钉',feishu:'飞书',apprise:'Apprise'};
function chTypeChip(type, withIcon=true){
  const s = CH_TYPE_STYLE[type] || {bg:'#f8fafc', fg:'#475569', border:'#e2e7ee', icon:'•'};
  const cn = CH_TYPE_CN[type] || type;
  const iconHtml = withIcon ? `<span data-css="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:${s.fg};color:#fff;font-size:9px;font-weight:700;line-height:1;margin-right:4px;font-family:Arial,sans-serif">${s.icon}</span>` : '';
  return `<span data-css="display:inline-flex;align-items:center;padding:1px 8px 1px 4px;background:${s.bg};color:${s.fg};border:1px solid ${s.border};border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap">${iconHtml}${cn}</span>`;
}

function toggleChTypeDropdown(){
  const m=document.getElementById('chTypeMenu');
  if(m)m.style.display=m.style.display==='none'?'block':'none';
}
function selectChType(t, triggerCfgChange=true){
  document.getElementById('cht').value=t;
  const cur=document.getElementById('chTypeCurrent');
  if(cur)cur.innerHTML=chTypeChip(t)+' <span data-css="color:var(--muted);font-size:11px;margin-left:4px">▾</span>';
  document.querySelectorAll('.ch-type-option').forEach(o=>{
    const isSel=o.dataset.t===t;
    o.dataset.selected=isSel?'1':'';
    o.style.background=isSel?'var(--primary-light)':'transparent';
  });
  const m=document.getElementById('chTypeMenu');
  if(m)m.style.display='none';
  if(triggerCfgChange && typeof chTypeCh==='function')chTypeCh();
}
// 关闭下拉(点击外部)
document.addEventListener('click',function(e){
  const dd=document.getElementById('chTypeDropdown');
  if(dd && !dd.contains(e.target)){
    const m=document.getElementById('chTypeMenu');
    if(m)m.style.display='none';
  }
});

// ── Dashboard Charts ─────────────────────────────────────────────
const PIE_COLORS = ['#4363e8','#f58536','#26a269','#e66100','#3d5afe','#bf4b8a','#00b8d4','#7cb101','#e6194b','#9e480e'];
// 产品颜色唯一来源: 趋势曲线 / 图例 / 产品分布表必须用同一份按名称索引的色表,
// 否则同一个产品在三处会显示成三种颜色(此前图例按数量排序、曲线按名称排序)
const _PROD_PALETTE = [
  '#4c78a8','#f58518','#54a24b','#e45756','#72b7b2',
  '#e0a584','#b07aa1','#ff9da6','#9d755d','#bab0ac',
  '#5e81ac','#d47e93','#a3be8b','#b48ead','#d08770',
  '#729ece','#ff9e6c','#a4c969','#6cc3d5','#dba1bc',
  '#cf9c5e','#7eb0d5','#d28b9b','#9bc995'
];
function _buildProdColorMap(names){
  const sorted=[...new Set((names||[]).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh'));
  const map={};
  sorted.forEach((p,i)=>{
    map[p]= i<_PROD_PALETTE.length ? _PROD_PALETTE[i] : `hsl(${(i*137.508)%360}, 55%, 50%)`;
  });
  return map;
}

// ── Email provider templates ───────────────────────────────────────────────
// Quick-select preset for common SMTP providers. User picks one to autofill
// host/port (still editable). "custom" leaves fields blank for manual entry.
// Recipients are no longer collected on channel config — see the rule-level
// `customer_emails` field on subscription rules for that.
const EMAIL_PROVIDERS = {
  '163':     { name: '163 邮箱',     host: 'smtp.163.com',     port: 465, user_ph: 'liloyo_x@163.com',   hint: '需用 163 邮箱授权码(非登录密码)。设置 → POP3/SMTP/IMAP → 开启并生成授权码。' },
  'qq':      { name: 'QQ 邮箱',      host: 'smtp.qq.com',      port: 465, user_ph: '123456789@qq.com',   hint: '需用 QQ 邮箱授权码。设置 → 账户 → POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务 → 开启。' },
  'nsfocus': { name: 'nsfocus 邮箱', host: 'mail.nsfocus.com', port: 465, user_ph: 'xuzijun@nsfocus.com',hint: '绿盟企业邮箱,请登录企业微信-工作台-IT智能客服,点击账号相关,再点击获取外网邮箱账号,获取发信密码。' },
  'custom':  { name: '自定义',        host: '',                 port: 465, user_ph: 'user@example.com',   hint: '请手动填写 SMTP 服务器地址、端口和完整邮箱地址(含 @domain)。仅支持 SSL(465)或 STARTTLS(587)加密连接。' },
};

function _matchEmailProvider(host){
  // Find the template whose configured host matches the given SMTP host.
  // Exact string match only — anything else falls back to "custom".
  if(!host) return 'custom';
  for(const [key, p] of Object.entries(EMAIL_PROVIDERS)){
    if(p.host && p.host === host) return key;
  }
  return 'custom';
}

// 全局 table thead 高度统一（规则在 app.css 末尾，CSP 禁止运行时注入 <style>）

function _renderProdDistTable(data){
  const container = document.getElementById('prodDistTable');
  if(!container) return;
  if(!data || data.length===0){
    container.innerHTML='<div data-css="padding:16px;text-align:center;color:var(--gray-disabled);font-size:12px">暂无数据</div>'; return;
  }
  const total = data.reduce((s,e)=>s+e.count,0);
  const cmap = _buildProdColorMap(data.map(d=>d.name));
  container.innerHTML='<table data-css="width:100%;border-collapse:collapse;font-size:12px">'+
    '<thead><tr data-css="height:36px;background:var(--gray-bg);border-bottom:1px solid var(--gray-border);line-height:1.2;box-sizing:border-box">'+
      '<th data-css="padding:8px 12px;height:36px;box-sizing:border-box;text-align:left;color:var(--gray-muted);font-weight:500">产品</th>'+
      '<th data-css="padding:8px 12px;height:36px;box-sizing:border-box;text-align:right;color:var(--gray-muted);font-weight:500">包数量</th>'+
      '<th data-css="padding:8px 12px;height:36px;width:160px;box-sizing:border-box;color:var(--gray-muted);font-weight:500">占比</th>'+
    '</tr></thead><tbody>'+
    data.map((item)=>{
      const pct = total>0 ? (item.count/total*100).toFixed(1) : '0';
      const color = cmap[item.name] || 'var(--gray-disabled)';
      return `<tr data-css="border-bottom:1px solid var(--gray-bg)">
        <td data-css="padding:9px 12px">
          <div data-css="display:flex;align-items:center;gap:6px;min-width:0">
            <span data-css="width:8px;height:8px;border-radius:50%;flex-shrink:0;background:${color}"></span>
            <span data-css="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1" title="${escHtml(item.name)}">${escHtml(item.name)}</span>
          </div>
        </td>
        <td data-css="padding:9px 14px 9px 12px;text-align:right;color:var(--gray-disabled);font-variant-numeric:tabular-nums">${item.count}</td>
        <td data-css="padding:9px 12px">
          <div data-css="display:flex;align-items:center;gap:6px">
            <div data-css="flex:1;height:5px;background:var(--gray-bg);border-radius:4px;overflow:hidden">
              <div data-css="width:${pct}%;height:100%;background:${color};border-radius:4px"></div>
            </div>
            <span data-css="color:var(--gray-muted);font-size:11px;min-width:36px;text-align:right">${pct}%</span>
          </div>
        </td>
      </tr>`;
    }).join('')+
    '</tbody></table>';
}

// 最近更新按 snapshot 聚合: 同一包推给 N 个客户只占一行
function _recentPackageGroups(list){
  const map=new Map();
  (list||[]).forEach(r=>{
    const key=r.snapshot_id||`${r.product_name||''}|${r.package_version||''}|${r.file_name||''}`;
    let g=map.get(key);
    if(!g){
      g={product:r.product_name,ver:r.package_version,file:r.file_name,sent_at:r.sent_at,total:0,sent:0,failed:0,customers:new Set()};
      map.set(key,g);
    }
    g.total++;
    if(r.delivery_status==='sent')g.sent++;
    else if(r.delivery_status==='failed')g.failed++;
    const cname=r.customer_name||(r.customer_id!=null?('#'+r.customer_id):'');
    if(cname)g.customers.add(cname);
    if(r.sent_at&&(!g.sent_at||String(r.sent_at)>String(g.sent_at)))g.sent_at=r.sent_at;
  });
  return [...map.values()]
    .sort((a,b)=>String(b.sent_at||'').localeCompare(String(a.sent_at||'')))
    .slice(0,8);
}

// feat/customer-push-card 2026-09-02: 客户维度推送聚合卡片
// 下钻: 点击客户行 → location.hash='history?days='+rng+'&cust='+cid
// 2026-11-09: 全量显示所有客户; 近N天 0 推送的客户置灰沉底显示"无推送", 不再隐藏
//              (此前过滤掉 0 推送行导致 5 个客户只看到 3 个 —— "客户显示不全")
// 跟产品发布分布保持一致的布局骨架: 色点+名称 / 数字右对齐 / 进度条+百分比
// 状态列 = 失败率 (failed/total*100%), 0% 时全绿, >0% 红色
function _hashIdx(s){let h=0;for(let i=0;i<s.length;i++){h=((h<<5)-h+s.charCodeAt(i))|0;}return Math.abs(h);}
function _renderCustPushTable(items, days){
  const container = document.getElementById('custPushTable');
  if(!container) return;
  if(!items || items.length===0){
    container.innerHTML='<div data-css="padding:16px;text-align:center;color:var(--gray-disabled);font-size:12px">暂无客户</div>';
    return;
  }
  // 2026-11-09 修复"客户显示不全": 0 推送客户不再隐藏, 全量渲染(后端已按 total DESC 排序, 0 推送自然沉底)
// 2026-09-04: 客户推送表 — 响应式: 渲染后用 JS 算 card 实际宽度按比例分配三列, 任意视口都对齐
  // 列宽比例 35%/15%/50% (跟用户截图的视觉比例一致), padding 让 td 内容左/右对齐
  // 用 table-layout:fixed + JS 设 th/td width (table-layout:auto 下 td 内容长度会破坏比例)
  container.innerHTML='<table data-css="width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed">'+
'<thead><tr data-css="height:36px;background:var(--gray-bg);border-bottom:1px solid var(--gray-border);line-height:1.2;box-sizing:border-box">'+
      '<th data-col="0" data-css="padding:8px 12px;height:36px;box-sizing:border-box;text-align:left;color:var(--gray-muted);font-weight:500">客户</th>'+
      '<th data-col="1" data-css="padding:8px 12px;height:36px;box-sizing:border-box;text-align:left;color:var(--gray-muted);font-weight:500">推送</th>'+
      '<th data-col="2" data-css="padding:8px 12px;height:36px;width:160px;box-sizing:border-box;text-align:left;color:var(--gray-muted);font-weight:500">状态</th>'+
    '</tr></thead><tbody>'+
    items.map((c,i)=>{
      const total = c.total||0, failed = c.failed||0;
      const isZero = total === 0 && failed === 0;
      const failPct = total>0 ? (failed/total*100).toFixed(1) : '0';
      const tipLines = [
        `客户: ${c.name}`,
        `总数/成功/失败: ${total} / ${(c.success||0)} / ${failed}`,
        c.last_product ? `最近推送: ${c.last_product}` : '',
        c.last_sent_at ? `时间: ${fmtTZ(c.last_sent_at, true)}` : `近${days}天无推送`,
        '点击查看该客户推送历史',
      ].filter(Boolean).join('\n');
      const color = PIE_COLORS[_hashIdx(String(c.id||c.name))%PIE_COLORS.length];
      return `<tr data-css="border-bottom:1px solid var(--gray-bg);cursor:pointer;transition:background .12s;${isZero?'opacity:.5':''}" class="pill-tip" data-tip="${escHtml(tipLines)}" data-on-click="_gotoHistoryDays" data-args='${_dataArgs(days, c.id)}'>
        <td data-col="0" data-css="padding:9px 12px;box-sizing:border-box">
          <div data-css="display:flex;align-items:center;gap:6px;min-width:0">
            <span data-css="width:8px;height:8px;border-radius:50%;flex-shrink:0;background:${isZero?'var(--gray-disabled)':color}"></span>
            <span data-css="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(c.name)}">${escHtml(c.name)}</span>
          </div>
        </td>
        <td data-col="1" data-css="padding:9px 14px 9px 12px;box-sizing:border-box;text-align:right;color:${isZero?'var(--gray-disabled)':(failed>0?'var(--danger)':'var(--success)')};font-variant-numeric:tabular-nums">${total}</td>
        <td data-col="2" data-css="padding:9px 12px;box-sizing:border-box">
          ${isZero
            ? '<span data-css="color:var(--gray-disabled);font-size:11px">无推送</span>'
            : `<div data-css="display:flex;align-items:center;gap:6px">
                <div data-css="flex:1;height:5px;background:var(--gray-bg);border-radius:4px;overflow:hidden">
                  <div data-css="width:${failPct}%;height:100%;background:${failed>0?'var(--danger)':'var(--success)'};border-radius:4px"></div>
                </div>
                <span data-css="color:var(--gray-muted);font-size:11px;min-width:36px;text-align:right">${failPct}%</span>
              </div>`}
        </td>
      </tr>`;
    }).join('')+
    '</tbody></table>';
  // 响应式列宽: 渲染后立刻算 card 实际 content 宽度, 按 35%/15%/50% 分配到 th/td
  // 任意视口下三列都等比例伸缩, th/td 严格对齐
  setTimeout(()=>{
    const tbl=container.querySelector('table');
    if(!tbl)return;
    const cardW=tbl.clientWidth;
    const ratios=[0.35,0.15,0.50];
    const widths=ratios.map(r=>Math.floor(cardW*r));
    // 最后一列拿剩余, 避免舍入误差
    widths[2]=cardW-widths[0]-widths[1];
    container.querySelectorAll('th[data-col],td[data-col]').forEach(el=>{
      const col=parseInt(el.dataset.col);
      el.style.width=widths[col]+'px';
    });
  },0);
}

function _renderTimelineChart(canvasId, data, days, visible){
  const canvas = document.getElementById(canvasId);
  if(!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth || 700;
  const H = 200;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  ctx.scale(dpr, dpr);
  ctx.clearRect(0,0,W,H);

  if(!data || data.length===0){
    ctx.font='13px system-ui'; ctx.fillStyle='var(--gray-disabled)'; ctx.textAlign='center';
    ctx.fillText('暂无数据', W/2, H/2); return;
  }

  const allProducts = [...new Set(data.flatMap(d => Object.keys(d.counts)))];
  const visibleProds = allProducts.filter(p => visible[p]);
  if(visibleProds.length===0){
    ctx.font='12px system-ui'; ctx.fillStyle='var(--gray-disabled)'; ctx.textAlign='center';
    ctx.fillText('请点击上方产品标签显示曲线', W/2, H/2); return;
  }

  // 与图例/产品分布表共用一份按产品名索引的色表
  const prodColor = _buildProdColorMap(allProducts);

  // 填充日期空洞
  const today=new Date();
  const dateMap={};
  data.forEach(d=>{ dateMap[d.date]=d.counts; });
  const points=[];
  for(let i=days-1;i>=0;i--){
    const dt=new Date(today); dt.setDate(dt.getDate()-i);
    const ds=dt.toISOString().substring(0,10);
    const counts={};
    allProducts.forEach(p=>{ counts[p] = (dateMap[ds] && dateMap[ds][p]) || 0; });
    points.push({date:ds, counts});
  }

  const max = Math.max(...points.map(p=>Math.max(...visibleProds.map(pd=>p.counts[pd]),0)),1);
  const pad=46, top=16, bot=H-16, right=12;
  const chartW=W-pad-right, chartH=bot-top;
  const xStep=chartW/(days-1||1);

  // 网格线（淡色）
  ctx.strokeStyle='var(--gray-border)'; ctx.lineWidth=1;
  const gridN=4;
  for(let g=0;g<=gridN;g++){
    const y=top+(chartH/gridN)*g;
    ctx.beginPath(); ctx.moveTo(pad,y); ctx.lineTo(W-right,y); ctx.stroke();
    ctx.fillStyle='var(--gray-disabled)'; ctx.font='11px system-ui'; ctx.textAlign='right';
    ctx.fillText(Math.round(max-(max/gridN)*g),pad-6,y+4);
  }
  // Y轴线
  ctx.strokeStyle='var(--gray-border)';
  ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(pad,top); ctx.lineTo(pad,bot); ctx.stroke();
  // X轴底线
  ctx.beginPath(); ctx.moveTo(pad,bot); ctx.lineTo(W-right,bot); ctx.stroke();

  // X轴标签：每个点都显示
  ctx.textAlign='center';
  for(let i=0;i<points.length;i++){
    const x=pad+i*xStep;
    const dt=new Date(points[i].date);
    ctx.fillStyle='var(--gray-disabled)'; ctx.font='10px system-ui';
    ctx.fillText((dt.getMonth()+1)+'/'+dt.getDate(),x,bot+14);
  }

  // 绘制每条可见产品曲线（hover 高亮：被悬停产品加粗，其他变淡）
  const hover = window._tlHover;
  const isHover = (p) => hover && hover === p;
  const isDim   = (p) => hover && hover !== p;
  visibleProds.forEach(product => {
    ctx.beginPath();
    points.forEach((p,i)=>{
      const x=pad+i*xStep;
      const y=bot-(p.counts[product]/max)*chartH;
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    });
    ctx.strokeStyle=prodColor[product];
    ctx.lineWidth=isHover(product)?4:2;
    ctx.globalAlpha=isDim(product)?0.22:1;
    ctx.stroke();

    // 数据点（稍大一点）
    points.forEach((p,i)=>{
      if(p.counts[product]===0) return;
      const x=pad+i*xStep;
      const y=bot-(p.counts[product]/max)*chartH;
      ctx.beginPath();
      ctx.arc(x,y,isHover(product)?5:3,0,Math.PI*2);
      ctx.fillStyle=prodColor[product];
      ctx.fill();
    });
  });
  ctx.globalAlpha=1;

  // 保存数据供 tooltip 使用
  canvas._chartData = { points, allProducts, visibleProds, prodColor, max, pad, top, bot, chartW, chartH, xStep, days, chartH, W, right };
  canvas.onmousemove = (ev) => _showChartTooltip(ev, canvas);
  canvas.onmouseleave = (ev) => _hideChartTooltip(ev, canvas);
}

function _tlFindHoveredProduct(d, mx, my){
  // 在 mx 最近的 X 索引上,找鼠标 Y 距离哪条可见产品曲线最近(阈值 8px)
  if(!d || d.visibleProds.length===0) return null;
  const idx = Math.round((mx - d.pad) / d.xStep);
  if(idx < 0 || idx >= d.points.length) return null;
  const pt = d.points[idx];
  const chartH = d.chartH, bot = d.bot, max = d.max;
  let best = null, bestDist = 8;  // 8px 阈值
  for(const p of d.visibleProds){
    const v = pt.counts[p] || 0;
    const y = bot - (v / max) * chartH;
    const dist = Math.abs(my - y);
    if(dist < bestDist){ bestDist = dist; best = p; }
  }
  return best;
}

function _showChartTooltip(e, canvas){
  const d = canvas._chartData;
  if(!d) return;
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const mx = (e.clientX - rect.left) * scaleX / (window.devicePixelRatio||1);
  const scaleY = canvas.height / rect.height;
  const my = (e.clientY - rect.top) * scaleY / (window.devicePixelRatio||1);
  if(mx < d.pad || mx > d.pad + d.chartW) { _hideChartTooltip(e, canvas); return; }

  // 检测鼠标是否靠近某条曲线 → 设置 hover 高亮
  const hovered = _tlFindHoveredProduct(d, mx, my);
  window._tlHover = hovered;

  // 重绘图表清除旧线
  _renderTimelineChart_Redraw(d, mx, e);
}

function _renderTimelineChart_Redraw(d, mx, e){
  const canvas = document.getElementById('chartTimeline');
  if(!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth || 700;
  const H = 200;
  ctx.clearRect(0,0,W,H);
  const pad=d.pad, top=d.top, bot=d.bot, chartH=d.chartH, chartW=d.chartW, right=d.right;
  const max=d.max, points=d.points, visibleProds=d.visibleProds, prodColor=d.prodColor, days=d.days;
  const xStep=d.xStep;

  // 重新绘制网格和轴
  ctx.strokeStyle='var(--gray-border)'; ctx.lineWidth=1;
  const gridN=4;
  for(let g=0;g<=gridN;g++){
    const y=top+(chartH/gridN)*g;
    ctx.beginPath(); ctx.moveTo(pad,y); ctx.lineTo(W-right,y); ctx.stroke();
    ctx.fillStyle='var(--gray-disabled)'; ctx.font='11px system-ui'; ctx.textAlign='right';
    ctx.fillText(Math.round(max-(max/gridN)*g),pad-6,y+4);
  }
  ctx.strokeStyle='var(--gray-border)'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(pad,top); ctx.lineTo(pad,bot); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(pad,bot); ctx.lineTo(W-right,bot); ctx.stroke();

  // X轴标签
  ctx.textAlign='center';
  for(let i=0;i<points.length;i++){
    const x=pad+i*xStep;
    const dt=new Date(points[i].date);
    ctx.fillStyle='var(--gray-disabled)'; ctx.font='10px system-ui';
    ctx.fillText((dt.getMonth()+1)+'/'+dt.getDate(),x,bot+14);
  }

  // 绘制曲线（hover 高亮：被悬停产品加粗，其他变淡）
  const hover = window._tlHover;
  const isHover = (p) => hover && hover === p;
  const isDim   = (p) => hover && hover !== p;
  visibleProds.forEach(product => {
    ctx.beginPath();
    points.forEach((p,i)=>{
      const x=pad+i*xStep;
      const y=bot-(p.counts[product]/max)*chartH;
      i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    });
    ctx.strokeStyle=prodColor[product];
    ctx.lineWidth=isHover(product)?4:2;
    ctx.globalAlpha=isDim(product)?0.22:1;
    ctx.stroke();

    points.forEach((p,i)=>{
      if(p.counts[product]===0) return;
      const x=pad+i*xStep;
      const y=bot-(p.counts[product]/max)*chartH;
      ctx.beginPath();
      ctx.arc(x,y,isHover(product)?5:3,0,Math.PI*2);
      ctx.fillStyle=prodColor[product];
      ctx.fill();
    });
  });
  ctx.globalAlpha=1;

  // 垂直参考线
  const idx = Math.round((mx - pad) / xStep);
  const clampedX = Math.max(pad, Math.min(pad + idx * xStep, pad + chartW));
  ctx.beginPath();
  ctx.moveTo(clampedX, top);
  ctx.lineTo(clampedX, bot);
  ctx.strokeStyle='var(--gray-disabled)'; ctx.lineWidth=1; ctx.setLineDash([4,4]); ctx.stroke(); ctx.setLineDash([]);

  // tooltip（仅在有效范围内绘制）
  if(mx >= pad && mx <= pad + chartW){
    const ptIdx = Math.round((clampedX - pad) / xStep);
    if(ptIdx >=0 && ptIdx < points.length){
    const pt = points[ptIdx];
    const dt = new Date(pt.date);
    const dateStr = (dt.getMonth()+1)+'月'+dt.getDate()+'日';

    // Sort products by today's publish count (desc) so the most active ones
    // appear first and don't get cut off by the tooltip height.
    // Then split into 2 columns to keep the tooltip compact.
    const sortedProds = visibleProds.slice().sort((a, b) =>
      (pt.counts[b] || 0) - (pt.counts[a] || 0));
    const half = Math.ceil(sortedProds.length / 2);
    const left = sortedProds.slice(0, half);
    const right = sortedProds.slice(half);
    const buildCol = (prods) => prods.map(p => {
      const val = pt.counts[p] || 0;
      const color = prodColor[p];
      const isHovered = window._tlHover && window._tlHover === p;
      const isDimmed  = window._tlHover && window._tlHover !== p;
      // Highlight: 当前 hover 的产品加粗 + 名字加产品色 + 数字大
      // 其他产品变淡 (透明度 0.45)
      const wrapStyle = isHovered
        ? 'display:flex;align-items:center;gap:6px;font-size:12px;line-height:1.5;background:var(--primary-light);border-radius:4px;padding:2px 4px;margin:-2px -4px'
        : `display:flex;align-items:center;gap:6px;font-size:11px;line-height:1.5;opacity:${isDimmed ? '0.45' : '1'}`;
      const dotStyle = isHovered
        ? `width:10px;height:10px;border-radius:50%;background:${color};display:inline-block;flex-shrink:0;box-shadow:0 0 0 2px #fff,0 0 0 3px ${color}`
        : `width:8px;height:2px;background:${color};display:inline-block;flex-shrink:0`;
      const nameStyle = isHovered
        ? `color:var(--primary);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:140px`
        : `color:var(--gray-disabled);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px`;
      const valStyle = isHovered
        ? 'margin-left:auto;font-weight:700;color:var(--primary);font-variant-numeric:tabular-nums'
        : 'margin-left:auto;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums';
      return `<div data-css="${wrapStyle}">
        <span data-css="${dotStyle}"></span>
        <span data-css="${nameStyle}" title="${p}">${p}</span>
        <span data-css="${valStyle}">${val}</span>
      </div>`;
    }).join('');

    let html = `<div data-css="font-size:11px;color:var(--gray-muted);margin-bottom:6px">${dateStr}</div>`;
    html += `<div data-css="display:flex;gap:14px">`;
    html += `<div data-css="flex:1;min-width:140px">${buildCol(left)}</div>`;
    if (right.length) html += `<div data-css="flex:1;min-width:140px">${buildCol(right)}</div>`;
    html += `</div>`;

    let tip = document.getElementById('chartTip');
    if(!tip){ tip=document.createElement('div'); tip.id='chartTip'; document.body.appendChild(tip); }
    tip.style.cssText='position:fixed;background:#fff;border:1px solid var(--gray-border);border-radius:8px;padding:10px 12px;box-shadow:0 4px 12px rgba(0,0,0,.1);z-index:9999;pointer-events:none;font-family:system-ui;max-width:360px';
    tip.innerHTML = html;
    const tx = e.clientX + 14, ty = e.clientY - 10;
    tip.style.left = tx + 'px';
    tip.style.top = ty + 'px';
    tip.style.display = 'block';
    }
  }
}

function _hideChartTooltip(e, canvas){
  const tip = document.getElementById('chartTip');
  if(tip) tip.style.display = 'none';
  // 清空 hover 状态
  if(window._tlHover){ window._tlHover = null; }
  // 重绘清除虚线
  const d = canvas._chartData;
  if(d) _renderTimelineChart_Redraw(d, -9999);
}

function _renderTlLegend(allProducts, visible){
  const container = document.getElementById('tlLegend');
  if(!container) return;
  const prodColor = _buildProdColorMap(allProducts);
  // 图例固定按名称排序,与曲线色表同源,避免顺序变化导致颜色对不上
  const sorted = [...new Set(allProducts||[])].sort((a,b)=>a.localeCompare(b,'zh'));
  container.innerHTML = sorted.map((p) => {
    const on = visible[p];
    return `<span data-on-click="_toggleTlProd" data-args='${_dataArgs(p)}' data-css="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;margin:2px;border-radius:4px;cursor:pointer;font-size:11px;border:1px solid ${on?prodColor[p]:'#ddd'};background:${on?'transparent':'#f5f5f5'};color:${on?prodColor[p]:'var(--gray-disabled)'};text-decoration:${on?'none':'line-through'}">
      <span data-css="width:10px;height:2px;background:${prodColor[p]};display:inline-block;border-radius:4px"></span>
      ${escHtml(p)}
    </span>`;
  }).join('');
}

function _toggleTlProd(p){
  window._tlVisible[p] = !window._tlVisible[p];
  _renderTimelineChart('chartTimeline', window._tlData, window._tlDays, window._tlVisible);
  const allProds = Object.keys(window._tlVisible);
  _renderTlLegend(allProds, window._tlVisible);
}
// ── Data Page ──────────────────────────────────────────────
const PAGE_SIZE = 20;
let _dataQuery = '';
let _dataFilters = { has_new: false, has_rb: false, empty: false };
// 搜索字段 (由 select 决定):
//   'description'  → 后端 search 端点 (整段 description_raw LIKE) — hit ids 入 _dataSearchHitIds
//   其他字段       → 前端本地匹配对应单字段
let _dataSearchField = 'description';
// Set of snap ids that match the current /api/latest/search?q=
// null = search not active or not yet returned (frontend falls back to local match).
// Set = backend returned hits; frontend shows ONLY those (full-text incl. description_raw).
let _dataSearchHitIds = null;
// Map<snap_id, list[chain]> 缓存后端 search 返回的 chain 列表,供 dataBuildFeed
// 按 chain 展开 hit 行用。同物理文件 + N chain → N 行,前端可见。
// null = search 未激活或未返回。
let _dataSearchSnapChains = null;
let _lastScheduler = null; // cached scheduler for header display across polls
let _dataPage = 1;
let _dataAll = {};
// Chain prefix cache: keyed by 'sid|chain.join("/")', value = precomputed prefixSegCount[]
// Eliminates repeated O(paths) traversal in dataRightToggleRow
let _dataChainPrefixCache = {};
// Name → sid map (built when _dataAll loads); used to detect stale chain
// subscriptions whose paths no longer exist after a vendor re-discovery.
let _nameToSid = {};
// Cached name|chain match results so render rules can reuse without recompute
let _chainStaleCache = {};
// Snapshot detail cache: keyed by snapshot_id, value = Promise from /api/snapshot/{id}
let _snapDetailCache = {};
// Per-source URL→chain map: keyed by sid, value = { normalizedUrl: chain }
// Built once in dataPage() after _dataAll loads, reused by dataBuildFeed every render
let _srcUrlChainMap = {};
// Per-source URL→path_id map (matches save_snapshot 4-tuple UNIQUE index).
// Backend attaches path_id = MD5(BASE_URL + url + JSON(chain))[:12] to each
// path in /latest/snapshots; we index by URL for O(1) lookup in dataRightGoto.
let _srcUrlPathIdMap = {};

async function dataPage() {
  const _seq=_navSeq;
  _dataPage = 1;
  _dataSelectedSid = null;
  _dataSelectedChain = null;
  _dataSelectedUrl = null;
  _dataSelectedNode = null;

  const [r, cfgR] = await Promise.all([
    api('/latest/snapshots').catch(() => ({ data: {} })),
    api('/settings/config').catch(() => ({})),
  ]);
  _dataAll = r.data || {};
  _feedPkgLimit = Math.max(1, parseInt(cfgR.data?.snapshots_per_group) || 5);
  // Rebuild name→sid index; clear stale-chain cache since _dataAll changed
  _nameToSid = {};
  Object.entries(_dataAll).forEach(([sid, src]) => {
    const nm = src.display_name || src.name;
    if (nm) _nameToSid[nm] = String(sid);
  });
  _chainStaleCache = {};
  const t0 = performance.now();
  // Build _srcUrlChainMap once: URL→chain lookup per source
  // Also clear chain prefix cache since _dataAll changed
  _srcUrlChainMap = {};
  _srcUrlPathIdMap = {};
  _dataChainPrefixCache = {};
  Object.entries(_dataAll).forEach(([sid, src]) => {
    if (!src.package_type?.paths) return;
    const map = {};
    const pathIdMap = {};
    src.package_type.paths.forEach(p => {
      if (p.url && Array.isArray(p.chain) && p.chain.length > 0) {
        const norm = ('/' + p.url).replace(/\/+$/, '');
        map[norm] = p.chain;
        // Backend attaches path_id to each path in /latest/snapshots — use it
        // directly (matches save_snapshot 4-tuple UNIQUE index).
        if (p.path_id) pathIdMap[norm] = p.path_id;
      }
    });
    _srcUrlChainMap[sid] = map;
    _srcUrlPathIdMap[sid] = pathIdMap;
  });

  // Fetch scheduler + detailed progress in parallel
  const [sR, prR] = await Promise.all([
    api('/settings/scheduler').catch(() => ({})),
    api('/system/collect/progress').catch(() => ({})),
  ]);
  console.log('[debug scheduler]', JSON.stringify(sR));
  const sch = sR.data || {};
  _lastScheduler = sch; // cache for poll updates
  const p = prR.data?.progress || {};  // detailed progress (current_product, products_done/total, etc.)
  const mode_label = { quick: '⚡Quick', full: '🔍Full', '': '' };
  const isRunning = sch.is_running;
  const curMode = mode_label[sch.current_mode] || sch.current_mode || '';
  let lastText = sch.last_run ? fmtTZ(sch.last_run, true) : '—';
  let nextText = '—';
  if (sch.last_run) {
    const d = new Date(sch.last_run + 'Z');
    d.setUTCHours(d.getUTCHours() + (sch.interval_hours || 24));
    nextText = fmtTZ(d.toISOString().substring(0, 19), true);
  }
  // Progress % for display
  const pct = (p.products_total && p.products_total > 0) ? Math.round(p.products_done / p.products_total * 100) : 0;
  const progressHtml = isRunning
    ? `<span data-css="color:var(--primary);font-weight:600;flex-shrink:0">⏳ 采集中 ${pct}% ${p.current_product || ''} (${p.products_done||0}/${p.products_total||0})</span>`
    : '';

  const totalActive = Object.values(_dataAll).filter(s => s.is_active).length;
  const filterHtml = [
    dataChipHtml('has_new', '近 30 天发包'),
    dataChipHtml('has_rb', '含撤回'),
    dataChipHtml('empty', '空数据'),
  ].join('');

  let h = `<div class="data-page">
    <div class="data-toolbar">
<div class="data-search-wrap">
        <span class="data-search-icon">🔍</span>
        <input class="data-search" id="dataSearch" placeholder="按所选字段搜索…" value="${escHtml(_dataQuery)}" data-on-input="dataSearchDebounced" data-args='${_dataArgs('$value')}'>
        <select id="dataSearchField" class="sel-sm dd" data-on-change="dataSearchFieldChange" data-css="margin-left:6px">
          <option value="description" selected>描述</option>
          <option value="product_name">产品名</option>
          <option value="version_branch">版本号</option>
          <option value="file_name">文件名</option>
          <option value="package_type">包类型</option>
        </select>
        ${_dataQuery ? `<span class="data-search-clear" data-on-click="dataSearch" data-args='[""]'>×</span>` : ''}
      </div>
      <div class="data-filters">
        <span class="data-filter-label">筛选:</span>
        ${filterHtml}
        <select id="dataUrgencyFilter" class="sel-sm dd" data-on-change="dataUrgencyChange" data-args='${_dataArgs('$value')}' data-css="margin-left:8px">
          <option value="">紧急度</option>
          <option value="critical">🔴 critical</option>
          <option value="high">⚠️ high</option>
          <option value="normal">ℹ️ normal</option>
        </select>
      </div>
      <div class="data-meta">
        <span class="data-count-badge">${totalActive} 个产品</span>
      </div>
    </div>
    <div class="data-layout">
      <div id="dataLeft"></div>
      <div class="data-right-wrap">
        <div class="data-right-body" id="dataRight"></div>
        <div class="feed-pager" id="dataPager"></div>
      </div>
    </div>
  </div>`;

  if(navStale(_seq))return;
  document.getElementById('app').innerHTML = h;
  dataRenderLeft();
  dataRenderRight(sch, p, { lastText, nextText, isRunning, curMode, mode_label, progressHtml });
  console.log('[perf] dataPage total:', Math.round(performance.now() - t0), 'ms');

  // ── Collect progress polling (banner 由全局轮询维护,这里只更新 header) ──
  if (isRunning) {
    _collectRunning = true;
    // banner 由 pollCollectBannerGlobal 全局维护,这里不重复启动
  } else {
    _collectRunning = false;
    // DataPage 进入但采集没在跑 → 不停全局轮询 (可能其他页面刚启动采集)
  }

  // ── One-time event delegation (NEVER inside dataRenderLeft) ──
  document.getElementById('dataLeft').addEventListener('click', function(e) {
    // data-on-* 委托在 document 上执行；带 data-stop 的子元素等价于原内联 stopPropagation，
    // 必须在祖先监听器里提前放行，否则会同时触发树节点/卡片折叠。
    if (e.target.closest('[data-stop]')) return;
    // Card header toggle
    var cardHd = e.target.closest('[data-action="cardToggle"]');
    if (cardHd) {
      var targetId = cardHd.getAttribute('data-body-id');
      if (targetId) {
        var card = cardHd.closest('.card');
        var body = document.getElementById(targetId);
        var wasVisible = body && body.style.display !== 'none';
        productCardToggle(targetId);
        // Highlight card when expanded; remove highlight when collapsed
        if (card) {
          if (!wasVisible) {
            document.querySelectorAll('.card-selected').forEach(el => el.classList.remove('card-selected'));
            card.classList.add('card-selected');
          } else {
            card.classList.remove('card-selected');
          }
        }
      }
      return;
    }
    // Tree arrow toggle (no inline onclick needed)
    var arrow = e.target.closest('[data-action="treeToggle"]');
    if (arrow) {
      treeToggleById(arrow.getAttribute('data-target'));
      return;
    }
    // Chain selector tree arrow toggle
    var csArrow = e.target.closest('[data-action="csTreeToggle"]');
    if (csArrow) {
      csTreeToggleById(csArrow.getAttribute('data-target'));
      return;
    }
    // Tree node click
    var node = e.target.closest('[data-action="treeNode"]');
    if (!node) {
      // Chain selector tree node click
      var csNode = e.target.closest('[data-action="csTreeNode"]');
      if (csNode) {
        const chain = JSON.parse(csNode.getAttribute('data-chain') || '[]');
        const url = csNode.getAttribute('data-url') || '';
        csTreeNodeClick(chain, url, csNode);
        return;
      }
      return;
    }
    if (!node) return;
    const sid = node.getAttribute('data-sid');
    const chain = JSON.parse(node.getAttribute('data-chain') || '[]');
    const url = node.getAttribute('data-url') || '';
    const isLeaf = node.getAttribute('data-isleaf') === '1';
    const snapIds = (node.getAttribute('data-snapids') || '').split(',').filter(Boolean);
    const verHint = node.getAttribute('data-verhint') || '';
    const pkgHint = node.getAttribute('data-pkghint') || '';
    // Backend-computed path_id for exact chain match (preferred over verHint).
    const pathId = node.getAttribute('data-pathid') || '';
    dataTreeNodeClick(sid, chain, url, isLeaf, snapIds, node, verHint, pkgHint, pathId);
  });
}

let _dataSelectedSid = null; // currently selected source id
let _dataSelectedChain = null; // selected tree node chain
let _dataSelectedUrl = null; // selected leaf URL (for exact URL filtering)
let _dataSelectedVerHint = null; // version_branch hint for intermediate node filtering
let _dataSelectedPkgHint = null; // package_type hint for intermediate node filtering
let _dataSelectedPathId = null; // path_id for exact chain match (avoids URL-collision false positives)
let _dataSelectedNode = null; // the tree node the user clicked (for descendants URL collection)

function _gotoHistoryDays(days,cust){
  location.hash=`history?days=${days}&cust=${cust}`;
  setTimeout(()=>showPage('history'),0);
}
function _pushChannelChanged(val){_pushChannelId=parseInt(val);updatePushConfirmState();}

function dataChipHtml(key, label) {
  const active = _dataFilters[key] ? 'data-chip-active' : '';
  return `<button class="data-chip ${active}" data-filter-key="${key}" data-on-click="dataToggleFilter" data-args='${_dataArgs(key)}'>${label}</button>`;
}

function dataSearchFieldChange() {
  const sel = document.getElementById('dataSearchField');
  if (!sel) return;
  _dataSearchField = sel.value;
  // 字段切换 → 立即重新匹配 (用现有 query)
  if (_dataQuery) {
    dataSearch(_dataQuery);
  }
}

function dataSearch(v) {
  _dataQuery = v.trim();
  _dataPage = 1;
  if (!_dataQuery || _dataQuery.length < 2) {
    _dataSearchHitIds = null;  // 清空: 进入本地匹配模式
    dataRenderLeft();
    dataRenderRight();
    return;
  }
  // 2026-08-21: 所有字段都走后端 /api/latest/search,统一获取每条 hit 的 chains
  // (解决 chain 共享 URL 搜索漏命中问题)。后端已为每个 ALLOWED_SEARCH_FIELDS
  // 实现对应 SQL 分支:description, product_name, version_branch,
  // package_type, file_name, all。
  _dataSearchHitIds = null;
  _dataSearchSnapChains = null;
  dataRenderLeft();
  dataRenderRight();
  const field = _dataSearchField || 'description';
  api('/latest/search?q=' + encodeURIComponent(_dataQuery) + '&field=' + encodeURIComponent(field) + '&limit=200')
    .then(r => {
      const hits = (r.data && r.data.snapshots) || [];
      // 仅在用户没改 query 时覆盖
      if (_dataQuery === v.trim()) {
        _dataSearchHitIds = new Set(hits.map(h => h.id));
        // 2026-08-21: 缓存每条 hit 的 chains 列表,dataBuildFeed 用它按 chain 展开 N 行
        const chainsBySnap = new Map();
        for (const h of hits) {
          if (Array.isArray(h.chains) && h.chains.length > 0) {
            chainsBySnap.set(h.id, h.chains);
          }
        }
        _dataSearchSnapChains = chainsBySnap;
        dataRenderLeft();
        dataRenderRight();
      }
    })
    .catch(() => {
      _dataSearchHitIds = null;
      _dataSearchSnapChains = null;
    });
}

// Debounce wrapper: 输入时延迟 250ms 才调 dataSearch,
// 避免每个按键都 re-render 80 个 card + 2200 行 feed row。
// 2026-07-28: 用户原始问题 - 输入搜索框时实时渲染 2200+ 行 feed,卡顿。
// fix(P0-C): 输入框 oninput 加 250ms debounce。
// clear (×) 按钮保持 immediate 调用 dataSearch('') — 用户期望即时响应。
let _dataSearchTimer = null;
function dataSearchDebounced(v) {
  if (_dataSearchTimer) clearTimeout(_dataSearchTimer);
  _dataSearchTimer = setTimeout(() => {
    _dataSearchTimer = null;
    dataSearch(v);
  }, 250);
}

function dataToggleFilter(key) {
  // 互斥单选 (radio-style toggle): 点击同一 key (已开) → 关掉,显示所有产品;
  // 点击另一个 key → 关掉其他 chip,只开这个。
  // 2026-07-28: 修复 3-chip AND 累积导致"未找到匹配条件的产品"反模式
  // (用户原话:多点击恢复成初始状态/清空筛选)。3 chip 是 chip group,互斥。
  // urgency 由 dataUrgencyChange 单独处理。
  const wasOn = !!_dataFilters[key];
  // 先关所有 chip key
  ['has_new', 'has_rb', 'empty'].forEach(k => { _dataFilters[k] = false; });
  // 若之前是关的,这次打开;否则保持全关(显示所有)
  if (!wasOn) _dataFilters[key] = true;
  _dataPage = 1;
  dataRenderLeft();
  dataRenderRight();
}

function dataUrgencyChange(v) {
  _dataFilters.urgency = v;
  _dataPage = 1;
  dataRenderLeft();
  dataRenderRight();
}

function dataSourceFlags(src) {
  const snaps = src.snapshots || [];
  const hasRb = snaps.some(s => s.status === 'withdrawn');
  // hasNew: 有 active 且未推送过(delivery_log 没有 sent 记录)的快照
  const hasNew = snaps.some(s => s.status === 'active' && !s.last_delivered_at);
  const isEmpty = snaps.length === 0;
  // freshness: newest published_at among all snapshots
  let freshness = 'idle'; // 'new' ≤30d, 'lowfreq' 31-365d, 'idle' >365d 或无 published_at
  let latestPublishedTs = 0; // 0 = 从未发布过包,排到末尾
  if (!isEmpty) {
    const now = Date.now();
    latestPublishedTs = snaps.reduce((max, s) => {
      if (!s.published_at) return max;
      const t = new Date(s.published_at).getTime();
      return t > max ? t : max;
    }, 0);
    if (latestPublishedTs > 0) {
      const days = (now - latestPublishedTs) / 86400000;
      if (days <= 30) freshness = 'new';
      else if (days <= 365) freshness = 'lowfreq';
    }
  }
  return { hasRb, hasNew, freshness, isEmpty, latestPublishedTs };
}

function dataFilteredSources() {
  return Object.entries(_dataAll).filter(([sid, src]) => {
    if (!src.is_active) return false;
    if (_dataQuery) {
      const q = _dataQuery.toLowerCase();
      // 2026-08-21: 所有字段搜索都走后端 /api/latest/search,统一通过
      // _dataSearchHitIds 判断 src 下是否有 snap 命中。
      // 1) srcHasHit:src.snapshots 中任一在 _dataSearchHitIds 集合里
      // 2) nameMatch:产品名匹配 (产品名不依赖后端搜索)
      // 任一为真就保留 src。
      if (_dataSearchHitIds) {
        const srcHasHit = (src.snapshots || []).some(s => _dataSearchHitIds.has(s.id));
        const nameMatch = (src.display_name || src.name || '').toLowerCase().includes(q);
        if (!nameMatch && !srcHasHit) return false;
      } else {
        // 后端 search 还没返回 (异步),先按 file_name 粗略匹配,避免空挡
        // 这是 search 后端返回前的 fallback。
        const matchSnap = (s) => (s.file_name || '').toLowerCase().includes(q);
        const nameMatch = (src.display_name || src.name || '').toLowerCase().includes(q);
        const verMatch = (src.snapshots || []).some(matchSnap);
        if (!nameMatch && !verMatch) return false;
      }
    }
    const { hasRb, freshness, isEmpty } = dataSourceFlags(src);
    if (_dataFilters.has_new && freshness !== 'new') return false;
    if (_dataFilters.has_rb && !hasRb) return false;
    if (_dataFilters.empty && !isEmpty) return false;
    if (_dataFilters.urgency) {
      const matchUrg = (src.snapshots || []).some(s => s.urgency === _dataFilters.urgency && s.status === 'active');
      if (!matchUrg) return false;
    }
    return true;
  });
}

function dataSortedSources(filt) {
  // 主排序:最近发布过更新的产品在前(latestPublishedTs 倒序)
  // 0 = 从未发布过包 → 排到末尾(其他产品之后)
  // 次排序:有未推送(hasNew)在前 → 含撤回(hasRb)在前
  return filt.sort(([sid, a], [sid2, b]) => {
    const fa = dataSourceFlags(a), fb = dataSourceFlags(b);
    const aHas = fa.latestPublishedTs > 0;
    const bHas = fb.latestPublishedTs > 0;
    if (aHas !== bHas) return aHas ? -1 : 1;
    if (fa.latestPublishedTs !== fb.latestPublishedTs) return fb.latestPublishedTs - fa.latestPublishedTs;
    if (fa.hasNew !== fb.hasNew) return fa.hasNew ? -1 : 1;
    if (fa.hasRb !== fb.hasRb) return fa.hasRb ? -1 : 1;
    return 0;
  });
}

// ── Left panel: multi-level directory tree ──
// Builds from paths.chain: 产品 → 子分类 → 版本 → 包类型
// ── Data Render (single-column product cards with multi-level tree) ──
// Fallback: render flat version list when no paths data
// ── 快照状态 badge helper ──
// active → 🆕最新包(绿)
// withdrawn → ↩已撤回(红 + 删除线)
// superseded → ⊘已替代(灰)
function statusBadge(status) {
  if (status === 'withdrawn')  return '<span class="badge badge-err" data-css="font-size:10px">↩已撤回</span>';
  if (status === 'superseded') return '<span class="badge badge-grey" data-css="font-size:10px">⊘已替代</span>';
  return '<span class="badge badge-ok" data-css="font-size:10px">🆕最新包</span>';
}
// 是否是已撤回/已替代的旧包(用于视觉弱化)
function isOldSnapshot(s) {
  return s && (s.status === 'withdrawn' || s.status === 'superseded');
}

// Detect if a subscription chain is still present in the vendor package_type.paths.
// Used to flag rules whose chains went stale after a re-discovery trimmed
// redundant ser_c_b_tit segments (commit 8c41709) or vendor-side structure changes.
// Returns true when the chain no longer matches any current path:
//   - leaf: requires exact chain match in vendor paths
//   - subtree: requires at least one path whose chain starts with this chain
// Also returns true when the product name in chain[0] is no longer registered.
function isChainStale(chain, match) {
  if (!Array.isArray(chain) || chain.length === 0) return false;
  const cacheKey = JSON.stringify(chain) + '|' + (match || 'leaf');
  if (cacheKey in _chainStaleCache) return _chainStaleCache[cacheKey];
  const sid = _nameToSid[chain[0]];
  if (!sid) return _chainStaleCache[cacheKey] = true;
  const paths = (_dataAll[sid] && _dataAll[sid].package_type && _dataAll[sid].package_type.paths) || [];
  if (paths.length === 0) return _chainStaleCache[cacheKey] = true;
  let stale;
  if (match === 'subtree') {
    const prefixLen = chain.length;
    stale = !paths.some(p => Array.isArray(p.chain) && p.chain.length >= prefixLen
      && p.chain.slice(0, prefixLen).every((seg, i) => seg === chain[i]));
  } else {
    const exact = JSON.stringify(chain);
    stale = !paths.some(p => Array.isArray(p.chain) && JSON.stringify(p.chain) === exact);
  }
  return _chainStaleCache[cacheKey] = stale;
}

// Diagnose why a subscription chain is stale; returns user-facing explanation
// suitable for a tooltip. Three failure modes:
//   1. product not found (renamed/deleted)
//   2. product has no discovered paths yet
//   3. chain text no longer matches any vendor path (vendor changed, or our
//      8c41709 trim removed redundant ser_c_b_tit middle layers)
function _diagnoseChainStale(chain, match) {
  if (!Array.isArray(chain) || chain.length === 0) {
    return '订阅路径为空';
  }
  const sid = _nameToSid[chain[0]];
  if (!sid) {
    return `订阅的产品「${chain[0]}」已不存在或已禁用，无法匹配任何快照。请编辑订阅重新选择产品。`;
  }
  const paths = (_dataAll[sid] && _dataAll[sid].package_type && _dataAll[sid].package_type.paths) || [];
  if (paths.length === 0) {
    return `产品「${chain[0]}」当前还没有可采集的包路径（可能尚未完成包类型发现），无法匹配订阅。请先完成发现后再编辑订阅。`;
  }
  const modeLabel = match === 'subtree' ? '子树（前缀）' : '精确（整条）';
  const chainStr = chain.join(' › ');
  // Show sibling paths under the same section (chain[1]) so user can pick a
  // replacement without opening the discovery tree.
  const sameSection = paths.filter(p => Array.isArray(p.chain)
    && p.chain[0] === chain[0] && p.chain[1] === chain[1]);
  if (sameSection.length === 0) {
    return `订阅路径已失效（${modeLabel}匹配）\n\n订阅路径：${chainStr}\n\n产品「${chain[0]}」下当前没有任何以「${chain[1] || ''}」开头的路径。可能原因：产品分组结构已变更（如新增/删除/重命名，或系统升级砍掉了冗余的 ser_c_b_tit 中间层）。\n\n请编辑订阅重新选择路径。`;
  }
  const sample = sameSection.slice(0, 3).map(p => '• ' + p.chain.join(' › ')).join('\n');
  const more = sameSection.length > 3 ? `\n… 共 ${sameSection.length} 条` : '';
  return `订阅路径已失效（${modeLabel}匹配）\n\n订阅路径：${chainStr}\n\n产品「${chain[0]}」的「${chain[1]}」分组下当前存在的路径为：\n${sample}${more}\n\n请编辑订阅,从上述路径中重新选择。`;
}

// Red ⚠ badge shown on stale-chain pills; tooltip explains the cause.
function chainWarnBadge(chain, match) {
  const tip = _diagnoseChainStale(chain, match);
  return `<span class="chain-stale-warn" title="${escHtml(tip)}" data-css="color:var(--danger);font-weight:700;cursor:help;font-size:13px;flex-shrink:0;margin-left:2px">⚠️</span>`;
}

function buildFlatVerTreeHtml(snapshots) {
  const MAP = { sys: '系统', rule: '规则', nti: '威胁情报', av: '病毒库', apprule: '应用规则', url: 'URL库', wcs: '恶意站点', judge: '研判', geo: '地理库', interface: '接口', special: '特殊', other: '其他', merge: '合并', client: '客户端', av_stream: '流式病毒库', test: '测试', engine: '引擎' };
  if (!snapshots || snapshots.length === 0) return '<div data-css="color:var(--muted);font-size:12px;padding:8px">暂无数据</div>';
  const byVer = {};
  // 2026-08-08: 用 _dataSelectedChain 派生 group key,不读 snap.version_branch。
  // 路径不空(走 buildFlatVerTreeHtml 通常意味着 paths 空)时 — group key 只能是
  // snap 行字段,但推送按钮显示用 _dataSelectedChain 的 chain[-2](fallback)。
  snapshots.forEach(s => {
    // 优先用 _dataSelectedChain[-2] (用户点的 chain 的版本层),fallback 到 s.version_branch
    const v = (_dataSelectedChain && _dataSelectedChain.length >= 2)
      ? _dataSelectedChain[_dataSelectedChain.length - 2]
      : (s.version_branch || '?');
    if (!byVer[v]) byVer[v] = [];
    byVer[v].push(s);
  });
  const vers = Object.keys(byVer).sort((a, b) => {
    const na = parseFloat(a), nb = parseFloat(b);
    if (!isNaN(na) && !isNaN(nb)) return nb - na;
    return b.localeCompare(a, 'zh');
  });

  // 用于推送按钮 — 从 _dataSelectedChain 派生
  const pushVer = (_dataSelectedChain && _dataSelectedChain.length >= 2)
    ? _dataSelectedChain[_dataSelectedChain.length - 2] : '';
  const pushPkg = (_dataSelectedChain && _dataSelectedChain.length > 0)
    ? _dataSelectedChain[_dataSelectedChain.length - 1] : '';
  let html = '';
  vers.forEach(ver => {
    const snaps = byVer[ver];
    const blockId = 'fv_' + ver.replace(/\W/g, '_');
    const isRb = snaps.some(s => s.status === 'withdrawn');
    const newCount = snaps.filter(s => s.status === 'active' && !s.last_delivered_at).length;
    const badge = isRb ? ' <span data-css="color:var(--danger);font-size:10px">↩</span>' : '';
    const freshSnap = snaps.reduce((latest, s) => {
      if (!s.published_at) return latest;
      const t = new Date(s.published_at).getTime();
      const lt = latest ? new Date(latest).getTime() : 0;
      return t > lt ? s.published_at : latest;
    }, null);
    const now = Date.now();
    let freshBadge = '';
    if (freshSnap) {
      const days = (now - new Date(freshSnap).getTime()) / 86400000;
      if (days <= 30) freshBadge = ' <span data-css="color:var(--success);font-size:10px">活跃</span>';
      else if (days <= 365) freshBadge = ' <span data-css="color:var(--warn);font-size:10px">低频</span>';
      else freshBadge = ' <span data-css="color:var(--gray-disabled);font-size:10px">沉寂</span>';
    }
    const verLabel = escHtml(ver) + badge + freshBadge;
    html += `<div class="ver-bar" data-on-click="verBarToggle" data-args='${_dataArgs('$el', blockId)}' data-css="margin-bottom:4px">
      <span class="tree-arrow" data-css="margin-right:4px">&#9658;</span>
      <span data-css="font-weight:600;font-size:13px">${verLabel}</span>
      <span data-css="margin-left:auto;font-size:11px;color:var(--muted)">${snaps.length} 个包</span>
    </div>`;
    // Build snapshot table
    let tbl = `<div id="${blockId}" data-css="display:none;padding:4px 0 8px 28px">`;
    snaps.forEach(s => {
      const sz = s.file_size > 1048576 ? (s.file_size / 1048576).toFixed(1) + 'M' : (s.file_size / 1024).toFixed(0) + 'K';
      const did = 'dd_v_' + s.id;
      const isRb = s.status === 'withdrawn';
      const isOld = isOldSnapshot(s);
      const pkgTypeBadge = `<span class="badge" data-css="background:var(--snap-pkg-type-badge);color:var(--snap-pkg-type-color);font-size:10px">${MAP[s.package_type] || s.package_type || ''}</span>`;
      const statusBadges = statusBadge(s.status) + (s.status === 'active' && !s.last_delivered_at ? ' <span class="badge badge-blue" data-css="font-size:10px">未推送</span>' : '');
      tbl += `<div data-css="display:flex;align-items:center;gap:8px;padding:4px 8px;cursor:pointer;font-size:12px;border-radius:4px;${isRb ? 'color:var(--muted);text-decoration:line-through' : isOld ? 'color:var(--muted)' : ''}" data-on-click="_toggleByIdClass" data-args='${_dataArgs(did, 'hidden')}'>
        ${pkgTypeBadge}
        ${statusBadges}
        <span data-css="color:var(--primary);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(s.file_name || '')}">${escHtml(s.file_name || '')}</span>
        <span data-css="color:var(--muted);font-size:11px">${fmtTZ(s.last_seen_at, true)}</span>
        <span data-css="font-size:11px;color:var(--muted)">${sz}</span>
        <span class="badge badge-${s.urgency === 'critical' ? 'err' : s.urgency === 'high' ? 'warn' : 'ok'}" data-css="font-size:10px">${escHtml(s.urgency || '')}</span>
        <button class="btn btn-xs btn-soft-success" data-on-click="showPushModal" data-stop data-args='${_dataArgs(s.id, s.product_name, pushVer, pushPkg)}'>推送</button>
      </div>
      <div id="${did}" class="hidden" data-css="padding:4px 8px 4px 28px">
        <div data-css="display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:12px;background:var(--success-light);padding:8px;border-radius:4px">
          <b>文件:</b><span class="mono">${escHtml(s.file_name || '')}</span>
          <b>版本:</b><span>${escHtml(s.package_version || '')}</span>
          <b>MD5:</b><span class="mono">${escHtml(s.md5_hash || '')}</span>
          <b>大小:</b><span>${sz}</span>
          ${s.min_sys_version ? `<b>依赖:</b><span>≥ ${escHtml(s.min_sys_version)}</span>` : ''}
          ${s.published_at ? `<b>发布:</b><span>${fmtTZ(s.published_at, true)}</span>` : ''}
          ${s.source_url ? `<b>详情:</b><span><a href="https://update.nsfocus.com${escHtml(s.source_url)}" target="_blank">绿盟更新页</a></span>` : ''}
          ${s.download_id ? `<b>下载:</b><span><a href="https://update.nsfocus.com/update/downloads/id/${escHtml(s.download_id)}" target="_blank">downloads/id/${escHtml(s.download_id)}</a></span>` : ''}
        </div>${s.description_raw ? `<div class="desc-box mt8">${escHtml(s.description_raw.substring(0, 1200))}</div>` : ''}
      </div>`;
    });
    tbl += '</div>';
    html += tbl;
  });
  return html;
}

function verBarToggle(el, blockId) {
  const block = document.getElementById(blockId);
  if (!block) return;
  const hidden = block.style.display === 'none';
  block.style.display = hidden ? 'block' : 'none';
  const arrow = el.querySelector('.tree-arrow');
  if (arrow) arrow.innerHTML = hidden ? '&#9660;' : '&#9658;';
}

function dataRenderLeft() {
  const filt = dataFilteredSources();
  const sorted = dataSortedSources(filt);
  // Render ALL products (no pagination) so scrollIntoView always finds nodes across pages
  const pageSlice = sorted;

  // Update filter chip active states
  document.querySelectorAll('.data-chip').forEach(c => {
    const key = c.getAttribute('data-filter-key');
    if (key != null) c.classList.toggle('data-chip-active', !!_dataFilters[key]);
  });

  if (pageSlice.length === 0) {
    document.getElementById('dataLeft').innerHTML = '<div class="empty" data-css="padding:24px;text-align:center;color:var(--muted)">未找到匹配条件的产品</div>';
    return;
  }

  // Build all product cards
  let html = '';
  pageSlice.forEach(([sid, src]) => {
    // Build snapshots_map (URL -> snapshot[]) for buildDataTreeHtml
    if (!src.snapshots_map) {
      src.snapshots_map = {};
      const BASE_URL = 'https://update.nsfocus.com';
      (src.snapshots || []).forEach(s => {
        if (s.source_url) {
          // Strip BASE_URL origin so key matches tree node __pathId (relative path)
          const urlKey = s.source_url.startsWith(BASE_URL)
            ? s.source_url.slice(BASE_URL.length)
            : s.source_url;
          if (!src.snapshots_map[urlKey]) src.snapshots_map[urlKey] = [];
          src.snapshots_map[urlKey].push(s);
        }
      });
    }
    const pt = src.package_type || {};
    const paths = pt.paths || [];
    let treeHtml;
    if (!paths.length) {
      // Fallback: no paths → render flat version list (old behavior)
      treeHtml = buildFlatVerTreeHtml(src.snapshots || []);
    } else {
      treeHtml = buildDataTreeHtml(pt, src.snapshots_map, typeCN, src.ver_type_map || {}, src.name || '', sid);
    }
    const { hasRb, freshness } = dataSourceFlags(src);
    const freshBadge = freshness === 'new' ? ' <span class="badge badge-ok" data-css="font-size:10px">活跃</span>' : (freshness === 'lowfreq' ? ' <span class="badge badge-warn" data-css="font-size:10px">低频</span>' : (freshness === 'idle' ? ' <span class="badge badge-grey" data-css="font-size:10px">沉寂</span>' : ''));
    const cardId = 'dpc_' + sid;
    const treeBodyId = cardId + '_body';
    html += `<div class="card" id="${cardId}" data-css="margin-bottom:10px">
      <div class="card-hd" data-css="cursor:pointer" data-action="cardToggle" data-body-id="${treeBodyId}">
        <span>${escHtml(src.display_name || src.name || sid)}</span>
        ${freshBadge && `<span data-css="margin-left:auto">${freshBadge}</span>`}
        <button class="hover-primary" data-css="margin-left:8px;width:24px;height:24px;border-radius:50%;background:var(--primary-light);color:var(--primary);border:1px solid var(--border);font-size:12px;font-weight:600;line-height:1;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;transition:background .15s,color .15s" data-on-click="dataCollectProduct" data-stop data-args='${_dataArgs(sid, src.name || sid)}' title="立即采集此产品">采</button>
      </div>
      <div class="card-bd pt0" id="${treeBodyId}" data-action="treeNode" data-card-hd="${cardId}_hd" data-css="display:none;padding-top:0">
        <div data-css="padding:8px 0 4px" id="${cardId}_tree">${treeHtml}</div>
      </div>
    </div>`;
  });

  document.getElementById('dataLeft').innerHTML = html;
}

// ── Tree toggle (inline onclick) ──
// Two callers:
//   1. User clicks arrow: treeToggleById(childId, thisArrowSpan)
//   2. Programmatic: treeToggleById(childId) — no arrowEl, must look it up
//
// Note: data-target="childId" is set on the <span class="tree-children"> itself
// (NOT on the arrow span), so a querySelector fallback would return the
// children div — not a tree-node ancestor. We resolve the tree-node via the
// arrow when available, otherwise via children.previousElementSibling.
function treeToggleById(childrenId, arrowEl) {
  if (!childrenId) return;
  let treeNode = null;
  if (arrowEl && arrowEl.closest) {
    treeNode = arrowEl.closest('.tree-node');
  }
  if (!treeNode) {
    const children = document.getElementById(childrenId);
    if (children && children.previousElementSibling && children.previousElementSibling.classList.contains('tree-node')) {
      treeNode = children.previousElementSibling;
    }
  }
  if (!treeNode) return;
  const children = treeNode.nextElementSibling;
  if (!children || !children.classList.contains('tree-children')) return;
  // Find the arrow inside this tree-node to update its glyph
  const arrow = treeNode.querySelector('span[data-toggle-arrow], .tree-arrow');
  const isHidden = children.style.display === 'none' || children.style.display === '';
  children.style.display = isHidden ? 'block' : 'none';
  if (arrow) arrow.innerHTML = isHidden ? '&#9660;' : '&#9658;';
  if (children._wasHidden === undefined) children._wasHidden = isHidden;
}

// Card product toggle (body id string, not element reference)
function productCardToggle(bodyId) {
  if (!bodyId) return;
  var target = document.getElementById(bodyId);
  if (!target) return;
  var isOpen = target.style.display !== 'none';
  target.style.display = isOpen ? 'none' : 'block';
  var card = target.closest('.card');
  if (!card) return;
  var hd = card.querySelector('.card-hd');
  if (!hd) return;
  var arrow = hd.querySelector('.tree-arrow');
  if (arrow) arrow.innerHTML = isOpen ? '&#9658;' : '&#9660;';
}

let _lastSelectedNode = null; // track last clicked tree node for toggle-off

function dataTreeNodeClick(sid, chain, url, isLeaf, snapIds, node, verHint, pkgHint, pathId) {
  // Toggle off 同一 node: 直接 return, 不做任何操作。
  //
  // 2026-07-29: 用户原始反馈:
  //   "点同节点应该只是把节点下方的目录收起来, 不是整个产品一起收起"
  //   "链式状态退到上一层反模式让我没法识别当前选中的是哪一层"
  //
  // 改语义:
  //   - 折叠 children = 点击节点的箭头 (独立 span, 走 treeToggleById, 已存在)
  //   - 改变选中 = 点其他节点
  //   - 完全清空 = 点重置筛选按钮 (dataClearFilter, 在右 header)
  //
  // 点击同一节点 = 选中保持不变 (避免误触清空)
  if (_lastSelectedNode === node && _dataSelectedSid === sid) {
    // No-op: 保持当前选中, 不清空、不退 chain
    return;
  }

  // Set selection
  _dataSelectedSid = sid;
  _dataSelectedChain = chain;
  _dataSelectedUrl = url || null;
  _dataSelectedNode = node; // 记下点击的 tree node,dataBuildFeed 从它收集 descendants URL
  // Backend path_id takes precedence over ver/pkg hints (the latter are
  // guessed by propagateHints and break when several chains share one URL).
  // For leaf nodes path_id is exact; for intermediate nodes fall back to
  // ver/pkg if no path_id was provided.
  if (pathId) {
    _dataSelectedPathId = pathId;
    _dataSelectedVerHint = null;
    _dataSelectedPkgHint = null;
  } else {
    _dataSelectedPathId = null;
    _dataSelectedVerHint = verHint || null;
    _dataSelectedPkgHint = pkgHint || null;
  }
  _lastSelectedNode = node;
  _dataFeedPage = 1;

  // Update visual selection highlight
  document.querySelectorAll('.tree-node-selected').forEach(el => el.classList.remove('tree-node-selected'));
  node.classList.add('tree-node-selected');

  // Update card selected state
  document.querySelectorAll('.card-selected').forEach(el => el.classList.remove('card-selected'));
  var selectedCard = node.closest('.card');
  if (selectedCard) selectedCard.classList.add('card-selected');

  // Expand parent tree children (so selected node is visible)
  let parent = node.parentElement;
  while (parent && !parent.classList.contains('card-bd')) {
    if (parent.classList.contains('tree-children') && parent.style.display === 'none') {
      parent.style.display = '';
      const arrow = parent.previousElementSibling?.querySelector('.tree-arrow');
      if (arrow) arrow.innerHTML = '&#9660;';
    }
    parent = parent.parentElement;
  }

  // Build _chainToUrlMap for dataBuildFeed filtering
  _chainToUrlMap = {};
  if (chain.length > 0) {
    const src = _dataAll[sid];
    if (src && src.package_type && src.package_type.paths) {
      src.package_type.paths.forEach(p => {
        if (p.url) {
          const key = sid + '|' + (p.chain || []).join('/');
          _chainToUrlMap[key] = p.url;
        }
      });
    }
  }

  // Re-render right panel immediately with current scheduler cache
  dataRenderRight();
  // Then refresh scheduler cache in background and re-render when API returns
  api('/settings/scheduler').then(r => {
    if (r.data?.scheduler) {
      _lastScheduler = r.data.scheduler;
      dataRenderRight();
    }
  });

  // If leaf node with snaps, auto-expand the LATEST snap row in right panel
  // (snaps already sorted by published_at DESC, so [0] is the most recent).
  // 2026-08-08: previously expanded every snap row in the leaf (snapIds.forEach),
  // which ended up toggling each in sequence and landing on the last one —
  // which is the OLDEST row, not the latest. Defaulting to latest.
  if (isLeaf && snapIds.length > 0) {
    setTimeout(() => {
      // snapIds[0] is the latest (snaps.map(s => s.id) preserves published_at DESC order)
      const firstId = snapIds[0];
      const row = document.getElementById('fr_' + String(firstId));
      if (row) dataRightToggleRow('fr_' + String(firstId));
    }, 30);
  }
}

function dataGoPage(p) {
  const filt = dataFilteredSources();
  const totalPages = Math.max(1, Math.ceil(filt.length / PAGE_SIZE));
  _dataPage = Math.min(Math.max(1, p), totalPages);
  dataRenderLeft();
  dataRenderRight();
}

// ── Right panel: latest feed ──
const FEED_PAGE_SIZE = 50;
let _dataFeedPage = 1;
let _feedPkgLimit = 3; // per (version+pkgtype) history limit, set from config
let _chainToUrlMap = {};

function dataBuildFeed() {
  const MAP = { sys: '系统', rule: '规则', nti: '威胁情报', av: '病毒库', apprule: '应用规则', url: 'URL库', wcs: '恶意站点', judge: '研判', geo: '地理库', interface: '接口', special: '特殊', other: '其他', merge: '合并', client: '客户端', av_stream: '流式病毒库', test: '测试', engine: '引擎' };

  let sources = dataFilteredSources();
  if (_dataSelectedSid) {
    const src = _dataAll[_dataSelectedSid];
    sources = src ? [[_dataSelectedSid, src]] : [];
  }

  // Determine if this is a leaf or intermediate node click with hints/path_id for filtering.
  // Path_id (preferred) takes precedence over ver/pkg hints. For leaf nodes
  // (isLeaf=true → _dataSelectedUrl is set), isHintClick MUST be true so the
  // exact path_id filter runs; otherwise all snaps sharing the leaf URL leak
  // through and wrong-chain rows appear in the feed.
  const isHintClick = (
    !!_dataSelectedPathId || _dataSelectedVerHint || _dataSelectedPkgHint
  );

  const rows = [];
  sources.forEach(([sid, src]) => {
    const snaps = src.snapshots || [];
    const byKey = {};
    // Use cached urlChainMap from _srcUrlChainMap (built once in dataPage)
    const urlChainMap = _srcUrlChainMap[sid] || {};

    snaps.forEach(s => {
      // 基础状态过滤(has_new/has_rb/urgency)
      if (_dataFilters.has_new && (s.status !== 'active' || s.last_delivered_at)) return;
      if (_dataFilters.has_rb && s.status !== 'withdrawn') return;
      if (_dataFilters.urgency && (s.urgency !== _dataFilters.urgency || s.status !== 'active')) return;

      // 2026-08-08: snap 过滤:用 _dataSelectedChain 找 src.package_type.paths[] 子树
            // 的 url 集合(链前缀匹配),snap.source_url 必须在该集合内。
            // - leaf click: chain 长度 = path chain 全长 → 严格匹配 → 1 个 path
            // - 中间节点 click: chain 长度 < path chain 全长 → 前缀匹配 → 该节点
            //   子树所有 leaf 的 path
            // 数据来源:content_sources.package_type.paths[] (不读 snap 行)。
            if (_dataSelectedChain && _dataSelectedChain.length > 0) {
                const allPaths = (src.package_type && src.package_type.paths) || [];
                const selChain = _dataSelectedChain;
                const inSubtreeUrls = new Set();
                for (const p of allPaths) {
                  if (!Array.isArray(p.chain) || p.chain.length < selChain.length) continue;
                  let isPrefix = true;
                  for (let i = 0; i < selChain.length; i++) {
                    if (p.chain[i] !== selChain[i]) { isPrefix = false; break; }
                  }
                  if (isPrefix && p.url) {
                    const full = p.url.startsWith('http') ? p.url :
                      ('https://update.nsfocus.com' + (p.url.startsWith('/') ? p.url : '/' + p.url));
                    inSubtreeUrls.add(full.replace(/\/+$/, ''));
                  }
                }
                if (inSubtreeUrls.size === 0) return;
                // 把 snap.source_url (相对路径) 展成 full URL
                const srcUrl = s.source_url || '';
                const snapFullUrl = srcUrl.startsWith('http')
                  ? srcUrl
                  : 'https://update.nsfocus.com' + (srcUrl.startsWith('/') ? srcUrl : '/' + srcUrl);
                const snapUrl = snapFullUrl.replace(/\/+$/, '');
                if (!inSubtreeUrls.has(snapUrl)) return;
            }
            // 2026-08-21: search 激活时,每条 hit 按其 chains 列表展开成 N 行(同物理文件 +
            // 多 chain 共享 URL 时,N = chains.length)。非 search 模式走原 path_id 分组。
            // - _dataSearchHitIds 是后端返回的 snap id 集合
            // - _dataSearchSnapChains 是 Map<snap_id, [chain, chain, ...]>,由
            //   dataSearch 的 .then 回调填充(从 /api/latest/search response 读
            //   hit.chains 字段)
            // - 有 _dataSearchHitIds 但 _dataSearchSnapChains 为空 (说明后端响应
            //   还在路上),保持单行模式不展开 — 避免异步 race
            if (_dataQuery && _dataSearchHitIds && _dataSearchSnapChains) {
              if (!_dataSearchHitIds.has(s.id)) return;  // 搜索未命中 → 过滤
              const snapChains = _dataSearchSnapChains.get(s.id);
              if (snapChains && snapChains.length > 0) {
                // 每条 chain 一行:用 snap_id + chain 索引做唯一 key(避免 path_id 重复)
                for (let i = 0; i < snapChains.length; i++) {
                  const k = s.path_id + '||chain||' + i;
                  if (!byKey[k]) byKey[k] = [];
                  // 用 s 的浅拷贝附带 _chain 字段,后续 Object.values 渲染用
                  byKey[k].push(Object.assign({}, s, { _chain: snapChains[i] }));
                }
                return;  // 已展开,跳过下面 path_id 分支
              }
            } else if (_dataQuery && _dataSearchHitIds) {
              // 后端响应还在路上:_dataSearchHitIds 已设但 _dataSearchSnapChains 还没
              // 此时仍按 path_id 分组(单行),不展开 — 异步数据一致
              if (!_dataSearchHitIds.has(s.id)) return;
            }
            // 左侧选中一个 source card (无 _dataSelectedChain):显示该 source 全量

            // Group by path_id (= MD5(url + chain)) instead of (version_branch, package_type).
            const k = s.path_id;
            if (!byKey[k]) byKey[k] = [];
            byKey[k].push(s);
        });
    // Sort + limit AFTER all snaps are collected (not per push)
    Object.entries(byKey).forEach(([k, snapList]) => {
      snapList.sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''));
      if (!isHintClick) {
        byKey[k] = snapList.slice(0, _feedPkgLimit);
      }
    });
    Object.values(byKey).forEach(snapList => snapList.forEach(s => {
      // Attach chain for chain display in row: prefer the path whose chain
      // 包含 _dataSelectedChain (用户点的 chain 是它前缀)。即 r._chain 反映用户
      // 选中的 chain 上下文。
      // - leaf click: _dataSelectedChain 是 leaf chain → 找精确 chain
      // - 中间节点 click: _dataSelectedChain 是中间 chain → 找 chain 前缀匹配
      //   (length >= _dataSelectedChain.length + first N match)
      // 这样多 chain 共享 URL 时,row 类型按用户选中的 chain 上下文显示。
      let _chain = null;
      const allPaths0 = (_dataAll[sid] && _dataAll[sid].package_type && _dataAll[sid].package_type.paths) || [];
      if (_dataSelectedChain && _dataSelectedChain.length > 0) {
        // 优先精确 chain length 匹配 (leaf)
        let matched = allPaths0.find(p =>
          Array.isArray(p.chain) &&
          p.chain.length === _dataSelectedChain.length &&
          p.chain.every((seg, i) => seg === _dataSelectedChain[i])
        );
        // fallback: chain prefix 匹配 (中间节点)
        if (!matched) {
          matched = allPaths0.find(p =>
            Array.isArray(p.chain) &&
            p.chain.length >= _dataSelectedChain.length &&
            p.chain.slice(0, _dataSelectedChain.length).every((seg, i) => seg === _dataSelectedChain[i])
          );
        }
        if (matched && matched.chain) _chain = matched.chain;
      }
      // fallback: path_id 反查 (URL-only, 多 chain 共享时取第一条 — 不准但兜底)
      if (!_chain && s.path_id) {
        const matched = allPaths0.find(p => p.path_id === s.path_id);
        if (matched && matched.chain) _chain = matched.chain;
      }
      if (!_chain && s.source_url) {
        const norm = ('/' + s.source_url).replace(/\/+$/, '');
        _chain = urlChainMap[norm] || null;
      }
      rows.push({ ...s, _sid: sid, _srcName: src.display_name || src.name, _chain });
    }));
  });

  rows.sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''));

  let filtered = rows;
  if (_dataQuery && !_dataSelectedPathId) {
    const q = _dataQuery.toLowerCase();
    const localMatch = r => {
      switch (_dataSearchField) {
        case 'product_name':   return (r._srcName || '').toLowerCase().includes(q);
        case 'version_branch': return (r.package_version || '').toLowerCase().includes(q);
        case 'file_name':      return (r.file_name || '').toLowerCase().includes(q);
        case 'package_type':   return (r.package_type || '').toLowerCase().includes(q);
        case 'description':
        default:
          // description: 后端走 /latest/search, 这里用 file_name 做 fallback (避免空显示)
          return (r.file_name || '').toLowerCase().includes(q);
      }
    };
    if (_dataSearchField === 'description' && _dataSearchHitIds) {
      // 后端已返回: 命中集合优先
      filtered = filtered.filter(r => _dataSearchHitIds.has(r.id));
    } else {
      // 其他字段/后端未回: 本地单字段匹配
      filtered = filtered.filter(localMatch);
    }
  }
  if (_dataFilters.urgency) {
    filtered = filtered.filter(r => r.urgency === _dataFilters.urgency && r.status === 'active');
  }
  return { rows: filtered, MAP };
}

function dataRenderRight(sch, p, info) {
  if (!sch) sch = {};
  if (!p) p = {};
  if (!info) info = {};
  // sch may be undefined when called from dataSearch/dataToggleFilter without args — fallback to cached _lastScheduler
  const _sch = sch || _lastScheduler || {};
  const mode_label = { quick: '⚡Quick', full: '🔍Full', '': '' };
  const {
    lastText = _sch.last_run ? fmtTZ(_sch.last_run, true) : '—',
    nextText = _sch.last_run ? (() => {
      const d = new Date(_sch.last_run + 'Z');
      d.setUTCHours(d.getUTCHours() + (_sch.interval_hours || 24));
      return fmtTZ(d.toISOString().substring(0, 19), true);
    })() : '—',
    isRunning = !!_sch.is_running,
    curMode = mode_label[_sch.current_mode] || '',
    progressHtml = ''
  } = info;
  const { rows, MAP } = dataBuildFeed();
  const totalPages = Math.max(1, Math.ceil(rows.length / FEED_PAGE_SIZE));
  if (_dataFeedPage > totalPages) _dataFeedPage = totalPages;
  const page = _dataFeedPage;
  const start = (page - 1) * FEED_PAGE_SIZE;
  const pageSlice = rows.slice(start, start + FEED_PAGE_SIZE);

  const src = _dataSelectedSid ? (_dataAll[_dataSelectedSid] || {}) : null;
  const srcName = src ? (src.display_name || src.name || '') : '';
  const hasFilter = !!_dataSelectedSid;
  let hdPathHtml = '';
  if (srcName && _dataSelectedChain && _dataSelectedChain.length > 0) {
    const chainParts = _dataSelectedChain.slice(1);
    chainParts.forEach((segment, i) => {
      const segChain = _dataSelectedChain.slice(0, i + 2);
      const mapKey = _dataSelectedSid + '|' + segChain.join('/');
      const segUrl = _chainToUrlMap[mapKey] || '';
      if (segUrl) {
        hdPathHtml += `<a href="https://update.nsfocus.com${escHtml(segUrl)}" target="_blank" class="data-hd-path-seg">${esc(segment)}</a>`;
      } else {
        hdPathHtml += `<span class="data-hd-path-seg">${esc(segment)}</span>`;
      }
      if (i < chainParts.length - 1) hdPathHtml += ' <span data-css="color:var(--muted)">→</span> ';
    });
  }
  const hdLabel = srcName ? `${esc(srcName)}${hdPathHtml ? ' → ' + hdPathHtml : ''} 的最新包` : '全部产品最新包';
  const resetBtn = hasFilter ? `<button class="btn btn-xs" data-css="background:var(--warn-light);color:#8a7000;border:1px solid #e6c94c;margin-left:8px" data-on-click="dataClearFilter">重置筛选</button>` : '';
  const lastMode = mode_label[sch.last_mode] || '';
  const nextMode = mode_label[sch.next_mode] || '';
  // Build header: left=label, center=last/next timeline, right=progress+btn
  const hdrRight = `<div class="hdr-right" data-css="display:flex;align-items:center;gap:10px;flex-shrink:0;flex-wrap:wrap;font-size:12px">
    <span data-css="color:var(--muted)">上次</span><span>${lastText}${lastMode ? ' [' + lastMode + ']' : ''}</span>
    <span data-css="color:var(--border)">|</span>
    <span data-css="color:var(--muted)">下次</span><span>${nextText}${nextMode ? ' [' + nextMode + ']' : ''}</span>
    ${progressHtml}
    <button class="btn btn-xs btn-soft-primary" data-on-click="triggerCollect">🔄 手动采集</button>${resetBtn}
  </div>`;

  if (rows.length === 0) {
    document.getElementById('dataRight').innerHTML = `<div class="data-right-empty">暂无数据</div>`;
    document.getElementById('dataPager').innerHTML = '';
    return;
  }

  const colStyle = 'data-css="font-size:12px;padding:0 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"';
  let html = `<div class="feed-list">
<div class="feed-hd">
  <div class="feed-cell feed-arrow-hd" data-css="width:28px;flex-shrink:0"></div>
  <div class="feed-cell" data-css="width:180px;flex-shrink:0;font-size:11px;color:var(--muted);font-weight:600">类型</div>
  <div class="feed-cell" data-css="width:84px;flex-shrink:0;font-size:11px;color:var(--muted);font-weight:600" title="紧急程度根据描述关键词判定：🔴 critical=高危/严重/critical/远程代码执行/紧急，⚠️ high=中危/high/漏洞/绕过，ℹ️ normal=其他">紧急度</div>
  <div class="feed-cell" data-css="width:72px;flex-shrink:0;font-size:11px;color:var(--muted);font-weight:600" title="基于发布时间的活跃状态：30天内=活跃，1年内=低频，更久=沉寂">状态</div>
  <div class="feed-cell" data-css="width:160px;flex-shrink:0;font-size:11px;color:var(--muted);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">产品名称</div>
  <div class="feed-cell" data-css="flex:1;min-width:160px;font-size:11px;color:var(--muted);font-weight:600;padding-left:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">版本</div>
  <div class="feed-cell" data-css="flex:1;min-width:200px;font-size:11px;color:var(--muted);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">文件</div>
  <div class="feed-cell" data-css="width:116px;flex-shrink:0;font-size:11px;color:var(--muted);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">发布时间</div>
  <div class="feed-cell" data-css="width:130px;flex-shrink:0;font-size:11px;color:var(--muted);font-weight:600">操作</div>
</div>`;

  pageSlice.forEach(r => {
    // 2026-08-08: 类型/版本字段用 _dataSelectedChain (用户点的 chain) 派生。
    // 完全不读 snap.version_branch / snap.package_type。
    // - 用户点 leaf (chain 是 leaf chain):_dataSelectedChain 反映该 chain
    // - 用户点中间节点:_dataSelectedChain 是该节点 chain,row 列表混合
    //   多个 leaf 的 snap(URL 去重共享),都按中间节点 chain 显示(数据真相)
    // 注:URL 去重下,同 URL 的 snap (8703 海光 / 5419 WAF) 都按 _dataSelectedChain
    // 显示。8703 的 ver/pkg 字段虽然"海光",但只用于 chain 归属识别(不读),
    // 显示字段一律按 _dataSelectedChain 派生的 chain 数组 — 用户的核心需求。
    const chainPkg = (r._chain && r._chain.length > 0) ? r._chain[r._chain.length - 1] : '';
    const chainVer = (r._chain && r._chain.length >= 2) ? r._chain[r._chain.length - 2] : '';
    const typeLabel = MAP[chainPkg] || chainPkg || r.package_type || '';
    const isRb = r.status === 'withdrawn';
    const rowClass = isRb ? 'rollback' : '';
    let urgencyBadge = '';
    if (r.urgency === 'critical') urgencyBadge = '<span class="badge badge-err" data-css="font-size:10px">🔴 critical</span>';
    else if (r.urgency === 'high') urgencyBadge = '<span class="badge badge-warn" data-css="font-size:10px">⚠️ high</span>';
    else if (r.urgency === 'normal') urgencyBadge = '<span class="badge badge-ok" data-css="font-size:10px">ℹ️ normal</span>';
    const rid = 'fr_' + r.id;
    html += `<div class="feed-row ${rowClass}" id="${rid}" data-sid="${escHtml(r._sid)}" data-id="${r.id}" data-snap-id="${r.id}">
  <div class="feed-cell feed-arrow" data-css="width:28px;flex-shrink:0" data-on-click="dataRightToggleRow" data-args='${_dataArgs(rid)}'>▶</div>
  <div class="feed-cell" data-css="width:180px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" ${colStyle}><span class="badge" data-css="background:var(--snap-pkg-type-badge);color:var(--snap-pkg-type-color);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;vertical-align:middle" title="${escHtml(typeLabel)}">${typeLabel}</span></div>
  <div class="feed-cell" data-css="width:84px;flex-shrink:0" ${colStyle}>${urgencyBadge}</div>
  <div class="feed-cell" data-css="width:72px;flex-shrink:0" ${colStyle}>${statusBadge(r.status)}</div>
  <div class="feed-cell" data-css="width:160px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r._srcName)}">${r._srcName}</div>
  <div class="feed-cell" data-css="flex:1;min-width:160px;padding-left:16px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.package_version || chainVer || '')}">${escHtml(r.package_version || chainVer || '')}</div>
  <div class="feed-cell" data-css="flex:1;min-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(r.file_name || '')}">${escHtml(r.file_name || '')}</div>
  <div class="feed-cell" data-css="width:116px;flex-shrink:0;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(fmtTZ(r.published_at, true))}">${fmtTZ(r.published_at, true)}</div>
  <div class="feed-cell" data-css="width:130px;flex-shrink:0;white-space:nowrap">
    <div class="data-right-actions" >
      <button class="btn btn-xs btn-soft-primary" data-on-click="dataRightGoto" data-args='${_dataArgs(r._sid, JSON.stringify(r._chain || []))}'>跳转</button>
      <button class="btn btn-xs btn-soft-success" data-on-click="showPushModal" data-args='${_dataArgs(r.id, r._srcName, (r._chain && r._chain.length >= 2) ? r._chain[r._chain.length - 2] : r.version_branch || '', (r._chain && r._chain.length > 0) ? r._chain[r._chain.length - 1] : r.package_type || '', JSON.stringify(r._chain || []))}'>推送</button>
    </div>
  </div>
</div>`;
  });

  html += '</div>';
  document.getElementById('dataRight').innerHTML = html;
  _dataRightBuilt = true;

  // 联动左侧 path 选中: 给匹配行加 .feed-row-selected 类,并滚动到第一个
  // 2026-07-28: 修复"左侧选 path 右侧看不到对应行"反模式
  // 匹配逻辑: _dataSelectedSid/_dataSelectedPathId/_dataSelectedUrl/_dataSelectedVerHint/_dataSelectedPkgHint
  dataHighlightPathMatches();

  const tp2 = Math.max(1, Math.ceil(rows.length / FEED_PAGE_SIZE));
  if (tp2 > 1) {
    const pg = _dataFeedPage;
    let phtml = '';
    if (pg > 1) phtml += `<button class="btn btn-xs" data-on-click="dataFeedGoPage" data-args='${_dataArgs(pg-1)}'>‹</button>`;
    const win = 5;
    const st = Math.max(1, pg - Math.floor(win/2));
    const en = Math.min(tp2, st + win - 1);
    const s2 = Math.max(1, en - win + 1);
    if (s2 > 1) { phtml += `<button class="btn btn-xs" data-on-click="dataFeedGoPage" data-args='${_dataArgs(1)}'>1</button>`; if (s2 > 2) phtml += `<span data-css="color:var(--muted);padding:0 2px">…</span>`; }
    for (let i = s2; i <= en; i++) { phtml += `<button class="btn btn-xs${i===pg?' btn-primary':''}" data-on-click="dataFeedGoPage" data-args='${_dataArgs(i)}'>${i}</button>`; }
    if (en < tp2) { if (en < tp2-1) phtml += `<span data-css="color:var(--muted);padding:0 2px">…</span>`; phtml += `<button class="btn btn-xs" data-on-click="dataFeedGoPage" data-args='${_dataArgs(tp2)}'>${tp2}</button>`; }
    if (pg < tp2) phtml += `<button class="btn btn-xs" data-on-click="dataFeedGoPage" data-args='${_dataArgs(pg+1)}'>›</button>`;
    document.getElementById('dataPager').innerHTML = phtml + `<span data-css="color:var(--muted);font-size:11px;margin-left:8px">${rows.length}条/共${tp2}页</span>`;
  } else {
    document.getElementById('dataPager').innerHTML = '';
  }
}

// 联动高亮: 给匹配当前左侧 path 选择的 feed-row 加 .feed-row-selected,
//            并滚动到第一个匹配行。配合 dataRenderRight() 在末尾调用。
// 2026-07-28: 修复"左侧选 path 右侧看不到对应行"反模式 — 用户原始问题:
// 选了 path 节点,右侧表格看上去跟没选一样,不告诉用户"已生效"。
// 复用 dataBuildFeed 的过滤规则(1930-1945),但绕过 _dataFilters.has_new 等,
// 只对 path 匹配生效。
// 注: 仅在精确选中 (path_id 或 url) 时高亮整批匹配;
//     中间节点的 ver/pkg hint (说明 "选了某个产品下某个版本的所有包")
//     因为该版本下所有包都合理显示,不高亮反而避免视觉污染。
function dataHighlightPathMatches() {
  const dr = document.getElementById('dataRight');
  if (!dr) return;
  dr.querySelectorAll('.feed-row-selected').forEach(el => el.classList.remove('feed-row-selected'));
  // 没选任何 → 不高亮,也不滚动
  if (!_dataSelectedSid) return;
  // 仅精确匹配才高亮 (path_id / url),中间节点 hint 不过滤
  const isExact = !!( _dataSelectedPathId || _dataSelectedUrl );
  if (!isExact) return;
  // 用与右侧已渲染的 feed-row DOM 一致的方式判定
  const rows = dr.querySelectorAll('.feed-row[data-snap-id]');
  let firstMatch = null;
  rows.forEach(row => {
    const sid = row.dataset.sid;
    const srcObj = _dataAll[sid];
    if (!srcObj) return;
    const snap = (srcObj.snapshots || []).find(s => String(s.id) === String(row.dataset.snapId));
    if (!snap) return;
    // 复用 dataBuildFeed 的 path 过滤链 (line 1929-1945)
    let match = true;
    if (_dataSelectedPathId) {
      if (snap.path_id !== _dataSelectedPathId) match = false;
    } else if (_dataSelectedUrl) {
      const stripSlashes = s => s.replace(/^\/+|\/+$/g, '');
      const normSnap = '/' + stripSlashes(snap.source_url || '');
      const normSel = '/' + stripSlashes(_dataSelectedUrl);
      if (normSnap !== normSel) match = false;
    } else if (_dataSelectedVerHint || _dataSelectedPkgHint) {
      if (_dataSelectedVerHint && (snap.version_branch || '') !== _dataSelectedVerHint) match = false;
      if (_dataSelectedPkgHint && (snap.package_type || '') !== _dataSelectedPkgHint) match = false;
    }
    if (match) {
      row.classList.add('feed-row-selected');
      if (!firstMatch) firstMatch = row;
    }
  });
  // 滚动到第一个匹配行
  if (firstMatch) firstMatch.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function dataFeedGoPage(p) {
  // Count total rows WITHOUT re-running the full feed build.
  // We stored byKey counts during the last dataBuildFeed call.
  // For simplicity, rebuild just the key statistics: count filtered rows per (ver,pkgtype).
  let totalFiltered = 0;
  const sources = dataFilteredSources();
  const selSid = _dataSelectedSid;
  const selSrc = selSid ? (_dataAll[selSid] || null) : null;
  const isHintClick = !_dataSelectedUrl && (_dataSelectedVerHint || _dataSelectedPkgHint);

  if (selSrc) {
    const byKey = {};
    (selSrc.snapshots || []).forEach(s => {
      if (_dataSelectedUrl && s.source_url !== _dataSelectedUrl) return;
      if (isHintClick) {
        if (_dataSelectedVerHint && (s.version_branch || '') !== _dataSelectedVerHint) return;
        if (_dataSelectedPkgHint && (s.package_type || '') !== _dataSelectedPkgHint) return;
      }
      if (_dataFilters.urgency && (s.urgency !== _dataFilters.urgency || s.status !== 'active')) return;
      const k = (s.version_branch || '?') + '|' + (s.package_type || '?');
      if (!byKey[k]) { byKey[k] = 0; }
      byKey[k]++;
    });
    if (!isHintClick) {
      Object.values(byKey).forEach(c => { totalFiltered += Math.min(c, _feedPkgLimit); });
    } else {
      totalFiltered = Object.values(byKey).reduce((a, b) => a + b, 0);
    }
  } else {
    sources.forEach(([sid, src]) => {
      const byKey = {};
      (src.snapshots || []).forEach(s => {
        if (_dataFilters.urgency && (s.urgency !== _dataFilters.urgency || s.status !== 'active')) return;
        const k = (s.version_branch || '?') + '|' + (s.package_type || '?');
        if (!byKey[k]) byKey[k] = 0;
        byKey[k]++;
      });
      Object.values(byKey).forEach(c => { totalFiltered += Math.min(c, _feedPkgLimit); });
    });
  }

  const totalPages = Math.max(1, Math.ceil(totalFiltered / FEED_PAGE_SIZE));
  _dataFeedPage = Math.min(Math.max(1, p), totalPages);
  dataRenderRight();
}

function dataRightToggleRow(rowId) {
  const t0 = performance.now();
  const rowEl = document.getElementById(rowId);
  if (!rowEl) return;

  // Close any open detail
  const t1 = performance.now();
  const openDetail = document.querySelector('.feed-detail');
  if (openDetail) {
    const prevRow = openDetail.previousElementSibling;
    if (prevRow && prevRow.classList.contains('feed-row')) {
      const arrow = prevRow.querySelector('.feed-arrow');
      if (arrow) arrow.textContent = '▶';
    }
    openDetail.remove();
    const snapId = prevRow ? prevRow.dataset.sid : null;
    if (snapId && _snapDetailCache[snapId]) {
      delete _snapDetailCache[snapId];
    }
  }

  if (rowEl.classList.contains('feed-expanded')) {
    rowEl.classList.remove('feed-expanded');
    const arrow = rowEl.querySelector('.feed-arrow');
    if (arrow) arrow.textContent = '▶';
    console.log('[perf] toggle close:', (performance.now() - t0).toFixed(1) + 'ms');
    return;
  }

  const t2 = performance.now();
  const sid = rowEl.dataset.sid;
  const snapId = rowEl.dataset.id ? parseInt(rowEl.dataset.id) : null;
  if (!sid || !snapId) return;

  const MAP = { sys: '系统', rule: '规则', nti: '威胁情报', av: '病毒库', apprule: '应用规则', url: 'URL库', wcs: '恶意站点', judge: '研判', geo: '地理库', interface: '接口', special: '特殊', other: '其他', merge: '合并', client: '客户端', av_stream: '流式病毒库', test: '测试', engine: '引擎' };
  const src = _dataAll[sid];
  if (!src) return;
  const snaps = src.snapshots || [];
  const snap = snaps.find(s => s.id === snapId);
  if (!snap) return;

  const t3 = performance.now();
  let chainDisplayHtml = '';
  const pt = src.package_type || {};
  const paths = pt.paths || [];
  const entryUrl = src.entry_url || '';
  // Match path by snap.path_id (exact, distinguishes multi-chain shared URLs).
  // Fall back to URL match only for legacy rows that have no path_id.
// 2026-08-08: matchedPath 反查只用 _dataSelectedChain(用户点的 chain 上下文)
// + path_id / URL 反查 — 不读 snap.version_branch / snap.package_type。
// 严格按你"不读数据库的 ver packageType,从 chain 构造"原则。
// URL 去重下,8703 这种同 URL 多 chain 的 snap 反查总是取第一条(URL-only
// path_id 区分不了 chain)— 这是 URL-only path_id 的固有限制。8703 用户点
// WAF leaf 显示 WAF 元数据,点海光 leaf 显示海光元数据(用户意图主导)。
let matchedPath = null;
const allPaths = paths || [];
if (_dataSelectedChain && _dataSelectedChain.length > 0) {
  // 用户点的 chain:精确匹配优先(leaf),prefix 匹配 fallback(中间节点)
  matchedPath = allPaths.find(p =>
    Array.isArray(p.chain) &&
    p.chain.length === _dataSelectedChain.length &&
    p.chain.every((seg, i) => seg === _dataSelectedChain[i])
  );
  if (!matchedPath) {
    matchedPath = allPaths.find(p =>
      Array.isArray(p.chain) &&
      p.chain.length >= _dataSelectedChain.length &&
      p.chain.slice(0, _dataSelectedChain.length).every((seg, i) => seg === _dataSelectedChain[i])
    );
  }
}
if (!matchedPath && snap.path_id) {
  matchedPath = allPaths.find(p => p.path_id === snap.path_id);
}
if (!matchedPath && snap.source_url) {
  matchedPath = allPaths.find(p => p.url === snap.source_url);
}
  if (matchedPath && matchedPath.chain && matchedPath.chain.length > 0) {
    const chain = matchedPath.chain;
    const leafUrl = matchedPath.url || '';
    const cacheKey = sid + '|' + chain.join('/');

    if (!_dataChainPrefixCache[cacheKey]) {
      const prefixSegCount = new Array(chain.length).fill(null);
      paths.forEach(p => {
        if (!p.url || !p.chain) return;
        let sharedLen = 0;
        const minLen = Math.min(p.chain.length, chain.length);
        for (let i = 0; i < minLen; i++) {
          if (p.chain[i] === chain[i]) sharedLen++;
          else break;
        }
        if (sharedLen === 0) return;
        const segs = p.url.split('/').filter(Boolean);
        const leafDepth = p.chain.length;
        for (let d = 1; d <= sharedLen; d++) {
          const segCount = segs.length - (leafDepth - d);
          const effectiveSegs = segCount > 0 ? segCount : 1;
          if (prefixSegCount[d - 1] === null) {
            prefixSegCount[d - 1] = effectiveSegs;
          } else {
            prefixSegCount[d - 1] = Math.min(prefixSegCount[d - 1], effectiveSegs);
          }
        }
      });
      _dataChainPrefixCache[cacheKey] = prefixSegCount;
    }
    const prefixSegCount = _dataChainPrefixCache[cacheKey];

    function findNodeUrl(depth) {
      if (depth === 0) return entryUrl;
      const idx = depth - 1;
      if (idx >= prefixSegCount.length || prefixSegCount[idx] === null) return entryUrl;
      const segCount = prefixSegCount[idx];
      const segs = leafUrl.split('/').filter(Boolean);
      const base = '/' + segs.slice(0, segCount).join('/');
      return base || entryUrl;
    }

    chainDisplayHtml = chain.map((name, i) => {
      const isLast = (i === chain.length - 1);
      if (isLast) {
        const fullUrl = 'https://update.nsfocus.com' + leafUrl;
        return `<a href="${escHtml(fullUrl)}" target="_blank" data-css="color:var(--primary);text-decoration:underline">${escHtml(name)}</a>`;
      }
      const nodeUrl = findNodeUrl(i + 1);
      const fullNodeUrl = nodeUrl ? ('https://update.nsfocus.com' + nodeUrl) : '';
      if (fullNodeUrl && nodeUrl !== entryUrl) {
        return `<a href="${escHtml(fullNodeUrl)}" target="_blank" data-css="color:var(--primary);text-decoration:underline">${escHtml(name)}</a>`;
      }
      return escHtml(name);
    }).join(' <span data-css="color:var(--gray-disabled)">›</span> ');
  }

  const t4 = performance.now();
  rowEl.classList.add('feed-expanded');
  const arrow = rowEl.querySelector('.feed-arrow');
  // 2026-08-08: 类型字段用 chain 派生,不读 snap.package_type(URL 去重后
  // snap 行被互相覆盖,snap.package_type 脏)。matchedPath 来自上方按
  // snap.path_id 在 paths[] 里 find,拿到 chain 后 chain[-1] 即为 pkg_type。
  const matchedPathPkg = (matchedPath && matchedPath.chain && matchedPath.chain.length > 0)
    ? matchedPath.chain[matchedPath.chain.length - 1]
    : '';
  if (arrow) arrow.textContent = '▼';

  const detailHtml = `<div class="feed-detail" id="snap-detail-${snapId}">
  <div class="detail-inner">
    <span class="data-detail-k">文件名</span><span data-css="font-size:12.5px">${escHtml(snap.file_name || '')}</span>
    <span class="data-detail-k">版本</span><span data-css="font-size:12.5px">${escHtml(snap.package_version || (matchedPath && matchedPath.chain && matchedPath.chain.length >= 2 ? matchedPath.chain[matchedPath.chain.length - 2] : '') || '')}</span>
    <span class="data-detail-k">类型</span><span data-css="font-size:12.5px">${MAP[matchedPathPkg] || matchedPathPkg || ''}</span>
    <span class="data-detail-k">紧急度</span><span data-css="font-size:12.5px"><span class="badge ${snap.urgency === 'critical' ? 'badge-err' : snap.urgency === 'high' ? 'badge-warn' : 'badge-ok'}">${escHtml(snap.urgency || '')}</span></span>
    <span class="data-detail-k">文件大小</span><span data-css="font-size:12.5px" id="snap-fs-${snapId}">—</span>
    <span class="data-detail-k">发布时间</span><span data-css="font-size:12.5px">${fmtTZ(snap.published_at, true)}</span>
    <span class="data-detail-k">首次发现</span><span data-css="font-size:12.5px">${fmtTZ(snap.first_seen_at, true)}</span>
    <span class="data-detail-k">最近采集</span><span data-css="font-size:12.5px">${fmtTZ(snap.last_seen_at, true)}</span>
    <span class="data-detail-k">下载地址</span><span data-css="font-size:12.5px"><a href="${escHtml('https://update.nsfocus.com/update/downloads/id/' + (snap.download_id || ''))}" target="_blank" data-css="color:var(--success)">${escHtml('https://update.nsfocus.com/update/downloads/id/' + (snap.download_id || ''))}</a></span>
    <span class="data-detail-k">发布地址</span><span data-css="font-size:12.5px"><a href="${escHtml('https://update.nsfocus.com' + (snap.source_url || ''))}" target="_blank" data-css="color:var(--primary)">${escHtml('https://update.nsfocus.com' + (snap.source_url || ''))}</a></span>
    ${chainDisplayHtml ? `<span class="data-detail-k">层级路径</span><span data-css="font-size:12px;color:var(--primary)">${chainDisplayHtml}</span>` : ''}
    <span class="data-detail-k">MD5</span><span data-css="font-size:12.5px" id="snap-md5-${snapId}">—</span>
    <span class="data-detail-k">页面哈希</span><span data-css="font-size:12.5px;word-break:normal;overflow-x:auto" id="snap-ph-${snapId}">—</span>
    <span class="data-detail-k">需重启</span><span data-css="font-size:12.5px" id="snap-rr-${snapId}">—</span>
    <span class="data-detail-k" data-css="grid-column:1/3;align-self:start">描述</span><span id="snap-desc-${snapId}" data-css="grid-column:1/3;background:#fff;border:1px solid var(--border);border-radius:6px;padding:8px 12px;display:block;max-height:150px;overflow-y:auto;white-space:pre-wrap;overflow-wrap:break-word;font-size:12px;color:var(--gray-muted)">—</span>
</div>
</div>`;
  const t5 = performance.now();
  rowEl.insertAdjacentHTML('afterend', detailHtml);
  const t6 = performance.now();

  // Lazy load detail fields: description_raw, md5_hash, page_hash, file_size, restart_required
  // Do NOT cache by snapId — multiple detail rows can share the same snapId (e.g. history list).
  // Check element existence before updating so closed/removed details are safely skipped.
  api(`/snapshot/${snapId}`).then(r => {
    if (r.code !== 0 || !r.data) return;
    const d = r.data;
    const descEl = document.getElementById(`snap-desc-${snapId}`);
    if (descEl) {
      if (d.description_raw) {
        descEl.outerHTML = `<div data-css="grid-column:1/3;background:#fff;border:1px solid var(--border);border-radius:6px;padding:8px 12px;display:block;max-height:150px;overflow-y:auto;white-space:pre-wrap;overflow-wrap:break-word;font-size:12px;color:var(--gray-muted)">${escHtml(d.description_raw)}</div>`;
      } else {
        descEl.style.display = 'none';
      }
    }
    const md5El = document.getElementById(`snap-md5-${snapId}`);
    if (md5El && d.md5_hash) md5El.textContent = d.md5_hash;
    const phEl = document.getElementById(`snap-ph-${snapId}`);
    if (phEl && d.page_hash) phEl.textContent = d.page_hash;
    const fsEl = document.getElementById(`snap-fs-${snapId}`);
    if (fsEl && d.file_size != null) {
      fsEl.textContent = d.file_size > 1048576
        ? (d.file_size / 1048576).toFixed(1) + ' MB'
        : (d.file_size / 1024).toFixed(0) + ' KB';
    }
    const rrEl = document.getElementById(`snap-rr-${snapId}`);
    if (rrEl && d.restart_required) {
      rrEl.outerHTML = `<span class="data-detail-k">需重启</span><span data-css="font-size:12.5px">⚠️ 需要重启设备</span>`;
    }
  }).catch(() => {});

  console.log('[perf] toggle open — close:' + (t1-t0).toFixed(1) + 'ms data:' + (t3-t2).toFixed(1) + 'ms chain:' + (t4-t3).toFixed(1) + 'ms html:' + (t5-t4).toFixed(1) + 'ms insert:' + (t6-t5).toFixed(1) + 'ms total:' + (t6-t0).toFixed(1) + 'ms');
}

function dataRightGoto(sid, chainJson) {
  _dataSelectedSid = sid;
  let targetChain = null;
  try { targetChain = JSON.parse(chainJson); } catch(e) { targetChain = []; }
  _dataSelectedChain = targetChain;
  _dataSelectedUrl = null;
  // Derive version & package type hints from the target chain's last node for right-panel filtering.
  // Match the FULL chain against paths to avoid ambiguity when multiple versions
  // share the same leaf name (e.g. several "入侵检测规则升级包" across versions
  // V2.0R00F01 / V2.0R00F05 / V2.0R00F06_CW would otherwise all match by leaf alone).
  const src = _dataAll[sid];
  let verHint = null, pkgHint = null;
  let targetPathId = null;
  if (src && targetChain && targetChain.length > 0) {
    const pt = src.package_type;
    if (pt && pt.paths) {
      const path = pt.paths.find(p =>
        Array.isArray(p.chain) &&
        p.chain.length === targetChain.length &&
        p.chain.every((seg, i) => seg === targetChain[i])
      );
      if (path && path.url) {
        // Backend attaches path_id to each path in /latest/snapshots; use it
        // directly to locate the snapshot row that belongs to *this* chain.
        // Without this, NSFocus's multi-chain-shared-URL structure would resolve
        // to the first matching snap (wrong chain) and feed would show wrong data.
        targetPathId = path.path_id || null;
        const snap = targetPathId
          ? (src.snapshots || []).find(s => s.path_id === targetPathId)
          : null;
        if (snap) {
          // 2026-08-08: ver/pkg 从 chain 派生,不读 snap 行(URL 去重后
          // snap 行被互相覆盖,snap.version_branch/snap.package_type 脏)
          verHint = path.chain.length >= 2 ? path.chain[path.chain.length - 2] : null;
          pkgHint = path.chain[path.chain.length - 1] || null;
        }
      }
    }
  }
  _dataSelectedVerHint = verHint;
  _dataSelectedPkgHint = pkgHint;
  _dataSelectedPathId = targetPathId;
  _dataFeedPage = 1;
  // Do NOT call dataRenderLeft() — it rebuilds the DOM and wipes all expanded states.
  // Instead, directly manipulate the existing DOM: expand target card + tree path, then scroll.
  // The event delegation listener on #dataLeft is set up once in dataPage() and persists.
  setTimeout(() => {
    const card = document.getElementById('dpc_' + sid);
    if (!card) {
      // Card not yet in DOM — fall back to dataRenderLeft() for initial render
      dataRenderLeft();
      return;
    }

    // Expand the card body (tree container) via productCardToggle, then expand tree path
    const cardBody = card.querySelector('.card-bd');
    const wasHidden = cardBody && cardBody.style.display === 'none';
    if (wasHidden) {
      // Use the existing productCardToggle to expand the card body cleanly
      const targetId = cardBody.id;
      productCardToggle(targetId);
    }

    // Find the tree node whose data-chain matches targetChain
    const allNodes = card.querySelectorAll('[data-action="treeNode"]');
    let targetNode = null;
    if (targetChain && targetChain.length > 0) {
      allNodes.forEach(n => {
        try {
          const nc = JSON.parse(n.getAttribute('data-chain') || '[]');
          if (JSON.stringify(nc) === JSON.stringify(targetChain)) {
            targetNode = n;
          }
        } catch(e) {}
      });
    }

    if (targetNode) {
      // Expand all ancestor .tree-children so target is visible
      let parent = targetNode.parentElement;
      while (parent && !parent.classList.contains('card-bd')) {
        if (parent.classList.contains('tree-children') && parent.style.display === 'none') {
          treeToggleById(parent.id); // use the named toggle to keep arrow icons in sync
        }
        parent = parent.parentElement;
      }
      // Highlight the target node
      document.querySelectorAll('.tree-node-selected').forEach(el => el.classList.remove('tree-node-selected'));
      targetNode.classList.add('tree-node-selected');
      targetNode.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      // Highlight the parent card
      document.querySelectorAll('.card-selected').forEach(el => el.classList.remove('card-selected'));
      var pc = targetNode.closest('.card');
      if (pc) pc.classList.add('card-selected');
    } else if (card) {
      // No specific node but card found — highlight the card at least
      document.querySelectorAll('.card-selected').forEach(el => el.classList.remove('card-selected'));
      card.classList.add('card-selected');
    }
  }, 50);
  dataRenderRight();
}

function dataRightExpand(btn, sid, ver, pkgType) {
  dataRightToggleRow(btn.closest('tr'));
}

function dataClearFilter() {
  _dataSelectedSid = null;
  _dataSelectedChain = null;
  _dataSelectedUrl = null;
  _dataSelectedNode = null;
  _dataSelectedVerHint = null;
  _dataSelectedPkgHint = null;
  _lastSelectedNode = null;
  document.querySelectorAll('.card-selected').forEach(el => el.classList.remove('card-selected'));
  document.querySelectorAll('.tree-node-selected').forEach(el => el.classList.remove('tree-node-selected'));
  _dataFeedPage = 1;
  dataRenderLeft();
  dataRenderRight();
}



// ── Old data page helpers (kept for compatibility) ──
async function dataShowDrill(sid, url, verHint, pkgHint) {
  const src = _dataAll[sid];
  if (!src) return;

  document.getElementById('dataAggView').classList.add('hidden');
  document.getElementById('dataPager').innerHTML = '';

  const MAP = { sys: '系统', rule: '规则', nti: '威胁情报', av: '病毒库', apprule: '应用规则', url: 'URL库', wcs: '恶意站点', judge: '研判', geo: '地理库', interface: '接口', special: '特殊', other: '其他', merge: '合并', client: '客户端', av_stream: '流式病毒库', test: '测试', engine: '引擎' };
  let snaps = src.snapshots || [];

  // Filter by exact URL when provided (leaf node)
  if (url) {
    snaps = snaps.filter(s => s.source_url === url);
  }
  // Filter by hints when provided (intermediate node).
  // 2026-08-08: 不读 snap.version_branch / snap.package_type。改用 paths[] 找 chain
  // 包含 (verHint, pkgHint) 的 path,snap.source_url 匹配该 path 的 url。
  // 这样即使 URL 共享多 chain,ver/pkg hint 仍精确过滤。
  if (verHint || pkgHint) {
    const allPaths = (src.package_type && src.package_type.paths) || [];
    const hintPath = allPaths.find(p =>
      Array.isArray(p.chain) && p.chain.length >= 2 &&
      (verHint ? p.chain[p.chain.length - 2] === verHint : true) &&
      (pkgHint ? p.chain[p.chain.length - 1] === pkgHint : true)
    );
    if (hintPath && hintPath.url) {
      const normHint = hintPath.url.startsWith('http') ? hintPath.url :
        ('https://update.nsfocus.com' + (hintPath.url.startsWith('/') ? hintPath.url : '/' + hintPath.url));
      snaps = snaps.filter(s => {
        const sFull = s.source_url && (s.source_url.startsWith('http') ? s.source_url :
          'https://update.nsfocus.com' + (s.source_url.startsWith('/') ? s.source_url : '/' + s.source_url));
        return sFull && sFull.replace(/\/+$/, '') === normHint.replace(/\/+$/, '');
      });
    } else {
      snaps = []; // 没匹配的 path → 空
    }
  }

  // 用 _dataSelectedChain[-2] 作 group key(用户点的 chain 的版本层),不读 snap.version_branch
  const byVer = {};
  const verKeyHint = (_dataSelectedChain && _dataSelectedChain.length >= 2)
    ? _dataSelectedChain[_dataSelectedChain.length - 2] : null;
  snaps.forEach(s => {
    // 优先用 verKeyHint (group by chain layer),fallback 到 s.version_branch(legacy data)
    const v = verKeyHint || s.version_branch || '?';
    if (!byVer[v]) byVer[v] = [];
    byVer[v].push(s);
  });

  // 用于推送按钮 — 从 _dataSelectedChain 派生
  const pushVer = verKeyHint || '';
  const pushPkg = (_dataSelectedChain && _dataSelectedChain.length > 0)
    ? _dataSelectedChain[_dataSelectedChain.length - 1] : '';

  const versions = Object.keys(byVer).sort((a, b) => {
    const na = parseFloat(a), nb = parseFloat(b);
    if (!isNaN(na) && !isNaN(nb)) return nb - na;
    return b.localeCompare(a, 'zh');
  });

  let drillHtml = `<div class="data-drill">
    <div class="data-drill-hd">
      <button class="btn btn-xs btn-outline" data-on-click="dataBackToAgg">← 返回列表</button>
      <span class="data-drill-title">📦 ${escHtml(src.display_name || src.name)}</span>
    </div>
    <div class="data-drill-body">`;

  versions.forEach(ver => {
    const verSnaps = byVer[ver].sort((a, b) => (b.last_seen_at || '').localeCompare(a.last_seen_at || ''));
    const isHighlight = ver === verHint;

    const hasRb = verSnaps.some(s => s.status === 'withdrawn');
    const hasNew = verSnaps.some(s => s.status === 'active' && !s.last_delivered_at);
    const latestCollected = verSnaps[0] ? fmtTZ(verSnaps[0].last_seen_at, true) : '—';
    const hasCritical = verSnaps.some(s => s.urgency === 'critical' && s.status === 'active');

    const verFlags = [];
    if (hasCritical) verFlags.push('<span class="badge badge-err" data-css="font-size:10px">🔴critical</span>');
    if (hasRb) verFlags.push('<span data-css="color:var(--danger);font-size:11px">含撤回</span>');
    if (hasNew) verFlags.push('<span data-css="color:var(--primary);font-size:11px">有未推送</span>');

    // 用 _dataSelectedChain[-1] 作 pkgType group key(用户点的 chain 的最深层),不读 snap.package_type
    const byType = {};
    const pkgKeyHint = (_dataSelectedChain && _dataSelectedChain.length > 0)
      ? _dataSelectedChain[_dataSelectedChain.length - 1] : null;
    verSnaps.forEach(s => {
      const t = pkgKeyHint || s.package_type || '?';
      if (!byType[t]) byType[t] = [];
      byType[t].push(s);
    });

    const typeKeys = Object.keys(byType).sort();
    const verKey = sid + '_' + ver;
    // Highlighted version: open by default
    const verBodyClass = isHighlight ? 'data-ver-body' : 'data-ver-body hidden';

    drillHtml += `<div class="data-ver-block ${isHighlight ? 'data-ver-highlight' : ''}" id="dvb_anchor_${escHtml(verKey)}">
      <div class="data-ver-hd" data-on-click="dataToggleVer" data-args='${_dataArgs('$el', verKey)}'>
        <span class="data-prod-arrow">${isHighlight ? '&#9660;' : '&#9658;'}</span>
        <span class="data-ver-label">${ver}</span>
        <div data-css="display:flex;gap:6px;align-items:center;margin-left:auto">${verFlags.join('')}</div>
        <span data-css="color:var(--muted);font-size:11px;margin-left:8px">采集 ${latestCollected}</span>
      </div>
      <div class="${verBodyClass}" id="dvb_${escHtml(verKey)}">`;

    typeKeys.forEach(pkgType => {
      const typeSnaps = byType[pkgType].sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''));
      const latest = typeSnaps[0];
      const historyCount = typeSnaps.length - 1;
      const typeLabel = MAP[pkgType] || pkgType || '';
      const isLatestPkg = historyCount > 0; // multiple packages exist, this one is the latest by published_at
      const sz = latest.file_size > 1048576 ? (latest.file_size / 1048576).toFixed(1) + 'M' : (latest.file_size / 1024).toFixed(0) + 'K';
      const urg = latest.urgency === 'critical' ? 'badge-err' : latest.urgency === 'high' ? 'badge-warn' : 'badge-ok';
      const isRb = latest.status === 'withdrawn';
      const isNewPkg = latest.status === 'active' && !latest.last_delivered_at;
      const histKey = verKey + '_' + pkgType;

      drillHtml += `<div class="data-type-row ${isRb ? 'data-type-rollback' : ''}">
        <div class="data-type-main" data-on-click="dataToggleSnapDetail" data-args='${_dataArgs('$el')}'>
          ${isNewPkg ? '<span class="data-pill-new-dot" data-css="margin-right:4px"></span>' : ''}
          ${isLatestPkg ? '<span class="badge badge-blue" data-css="font-size:10px;margin-right:4px">最新</span>' : ''}
          <span class="data-type-label">${typeLabel}</span>
          <span class="mono" data-css="color:var(--primary);font-size:12px">${escHtml((latest.file_name || '').substring(0, 35))}</span>
          <span data-css="color:var(--muted);font-size:11px">${fmtTZ(latest.published_at)}</span>
          <span class="badge ${urg}" data-css="font-size:10px">${latest.urgency || ''}</span>
          ${latest.last_delivered_at ? '<span data-css="font-size:11px;color:var(--success)" title="已推送">✅</span>' :
            (latest.status === 'active' ? '<span data-css="font-size:11px;color:var(--muted)" title="未推送">○</span>' : '')}
          <button class="btn btn-xs btn-soft-success" data-css="margin-left:auto" data-on-click="showPushModal" data-stop data-args='${_dataArgs(latest.id, src.name, pushVer, pushPkg)}'>推送</button>
          ${historyCount > 0 ? `<span class="data-history-toggle" data-on-click="dataToggleHistory" data-stop data-args='${_dataArgs('$el', histKey)}'>+${historyCount} 条历史 ▼</span>` : ''}
        </div>
        <div class="data-snap-detail hidden">${dataSnapDetailHtml(latest, MAP)}</div>
        ${historyCount > 0 ? `<div class="data-history hidden" id="dh_${escHtml(histKey)}">
          ${typeSnaps.slice(1).map(s => {
            const sz2 = s.file_size > 1048576 ? (s.file_size / 1048576).toFixed(1) + 'M' : (s.file_size / 1024).toFixed(0) + 'K';
            const urg2 = s.urgency === 'critical' ? 'badge-err' : s.urgency === 'high' ? 'badge-warn' : 'badge-ok';
            const isRb2 = s.status === 'withdrawn';
            return `<div class="data-hist-row ${isRb2 ? 'data-type-rollback' : ''}" data-on-click="_toggleClass" data-args='${_dataArgs('data-hist-expanded')}'>
              <span data-css="color:var(--muted);font-size:11px;width:130px;flex-shrink:0">${fmtTZ(s.published_at, true)}</span>
              <span class="mono" data-css="color:var(--primary);font-size:12px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml((s.file_name || '').substring(0, 30))}</span>
              <span class="badge ${urg2}" data-css="font-size:10px">${escHtml(s.urgency || '')}</span>
              ${s.last_delivered_at ? '<span data-css="font-size:11px;color:var(--success)">✅</span>' : '<span data-css="font-size:11px;color:var(--muted)">○</span>'}
              <button class="btn btn-xs btn-soft-success" data-css="margin-left:4px" data-on-click="showPushModal" data-stop data-args='${_dataArgs(s.id, src.name, pushVer, pushPkg)}'>推送</button>
              <div class="data-snap-detail hidden">${dataSnapDetailHtml(s, MAP)}</div>
            </div>`;
          }).join('')}
        </div>` : ''}
      </div>`;
    });

    drillHtml += `</div></div>`;
  });

  drillHtml += `</div></div>`;
  document.getElementById('dataDrillView').innerHTML = drillHtml;
  document.getElementById('dataDrillView').classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'instant' });
  // Scroll to highlighted version block if one was specified
  if (verHint) {
    const anchor = document.getElementById('dvb_anchor_' + verHint.replace(/\\/g, '\\\\'));
    if (anchor) anchor.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function dataSnapDetailHtml(s, MAP) {
  const sz = s.file_size > 1048576 ? (s.file_size / 1048576).toFixed(1) + 'M' : (s.file_size / 1024).toFixed(0) + 'K';
  const typeLabel = MAP[s.package_type] || s.package_type || '';
  return `<div class="data-detail-grid">
    <span class="data-detail-k">类型</span><span>${typeLabel}</span>
    <span class="data-detail-k">版本</span><span>${escHtml(s.package_version || '')}</span>
    <span class="data-detail-k">大小</span><span>${sz}</span>
    ${s.min_sys_version ? `<span class="data-detail-k">依赖</span><span>≥ ${escHtml(s.min_sys_version)}</span>` : ''}
    ${s.published_at ? `<span class="data-detail-k">发布</span><span>${fmtTZ(s.published_at, true)}</span>` : ''}
    ${s.first_seen_at ? `<span class="data-detail-k">发现</span><span>${fmtTZ(s.first_seen_at, true)}</span>` : ''}
    ${s.md5_hash ? `<span class="data-detail-k">MD5</span><span class="mono">${escHtml(s.md5_hash)}</span>` : ''}
    ${s.download_id ? `<span class="data-detail-k">下载</span><span><a href="https://update.nsfocus.com/update/downloads/id/${escHtml(s.download_id)}" target="_blank">downloads/id/${escHtml(s.download_id)}</a></span>` : ''}
    ${s.source_url ? `<span class="data-detail-k">详情</span><span><a href="https://update.nsfocus.com${escHtml(s.source_url)}" target="_blank">绿盟更新页</a></span>` : ''}
    ${s.description_raw ? `<div class="data-desc-box">${escHtml(s.description_raw.substring(0, 800))}${s.description_raw.length > 800 ? '…' : ''}</div>` : ''}
  </div>`;
}

function dataToggleVer(el, key) {
  const body = document.getElementById('dvb_' + key);
  const arrow = el.querySelector('.data-prod-arrow');
  if (!body) return;
  const open = body.classList.toggle('hidden');
  if (arrow) arrow.innerHTML = open ? '&#9658;' : '&#9660;';
}

function dataToggleSnapDetail(el) {
  const detail = el.nextElementSibling;
  if (detail && detail.classList.contains('data-snap-detail')) detail.classList.toggle('hidden');
}

function dataToggleHistory(el, key) {
  const hist = document.getElementById('dh_' + key);
  if (!hist) return;
  const open = hist.classList.toggle('hidden');
  el.textContent = open ? `+${hist.querySelectorAll('.data-hist-row').length} 条历史 ▼` : `+${hist.querySelectorAll('.data-hist-row').length} 条历史 ▲`;
}

function dataBackToAgg() {
  document.getElementById('dataDrillView').classList.add('hidden');
  document.getElementById('dataDrillView').innerHTML = '';
  document.getElementById('dataAggView').classList.remove('hidden');
  document.getElementById('dataPager').innerHTML = '';
  const filt = dataFilteredSources();
  const totalPages = Math.max(1, Math.ceil(filt.length / PAGE_SIZE));
  if (_dataPage > totalPages) _dataPage = totalPages;
  dataRenderAgg();
}

async function manualPush(sid) { await confirmAct('确认推送？', async ()=>{ const r = await api('/history/' + sid + '/resend', { method: 'POST' }); toast(r.code === 0 ? '已推送' : r.message || '失败', r.code === 0 ? 'success' : 'error'); }, '📤', '确认推送'); }

// ── Push Modal (email send OR channel send) ──
let _pushSid=0,_pushTab='email',_pushRecipientMode='customer',_pushSelectedCustomers=new Set(),_pushChannelId=0,_pushManualEmail='',_pushBotChannelId=0,_banTimer=null,_pushChain=[];

async function showPushModal(sid, product, version, pkgType, chain){
  _pushSid = sid;
  // 2026-08-08: 记下前端从 r._chain 传的 chain 数组(content_source 派生的
  // chain 数组,URL-only path_id 反查第一条的固有限制)。from_snapshot 用这个
  // 构造推送消息内容,不读 snap 行反查。
  // chain 参数来自 JSON.stringify(r._chain) — 字符串格式,反序列化。
  // onclick 是 HTML 属性,JSON 里 " 被 escHtml 转成 &quot;,这里反转回 "。
  let chainArr = [];
  if (Array.isArray(chain)) {
    chainArr = chain;
  } else if (typeof chain === 'string' && chain) {
    try {
      const decoded = chain.replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
      chainArr = JSON.parse(decoded);
    } catch (e) { chainArr = []; }
  }
  _pushChain = chainArr;
  _pushTab='email';
  _pushRecipientMode='customer';
  _pushSelectedCustomers.clear();
  _pushChannelId=0;
  _pushManualEmail='';
  _pushBotChannelId=0;
  if(_banTimer){clearInterval(_banTimer);_banTimer=null;}
  document.getElementById('app').insertAdjacentHTML('beforeend',
    `<div id="pushModal" class="push-modal" data-on-click="closePushModal" data-self>
      <div class="card"  data-css="width:560px;max-height:90vh;display:flex;flex-direction:column">
        <div class="card-hd">📤 推送通知 <span data-css="color:var(--muted);font-weight:400;font-size:11px">${escHtml(product)} ${escHtml(version)} ${escHtml(pkgType)}</span><button class="btn btn-xs btn-outline" data-css="margin-left:auto" data-on-click="closePushModal">×</button></div>
        <div class="card-bd" data-css="overflow-y:auto;flex:1">
          <div data-css="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:14px">
            <div data-on-click="switchPushTab" data-args='${_dataArgs('email')}' id="pushTabEmailBtn" data-css="flex:1;text-align:center;padding:10px;cursor:pointer;font-size:13px;font-weight:600;border-bottom:2px solid var(--primary);color:var(--primary);margin-bottom:-2px">📧 邮件发送</div>
            <div data-on-click="switchPushTab" data-args='${_dataArgs('bot')}' id="pushTabBotBtn" data-css="flex:1;text-align:center;padding:10px;cursor:pointer;font-size:13px;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-2px">🤖 按渠道发送</div>
          </div>
          <div id="pushTabEmail">
            <div class="form-group">
              <label>发件渠道 (SMTP)</label>
              <div id="pushChannelSelect"><div class="empty" data-css="padding:8px">加载中...</div></div>
            </div>
            <div class="form-group">
              <label>收件人来源</label>
              <div data-css="display:flex;gap:6px">
                <label data-css="flex:1;padding:8px;border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:13px;text-align:center"><input type="radio" name="prec" value="customer" checked data-on-change="switchRecipientMode" data-args='${_dataArgs('customer')}'> 👤 从客户选择</label>
                <label data-css="flex:1;padding:8px;border:1px solid var(--border);border-radius:6px;cursor:pointer;font-size:13px;text-align:center"><input type="radio" name="prec" value="manual" data-on-change="switchRecipientMode" data-args='${_dataArgs('manual')}'> ✏️ 自定义填写</label>
              </div>
            </div>
            <div id="pushCustomerList" class="push-target"></div>
            <div id="pushManualInput" class="hidden">
              <textarea id="pushEmail" rows="3" placeholder="输入邮箱，多个用逗号或换行分隔" data-css="width:100%;box-sizing:border-box;padding:8px 12px;border:1px solid var(--border);border-radius:8px;font-size:13px;outline:none;resize:vertical" data-on-input="onPushEmailChange"></textarea>
              <div id="pushEmailHint" data-css="font-size:11px;color:var(--muted);margin-top:4px"></div>
            </div>
          </div>
          <div id="pushTabBot" class="hidden">
            <p class="cfg-hint" data-css="margin-bottom:8px;color:var(--gray-muted)">将通知推送到所选渠道(企微/钉钉/邮件)。邮件渠道下,收件人为 SMTP 用户自身(测试链路用)。</p>
            <div id="pushBotChannelList" class="push-target"><div class="empty" data-css="padding:8px">加载中...</div></div>
          </div>
        </div>
        <div data-css="padding:12px 16px;border-top:1px solid var(--border);background:var(--gray-bg)">
          <div class="flex gap8"><button class="btn btn-success btn-sm flex1" id="pushConfirmBtn" data-on-click="confirmPush" disabled>确认推送</button><button class="btn btn-sm btn-outline" data-on-click="closePushModal">取消</button></div>
        </div>
      </div>
    </div>`);
  await loadPushChannelSelect();
  await loadPushCustomerList();
  await loadPushBotChannels();
  updatePushConfirmState();
}

function closePushModal(){let m=document.getElementById('pushModal');if(m){if(_banTimer){clearInterval(_banTimer);_banTimer=null;}m.remove();}}

function switchPushTab(tab){
  _pushTab=tab;
  const emailBtn=document.getElementById('pushTabEmailBtn');
  const botBtn=document.getElementById('pushTabBotBtn');
  const emailPanel=document.getElementById('pushTabEmail');
  const botPanel=document.getElementById('pushTabBot');
  if(tab==='email'){
    emailBtn.style.color='var(--primary)';emailBtn.style.borderBottomColor='var(--primary)';
    botBtn.style.color='var(--muted)';botBtn.style.borderBottomColor='transparent';
    emailPanel.classList.remove('hidden');
    botPanel.classList.add('hidden');
  }else{
    botBtn.style.color='var(--primary)';botBtn.style.borderBottomColor='var(--primary)';
    emailBtn.style.color='var(--muted)';emailBtn.style.borderBottomColor='transparent';
    botPanel.classList.remove('hidden');
    emailPanel.classList.add('hidden');
  }
  updatePushConfirmState();
}

function switchRecipientMode(mode){
  _pushRecipientMode=mode;
  document.getElementById('pushCustomerList').classList.toggle('hidden',mode!=='customer');
  document.getElementById('pushManualInput').classList.toggle('hidden',mode!=='manual');
  if(mode==='manual'){
    const saved=localStorage.getItem('pushModalLastManualEmail')||'';
    const input=document.getElementById('pushEmail');
    if(input && !input.value && saved){ input.value=saved; }
    onPushEmailChange();
  }
  updatePushConfirmState();
}

function onPushEmailChange(){
  const input=document.getElementById('pushEmail');
  const v=(input?.value||'').trim();
  _pushManualEmail=v;
  const hint=document.getElementById('pushEmailHint');
  if(!v){hint.textContent='';updatePushConfirmState();return;}
  const emails=v.split(/[,\n]/).map(e=>e.trim()).filter(Boolean);
  const re=/^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const bad=emails.filter(e=>!re.test(e));
  if(bad.length){hint.textContent='⚠️ 格式无效: '+bad.join(', ');hint.style.color='var(--danger)';}
  else{hint.textContent='✅ '+emails.length+' 个邮箱';hint.style.color='var(--success)';}
  updatePushConfirmState();
}

async function loadPushChannelSelect(){
  const el=document.getElementById('pushChannelSelect');
  if(!el) return;
  let channels=[];
  try{
    const r=await api('/channels');
    channels=(r.data||[]).filter(x=>x.type==='email' && x.is_active);
  }catch(e){}
  if(!channels.length){
    el.innerHTML='<div data-css="padding:8px;color:var(--danger);font-size:12px">暂无可用 SMTP 渠道,请先在通知渠道中配置邮箱</div>';
    return;
  }
  const nsfocus=channels.find(c=>c.config?.smtp_host==='mail.nsfocus.com');
  const defaultCh=nsfocus||channels[0];
  _pushChannelId=defaultCh.id;
  el.innerHTML='<select class="dd" id="pushChannelDropdown" data-on-change="_pushChannelChanged" data-args=\'["$value"]\' data-css="width:100%">'
    +channels.map(c=>`<option value="${c.id}" ${c.id===defaultCh.id?'selected':''}>${escHtml(c.name)} (${escHtml(c.config?.smtp_host||'')}:${c.config?.smtp_port||''})</option>`).join('')
    +'</select>';
}

async function loadPushCustomerList(){
  const el=document.getElementById('pushCustomerList');
  if(!el) return;
  el.innerHTML='<div class="empty" data-css="padding:8px">加载中...</div>';
  try{
    const r=await api('/customers');
    const list=(r.data||[]).filter(x=>(x.email||'').trim());
    if(!list.length){
      el.innerHTML='<div class="empty" data-css="padding:8px;color:var(--muted)">暂无有邮箱的客户,请先在客户管理中设置邮箱</div>';
      return;
    }
    el.innerHTML=list.map(c=>`<label class="item" data-custid="${c.id}" data-css="display:flex;align-items:center;gap:8px;cursor:pointer;padding:8px 12px">
      <input type="checkbox" data-custid="${c.id}" data-email="${escHtml(c.email)}" data-on-change="onCustomerCheck" data-args='${_dataArgs('$el')}' data-css="flex-shrink:0">
      <span data-css="flex:1;min-width:0">
        <span data-css="font-weight:500">${escHtml(c.name)}</span>
        <span data-css="color:var(--muted);font-size:11px;margin-left:6px">${escHtml(c.email)}</span>
        ${c.company?`<span data-css="color:var(--muted);font-size:11px;margin-left:6px">· ${escHtml(c.company)}</span>`:''}
      </span>
    </label>`).join('');
  }catch(e){el.innerHTML='<div class="empty">加载失败</div>';}
}

async function loadPushBotChannels(){
  const el=document.getElementById('pushBotChannelList');
  if(!el) return;
  let list=[];
  try{
    const r=await api('/channels');
    list=(r.data||[]).filter(x=>x.is_active);
  }catch(e){}
  if(!list.length){
    el.innerHTML='<div class="empty" data-css="padding:8px;color:var(--muted)">暂无可用渠道</div>';
    return;
  }
  const typeLabel={wecom:'企微',dingtalk:'钉钉',feishu:'飞书',email:'邮件',apprise:'Apprise'};
  el.innerHTML=list.map(c=>{
    const isEmail=c.type==='email';
    const sub=isEmail
      ? `${escHtml((c.config?.smtp_user)||'')} → 自己`
      : typeLabel[c.type]||c.type;
    return `<label class="item" data-botchid="${c.id}" data-css="display:flex;align-items:center;gap:8px;cursor:pointer;padding:10px 12px">
      <input type="radio" name="botch" value="${c.id}" data-on-change="onBotChannelCheck" data-args='${_dataArgs('$el')}' data-css="flex-shrink:0">
      <span data-css="flex:1;min-width:0">
        <span data-css="font-weight:500">${escHtml(c.name)}</span>
        <span data-css="color:var(--muted);font-size:11px;margin-left:6px">${sub}</span>
      </span>
    </label>`;
  }).join('');
}

function onCustomerCheck(cb){
  const id=parseInt(cb.dataset.custid);
  const email=cb.dataset.email;
  if(cb.checked){ _pushSelectedCustomers.add(id+'|'+email); }
  else{ _pushSelectedCustomers.delete(id+'|'+email); }
  updatePushConfirmState();
}

function onBotChannelCheck(rb){
  _pushBotChannelId=parseInt(rb.value);
  updatePushConfirmState();
}

function updatePushConfirmState(){
  const btn=document.getElementById('pushConfirmBtn');
  if(!btn) return;
  let ready=false;
  if(_pushTab==='email'){
    let hasRecipients=false;
    if(_pushRecipientMode==='customer'){
      hasRecipients=_pushSelectedCustomers.size>0;
    }else{
      const emails=_pushManualEmail.split(/[,\n]/).map(e=>e.trim()).filter(Boolean);
      const re=/^[^@\s]+@[^@\s]+\.[^@\s]+$/;
      hasRecipients=emails.length>0 && emails.every(e=>re.test(e));
    }
    ready=hasRecipients && _pushChannelId>0;
  }else{
    ready=_pushBotChannelId>0;
  }
  btn.disabled=!ready;
}

async function confirmPush(){
  const btn=document.getElementById('pushConfirmBtn');
  btn.disabled=true;btn.textContent='推送中...';
  try{
    let r;
    if(_pushTab==='email'){
      if(!_pushChannelId)return toast('请选择 SMTP 渠道','error');
      let recipients=[];
      let customerIds = [];
      if (_pushRecipientMode === 'customer') {
        _pushSelectedCustomers.forEach(item => {
            const parts = item.split('|');
            if (parts.length === 2) {
                recipients.push(parts[1]);
                customerIds.push(parseInt(parts[0]));
            }
        });
        if(!recipients.length)return toast('请至少选择一个客户','error');
      }else{
        recipients=_pushManualEmail.split(/[,\n]/).map(e=>e.trim()).filter(Boolean);
        if(!recipients.length)return toast('请输入邮箱','error');
        try{localStorage.setItem('pushModalLastManualEmail', _pushManualEmail);}catch(e){}
      }
      const firstCustId = customerIds.length > 0 ? customerIds[0] : null;
      r=await api('/history/'+_pushSid+'/email-push',{method:'POST',body:JSON.stringify({channel_id:_pushChannelId, recipients: recipients, customer_id: firstCustId, user_chain: _pushChain})});
    }else{
      if(!_pushBotChannelId)return toast('请选择渠道','error');
      r=await api('/history/'+_pushSid+'/push',{method:'POST',body:JSON.stringify({mode:'channel', target_id:_pushBotChannelId, user_chain: _pushChain})});
    }
    if(r.code===0){
      showPushResult(r.data||{}, true);
      closePushModal();
    }else if(r.code===42900){
      let sec=r.data?.retry_after||600;toast(r.message,'error');startBanCountdown(sec);
    }else{
      if(r.data){ showPushResult(r.data, false); }
      else{ toast(r.message||'推送失败','error'); }
      btn.disabled=false;btn.textContent='确认推送';
    }
  }catch(e){toast('推送失败','error');btn.disabled=false;btn.textContent='确认推送';}
}

// Issue #6: structured push result toast with per-channel status
function showPushResult(data, success){
  const results = data.results || [];
  if(!results.length){
    toast(data.message || (success ? '已推送' : '推送失败'), success ? 'success' : 'error');
    return;
  }
  // Build HTML list
  const lines = results.map(r => {
    const ok = r.success;
    const icon = ok ? '✓' : '✗';
    const color = ok ? 'var(--success)' : 'var(--danger)';
    const logBtn = (!ok && r.log_path)
      ? ` <a href="#" data-on-click="showPushLogByPath" data-prevent data-args='${_dataArgs(r.log_path, r.channel || '')}' data-css="color:var(--primary);font-size:11px;margin-left:6px">📋 日志</a>`
      : '';
    return `<div data-css="display:flex;align-items:center;gap:6px;padding:2px 0;font-size:12px;color:#ffffff">
      <span data-css="font-weight:600">${icon}</span>
      <span>${escHtml(r.channel||'')}</span>
      ${r.error ? `<span data-css="color:rgba(255,255,255,.7);font-size:11px">${escHtml(r.error)}</span>` : ''}
      ${logBtn}
    </div>`;
  }).join('');
  const head = success
    ? `<div data-css="font-weight:700;color:#ffffff;margin-bottom:4px">推送完成</div>`
    : `<div data-css="font-weight:700;color:#ffffff;margin-bottom:4px">推送失败</div>`;
  showRichToast(head + lines, success ? 'success' : 'error', 8000);
}

// Reuse the existing test log viewer for push log files too
async function showPushLogByPath(path, name){
  // Just reuse showTestLog with a synthetic channel id derived from filename
  // (showTestLog reads /tmp/email_test_{id}.log, but push uses push_{id}_{ts}.log
  // — we need a direct file read instead.)
  showLogFile(path, name || '推送日志');
}

async function showLogFile(path, name){
  const existing=document.getElementById('testLogModal');if(existing)existing.remove();
  // Fetch via a generic log endpoint — we'll add it on backend
  const body=await api('/channels/log-file?path='+encodeURIComponent(path));
  const content=body.code===0 ? body.data.content : `(${body.message||'读取失败'})`;
  const m=document.createElement('div');
  m.id='testLogModal';
  m.className='confirm-modal active';
  m.innerHTML=`<div class="confirm-box" data-css="width:720px;max-width:95vw;max-height:85vh;display:flex;flex-direction:column">
    <div class="confirm-icon">📋</div>
    <div class="confirm-title">日志 - ${escHtml(name)}</div>
    <div data-css="font-size:11px;color:var(--muted);margin-bottom:10px">文件: ${escHtml(path)}</div>
    <pre data-css="flex:1;min-height:300px;max-height:55vh;overflow:auto;background:var(--text);color:var(--gray-disabled);padding:12px;border-radius:6px;font-family:SF Mono,Fira Code,Consolas,monospace;font-size:12px;line-height:1.5;text-align:left;white-space:pre-wrap;word-break:break-all;margin:0">${escHtml(content)}</pre>
    <div class="confirm-btns" data-css="margin-top:14px">
      <button class="confirm-btn-cancel" data-on-click="_removeById" data-args='${_dataArgs('testLogModal')}'>关闭</button>
    </div>
  </div>`;
  m.addEventListener('click', e=>{if(e.target===m)m.remove();});
  document.body.appendChild(m);
}

// Rich HTML toast (replaces text-only toast for structured results).
// Inline styles fully override the global .toast class because CSS class
// rules like `white-space:nowrap; overflow:hidden; max-width:90vw` would
// otherwise chop off the multi-line push result content (the "推送完成"
// header and the per-channel list rows). We re-center using left:50% +
// transform:translateX(-50%) since the .toast class only provides centering
// for single-line text and our HTML has multiple lines.
function showRichToast(html, type, durationMs){
  const e=document.createElement('div');
  e.className=`toast toast-${type}`;
  e.style.cssText='position:fixed;top:20px;left:50%;transform:translateX(-50%);color:#fff;font-size:13px;font-weight:500;padding:12px 18px;border-radius:8px;z-index:99999;box-shadow:0 4px 16px rgba(0,0,0,.25);animation:fadeDown .2s ease;white-space:normal;max-width:520px;min-width:240px;overflow:visible;text-align:left;line-height:1.5';
  e.innerHTML=html;
  document.body.appendChild(e);
  setTimeout(()=>e.remove(), durationMs||4000);
}
function startBanCountdown(seconds){
  let btn=document.getElementById('pushConfirmBtn');if(!btn)return;
  btn.disabled=true;
  function tick(){let m=Math.floor(seconds/60),s=seconds%60;btn.textContent=`已禁用 ${m}:${String(s).padStart(2,'0')}`;if(seconds<=0){btn.disabled=false;btn.textContent='确认推送';if(_banTimer){clearInterval(_banTimer);_banTimer=null;}return;}seconds--;}
  tick();_banTimer=setInterval(tick,1000);
}

async function resendTargeted(sid,cid,custid,btn){
  await confirmAct('确认重新推送到该客户？', async ()=>{
    // 推送期间:当前按钮变 "⏳ 推送中..." 并 disable;同 snap 其他按钮变灰色 disable
    // 但保留 "📤 重推" 文本,避免让用户以为那几行也在被推送
    // 用 [data-resend-sid] 属性选,history() 重渲染后属性消失 → finally 兜底
    const btns = document.querySelectorAll(`button[data-resend-sid="${sid}"]`);
    btns.forEach(b => {
      b.disabled = true;
      if (b === btn) {
        b.textContent = '⏳ 推送中...';
      } else {
        b.classList.add('btn-locked');
        // 保留原 textContent ("📤 重推"),不改
      }
    });
    try {
      const r = await api('/history/'+sid+'/resend-targeted',{method:'POST',body:JSON.stringify({channel_id:cid,customer_id:custid})});
      toast(r.code===0?'已推送':r.message||'失败',r.code===0?'success':'error');
      // 推送成功后刷新历史列表,显示新生成的 delivery_log 行
      await history();
    } finally {
      // history() 重渲染后原按钮对象已失效,query 重新选
      document.querySelectorAll(`button[data-resend-sid="${sid}"]`).forEach(b => {
        b.disabled = false;
        b.textContent = '📤 重推';
        b.classList.remove('btn-locked');
      });
    }
  }, '📤', '确认推送');
}
