// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// ── Data · 推送弹窗 ──
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
        <div class="card-hd">📤 推送通知 <span data-css="color:var(--muted);font-weight:400;font-size:11px">${escHtml(product)} ${escHtml(version)} ${escHtml(pkgType)}</span><button class="btn btn-xs btn-outline ml-auto" data-on-click="closePushModal">×</button></div>
        <div class="card-bd" data-css="overflow-y:auto;flex:1">
          <div data-css="display:flex;gap:0;border-bottom:2px solid var(--border);margin-bottom:14px">
            <div data-on-click="switchPushTab" data-args='${_dataArgs('email')}' id="pushTabEmailBtn" data-css="flex:1;text-align:center;padding:10px;cursor:pointer;font-size:13px;font-weight:600;border-bottom:2px solid var(--primary);color:var(--primary);margin-bottom:-2px">📧 邮件发送</div>
            <div data-on-click="switchPushTab" data-args='${_dataArgs('bot')}' id="pushTabBotBtn" data-css="flex:1;text-align:center;padding:10px;cursor:pointer;font-size:13px;font-weight:600;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-2px">🤖 按渠道发送</div>
          </div>
          <div id="pushTabEmail">
            <div class="form-group">
              <label>发件渠道 (SMTP)</label>
              <div id="pushChannelSelect"><div class="empty p8">加载中...</div></div>
            </div>
            <div class="form-group">
              <label>收件人来源</label>
              <div class="flex gap6">
                <label class="pick-card"><input type="radio" name="prec" value="customer" checked data-on-change="switchRecipientMode" data-args='${_dataArgs('customer')}'> 👤 从客户选择</label>
                <label class="pick-card"><input type="radio" name="prec" value="manual" data-on-change="switchRecipientMode" data-args='${_dataArgs('manual')}'> ✏️ 自定义填写</label>
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
            <div id="pushBotChannelList" class="push-target"><div class="empty p8">加载中...</div></div>
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
  el.innerHTML='<select class="dd w-full" id="pushChannelDropdown" data-on-change="_pushChannelChanged" data-args=\'["$value"]\'>'
    +channels.map(c=>`<option value="${c.id}" ${c.id===defaultCh.id?'selected':''}>${escHtml(c.name)} (${escHtml(c.config?.smtp_host||'')}:${c.config?.smtp_port||''})</option>`).join('')
    +'</select>';
}

async function loadPushCustomerList(){
  const el=document.getElementById('pushCustomerList');
  if(!el) return;
  el.innerHTML='<div class="empty p8">加载中...</div>';
  try{
    const r=await api('/customers');
    const list=(r.data||[]).filter(x=>(x.email||'').trim());
    if(!list.length){
      el.innerHTML='<div class="empty p8 text-muted">暂无有邮箱的客户,请先在客户管理中设置邮箱</div>';
      return;
    }
    el.innerHTML=list.map(c=>`<label class="item" data-custid="${c.id}" data-css="display:flex;align-items:center;gap:8px;cursor:pointer;padding:8px 12px">
      <input class="shrink0" type="checkbox" data-custid="${c.id}" data-email="${escHtml(c.email)}" data-on-change="onCustomerCheck" data-args='${_dataArgs('$el')}'>
      <span class="flex1">
        <span class="fw500">${escHtml(c.name)}</span>
        <span class="muted-11-ml6">${escHtml(c.email)}</span>
        ${c.company?`<span class="muted-11-ml6">· ${escHtml(c.company)}</span>`:''}
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
    el.innerHTML='<div class="empty p8 text-muted">暂无可用渠道</div>';
    return;
  }
  const typeLabel={wecom:'企微',dingtalk:'钉钉',feishu:'飞书',email:'邮件',apprise:'Apprise'};
  el.innerHTML=list.map(c=>{
    const isEmail=c.type==='email';
    const sub=isEmail
      ? `${escHtml((c.config?.smtp_user)||'')} → 自己`
      : typeLabel[c.type]||c.type;
    return `<label class="item" data-botchid="${c.id}" data-css="display:flex;align-items:center;gap:8px;cursor:pointer;padding:10px 12px">
      <input class="shrink0" type="radio" name="botch" value="${c.id}" data-on-change="onBotChannelCheck" data-args='${_dataArgs('$el')}'>
      <span class="flex1">
        <span class="fw500">${escHtml(c.name)}</span>
        <span class="muted-11-ml6">${sub}</span>
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
      <span class="fw600">${icon}</span>
      <span>${escHtml(r.channel||'')}</span>
      ${r.error ? `<span data-css="color:rgba(255,255,255,.7);font-size:11px">${escHtml(r.error)}</span>` : ''}
      ${logBtn}
    </div>`;
  }).join('');
  const head = success
    ? `<div class="fw700 text-white mb4">推送完成</div>`
    : `<div class="fw700 text-white mb4">推送失败</div>`;
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
  m.innerHTML=`<div class="confirm-box confirm-box-lg">
    <div class="confirm-icon">📋</div>
    <div class="confirm-title">日志 - ${escHtml(name)}</div>
    <div class="muted-11 mb10">文件: ${escHtml(path)}</div>
    <pre class="log-viewer">${escHtml(content)}</pre>
    <div class="confirm-btns mt14">
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
