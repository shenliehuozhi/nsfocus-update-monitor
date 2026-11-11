// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// ── History ──
async function history(){
  const _seq=_navSeq;
  const _q=new URLSearchParams(location.hash.split('?')[1]||'');
  let days=_q.get('days')||'30';
  // feat/customer-push-card 2026-09-02: 支持 cust query 下钻 (来自 dashboard 客户推送卡片)
  const cust=_q.get('cust')||'';
  const rule=_q.get('rule')||'';
  // 2026-11-09 P1: 后端每页固定 200 个包,原先没有 page → 第 2 页之后的数据永远看不到
  const page=Math.max(1,parseInt(_q.get('page')||'1',10)||1);
  let _apiUrl='/history?limit=200&page='+page+'&days='+days;
  if(cust) _apiUrl+='&customer_id='+encodeURIComponent(cust);
  if(rule) _apiUrl+='&rule_id='+encodeURIComponent(rule);
  // 过滤下拉的候选项: 客户全量 + 订阅规则全量 (与列表并行拉取,不额外等一轮)
  const [r,custR,ruleR]=await Promise.all([
    api(_apiUrl),
    api('/customers').catch(()=>({data:[]})),
    api('/subscriptions').catch(()=>({data:[]}))
  ]);
  const list=r.data?.items||[];
  // 不再按 minute-level 聚合,每个 delivery 直接一行
  // 渠道类型 → 中文标签 + emoji
  const botTypes = new Set(['wecom','dingtalk','feishu','webhook']);
  let html='';
  list.forEach(h=>{
    let dels=h.deliveries||[];
    if(!dels.length){
      html+=`<tr class="bg-subtle"><td>${fmtTZ(h.pushed_at||h.first_seen_at||'',true)}</td><td>—</td><td>${escHtml(h.product_name||'')}</td><td>${typeCN(h.package_type)}</td><td class="max-w380" title="${escHtml(h.file_name||'')}"><span class="code-chip">${escHtml(h.file_name||'')}</span></td><td><span class="badge badge-${h.urgency==='critical'?'err':h.urgency==='high'?'warn':'ok'}">${escHtml(h.urgency||'')}</span></td><td>—</td><td>—</td><td>—</td></tr>`;
    }else{
      dels.forEach(d=>{
        // 2026-07-11: channel_name 可能含客户名,格式 "<原渠道名>|Nsf0cus|<客户名>"
        // 拆出原渠道名(展示用)和客户名(代替 LEFT JOIN customers 的 d.customer_name)
        const _SEP='|Nsf0cus|';
        let _chanName = d.channel_name || '';
        let _custName = '';
        if (_chanName.includes(_SEP)) {
          const _parts = _chanName.split(_SEP);
          _chanName = _parts[0];
          _custName = _parts.slice(1).join(_SEP);  // 客户名理论上不含分隔符,但 slice(1).join 防止嵌套
        }
        // 渠道列: 机器人显示「<chip 类型> 渠道名」;email 显示「渠道名 (发件邮箱)」
        // 发件邮箱 sender 由 email notifier 写入;老数据/机器人渠道为空时 fallback 到 recipient
        let channelDisp;
        if (botTypes.has(d.channel_type)) {
          channelDisp = `${chTypeChip(d.channel_type)} ${escHtml(_chanName||'—')}`;
        } else if (d.channel_type === 'email') {
          let senderEmail = d.sender || d.recipient || '';
          channelDisp = senderEmail ? `${escHtml(_chanName||'—')} (${escHtml(senderEmail)})` : escHtml(_chanName||'—');
        } else {
          channelDisp = escHtml(_chanName) || '—';
        }
        // 收件人:email 渠道显示邮箱;机器人渠道没个人收件人,用 _chanName 替代,跟机器人名一致
        let recipient = d.recipient || _chanName || '—';
        // package_type: 同 chain-scoped 派生,d.derived_package_type 来自 delivery_log 的 chain_json 末项
        // 老链路 chain_json='[]' 时 fallback 到 h.package_type
        const pkgType = d.derived_package_type || h.package_type;
        html += `<tr class="bg-subtle">
            <td>${fmtTZ(d.sent_at||h.pushed_at||h.first_seen_at||'',true)}</td>
            <td>${escHtml(_custName||d.customer_name||'—')}</td>
            <td>${escHtml(h.product_name||'')}</td>
            <td>${typeCN(pkgType)}</td>
            <td class="max-w380" title="${escHtml(h.file_name||'')}"><span class="code-chip-muted">${escHtml(h.file_name||'')}</span></td>
            <td><span class="badge badge-${h.urgency==='critical'?'err':h.urgency==='high'?'warn':'ok'}">${escHtml(h.urgency||'')}</span></td>
            <td>${channelDisp}</td>
            <td class="max-w200 ellipsis fs11" title="${escHtml(recipient)}">${escHtml(recipient)}</td>
            <td><span class="badge ${d.delivery_status==='sent'?'badge-ok':'badge-err'}">${d.delivery_status==='sent'?'✅ 成功':'❌ 失败'}</span></td>
            <td><button class="btn btn-xs btn-primary" data-resend-sid="${h.id}" data-on-click="resendTargeted" data-args='${_dataArgs(h.id, d.channel_id||0, d.customer_id||0, '$el')}'>📤 重推</button></td>
        </tr>`;
      });
    }
  });
  // 2026-11-09: 过滤条 — 订阅规则 / 客户 / 时间范围, 三者都写进 hash, 可分享也可从仪表盘卡片带 cust 下钻进来
  const _opt=(v,l,sel)=>`<option value="${v}" ${String(v)===String(sel)?'selected':''}>${escHtml(l)}</option>`;
  const ruleSel=`<select id="histRule" class="sel-sm dd max-w190" data-on-change="histSetFilter" data-args='${_dataArgs('rule', '$value')}' title="按订阅规则过滤">${_opt('','全部订阅规则',rule)}${(ruleR.data||[]).map(x=>_opt(x.id,x.name||('规则#'+x.id),rule)).join('')}</select>`;
  const custSel=`<select id="histCust" class="sel-sm dd max-w190" data-on-change="histSetFilter" data-args='${_dataArgs('cust', '$value')}' title="按客户过滤">${_opt('','全部客户',cust)}${(custR.data||[]).map(c=>_opt(c.id,c.name,cust)).join('')}</select>`;
  const daysSel=`<select id="histDays" class="sel-sm dd max-w190" data-on-change="histSetFilter" data-args='${_dataArgs('days', '$value')}'>${[[1,'近1天'],[3,'近3天'],[7,'近7天'],[30,'近30天'],[90,'近90天'],[365,'近1年'],[0,'全部']].map(([v,l])=>_opt(v,l,days)).join('')}</select>`;
  // 分页条 (2026-11-09 P1): total 现在是包数, 每页 200 包。原先既不显示总数也无法翻到
  // 第 2 页, 用户以为 200 包就是全部 —— 这跟上次"客户显示不全"是同一类静默截断。
  const total=r.data?.total||0;
  const pages=Math.max(1,Math.ceil(total/200));
  const _pgBtn=(dis)=>`class="btn btn-xs btn-outline px2-10${dis?' pager-btn-disabled':''}"`;
  const pager=`<div class="cfg-foot"><span>共 <b class="text-body">${total}</b> 个包 · 第 ${page}/${pages} 页 (每页 200)</span><button ${_pgBtn(page<=1)} data-on-click="histSetPage" data-args='${_dataArgs(page-1)}'>← 上一页</button><button ${_pgBtn(page>=pages)} data-on-click="histSetPage" data-args='${_dataArgs(page+1)}'>下一页 →</button></div>`;
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">📜 推送历史 <span class="ml-auto muted-12 fw400 flex items-center gap8">${ruleSel}${custSel}${daysSel}<a href="#" data-on-click="history">刷新</a><button class="btn btn-xs btn-outline danger-outline-text" data-on-click="confirmClearHistory">🗑 清空</button></span></div><div class="card-bd pt0"><table><tr><th class="w140">时间</th><th>客户</th><th>产品</th><th>类型</th><th>文件名</th><th>紧急度</th><th>渠道</th><th>收件人</th><th>状态</th><th>操作</th></tr>${html||'<tr><td colspan="10" class="empty">暂无推送记录</td></tr>'}</table>${pager}</div></div>`;
}

// 翻页: 只改 page, 保留当前过滤条件
function histSetPage(n){
  const q=new URLSearchParams(location.hash.split('?')[1]||'');
  if(!q.get('days'))q.set('days','30');
  q.set('page',String(Math.max(1,n|0)));
  location.hash='history?'+q.toString();
  history();
}

// 改写 hash 里的过滤条件再重渲染;val 为空 = 清除该条件
function histSetFilter(key,val){
  const q=new URLSearchParams(location.hash.split('?')[1]||'');
  if(!q.get('days'))q.set('days','30');
  if(val)q.set(key,val);else q.delete(key);
  q.delete('page');   // 换过滤条件后回到第 1 页,否则会停在一张空页
  location.hash='history?'+q.toString();
  history();
}
async function confirmClearHistory(){
  let days=document.getElementById('histDays')?.value||'30';
  let label=days==='0'?'全部':`最近${days}天之前的`;
  await confirmAct(`⚠️ 确认清空${label}推送历史记录？此操作不可撤销。`, async ()=>{const r=await api('/history?days='+(days==='0'?'':days),{method:'DELETE'});toast(r.message||'已清空',r.code===0?'success':'error');if(r.code===0)history();}, '🗑️', '确认清空', true);
}

function _renderRecentDeliveries(deliveries,range){
  if(!deliveries||!deliveries.length)return '';
  const ranges={1:'1天',7:'7天',30:'1个月',365:'1年'};
  let btns=Object.entries(ranges).map(([v,l])=>`<button class="btn btn-xs ${range===v?'btn-primary':'btn-outline'}" data-on-click="dashboard" data-args='${_dataArgs(v)}'>${l}</button>`).join('');
  // Aggregate: group by customer+product+chain-specific type
  // derived_package_type 来自后端 CASE: chain_json 末项 OR snapshots.package_type fallback
  // 同一物理 snap + 不同 chain_json 会分成两行 (e.g. WAF V6.0.9规则升级包 vs 海光 V6.0.9规则升级)
  let agg={};
  deliveries.forEach(d=>{
    // 2026-07-11: channel_name 可能含客户名, split 出原渠道名再聚合,避免渠道列变长
    let _cn = d.channel_name || '';
    const _idx = _cn.indexOf('|Nsf0cus|');
    if (_idx >= 0) _cn = _cn.substring(0, _idx);
    const pkgType = d.derived_package_type || d.package_type;
    let key=`${d.customer_name||'—'}|${d.product_name}|${pkgType}`;
    if(!agg[key])agg[key]={cust:d.customer_name||'—',prod:d.product_name,pkg:pkgType,channels:new Set(),count:0,last:d.sent_at,status:d.delivery_status};
    agg[key].count++;
    agg[key].channels.add(_cn||d.channel_type);
    if(d.sent_at>agg[key].last)agg[key].last=d.sent_at;
  });
  let rows=Object.values(agg).sort((a,b)=>b.last.localeCompare(a.last)).slice(0,15).map(a=>{
    return `<tr><td>${escHtml(a.cust)}</td><td>${escHtml(a.prod)}</td><td>${typeCN(a.pkg)}</td><td>${a.count}次</td><td>${escHtml([...a.channels].join(', '))}</td><td>${a.last?fmtTZ(a.last,true):''}</td></tr>`;
  }).join('');
  return `<div class="card mt14"><div class="card-hd">📨 最近推送明细 <span class="ml-auto fw400">${btns}</span></div><div class="card-bd pt0"><table><tr><th>客户</th><th>产品</th><th>类型</th><th>次数</th><th>渠道</th><th>最近</th></tr>${rows||'<tr><td colspan="6" class="empty">暂无</td></tr>'}</table></div></div>`;
}

// ── Settings ──
function _toggleMaintenance(el){
  const v=el.checked?'1':'0';
  api('/settings/config',{method:'PUT',body:JSON.stringify({maintenance_mode:v})})
    .then(r=>toast(r.code===0?(v==='1'?'🔇 维护模式已开启':'🔔 维护模式已关闭'):'失败',r.code===0?'success':'error'));
}
async function _toggleApiLocal(el){
  const v=el.checked?'1':'0';
  if(v==='1'){
    await confirmAct('⚠️ 开启后将拒绝所有非本机(127.0.0.1)的 API 请求。\n\n你当前通过外部 IP 访问时，开启后页面将无法正常工作。', async()=>{
      api('/settings/config',{method:'PUT',body:JSON.stringify({api_localhost_only:'1'})})
        .then(r=>toast(r.code===0?'🔒 API 已锁定为 localhost':'失败',r.code===0?'success':'error'));
    },'⚠️','确认开启',true);
    el.checked=false;
  }else{
    api('/settings/config',{method:'PUT',body:JSON.stringify({api_localhost_only:'0'})})
      .then(r=>toast(r.code===0?'🔓 API 已开放':'失败',r.code===0?'success':'error'));
  }
}

async function settings(){
  const _seq=_navSeq;
  const r=await api('/settings/config').catch(()=>({data:{}}));
  const cfg=r.data||{};
  // 获取渠道列表（用于系统事件通知渠道选择）
  const ch=await api('/channels').catch(()=>({data:[]}));
  cfg.channels=ch.data||[];
  // 获取系统事件配置
  const ev=await api('/settings/event-config').catch(()=>({data:{}}));
  cfg.event_enabled=ev.data?.enabled||false;
  cfg.event_channel_id=ev.data?.channel_id||null;
  cfg.event_types=ev.data?.event_types||[];
  // 检查采集状态
  let isCollecting=false;
  try{const cr=JSON.parse(cfg.collection_running||'{}');isCollecting=cr.status==='1';}catch(e){}

  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">⚙️ 系统配置</div><div class="card-bd">
    ${isCollecting?'<div class="warning-banner">⚠️ 采集正在进行中，保存配置将同时停止采集</div>':''}
    <div class="cfg-section">
      <div class="cfg-section-hd">🔶 开关选项</div>
      <div class="cfg-row grid-3">
        <div class="cfg-flag banner-warn">
          <label class="cfg-toggle"><label class="toggle-switch"><input type="checkbox" id="cfg-maintenance" ${cfg.maintenance_mode==='1'?'checked':''} data-on-change="_toggleMaintenance" data-args='${_dataArgs('$el')}'><span class="toggle-slider"></span></label><span>🔧 维护模式</span></label>
          <span class="cfg-flag-desc">开启后所有推送静默（采集照常），调试时避免误发通知</span>
        </div>
        <div class="cfg-flag banner-danger">
          <label class="cfg-toggle"><label class="toggle-switch"><input type="checkbox" id="cfg-apilocal" ${cfg.api_localhost_only==='1'?'checked':''} data-on-change="_toggleApiLocal" data-args='${_dataArgs('$el')}'><span class="toggle-slider"></span></label><span>🔒 API 仅限本机</span></label>
          <span class="cfg-flag-desc">/api/* 仅允许 127.0.0.1 调用，TCP 层校验不可伪造</span>
        </div>
      </div>
    </div>

    <div class="cfg-section">
      <div class="cfg-section-hd">📡 采集设置</div>
      <div class="cfg-row">
        <div class="cfg-toggle">
          <label class="toggle-switch"><input id="cfg-scheduler-enabled" type="checkbox" ${cfg.scheduler_enabled!=='0'?'checked':''} data-on-change="handleSchedulerToggle" data-args='${_dataArgs('$el')}'><span class="toggle-slider"></span></label>
          <span>启用调度器</span>
        </div>
        <p class="cfg-hint">关闭后采集和心跳任务暂停，可用于维护调试</p>
      </div>
      <div class="cfg-row">
        <div class="form-group"><label>采集间隔 <span class="cfg-unit">(小时)</span></label><input id="cfg-interval" type="number" value="${cfg.collect_interval||4}" min="1" max="24"><p class="cfg-hint">快速采集周期，建议1-4小时；过短易被封IP</p></div>
        <div class="form-group"><label>回退确认次数</label><input id="cfg-rollback" type="number" value="${cfg.rollback_confirm||2}" min="1" max="5"><p class="cfg-hint">包版本降低需连续N次确认才通知，防止误报</p></div>
      </div>
      <div class="cfg-row">
        <div class="form-group"><label>速率下限 <span class="cfg-unit">(秒)</span></label><input id="cfg-delay-min" type="number" value="${cfg.collect_delay_min||1.0}" min="0.1" max="10" step="0.1"><p class="cfg-hint">每次请求间隔随机下限，建议0.3-1秒</p></div>
        <div class="form-group"><label>速率上限 <span class="cfg-unit">(秒)</span></label><input id="cfg-delay-max" type="number" value="${cfg.collect_delay_max||3.0}" min="0.1" max="10" step="0.1"><p class="cfg-hint">每次请求间隔随机上限，建议不超过5秒</p></div>
      </div>
      <div class="cfg-row">
        <div class="form-group"><label>升级包跟踪数量</label>
          <select id="cfg-pkg-track" class="dd">
            <option value="5" ${(cfg.pkg_track_limit||'5')==='5'?'selected':''}>最新 5 个（默认）</option>
            <option value="3" ${cfg.pkg_track_limit==='3'?'selected':''}>最新 3 个</option>
            <option value="10" ${cfg.pkg_track_limit==='10'?'selected':''}>最新 10 个</option>
            <option value="all" ${cfg.pkg_track_limit==='all'?'selected':''}>全部（不推荐）</option>
          </select>
          <p class="cfg-hint">规则历史页会累积上百个包，每轮只入库/推送最新的 N 个（同一页一次发布多个包时按发布时间取前 N）。改动下一轮采集生效，无需重启。</p>
        </div>
      </div>
    </div>

    <div class="cfg-section">
      <div class="cfg-section-hd">💾 存储与保留</div>
      <div class="cfg-row">
        <div class="form-group"><label>附件上限 <span class="cfg-unit">(MB)</span></label><input id="cfg-attach" type="number" value="${cfg.attachment_max_mb||10}" min="1" max="50"><p class="cfg-hint">邮件附件最大体积；超过则在正文显示下载地址</p></div>
      </div>
      <div class="cfg-row">
        <div class="form-group"><label>历史保留条数 <span class="muted-10">(每版本+包类型组合)</span></label><input id="cfg-snapshots-per-group" type="number" value="${cfg.snapshots_per_group||5}" min="1" max="10"><p class="cfg-hint">每个(版本+包类型)组合保留几条历史快照。后端磁盘存储与前端采集数据页显示共用此值;超出自动删旧。</p></div>
      </div>
    </div>

    <div class="cfg-section">
      <div class="cfg-section-hd flex-center-10">
        <span>🔇 静默时段</span>
        <label class="toggle-switch"><input type="checkbox" id="cfg-quiet-enabled" ${cfg.quiet_hours_enabled==='1'?'checked':''} data-on-change="_setDisplay" data-args='${_dataArgs('quiet-times', '$checked')}'><span class="toggle-slider"></span></label>
        <span>启用</span>
      </div>
      <div id="quiet-times" class="${cfg.quiet_hours_enabled==='1'?'':'hidden'}">
        <div class="cfg-row cfg-row-inline">
          <div class="form-group"><label>开始时间</label><input id="cfg-quiet-start" type="time" value="${cfg.quiet_hours_start||'22:00'}"></div>
          <span class="cfg-sep">—</span>
          <div class="form-group"><label>结束时间</label><input id="cfg-quiet-end" type="time" value="${cfg.quiet_hours_end||'08:00'}"></div>
        </div>
        <p class="cfg-hint mt-neg6">此时段内不发送任何通知（邮件/Webhook），采集仍正常进行</p>
      </div>
    </div>

    <div class="cfg-section">
      <div class="cfg-section-hd">⏰ 高级选项</div>
      <div class="cfg-row">
        <div class="form-group"><label>主动心跳</label>
          <label class="toggle-switch"><input type="checkbox" id="cfg-heartbeat-enabled" ${cfg.heartbeat_enabled==='1'?'checked':''} data-on-change="_setDisplay" data-args='${_dataArgs('heartbeat-interval-row', '$checked')}'><span class="toggle-slider"></span></label>
          <p class="cfg-hint">开启后定时发送心跳请求维持Session活跃</p>
        </div>
      </div>
      <div class="cfg-row${cfg.heartbeat_enabled==='1'?'':' hidden'}" id="heartbeat-interval-row">
        <div class="form-group"><label>心跳间隔 <span class="cfg-unit">(分钟)</span></label><input id="cfg-heartbeat" type="number" value="${cfg.heartbeat_interval||30}" min="5" max="120"><p class="cfg-hint">Session 保活间隔，默认30分钟</p></div>
      </div>
      <div class="cfg-row">
        <div class="form-group"><label>预检 URL <span class="tp" data-tip="采集前/主动心跳时用于快速探测 Session 状态的轻量接口。\n\n【判定机制】\n• HTTP 200 + 正常 HTML → Session 正常\n• 重定向到 /update/upLic → Session 已过期(vendor 把过期会话踢到 license 页)\n• 响应 body 含 downloadsVm/id → Session 被虚拟化上下文污染(被带进 upLic/Vm 格式上下文,返回的不是预期页面)\n\n【选择建议】需是返回 v2 升级包列表的轻量 GET 接口,响应稳定 + 无重定向。默认 /update/listBvsV6/v/bvssys 是绿盟升级站点默认入口(对应 v2 升级包列表页)。改错会导致 Session 状态判错:过期/污染漏报或误报。\n\n【生效时机】保存后下次采集/心跳生效,无需重启进程。">ⓘ</span></label>
          <div class="flex gap6 items-center">
            <input class="bg-subtle flex-1" id="cfg-heartbeat-url" type="text" value="${cfg.heartbeat_url||'/update/listBvsV6/v/bvssys'}" placeholder="/update/listBvsV6/v/bvssys" readonly>
            <button type="button" id="cfg-heartbeat-url-edit" class="btn btn-xs btn-outline" data-on-click="editHeartbeatUrl">✏️ 编辑</button>
          </div>
          <p class="cfg-hint">默认 /update/listBvsV6/v/bvssys (绿盟升级站点默认端点)。修改会影响 Session 过期/污染判断,点编辑后需二次确认</p>
        </div>
      </div>
      <div class="cfg-row">
        <div class="form-group"><label>每日渠道体检</label>
          <label class="toggle-switch"><input type="checkbox" id="cfg-channel-health" ${cfg.channel_health_enabled==='0'?'':'checked'}><span class="toggle-slider"></span></label>
          <p class="cfg-hint">每天 04:00（服务器时间）对邮件渠道只做 SMTP 连接+登录（不发信），探测密码/授权码是否失效；失败按「渠道体检失败」事件告警</p>
        </div>
      </div>
    </div>

    <div class="cfg-section">
      <div class="cfg-section-hd">📧 邮件频率限制</div>
      <p class="cfg-section-sub">防止 Bug 导致邮件轰炸。0=不限制。</p>
      <div class="cfg-row">
        <div class="form-group"><label>全局-每小时上限</label><input id="cfg-email-global-h" type="number" value="${cfg.email_global_hourly_limit||100}" min="0" max="9999"></div>
        <div class="form-group"><label>全局-每天上限</label><input id="cfg-email-global-d" type="number" value="${cfg.email_global_daily_limit||500}" min="0" max="99999"></div>
      </div>
      <div class="cfg-row">
        <div class="form-group"><label>客户-每小时上限</label><input id="cfg-email-cust-h" type="number" value="${cfg.email_customer_hourly_limit||10}" min="0" max="9999"><p class="cfg-hint">每个客户独立计数</p></div>
        <div class="form-group"><label>客户-每天上限</label><input id="cfg-email-cust-d" type="number" value="${cfg.email_customer_daily_limit||50}" min="0" max="99999"><p class="cfg-hint">每个客户独立计数</p></div>
      </div>
    </div>

    <div class="cfg-section">
      <div class="cfg-section-hd">📤 通知联系信息</div>
      <p class="cfg-section-sub">用于在邮件正文与底部显示运维联系方式。由于邮件通常通过第三方 SMTP 渠道(如阿里云/腾讯企业邮箱)而非公司域名发送,客户收到邮件时可能误判为钓鱼邮件。配置后,客户可看到明确的发件人姓名、邮箱和联系电话。</p>
      <p class="cfg-section-sub text-amber-dark">⚠️ 姓名、邮箱、电话<strong>三项必须全部填写</strong>才会显示;任一为空则邮件中不展示该信息(避免半填给客户造成更大困惑)。</p>
      <div class="cfg-row">
        <div class="form-group"><label>工程师姓名</label><input id="cfg-ops-name" type="text" value="${cfg.ops_contact_name||''}" placeholder="如:张三" maxlength="50"></div>
        <div class="form-group"><label>工程师邮箱</label><input id="cfg-ops-email" type="email" value="${cfg.ops_contact_email||''}" placeholder="如:zhangsan@company.com" maxlength="100"></div>
      </div>
      <div class="cfg-row">
        <div class="form-group"><label>联系电话</label><input id="cfg-ops-phone" type="tel" value="${cfg.ops_contact_phone||''}" placeholder="如:138-0000-0000" maxlength="30"><p class="cfg-hint">邮件中会渲染为 tel: 链接,手机端可点击直接拨号</p></div>
      </div>
    </div>

    <div class="cfg-section">
      <div class="cfg-section-hd">🔔 系统事件通知</div>
      <p class="cfg-section-sub">系统异常时发送告警通知，与订阅规则推送独立</p>
      <div class="cfg-row">
        <div class="cfg-toggle">
          <label class="toggle-switch"><input id="cfg-event-enabled" type="checkbox" ${cfg.event_enabled?'checked':''} data-on-change="_setBlock" data-args='${_dataArgs('event-config-area', '$checked')}'><span class="toggle-slider"></span></label>
          <span>启用系统事件通知</span>
        </div>
      </div>
      <div id="event-config-area" class="${cfg.event_enabled?'':'hidden'}">
        <div class="cfg-row">
          <div class="form-group"><label>通知渠道</label>
            <select class="dd" id="cfg-event-channel">
              <option value="">-- 选择渠道 --</option>
              ${(cfg.channels||[]).filter(ch=>ch.type!=='email').map(ch=>`<option value="${ch.id}" ${cfg.event_channel_id==ch.id?'selected':''}>${escHtml(ch.name)} (${escHtml(ch.type)})</option>`).join('')}
            </select>
            <p class="cfg-hint">通知将发送到此渠道（需先在渠道管理中添加）。仅支持企微/钉钉/飞书：邮件渠道已不保存收件人，系统通知会发不出去</p>
          </div>
        </div>
        <div class="cfg-row">
          <div class="form-group"><label>事件类型</label>
            <div class="flex wrap gap8 mt6 nowrap">
              <label class="cfg-event-label"><input type="checkbox" class="cfg-event-type" value="push_summary" ${(cfg.event_types||[]).includes('push_summary')?'checked':''}> 推送汇总</label>
              <label class="cfg-event-label"><input type="checkbox" class="cfg-event-type" value="collection_summary" ${(cfg.event_types||[]).includes('collection_summary')?'checked':''}> 采集完成</label>
              <label class="cfg-event-label"><input type="checkbox" class="cfg-event-type" value="log_error" ${(cfg.event_types||[]).includes('log_error')?'checked':''}> 日志异常</label>
              <label class="cfg-event-label"><input type="checkbox" class="cfg-event-type" value="session_error" ${(cfg.event_types||[]).includes('session_error')?'checked':''}> Session异常</label>
              <label class="cfg-event-label"><input type="checkbox" class="cfg-event-type" value="channel_error" ${(cfg.event_types||[]).includes('channel_error')?'checked':''}> 渠道体检失败</label>
              <label class="cfg-event-label"><input type="checkbox" class="cfg-event-type" value="delivery_error" ${(cfg.event_types||[]).includes('delivery_error')?'checked':''}> 投递终止</label>
            </div>
            <p class="cfg-hint">推送汇总合并本 cycle 所有推送成功/失败详情(单条不再发);采集完成=cycle 末汇总;空=全部启用</p>
          </div>
        </div>
      </div>
    </div>

    ${isCollecting?
      '<p class="cfg-hint text-red mb12">采集进行中，系统配置无法直接修改。点击"强制停止并保存"将中断采集。</p>':
      ''}
    <button class="btn btn-primary" data-on-click="${isCollecting?'forceStopAndSave':'saveSettings'}">${isCollecting?'强制停止并保存':'保存配置'}</button>
  </div></div>
`;}

// 自动发现 banner — 仅在产品管理页面显示，支持手动关闭/重新打开
// 由 products() 和 MutationObserver 触发
let _discObs;
function _initDiscObserver(){
  if(_discObs) return;
  const app=document.getElementById('app');
  if(!app) return;
  _discObs=new MutationObserver(()=>{ _checkDiscBanner(); });
  _discObs.observe(app,{childList:true});
}
function _checkDiscBanner(){
  // 只在产品管理页面处理
  const isProductsPage=document.getElementById('disc-area')||document.querySelector('[data-page="products"]');
  if(!isProductsPage) return;
  // 每次进入产品页都清除隐藏状态，保证切页回来能重新显示
  window._discBannerHidden=false;
  window._discTimer && clearInterval(window._discTimer);
  api('/system/products/discover/status').then(s=>{
    if(s.code===0 && s.data?.active){
      _showDiscBanner(s.data);
    } else if(s.code===0 && s.data?.phase==='done' && (window._discLastDone || s.data.result)){
      // 页面刷新后 window._discLastDone 丢失，优先用 is_stale 标记提示用户
      if(s.data.is_stale){
        // 服务重启恢复：保存结果到内存，按钮变为待确认状态，等用户点击按钮再显示弹窗
        window._pendingDiscResult=s.data.result;
        window._pendingDiscStale=true;
        updateNavPendingBadge();
      } else if(window._discLastDone){
        showDiscoverResult(window._discLastDone);
      }
      window._discLastDone=null;
    }
  });
}
function _hideDiscBanner(){
  clearInterval(window._discTimer);
  const b=document.getElementById('disc-banner');
  if(b) b.remove();
  // 显示一个小悬浮按钮，点击恢复 banner
  if(document.getElementById('disc-mini-btn')) return;
  const mini=document.createElement('div');
  mini.id='disc-mini-btn';
  mini.className='disc-mini-btn';
  mini.innerHTML='<span class="fs15">🔍</span><span>自动发现进行中…</span>';
  mini.onclick=async()=>{
    mini.remove();
    const s=await api('/system/products/discover/status');
    if(s.code===0 && s.data?.active) _showDiscBanner(s.data);
  };
  document.body.appendChild(mini);
}

// ── Collect banner (2026-08-21) ─────────────────────────────────────────
// 仿 _showDiscBanner 风格: 底部悬浮卡片 + 进度条 + 阶段文案, 替代旧的
// 顶部红条 (dashRunningBar / .collecting-warning)。 配色用 primary 蓝,
// 区别于 auto-discover 的紫色, 避免视觉混淆。
function _collectPhaseText(state) {
  // 阶段文案: current_product (banner 顶部 titleText 已经是 phase label "采集中",这里不重复)。
  // (done/total) 由 banner 模板统一拼接。
  const product = state.current_product || '';
  if (product) {
    return product;
  }
  // 没有 current_product (例如 init / phase 但产品名未传):fallback 到 phase label
  const phaseMap = {
    'init': '初始化',
    'collecting': '采集中',
    'detecting': '变更检测',
    'notifying': '推送通知',
    'done': '已完成',
    'error': '出错',
    'cancelled': '已取消',
  };
  return phaseMap[state.phase] || state.phase || '处理中';
}

function _collectBannerHtml(state) {
  // 纯函数: state → HTML 字符串 (不含 DOM 操作)。
  // pct 直接按 products_done / products_total 算 (0-100%), 不做 ETA 估算。
  const isDone = state.phase === 'done';
  const pct = isDone ? 100 :
    (state.products_total > 0 ?
      Math.min(100, Math.round((state.products_done / state.products_total) * 100)) : 0);
  const phaseText = _collectPhaseText(state);
  // 完成态用绿色渐变, 进行中用 primary 蓝渐变
  const titleText = isDone ? '✅ 采集完成' : '⏳ 采集中';
  // 2026-08-22: 旧顶部红条的核心提醒挪到 phase 行右边,不再占独立一行,避免
  // 撑高 banner 高度。完成时不显示。
  const warnSuffix = isDone ? '' :
    ' <span class="text-red-light fw600">⚠️ 请勿增删改配置</span>';
  return `
    <div class="flex1">
      <div class="fw600 mb6 lh14">${titleText}${warnSuffix}</div>
      <div class="opacity-90 fs12 lh15" id="collect-banner-phase">
        <span>${escHtml(phaseText)} (${state.products_done || 0}/${state.products_total || 0})</span>
      </div>
      <div class="banner-bar-3">
        <div id="collect-banner-bar" class="banner-bar-fill-white" data-pct="${pct}"></div>
      </div>
    </div>
    <div class="banner-chip pointer" data-on-click="_openCollectLog" data-stop>📋 日志</div>
    <div class="banner-close" data-on-click="_hideCollectBanner" data-stop title="关闭">×</div>`;
}

function _showCollectBanner(state) {
  // 渲染: 底部悬浮卡片, 进度条 + 阶段文案 + ETA + 日志按钮。
  // state 字段 (来自 /system/collect/progress.data.progress):
  //   phase, products_done, products_total, current_product, started_at, ...
  if (!state || !state.phase) return;
  let banner = document.getElementById('collect-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'collect-banner';
    banner.className = 'collect-banner';
    document.body.appendChild(banner);
    // 点击 banner (空白处) 跳转到仪表盘
    banner.addEventListener('click', (e) => {
      if (e.target.closest('[data-on-click]')) return;
      showPage('dashboard');
    });
  }
  // 根据 phase 切换背景色 (完成 → 绿色)
  banner.classList.toggle('done', state.phase === 'done');
  banner.innerHTML = _collectBannerHtml(state);
  _applyPctBars(banner);
}

function _hideCollectBanner() {
  const b = document.getElementById('collect-banner');
  if (b) b.remove();
}

function _openCollectLog() {
  // 跳转系统日志页面 + 自动定位最近 5min 采集日志 (复用既有日志查看器)
  location.hash = 'logs';
  logs();
  setTimeout(() => {
    const sel = document.querySelector('select'); // 日志文件选择
    if (sel && sel.value !== 'app.log') sel.value = 'app.log';
    const logBox = document.getElementById('logContent') || document.querySelector('[id^="log"]');
    if (logBox) logBox.scrollTop = logBox.scrollHeight; // 滚到底部 (最新日志)
  }, 200);
}

function _showDiscBanner(state){
  const existing=document.getElementById('disc-banner');
  if(existing) existing.remove();
  // phase 文案统一走 formatDiscPhase(支持并发 running_products 显示)
  const pct=state.phase==='done'?100:
    state.phase==='scanning_products'?5:
    state.phase==='discovering_pkg_types' && state.total?
      5+Math.round((state.progress/state.total)*90):0;
  const banner=document.createElement('div');
  banner.id='disc-banner';
  banner.className='disc-banner';
  banner.innerHTML=`
    <div class="flex-1">
      <div class="fw600-mb4">🔍 自动发现任务进行中</div>
      <div class="opacity-90 fs12" id="disc-banner-phase">${formatDiscPhase(state)}</div>
      <div class="banner-bar-3-tight">
        <div id="disc-banner-bar" class="banner-bar-fill-white" data-pct="${pct}"></div>
      </div>
    </div>
    <div class="banner-chip" data-on-click="_showDiscLogFloat" data-stop>📋 日志</div>
    <div class="banner-chip" data-on-click="_reopenDiscModal" data-stop>查看</div>
    <div class="banner-chip-lit pointer" data-on-click="_cancelDisc" data-stop title="停止自动发现(已完成产品丢弃)">⏹ 停止</div>
    <div class="banner-close" data-on-click="_hideDiscBanner" data-stop title="关闭">×</div>`;
  banner.onclick=_reopenDiscModal;
  document.body.appendChild(banner);
  _applyPctBars(banner);

  // ETA tracking — single timestamp shared with polling closure
  window._discStartTs = Date.now();

  let done=false;
  window._discTimer=setInterval(async()=>{
    if(done) return;
    const s=await api('/system/products/discover/status');
    if(s.code===0){
      const phase=document.getElementById('disc-banner-phase');
      const bar=document.getElementById('disc-banner-bar');
      // Phase text + ETA suffix (estimated time remaining)
      let phaseText = formatDiscPhase(s.data);
      if(s.data.phase==='discovering_pkg_types' && s.data.total && s.data.progress){
        const elapsedSec = (Date.now() - (window._discStartTs||Date.now())) / 1000;
        const ratio = s.data.progress / s.data.total;
        // Skip ETA in first 5s to avoid divide-by-tiny
        if(elapsedSec > 5 && ratio > 0.05){
          const remainSec = Math.round(elapsedSec * (1 - ratio) / ratio);
          if(remainSec > 0 && remainSec < 3600){
            phaseText += ` · 约剩 ${remainSec<60?remainSec+'秒':Math.round(remainSec/60)+'分钟'}`;
          }
        }
      }
      if(phase) phase.textContent=phaseText;
      if(bar){
        const p=s.data.phase==='done'?100:
          s.data.phase==='scanning_products'?5:
          s.data.phase==='discovering_pkg_types' && s.data.total?
            5+Math.round((s.data.progress/s.data.total)*90):0;
        bar.style.width=p+'%';
      }
      // 更新浮窗日志（如果已创建）
      const discLogId=window.__discLogId||'disc-float-log';
      const logBody=document.getElementById(discLogId+'-body');
      if(logBody && s.data.log_lines?.length){
        const renderLogLine=l=>{
          const txt=escHtml(l);
          if(l.startsWith('🔍')) return `<div class="rd-ver-line text-primary">${txt}</div>`;
          if(l.startsWith('📦')&&l.includes('发现包类型')) return `<div class="rd-ver-line text-purple fw600">${txt}</div>`;
          if(l.startsWith('  →')) return `<div class="rd-ver-line text-cyan">${txt}</div>`;
          if(l.startsWith('✅')||l.startsWith('✔')) return `<div class="rd-ver-line text-success-text fw600">${txt}</div>`;
          if(l.startsWith('❌')||l.startsWith('⚠️')) return `<div class="rd-ver-line text-danger">${txt}</div>`;
          if(l.startsWith('[')) return `<div class="rd-ver-line text-disabled">${txt}</div>`;
          return `<div class="rd-ver-line text-gray-muted">${txt}</div>`;
        };
        logBody.innerHTML=s.data.log_lines.map(renderLogLine).join('');
        logBody.scrollTop=logBody.scrollHeight;
      }
      if(s.data.phase==='done'){
        done=true;clearInterval(window._discTimer);
        const banner=document.getElementById('disc-banner');
        if(banner){banner.querySelector('#disc-banner-phase').textContent='✅ 发现完成 — 点击查看结果';banner.querySelector('#disc-banner-bar').style.width='100%';banner.classList.add('done');}
        window._discLastDone=s.data.result;
        const mini=document.getElementById('disc-mini-btn');if(mini)mini.remove();
        setTimeout(()=>{const b=document.getElementById('disc-banner');if(b)b.remove();_reopenDiscModal();},500);
      } else if(s.data.phase==='cancelled'){
        done=true;clearInterval(window._discTimer);
        toast('自动发现已取消','warn');
        const b=document.getElementById('disc-banner');if(b)b.remove();
        const mini=document.getElementById('disc-mini-btn');if(mini)mini.remove();
      } else if(s.data.phase==='error'){
        done=true;clearInterval(window._discTimer);
        toast('自动发现失败: '+(s.data.result?.error_message||s.data.result?.error||''),'error');
        const b=document.getElementById('disc-banner');if(b)b.remove();
        const mini=document.getElementById('disc-mini-btn');if(mini)mini.remove();
      }
      // 跨 tab 广播:任一终态通知其他 tab 刷新 UI
      if(done){try{localStorage.setItem('nsm_disc_status_update', String(Date.now()));}catch(e){}}
    }
  },2000);
}

async function products(){
  const _seq=_navSeq;
  // 检查是否有待确认的发现结果（来自temp file）
  let pendingDiscResult = null;
  console.log('[products] checking discover status...');
  try {
    const s=await api('/system/products/discover/status');
    console.log('[products] discover status result:', JSON.stringify(s));
    if(s.code===0 && s.data?.phase==='done' && s.data?.result && !s.data?.active){
      pendingDiscResult = s.data.result;
      window._pendingDiscResult = s.data.result;
      window._pendingDiscStale = !!s.data.is_stale;
      updateNavPendingBadge();
    }
  } catch(e){ console.log('[products] discover status error:', e); }
  const r=await api('/system/products');
  const allProds=r.data?.products||[];
  window._allProducts=allProds;

  const total=allProds.length;
  const enabled=allProds.filter(p=>p.is_active).length;
  const disabled=allProds.filter(p=>!p.is_active).length;
  const errors=allProds.filter(p=>p.health_status==='error').length;

  let h=`<div class="card">
    <div class="card-hd flex items-center justify-between">
      <div class="flex items-center gap4">
        🏭 产品管理
        <span class="tp" data-tip="自动扫描绿盟升级站点，发现并添加新产品的入口页面">ⓘ</span>
      </div>
      <div class="flex items-center gap8">
        <button class="btn btn-sm disc-btn${pendingDiscResult?' disc-btn-pending':''}" id="disc-btn" data-on-click="_handleDiscBtn">${pendingDiscResult?'⚠️ 待确认':'🔍 自动发现'}</button>
        <span class="tp" data-tip="自动扫描绿盟更新站点:\n• 用途:发现新增/下线产品 + 重新发现每个产品的包类型\n• 场景:首次接入系统 / 绿盟上线新产品 / 没找到自己想要的产品时全量扫描(\u{1F50D})或单产品刷新(\u{1F504})\n• 耗时:长任务(数分钟到十几分钟,跟现有产品数相关),可中途取消\n• 注意:后台每 4 小时自动 quick cycle,无需手动定期刷;立刻抓取绿盟站点,确认发现 Session 有效">ⓘ</span>
        <button class="btn btn-sm tag-info-soft" data-on-click="showProductForm">+ 手动添加</button>
        <span class="tp" data-tip="手动录入产品:\n• 用途:自动发现没扫到的产品(自定义/灰度/私有/内部测试)\n• 建议:绿盟标准产品 — 用「自动发现」批量加\n• 不推荐:已有同名产品(去重检查会失败)\n• 下一步:保存后弹窗问是否立即刷新包类型">ⓘ</span>
      </div>
    </div>
    <div class="card-bd">
      <div class="flex items-center gap8 mb10 fs13">
        <span>共 <strong>${total}</strong> 个</span>
        <span class="text-success-text">● 启用 <strong>${enabled}</strong></span>
        <span class="text-disabled">○ 禁用 <strong>${disabled}</strong></span>
        ${errors>0?'<span class="text-danger">● 异常 <strong>'+errors+'</strong></span>':''}
        <span class="tp" data-tip="采集状态反映上次刷新包类型的结果：正常=成功，异常=失败，未知=从未刷新过">ⓘ 采集状态</span>
        <span class="disabled-12 ml8" id="prod-sel-count"></span>
        <button class="btn btn-sm btn-flat-success" id="btn-batch-enable" data-on-click="batchEnable" disabled>批量启用</button>
        <button class="btn btn-sm btn-flat-danger" id="btn-batch-disable" data-on-click="batchDisable" disabled>批量禁用</button>
        <div class="ml-auto flex items-center gap8">
          <input class="input-sm" id="prod-search" placeholder="搜索产品…" data-on-input="filterProducts">
          <select id="prod-filter" class="sel-sm dd" data-on-change="filterProducts">
            <option value="all">全部</option>
            <option value="enabled">仅启用</option>
            <option value="disabled">仅禁用</option>
          </select>
        </div>
      </div>
      <div class="ox-auto">
      <table class="table w-full">
        <thead><tr>
          <th class="w36">#</th>
          <th class="w40"><input class="pointer" type="checkbox" id="prod-sel-all" data-on-change="toggleAllProds" data-args='${_dataArgs('$checked')}' title="全选/取消全选"></th>
          <th class="w64">状态</th>
          <th>产品名称</th>
          <th>入口URL</th>
          <th>包类型</th>
          <th class="w76" title="正常=上次采集成功，异常=上次采集失败，未知=从未采集">采集状态</th>
          <th class="w100">最近采集</th>
          <th>操作</th>
        </tr></thead>
        <tbody id="prod-tbody">`;
  h+=_renderProductRows(allProds);
  h+=`</tbody></table></div></div></div>`;
  h+=`<div id="disc-area"></div>`;
  h+=`<div id="pform-area"></div>`;
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=h;
  _initDiscObserver();
}

function _showDiscLogFloat(){
  let discLogId=window.__discLogId||'disc-float-log';
  let el=document.getElementById(discLogId);
  if(!el){
    // 动态创建（banner显示时disc-float-log可能还不存在）
    el=_createFloatLog(discLogId, '📋 自动发现日志');
    document.body.appendChild(el);
    window.__discLogId=discLogId;
  }
  el.classList.remove('hidden');
}

function _viewDiscResult(stale){
  closeModal('disc');
  showDiscoverResult(window._discLastDone||{}, !!stale);
}

function _reopenDiscModal(){
  clearInterval(window._discTimer);
  const b=document.getElementById('disc-banner');
  if(b) b.remove();
  api('/system/products/discover/status').then(s=>{
    if(s.code===0 && (s.data.active||s.data.phase==='done')){
      _showModal('disc');
      const logId=window.__discLogId||'disc-float-log';
      modalBody('disc',`<div class="p24 ta-center pos-rel">
        <div class="dialog-x-abs" data-on-click="closeModal" data-args='${_dataArgs('disc')}'>×</div>
        <div class="fs16 fw600 text-purple mb16">${formatDiscPhase(s.data)}</div>
        <div class="gray-muted-13 mb12">${s.data.active?'任务运行中，可关闭此窗口':'发现完成'}</div>
        <div class="mt10"><button class="btn btn-purple-outline" data-on-click="_showById" data-args='${_dataArgs(logId)}'>📋 查看日志</button></div>
        ${s.data.phase==='done'?`<div class="mt12"><button class="btn btn-primary" data-on-click="_viewDiscResult" data-args='${_dataArgs(!!s.data.is_stale)}'>查看结果</button></div>`:''}
      </div>`);
      if(s.data.phase==='done'&&s.data.result){
        setTimeout(()=>showDiscoverResult(s.data.result, !!s.data.is_stale),100);
      }
    }
  });
}

// Floating modal shell
function _showModal(id){
  closeModal(id); // 防止重复创建
  // disc 弹窗用紫色主题(背景白+边框浅紫+紫色阴影)在浅蓝灰环境上才看得清
  // 其他 modal(pkgtype/pform)保持默认
  const boxClass = id === 'disc' ? 'modal-box-disc' : 'modal-box';
  document.getElementById('app').insertAdjacentHTML('beforeend',
    `<div class="overlay-1000" id="${id}-overlay" data-on-click="closeModal" data-self data-args='${_dataArgs(id)}'>`+
    `<div id="${id}-box" class="${boxClass}"></div>`+
    `</div>`);
}
function closeModal(id){
  const o=document.getElementById(id+'-overlay');
  const b=document.getElementById(id+'-box');
  if(o)o.remove();
  if(b)b.remove();
}
function modalBody(id,h){const b=document.getElementById(id+'-box');if(b)b.innerHTML=h;}
function modalFooter(id,btns){const b=document.getElementById(id+'-box');if(b){const existing=b.querySelector('.modal-ft');if(existing)existing.remove();b.insertAdjacentHTML('beforeend',`<div class="modal-ft">${btns}</div>`);}}

function _hlthClass(s){return{'正常':'text-success-text','异常':'text-danger','未知':'text-gray-muted'}[s]||'text-gray-muted';}
function _hlthLabel(s){return{'ok':'正常','error':'异常','unknown':'未知'}[s]||'未知';}

function fmtPkgTypes(v){
  if(!v) return '';
  let types = v;
  try { types = JSON.parse(v); } catch {}
  if (!types) return '';
  // 新格式: {types:[...], paths:[...], modes:{}}
  if (types.types) return _groupPkgTypes(types);
  // 旧格式: [...]
  if (Array.isArray(types)) return _fmtOldTypes(types);
  return '';
}
