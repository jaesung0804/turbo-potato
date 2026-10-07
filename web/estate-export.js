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
    // A:V follows the user's comparison photograph. One physical complex per row.
    const columns=[column('번호',7),column('이름',27),column('위치',12),column('세대수',10),column('준공년도',11),column('연차',8),column('주차대수',11),column('세대당 주차',12,2),column('근접역',16),column('역까지거리(도보)',17,4),column('신랑직주',13,4),column('아내직주',13,4),column('강남역',12,4),column('20평대 호가',13,2),column('30평대 호가',13,2),column('40평대 호가',13,2),column('초교근접',11),column('지하주차장',13),column('승강기 연결',13),column('커뮤니티',25),column('조합여부',17),column('임장',9,5),column('용적률(%)',12,3),column('대지지분(㎡)',14,2),column('신랑 본가',13,4),column('신부 본가',13,4),column('조건 충족 전용(㎡)',25,7),column('모델 비교 전용(㎡)',17,2),column('가격 비교점수',14,2),column('최근90일 실거래(억)',19,2),column('중앙값 P50(억)',17,2),column('하위 P10(억)',16,2),column('상위 P90(억)',16,2),column('메모',32,5)];
    const groups=new Map();for(const item of items){const k=key(item);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(item);}
    const notes=[['작성 기준 연도',currentYear],['실거래 집계 연도',year],['자료 마감일',options.dataThrough??''],['내려받은 시각',new Date().toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})+' (한국시간)'],['이동 기준','평일 오전 08:00 출발 · 대중교통 · 아파트에서 목적지까지'],...profile.destinations.map(d=>[d.label,d.name||'']),['강남역','서울 강남역'],['실거래 출처','https://rt.molit.go.kr/'],['필터 조건',options.filterText??'현재 화면 필터'],['결과 범위',`${groups.size}개 단지 · ${items.length}개 전용면적 · 필터 결과 전체`],['사진 양식','A~V는 제공한 사진과 같은 열 순서입니다. 같은 지번의 단지는 한 행으로 묶습니다. 모형의 비교 가격·점수는 조건에 맞는 평형 중 점수가 가장 높은 전용면적 기준입니다. 모든 평형의 수치는 결과 CSV에서 확인할 수 있습니다.'],['호가 단위','억원. 20·30·40평대는 공급면적 평형대(각 20~29, 30~39, 40~49평)별 확인된 매물 최저호가입니다. 전용면적·실거래가·모델 추정가를 호가로 대체하지 않습니다.'],['분위수 가격','P10~P90은 학습한 거래가격 분포의 가운데 80% 범위입니다. 범위 밖이라는 이유로 증여·오류로 판정하지 않습니다.'],['초교근접','O/X는 모델의 초등학교 500m 내(초품아) 지표입니다. 배정학교나 실제 통학로를 보장하지 않습니다.'],['이동 시간','네이버 지도 평일 08:00 출발의 추천 최적 경로입니다. 최초 대기를 포함합니다.'],['대지지분','모델 비교 전용면적에 대해 확인한 값만 적습니다. 단지 대지면적÷세대수로 대체하지 않습니다.'],['임장·메모','직접 입력하는 빈 칸입니다. 자동 채우기로 덮어쓰지 않습니다.'],['자동 채우기','공개 단지의 실제 값만 입력합니다. 조회 불가·미제공 내용은 이 시트에 남깁니다. 목적지는 개인 엑셀과 브라우저에만 보관합니다.']];
    for(const [id,label] of roles){
      const destination=profile.destinations.find(d=>d.id===id)?.name;
      const template=Object.values(profile.routes).map(v=>v?.[id]).find(v=>v&&v.destination===destination&&v.mode==='transit'&&v.day==='weekday'&&v.departure_time==='08:00'&&/^https:\/\/map\.naver\.com\/p\/directions\//.test(v.source_url??''));
      if(template)notes.push(['네이버 목적지 · '+label,template.source_url]);
    }
    notes.push(['네이버 목적지 · 강남역','https://map.naver.com/p/directions/-/3zjKf0,2AJaFo,%EA%B0%95%EB%82%A8%EC%97%AD%202%ED%98%B8%EC%84%A0,222,SUBWAY_STATION/-/transit']);
    const rows=[...groups.values()].map((types,i)=>{
      const sorted=[...types].sort((a,b)=>(recFor(b)?.valuation_comparison?.score??-1)-(recFor(a)?.valuation_comparison?.score??-1)||a.building.key.localeCompare(b.building.key));
      const item=sorted[0],b=item.building,r=item.region,f=facts(profile,item),rec=recFor(item),v=rec?.current_valuation?.status==='available'?rec.current_valuation:null,c=rec?.valuation_comparison?.status==='available'?rec.valuation_comparison:null,q=v?.quantiles?.status==='available'?v.quantiles.prices_billion:null;
      const areaOf=x=>Number(x.building.area_type?.match(/([\d.]+)\s*㎡/)?.[1]);
      const area=areaOf(item),complex=b.complex_key??b.key.split(' | ')[0],localAddress=complex.endsWith(' '+b.building_name)?complex.slice(0,-b.building_name.length-1):`${r.gu_name} ${r.dong_name}`,address=`${r.sido_name} ${localAddress}`;
      const households=positive(f.households)??b.households??null,parking=positive(f.parking_spaces),share=f.land_shares?.[b.key],shareValid=share&&positive(share.area_m2)&&/^https:\/\//.test(share.source_url??'');
      notes.push([`단지 주소 · ${i+1}`,address]);
      if(f.source_url||f.source_file)notes.push([`시설 출처 · ${i+1}`,`${f.source_url||f.source_file} · ${f.observed_at??''}`]);
      if(shareValid)notes.push([`대지지분 출처 · ${i+1}`,share.source_url]);
      const routes=roles.map(([id,label])=>{const t=route(profile,item,id);if(t)notes.push([`이동 출처 · ${i+1} · ${label}`,`${t.source_url} · ${t.observed_at} · ${t.minutes}분`]);return t?.minutes??null;});
      const known=x=>x&&x!=='미확인'?x:null;
      return [i+1,b.building_name,r.dong_name,households,b.built_year??null,b.built_year?{formula:`MAX(0,'기준 및 출처'!$B$2-E${i+2})`,result:Math.max(0,currentYear-b.built_year)}:null,parking,parking&&households?{formula:`IFERROR(G${i+2}/D${i+2},"")`,result:parking/households}:null,b.subway_station??null,positive(f.station_walk_minutes),routes[0],routes[1],null,null,null,null,b.elementary_500m===true?'O':b.elementary_500m===false?'X':null,known(f.underground_parking),known(f.elevator_connection),known(f.community),known(f.association),null,positive(f.floor_area_ratio),shareValid?share.area_m2:null,routes[2],routes[3],[...new Set(types.map(areaOf))].filter(Number.isFinite).sort((a,b)=>a-b).join(', '),positive(area),c?.score??null,c?.comparison_price_billion??null,q?.p50??v?.price_billion??null,q?.p10??null,q?.p90??null,null];
    });
    return [{name:'임장 비교',template:true,columns,rows},{name:'기준 및 출처',freeze:1,columns:[column('항목',34),column('값·설명',110,7)],rows:notes}];
  }
  function panel(){const p=load();return `<details class="export-settings"><summary>엑셀 비교표 설정 · 평일 08:00 대중교통</summary><p>목적지는 이 브라우저에만 저장됩니다. 출처가 없는 이동 시간·용적률·대지지분은 엑셀에서 빈 칸으로 표시합니다.</p><div class="export-destinations">${p.destinations.map(d=>`<label><span>${d.label}</span><input id="export-${d.id}" value="${ResultPages.escape(d.name)}" placeholder="목적지 이름 또는 주소"/></label>`).join('')}</div><button type="button" id="export-save-profile">목적지 저장</button><label class="export-import">확인 자료 불러오기<input id="export-profile-file" type="file" accept="application/json,.json"/></label><p id="export-profile-status" role="status"></p></details>`;}
  function wire(){
    const host=document.getElementById('export-settings');host.innerHTML=panel();
    document.getElementById('export-save-profile').addEventListener('click',()=>{try{const p=load();for(const d of p.destinations)d.name=document.getElementById('export-'+d.id).value;save(p);document.getElementById('export-profile-status').textContent='목적지를 저장했습니다. 이동 시간은 조건이 일치하는 확인 자료만 사용합니다.';}catch(_){document.getElementById('export-profile-status').textContent='브라우저 저장 공간을 사용할 수 없습니다.';}});
    document.getElementById('export-profile-file').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>5000000)throw Error('설정 파일은 5MB 이하여야 합니다.');save(JSON.parse(await file.text()));wire();document.getElementById('export-profile-status').textContent='개인 목적지와 확인 자료를 불러왔습니다.';}catch(error){document.getElementById('export-profile-status').textContent=error.message;}finally{e.target.value='';}});
  }
  return {sheets,load,save,validate,route,key,wire,empty};
})();
