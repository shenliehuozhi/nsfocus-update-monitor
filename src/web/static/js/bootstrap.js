// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// Init
document.querySelectorAll('.nav a').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();showPage(a.dataset.page);}));
// 凭证在 HttpOnly Cookie 里，页面刷新后没有 localStorage 也要先问 /auth/me。
// 旧标签页遗留的 Bearer token 由服务端换发 Cookie，成功后本地副本立即清除。
api('/auth/me').then(r=>{
  localStorage.removeItem('token');T=null;
  if(r.code===0){U=r.data;document.getElementById('unameText').textContent=U.username;document.getElementById('header').classList.remove('hidden');showPage('dashboard');_refreshVersionBadge();}
  else renderLogin();
});

// hook checkbox change to update UI
document.addEventListener('change',e=>{if(e.target.classList.contains('prod-cb'))_updateBatchUI();});
_initDiscObserver();
