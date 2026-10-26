// Static hosting adapter: shared public news; browser-local personal watchlists.
(() => {
  const nativeFetch = window.fetch.bind(window);
  let snapshot;
  const read = key => { try {return JSON.parse(localStorage.getItem('maixun:'+key)||'[]');}catch{return [];} };
  const write = (key,value) => localStorage.setItem('maixun:'+key,JSON.stringify(value));
  window.fetch = async (input, options={}) => {
    if (typeof input !== 'string' || !input.startsWith('/api/')) return nativeFetch(input,options);
    const respond = (value,status=200) => new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
    try {
      const body=JSON.parse(options.body||'{}');
      if (input === '/api/state') {
        if (!snapshot) {
          const response=await nativeFetch('./data.json',{cache:'no-store'});
          if(!response.ok)throw Error('日报数据暂不可用');
          snapshot=await response.json();
        }
        const saved=read('saved'), companies=read('companies');
        return respond({...snapshot,companies,articles:snapshot.articles.map(a=>({...a,saved:saved.includes(a.id)?1:0}))});
      }
      if (input === '/api/collect') {
        // A public browser cannot dispatch authenticated GitHub Actions jobs.
        snapshot=null;
        return respond({ok:true});
      }
      if (input === '/api/company') {
        let companies=read('companies');
        const name=String(body.name||'').trim();
        if(!name||name.length>80)return respond({error:'企业名称需要 1–80 个字符'},400);
        companies=body.remove?companies.filter(n=>n!==name):[...new Set([name,...companies])];
        write('companies',companies);
        return respond({ok:true});
      }
      if (input === '/api/save') {
        let saved=read('saved');
        saved=body.saved?[...new Set([...saved,body.id])]:saved.filter(id=>id!==body.id);
        write('saved',saved);
        return respond({ok:true});
      }
      return respond({error:'公开资讯版暂不支持此操作'},400);
    } catch(e) {return respond({error:e.message},500);}
  };
  window.addEventListener('DOMContentLoaded',()=>{
    document.getElementById('collect').textContent='查看更新';
    document.querySelector('.workspace span').textContent='公开资讯版';
  });
})();
