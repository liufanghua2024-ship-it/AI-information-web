let state = {articles: [], companies: [], runs: [], settings: {}}, view = 'daily', demo = false;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = s => s ? new Date(s).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '来源未提供发布日期';
const sample = [{id:'demo', title:'【虚构演示】示例生物完成新一轮融资，计划推进临床项目', url:'', source:'虚构演示 · 非真实新闻', published_at:new Date().toISOString(), collected_at:new Date().toISOString(), category:'融资与交易', roles:'临床运营 / 注册 / 医学事务', action:'核实融资用途、临床项目阶段及团队建设计划。', saved:0}];
function message(text, good=false) { $('notice').innerHTML = text ? `<div class="notice ${good?'success':''}">${esc(text)}</div>` : ''; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '请求失败');
  return data;
}
function empty(title, text) { return `<div class="empty"><div class="empty-icon">◎</div><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`; }
function card(a) {
  const demoItem = a.id === 'demo';
  return `<article class="article"><div class="article-top"><span class="tag">${esc(a.category)}</span><span>${esc(a.source)}</span><span>${esc(date(a.published_at))}</span></div><h3>${demoItem?esc(a.title):`<a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>`}</h3><span class="badge">${demoItem?'虚构演示':'来源单一 · 未核验原文'}</span><p><b>岗位方向（规则推断）：</b>${esc(a.roles)}</p><p><b>建议行动：</b>${esc(a.action)}</p><div class="article-actions">${demoItem?'<span>演示内容不会保存到数据库</span>':`<a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a><button class="subtle" data-save="${esc(a.id)}" data-saved="${a.saved?'false':'true'}">${a.saved?'★ 已收藏':'☆ 收藏线索'}</button>`}</div></article>`;
}
function render() {
  const names = {daily:'今日日报', companies:'企业关注', saved:'线索收藏', history:'历史追踪'};
  $('page-title').textContent = names[view];
  $('date-line').textContent = new Date().toLocaleDateString('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'long',day:'numeric',weekday:'long'}) + ' · 北京时间';
  document.querySelectorAll('[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  const recent = a => a.published_at && Date.now()-new Date(a.published_at).getTime() >= 0 && Date.now()-new Date(a.published_at).getTime() <= 86400000;
  $('stat-today').textContent = state.articles.filter(recent).length;
  $('stat-companies').textContent = state.companies.length;
  $('stat-saved').textContent = state.articles.filter(a=>a.saved).length;
  const run = state.runs[0];
  $('stat-status').textContent = state.collecting ? '采集中' : !run ? '尚未采集' : !run.finished_at ? '任务中断' : run.results.every(r=>r.ok) ? '采集完成' : run.results.some(r=>r.ok) ? '部分成功' : '采集失败';
  $('stat-time').textContent = run ? date(run.started_at) : '等待首次任务';
  $('collect').disabled = !!state.collecting;
  $('collect').textContent = state.collecting ? '↻ 采集中…' : state.hosting === 'github-pages' ? '↻ 刷新资讯' : '↻ 立即采集';
  const listView = ['daily','saved'].includes(view);
  $('controls').hidden = !listView;
  $('demo').hidden = !listView;
  $('demo').textContent = demo ? '退出界面演示' : '查看界面演示';
  $('section-title').textContent = {daily:'值得你关注的信号', saved:'你收藏的业务线索', companies:'建立你的企业雷达', history:'每一次采集，都有记录'}[view];
  $('section-description').textContent = {daily:'新闻线索与岗位建议分开呈现，所有推断均需进一步核实。',saved:'收藏保留原始来源与采集信息；不代表已确认的招聘委托。',companies:'按新闻标题中的企业名称匹配，建议使用常见简称或英文名。',history:'查看采集时间、来源状态和新增数量。重复 URL 不会重复入库。'}[view];
  if (view === 'companies') {
    $('content').innerHTML = `<form id="company-form" class="company-form"><input id="company-name" maxlength="80" placeholder="输入企业名称，例如：恒瑞、百济神州、AstraZeneca" aria-label="企业名称" required><button class="primary">添加关注</button></form>` + (state.companies.length ? state.companies.map(name=>`<div class="company-row"><div><b>${esc(name)}</b><p>已有 ${state.articles.filter(a=>a.title.toLowerCase().includes(name.toLowerCase())).length} 条标题匹配资讯</p></div><button class="subtle" data-remove="${esc(name)}">取消关注</button></div>`).join('') : empty('从你熟悉的企业开始','添加目标客户，随后在日报中开启“只看关注企业”。'));
    $('company-form').onsubmit = async e => {e.preventDefault(); await mutate('/api/company',{name:$('company-name').value});};
  } else if (view === 'history') {
    $('content').innerHTML = state.runs.length ? state.runs.map(r=>`<div class="run"><b>${esc(date(r.started_at))}</b><p>${r.finished_at?'已结束':'未结束（可能正在采集或上次服务中断）'} · 新增 ${r.added} 条</p><ul>${r.results.map(s=>`<li class="${s.ok?'':'error-text'}">${esc(s.source)}：${s.ok?`读取 ${s.found} 条，新增 ${s.added} 条`:'失败 — '+esc(s.error)}</li>`).join('')}</ul></div>`).join('') : empty('还没有采集记录','点击“立即采集”，任务结果会保存在这里。');
  } else {
    const text = $('search').value.trim().toLowerCase(), category=$('category').value, hours=$('period').value;
    let rows = demo ? sample : state.articles.filter(a=>view!=='saved'||a.saved);
    rows = rows.filter(a=>(!text||[a.title,a.roles,a.source].join(' ').toLowerCase().includes(text)) && (!category||a.category===category) && (hours==='all'||(a.published_at && Date.now()-new Date(a.published_at).getTime()>=0 && Date.now()-new Date(a.published_at).getTime()<=Number(hours)*3600000)) && (!$('followed').checked||state.companies.some(n=>a.title.toLowerCase().includes(n.toLowerCase()))));
    $('content').innerHTML = (demo?'<div class="demo-label">界面演示模式：以下事件完全虚构，不代表真实新闻或招聘需求。</div>':'') + (rows.length ? rows.map(card).join('') : empty(view==='saved'?'暂时没有匹配的收藏':'暂无匹配的情报', state.articles.length?'尝试扩大时间范围、调整关键词或关闭“只看关注企业”。':'点击“立即采集”获取公开 RSS。采集失败时请在历史追踪中查看原因；也可查看明确标注的界面演示。'));
  }
}
async function refresh() {
  try { state = await api('/api/state'); if(document.activeElement !== $('daily-time')) $('daily-time').value=state.settings.daily_time; render(); }
  catch(e) { message('无法读取服务状态：'+e.message); }
}
async function mutate(path, body) {try {await api(path,body); await refresh();} catch(e){message(e.message);} }
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{view=b.dataset.view;demo=false;render();});
['search','category','period','followed'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',render));
$('demo').onclick=()=>{demo=!demo;render();};
$('collect').onclick=async()=>{try{await api('/api/collect',{});message(state.hosting==='github-pages'?'已刷新资讯快照。后台每日北京时间 08:00 自动采集，可能延迟；可在历史追踪查看最近结果。':'采集已提交，请稍候。各来源的成功或失败都会显示在历史追踪。',true);await refresh();}catch(e){message(e.message);}};
$('schedule-form').onsubmit=async e=>{e.preventDefault();await mutate('/api/settings',{daily_time:$('daily-time').value});message('每日采集时间已保存（北京时间）。服务需要保持运行。',true);};
$('content').onclick=async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.save)await mutate('/api/save',{id:b.dataset.save,saved:b.dataset.saved==='true'});if(b.dataset.remove)await mutate('/api/company',{name:b.dataset.remove,remove:true});};
refresh();setInterval(()=>{if(state.collecting)refresh();},2000);setInterval(refresh,30000);
