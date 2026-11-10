// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// ── About Modal — version identification ──
let _aboutInfo = null;
async function showAboutModal(){
  let m=document.getElementById('aboutModal');
  if(!m){
    m=document.createElement('div');m.id='aboutModal';
    m.innerHTML=`
      <div class="modal-overlay" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.4);z-index:999;display:flex;align-items:center;justify-content:center" onclick="if(event.target===this)closeAboutModal()">
        <div class="card" style="width:480px;max-width:92vw">
          <div class="card-hd">ⓘ 关于 nsfocus-monitor <span onclick="closeAboutModal()" style="float:right;cursor:pointer;color:var(--gray-muted);font-size:18px;line-height:1">×</span></div>
          <div class="card-bd" id="aboutBody" style="font-size:13px">
            <div style="color:var(--gray-muted);text-align:center;padding:24px">加载中…</div>
          </div>
          <div class="card-bd" style="border-top:1px solid var(--gray-border);padding-top:12px;display:flex;justify-content:flex-end;gap:8px">
            <button class="btn btn-primary" onclick="copyAboutToClipboard()">复制全部信息</button>
            <button class="btn" onclick="closeAboutModal()">关闭</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(m);
  }
  // Bug fix: 之前写 `if(m.style.display!=='none') m.style.display=''` 是反逻辑,
  // 导致第一次关闭后 m.style.display='none',第二次点击时 if 条件命中(已经是 none
  // 之外的别的?) 实际语义是 "如果不是 none 才显示" → 永远不显示。改成无条件
  // 强制 display=''(跟 showPwdModal 同款)。
  m.style.display='';
  // 每次打开都拉一次(防止服务重启后 info 变)
  try{
    const r=await api('/system/version');
    if(r.code===0){
      _aboutInfo=r.data;
      document.getElementById('aboutBody').innerHTML=renderAboutBody(r.data);
    } else {
      document.getElementById('aboutBody').innerHTML='<div style="color:var(--danger);padding:12px">加载失败: '+escHtml(r.message||JSON.stringify(r))+'</div>';
    }
  }catch(e){
    document.getElementById('aboutBody').innerHTML='<div style="color:var(--danger);padding:12px">网络错误: '+escHtml(String(e))+'</div>';
  }
}

function closeAboutModal(){
  const m=document.getElementById('aboutModal');
  if(m) m.style.display='none';
}

function renderAboutBody(info){
  // 渲染一个清晰的"版本身份证"视图
  const rows = [];
  rows.push(['应用', info.app_name || 'nsfocus-monitor']);
  rows.push(['版本标识', info.commit || 'unknown']);
  if(info.source==='git'){
    rows.push(['Git 分支', info.branch || '?']);
    rows.push(['源码状态', info.dirty ? '⚠️ 有未提交修改 (dirty)' : '✓ 干净 (clean)']);
  } else if(info.source==='exe'){
    rows.push(['运行模式', 'PyInstaller 打包 (onefile)']);
    rows.push(['exe 路径', info.exe_path || '?']);
    if(info.exe_mtime) rows.push(['exe 构建时间', fmtAboutUTC(info.exe_mtime)]);
    if(info.exe_size) rows.push(['exe 大小', (info.exe_size/1024/1024).toFixed(2) + ' MB']);
  } else {
    rows.push(['来源', 'unknown — 未检测到 .git 也无法读取 exe 元数据']);
  }
  rows.push(['运行模式', info.frozen ? '打包 (frozen)' : '开发 (python run.py)']);
  rows.push(['Python', info.python || '?']);
  rows.push(['操作系统', info.platform || '?']);
  rows.push(['数据目录', info.data_dir || '?']);
  rows.push(['进程启动', fmtAboutUTC(info.process_start)]);
  rows.push(['获取时间', fmtAboutUTC(new Date().toISOString())]);
  return `<table style="width:100%;border-collapse:collapse">
    ${rows.map(([k,v])=>`<tr>
      <td style="padding:6px 8px;color:var(--gray-muted);white-space:nowrap;vertical-align:top;width:100px">${escHtml(k)}</td>
      <td style="padding:6px 8px;font-family:monospace;font-size:12px;word-break:break-all">${escHtml(String(v))}</td>
    </tr>`).join('')}
  </table>
  <div style="margin-top:12px;padding:8px 10px;background:var(--gray-bg);border-radius:6px;font-size:11px;color:var(--gray-muted)">
    💡 同一份可执行文件 / 同一次 commit 是同一版本,以上信息组合可唯一标识当前实例。
    报问题时把"复制全部信息"贴出来。
  </div>`;
}

function fmtAboutUTC(iso){
  if(!iso) return '?';
  try{
    // 服务端给的是 UTC ISO 字符串,展示时转本地时区(跟 fmtTZ 保持一致)
    const d=new Date(iso);
    if(isNaN(d.getTime())) return iso;
    const pad=n=>String(n).padStart(2,'0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }catch{return iso;}
}

async function copyAboutToClipboard(){
  if(!_aboutInfo) return;
  const text = Object.entries(_aboutInfo).map(([k,v])=>`${k}: ${v}`).join('\n');
  // 兜底 textarea 要先准备好,不论走哪条路都保证清理
  let _ta = null;
  try{
    await navigator.clipboard.writeText(`nsfocus-monitor version info\n${'='.repeat(40)}\n${text}\n`);
    toast('已复制到剪贴板', 'success');
    return;
  }catch(e){
    // HTTP 页面下 navigator.clipboard.writeText 抛 NotAllowedError,走兜底
    _ta = document.createElement('textarea');
    _ta.value = text;
    // 关键: 必须把 textarea 放在屏幕外 + 不可见,避免出现"左下角嵌入文本框"残留
    _ta.style.position = 'fixed';
    _ta.style.top = '0';
    _ta.style.left = '0';
    _ta.style.width = '1px';
    _ta.style.height = '1px';
    _ta.style.opacity = '0';
    _ta.style.pointerEvents = 'none';
    _ta.setAttribute('readonly', '');
    document.body.appendChild(_ta);
    _ta.select();
    try{
      const ok = document.execCommand('copy');
      if (ok) toast('已复制', 'success');
      else toast('复制失败: 浏览器拒绝', 'error');
    }catch(copyErr){
      toast('复制失败: ' + copyErr.message, 'error');
    }
  } finally {
    // Bug fix: 之前在 catch 块外用 document.body.removeChild(ta),但 ta 是 catch 块
    // 内 const 声明的 — 实际作用域没问题。但旧实现里 catch 内 execCommand 异常被
    // 直接吞掉 → 后续 removeChild 在异常栈下还能执行,理论上应该清掉。
    // 现实是: 某些浏览器(HTTP + iframe)下 appendChild 之后 ta 被自动绑到 viewport
    // 之外的位置,removeChild 找得到但 timing 出问题导致残留。改用 finally + ta 引用
    // 提前到外层 + 防御性 removeChild,保证不留 DOM 垃圾。
    if (_ta && _ta.parentNode) {
      try { _ta.parentNode.removeChild(_ta); } catch (_) { /* ignore */ }
    }
  }
}

// 启动时拉一次,只为了更新 header 上的 version badge
async function _refreshVersionBadge(){
  try{
    const r=await api('/system/version');
    if(r.code===0){
      const info=r.data;
      const commit=info.commit||'?';
      const dirty=info.dirty?'-dirty':'';
      const text=info.source==='git' ? `${commit}${dirty}` : (info.source==='exe' ? `exe@${commit}` : commit);
      const el=document.getElementById('versionBadge');
      if(el){ el.textContent=text; el.title=`${text}\n点击查看完整版本信息`; }
    }
  }catch{}
}

// ── Password Change Modal ──
function showPwdModal(){
  let m=document.getElementById('pwdModal');
  if(!m){
    m=document.createElement('div');m.id='pwdModal';
    m.innerHTML=`<div class="modal-overlay" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.4);z-index:999;display:flex;align-items:center;justify-content:center" onclick="if(event.target===this)closePwdModal()"><div class="card" style="width:380px;max-width:90vw"><div class="card-hd">🔒 修改密码</div><div class="card-bd"><div class="form-group"><label>旧密码</label><input id="pwdOld" type="password"></div><div class="form-group"><label>新密码</label><input id="pwdNew" type="password" placeholder="至少4位"></div><div class="form-group"><label>确认新密码</label><input id="pwdConfirm" type="password"></div><div class="flex gap8"><button class="btn btn-primary btn-sm" onclick="doChangePwd()">确认修改</button><button class="btn btn-sm btn-outline" onclick="closePwdModal()">取消</button></div></div></div></div>`;
    document.body.appendChild(m);
  }
  m.style.display='';
  document.getElementById('pwdOld').value='';
  document.getElementById('pwdNew').value='';
  document.getElementById('pwdConfirm').value='';
}
function closePwdModal(){let m=document.getElementById('pwdModal');if(m)m.style.display='none';}
async function doChangePwd(){
  let oldPw=document.getElementById('pwdOld').value,
      newPw=document.getElementById('pwdNew').value,
      cfm=document.getElementById('pwdConfirm').value;
  if(!oldPw||!newPw)return toast('请填写所有字段','error');
  if(newPw.length<4)return toast('新密码至少4位','error');
  if(newPw!==cfm)return toast('两次新密码不一致','error');
  let r=await api('/auth/password',{method:'PUT',body:JSON.stringify({old_password:oldPw,new_password:newPw})});
  if(r.code===0){toast('密码已修改');closePwdModal();}else toast(r.message,'error');
}
function toggleSection(key){
  const body=document.getElementById('sec-body-'+key);
  const icon=document.getElementById('sec-icon-'+key);
  if(!body)return;
  const open=body.style.display!=='none';
  body.style.display=open?'none':'block';
  icon.textContent=open?'▸':'▾';
}

async function confirmToggleActive(id,active,rowId){await confirmAct(active?'确认要启用该产品？':'确认要禁用该产品？', async ()=>{const r=await api(`/system/products/${id}`,{method:'PUT',body:JSON.stringify({is_active:active})});if(r.code===0){toast(active?'已禁用':'已启用');const row=document.getElementById('pr-'+id);if(row){row.style.opacity=active?'':'0.6';const badgeEl=row.querySelector('.active-badge');if(badgeEl){badgeEl.textContent=active?'● 启用':'● 禁用';badgeEl.style.color=active?'var(--success-text)':'var(--danger)';}const cb=row.querySelector('input[type="checkbox"]');if(cb)cb.checked=active;const btns=row.querySelectorAll('[data-action]');btns.forEach(b=>{const isActionBtn=b.tagName==='BUTTON'&&!b.getAttribute('onclick')?.startsWith('editProduct');b.disabled=!active;b.style.opacity=active?'':'0.35';b.style.cursor=active?'pointer':'not-allowed';});}else{await products();}}else{toast(r.message||'操作失败','error');}}, active?'⏸️':'▶️', active?'启用':'禁用');}
function _updateBatchUI(){
  const cbs=document.querySelectorAll('.prod-cb');
  const checked=document.querySelectorAll('.prod-cb:checked');
  const n=checked.length;
  const cnt=document.getElementById('prod-sel-count');
  if(cnt)cnt.textContent=n>0?`已选 ${n} 个`:'';
  const be=document.getElementById('btn-batch-enable');
  const bd=document.getElementById('btn-batch-disable');
  if(be){be.disabled=n===0;be.style.opacity=n===0?'0.4':'1';}
  if(bd){bd.disabled=n===0;bd.style.opacity=n===0?'0.4':'1';}
  const allCb=document.getElementById('prod-sel-all');
  if(allCb&&cbs.length>0)allCb.checked=n===cbs.length&&n>0;
}
function toggleAllProds(checked){
  document.querySelectorAll('.prod-cb').forEach(cb=>cb.checked=checked);
  _updateBatchUI();
}
async function batchEnable(){await batchAction('enable');}
async function batchDisable(){await batchAction('disable');}
async function batchAction(action){
  const checked=document.querySelectorAll('.prod-cb:checked');
  if(!checked.length){toast('请先选择产品','error');return;}
  const ids=[...checked].map(cb=>parseInt(cb.value));
  const label=action==='enable'?'启用':'禁用';
  await confirmAct(`确认要${label}选中的 ${ids.length} 个产品？`, async ()=>{
    const r=await api('/options/products/batch',{method:'POST',body:JSON.stringify({ids,action})});
    if(r.code===0){toast(`已${label} ${ids.length} 个产品`);await products();}
    else toast(r.message||'操作失败','error');
  }, action==='enable'?'▶️':'⏸️', label);
}
