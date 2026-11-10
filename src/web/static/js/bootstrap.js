// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// Init
document.querySelectorAll('.nav a').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();showPage(a.dataset.page);}));
if(T){api('/auth/me').then(r=>{if(r.code===0){U=r.data;document.getElementById('unameText').textContent=U.username;document.getElementById('header').style.display='flex';showPage('dashboard');_refreshVersionBadge();}else{T=null;renderLogin();}});}else renderLogin();

// hook checkbox change to update UI
document.addEventListener('change',e=>{if(e.target.classList.contains('prod-cb'))_updateBatchUI();});
_initDiscObserver();
