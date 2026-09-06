const ResultPages=(()=>{
  const state=new Map();
  const escape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function paginate(rows,page=1,size=50){
    const pageCount=Math.max(1,Math.ceil(rows.length/size));page=Math.max(1,Math.min(pageCount,Math.floor(Number(page)||1)));
    const offset=(page-1)*size;return {rows:rows.slice(offset,offset+size),page,pageCount,offset,total:rows.length};
  }
  function view(id,rows,size=50){
    const r=paginate(rows,state.get(id),size);state.set(id,r.page);
    let nav=document.getElementById(id+"-pages");
    if(!nav){nav=document.createElement("nav");nav.id=id+"-pages";nav.className="result-pagination";nav.setAttribute("aria-label","결과 페이지");document.getElementById(id).insertAdjacentElement("afterend",nav);}
    nav.innerHTML=`<span>전체 ${r.total.toLocaleString("ko-KR")}개 · ${r.total?r.offset+1:0}–${Math.min(r.offset+size,r.total)}</span>
      <button data-page-list="${id}" data-page="1" ${r.page===1?"disabled":""}>처음</button>
      <button data-page-list="${id}" data-page="${r.page-1}" ${r.page===1?"disabled":""}>이전</button>
      <label><input type="number" min="1" max="${r.pageCount}" value="${r.page}" data-page-input="${id}" aria-label="이동할 페이지"> / ${r.pageCount}</label>
      <button data-page-list="${id}" data-page="${r.page+1}" ${r.page===r.pageCount?"disabled":""}>다음</button>
      <button data-page-list="${id}" data-page="${r.pageCount}" ${r.page===r.pageCount?"disabled":""}>마지막</button>`;
    let top=document.getElementById(id+'-pages-top');
    if(!top){top=document.createElement('nav');top.id=id+'-pages-top';top.className='result-pagination';top.setAttribute('aria-label','결과 페이지 (목록 위)');document.getElementById(id).insertAdjacentElement('beforebegin',top);}
    top.innerHTML=nav.innerHTML;
    return r;
  }
  function csvCell(value){let v=String(value??"");if(/^[=+@\-\t\r]/.test(v))v="'"+v;return '"'+v.replace(/"/g,'""')+'"';}
  function downloadCsv(name,headers,rows){
    const csv="\ufeff"+[headers,...rows].map(row=>row.map(csvCell).join(",")).join("\r\n");
    const url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return {paginate,view,escape,csvCell,downloadCsv,set:(id,page)=>state.set(id,page),reset:()=>state.clear()};
})();
