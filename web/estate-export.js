/* Personal destinations and reviewed facts stay in this browser, never in the public release. */
const EstateExport = (() => {
  const storageKey='estate-comparison-profile-v1';
  const roles=[['groom_work','신랑 직주'],['bride_work','신부 직주'],['groom_family','신랑 본가'],['bride_family','신부 본가']];
  const empty=()=>({schema_version:1,destinations:roles.map(([id,label])=>({id,label,name:''})),departure:{day:'weekday',time:'08:00',mode:'transit'},facts:{},routes:{}});
  const text=v=>typeof v==='string'?v.trim().slice(0,500):'';
  const positive=v=>typeof v==='number'&&Number.isFinite(v)&&v>0?v:null;
  function validate(raw){
    if(!raw||raw.schema_version!==1||!Array.isArray(raw.destinations))throw Error('지원하는 개인 설정 파일이 아닙니다.');
    const out=empty();out.destinations=roles.map(([id,label])=>({id,label,name:text(raw.destinations.find(d=>d.id===id)?.name)}));
    // This export uses one explicit travel scenario; imports cannot silently change it.
    if(raw.departure && (raw.departure.mode!=='transit'||raw.departure.time!=='08:00'||raw.departure.day!=='weekday'))throw Error('이동 기준은 평일 오전 8시 출발·대중교통이어야 합니다.');
    for(const field of ['facts','routes'])if(raw[field]&&typeof raw[field]==='object'&&!Array.isArray(raw[field]))out[field]=raw[field];
    return out;
  }
  function load(){try{const raw=localStorage.getItem(storageKey);return raw?validate(JSON.parse(raw)):empty();}catch(_){return empty();}}
  function save(profile){const p=validate(profile);localStorage.setItem(storageKey,JSON.stringify(p));return p;}
  const key=({region:r,building:b})=>`${r.code}|${b.complex_key??b.key}`;
  function route(profile,item,id){
    const destination=profile.destinations.find(d=>d.id===id),record=profile.routes[key(item)]?.[id];
    if(!destination?.name||!record)return null;
    if(record.destination!==destination.name||record.mode!=='transit'||record.day!=='weekday'||record.departure_time!=='08:00'
      ||!/^\d{4}-\d{2}-\d{2}/.test(record.observed_at??'')||!/^https:\/\/(?:map|m\.map)\.naver\.com\//.test(record.source_url??'')||!positive(record.minutes))return null;
    return record;
  }
  function facts(profile,item){const f=profile.facts[key(item)];return f&&(text(f.source_file)||(text(f.source_url)&&/^https:\/\//.test(f.source_url)))&&/^\d{4}-\d{2}-\d{2}/.test(f.observed_at??'')?f:{};}
  function column(label,width=14,style=0){return {label,width,style};}
  function sheets(items,options={}){
    const profile=validate(options.profile??empty()),year=String(options.year??''),currentYear=Number(options.currentYear)||new Date().getFullYear();
    const recFor=options.recommendationFor??(()=>null);
    const columns=[column('번호',7),column('단지',26),column('주소',39),column('전용(㎡)',12,2),column('최근90일 실거래(억)',19,2),column('기준가·중앙값(억)',19,2),column('하위10% 가격(억)',18,2),column('상위10% 가격(억)',18,2),column('매물 최저호가(억)',19,2),column('호가 평형 범위',24),column('세대수',10),column('준공년도',11),column('연차',8),column('세대당 주차',13,2),column('용적률(%)',13,3),column('건폐율(%)',13,3),column('대지지분(㎡)',15,2),column('근접역',16),column('역 도보(분)',13,4),column('초품아',10),column('지하주차장',14),column('승강기 연결',14),column('커뮤니티',24),column('정비사업',19),...roles.map(([,label])=>column(label+'(분)',14,4)),column('임장',10,5),column('메모',40,5),column('단지 정보',16)];
    const notes=[['작성 기준 연도',currentYear],['실거래 집계 연도',year],['자료 마감일',options.dataThrough??''],['내려받은 시각',new Date().toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})+' (한국시간)'],['이동 기준','평일 오전 08:00 출발 · 대중교통 · 아파트에서 목적지까지'],...profile.destinations.map(d=>[d.label,d.name||'']),['실거래 출처','https://rt.molit.go.kr/'],['필터 조건',options.filterText??'현재 화면 필터'],['결과 범위',`${new Set(items.map(key)).size}개 단지 · ${items.length}개 전용면적 · 필터 결과 전체`],['실거래·호가','최근90일 실거래는 해당 전용면적의 중앙값입니다. 매물 호가는 실거래와 구분하며 자동 채우기에서 확인한 공급평형 범위를 함께 적습니다. 호가가 비어 있으면 매물이 없는지 미조회인지 출처에서 확인하세요.'],['분위수 가격','P10·P50·P90은 직접 학습·검증한 모델에 한해 표시합니다. P10~P90은 거래가격 분포의 가운데 80% 구간이며 증여·급매 여부를 판정하지 않습니다. 기존 모델의 대칭 오차 범위는 분위수 칸에 넣지 않습니다.'],['초품아','현 모델의 초등학교 500m 내 지표입니다. 단지 내 학교·배정학교·안전한 통학로를 뜻하지 않습니다.'],['이동 시간','네이버 지도 평일 08:00 출발의 추천 최적 경로입니다. 최초 대기를 포함해 08:00부터 도착까지 계산합니다. 빈 칸은 0분이 아닙니다.'],['대지지분','해당 평형의 확인값만 기록합니다. 단지 대지면적÷세대수로 대체하지 않습니다.'],['자동 채우기','사이트의 자동 채우기 받기를 내려받고 실행하세요. 주소·면적이 맞는 공개 단지 자료를 조회해 별도 완성 파일로 저장합니다. 사이트에 없는 정보·접근 제한은 이 시트에 남깁니다.'],['임장·메모','처음에는 빈 칸입니다. 자동 채우기를 다시 실행해도 사용자가 적은 값은 보존합니다.'],['개인정보','목적지와 이동 경로는 이 엑셀과 현재 브라우저에만 보관됩니다. 공개 저장소에 포함되지 않습니다.']];
    for(const [id,label] of roles){
      const destination=profile.destinations.find(d=>d.id===id)?.name;
      const template=Object.values(profile.routes).map(v=>v?.[id]).find(v=>v&&v.destination===destination&&v.mode==='transit'&&v.day==='weekday'&&v.departure_time==='08:00'&&/^https:\/\/map\.naver\.com\/p\/directions\//.test(v.source_url??''));
      if(template)notes.push(['네이버 목적지 · '+label,{value:template.source_url,href:template.source_url}]);
    }
    const rows=items.map((item,i)=>{
      const b=item.building,r=item.region,f=facts(profile,item),rec=recFor(item),v=rec?.current_valuation?.status==='available'?rec.current_valuation:null,c=rec?.valuation_comparison?.status==='available'?rec.valuation_comparison:null,direct=v?.quantiles?.status==='available'?v.quantiles.prices_billion:null;
      const area=Number(b.area_type?.match(/([\d.]+)\s*㎡/)?.[1]),complex=b.complex_key??b.key.split(' | ')[0];
      const localAddress=complex.endsWith(' '+b.building_name)?complex.slice(0,-b.building_name.length-1):`${r.gu_name} ${r.dong_name}`;
      const address=`${r.sido_name} ${localAddress}`,households=positive(f.households)??b.households??null,parking=positive(f.parking_spaces),share=f.land_shares?.[b.key];
      const shareValid=share&&positive(share.area_m2)&&/^https:\/\//.test(share.source_url??'');
      const infoUrl=f.source_url||'https://map.naver.com/p/search/'+encodeURIComponent(address+' '+b.building_name);
      const sources=[];
      if(f.source_url||f.source_file)sources.push(`시설: ${f.source_url||f.source_file} · 자료 ${f.source_date??''} · 확인 ${f.observed_at??''}`);
      if(shareValid)sources.push(`대지지분: ${share.source_url}`);
      const routes=roles.map(([id,label])=>{const t=route(profile,item,id);if(t)sources.push(`${label}: ${t.source_url} · ${t.observed_at} · 08:00→${t.arrival_time??''} · ${t.minutes}분`);return t?.minutes??null;});
      if(sources.length)notes.push([`${i+1}. ${b.building_name} ${area}㎡`,sources.join('\n')]);
      const known=x=>x&&x!=='미확인'?x:null;
      return [i+1,b.building_name,address,positive(area),c?.comparison_price_billion??null,direct?.p50??v?.price_billion??null,direct?.p10??null,direct?.p90??null,null,null,households,b.built_year??null,b.built_year?{formula:`MAX(0,'기준 및 출처'!$B$2-L${i+2})`,result:Math.max(0,currentYear-b.built_year)}:null,parking&&households?parking/households:null,positive(f.floor_area_ratio),positive(f.building_coverage_ratio),shareValid?share.area_m2:null,b.subway_station??null,positive(f.station_walk_minutes),b.elementary_500m===true?'O':b.elementary_500m===false?'X':null,known(f.underground_parking),known(f.elevator_connection),known(f.community),known(f.association),...routes,null,null,{value:'정보 보기',href:infoUrl}];
    });
    return [{name:'임장 비교',columns,rows},{name:'기준 및 출처',freeze:1,columns:[column('항목',34),column('값·설명',110,7)],rows:notes}];
  }
  function panel(){const p=load();return `<details class="export-settings"><summary>엑셀 비교표 설정 · 평일 08:00 대중교통</summary><p>목적지는 이 브라우저에만 저장됩니다. 출처가 없는 이동 시간·용적률·대지지분은 엑셀에서 빈 칸으로 표시합니다.</p><div class="export-destinations">${p.destinations.map(d=>`<label><span>${d.label}</span><input id="export-${d.id}" value="${ResultPages.escape(d.name)}" placeholder="목적지 이름 또는 주소"/></label>`).join('')}</div><button type="button" id="export-save-profile">목적지 저장</button><label class="export-import">확인 자료 불러오기<input id="export-profile-file" type="file" accept="application/json,.json"/></label><p id="export-profile-status" role="status"></p></details>`;}
  function wire(){
    const host=document.getElementById('export-settings');host.innerHTML=panel();
    document.getElementById('export-save-profile').addEventListener('click',()=>{try{const p=load();for(const d of p.destinations)d.name=document.getElementById('export-'+d.id).value;save(p);document.getElementById('export-profile-status').textContent='목적지를 저장했습니다. 이동 시간은 조건이 일치하는 확인 자료만 사용합니다.';}catch(_){document.getElementById('export-profile-status').textContent='브라우저 저장 공간을 사용할 수 없습니다.';}});
    document.getElementById('export-profile-file').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>5000000)throw Error('설정 파일은 5MB 이하여야 합니다.');save(JSON.parse(await file.text()));wire();document.getElementById('export-profile-status').textContent='개인 목적지와 확인 자료를 불러왔습니다.';}catch(error){document.getElementById('export-profile-status').textContent=error.message;}finally{e.target.value='';}});
  }
  return {sheets,load,save,validate,route,key,wire,empty};
})();
