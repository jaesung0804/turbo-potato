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
    const recFor=options.recommendationFor??(()=>null),groups=new Map();
    for(const item of items){const id=key(item);if(!groups.has(id))groups.set(id,{item,items:[]});groups.get(id).items.push(item);}
    const columns=[column('번호',7),column('이름',28),column('위치',30),column('세대수',11),column('준공년도',11),column('연차',8),column('주차대수',11),column('세대당 주차',12,2),column('근접역',19),column('역 도보(분·확인값)',17,4),...roles.map(([,label])=>column(label+'(분)',14,4)),column('강남역(분)',14,4),column('20평대 호가(억·입력)',19,5),column('30평대 호가(억·입력)',19,5),column('40평대 호가(억·입력)',19,5),column('초품아(500m 내)',18),column('지하주차장',15),column('승강기 연결',15),column('커뮤니티',24),column('조합여부',18),column('임장',10,5),column('용적률(%)',14,3),column('건폐율(%)',14,3),column('대지면적(㎡)',16,2),column('선택 평형 수',12),column('세대수 출처 상태',26),column('시설·이동 확인 상태',52),column('시설 출처',45),column('시설 자료 기준일',20),column('네이버 지도',36),column('단지 식별키',50)];
    const comparison=[];let index=0;
    for(const {item,items:types} of groups.values()){
      const b=item.building,r=item.region,f=facts(profile,item),row=index+2;index++;
      const households=positive(f.households)??b.households??null,parking=positive(f.parking_spaces);
      const routes=roles.map(([id])=>route(profile,item,id)),available=routes.filter(Boolean).length;
      const source=f.source_url?{value:f.source_url,href:f.source_url}:f.source_file??null;
      comparison.push([index,b.building_name,`${r.sido_name} ${r.gu_name} ${r.dong_name}`,households,b.built_year??null,
        b.built_year?{formula:`MAX(0,'기준 및 출처'!$B$2-E${row})`,result:Math.max(0,currentYear-b.built_year)}:null,
        parking,parking&&households?{formula:`IF(OR(D${row}="",G${row}=""),"",G${row}/D${row})`,result:parking/households}:null,
        b.subway_station??null,positive(f.station_walk_minutes),...routes.map(v=>v?.minutes??null),positive(f.gangnam_transit_minutes),null,null,null,
        b.elementary_500m===true?'O':b.elementary_500m===false?'X':'미확인',f.underground_parking??'미확인',f.elevator_connection??'미확인',f.community??'미확인',f.association??'미확인',null,
        positive(f.floor_area_ratio),positive(f.building_coverage_ratio),positive(f.land_area_m2),types.length,
        positive(f.households)?(f.source_label??'개인 확인 자료'):b.households_verified?'공식 주소 대조 완료':'기존 자료·범위 미검증',
        `시설 ${f.source_url||f.source_file?'출처 기록 있음':'미확인'} · 네이버 평일 08:00 ${available}/4곳 확인`,source,f.source_date??f.observed_at??null,
        {value:'단지 검색',href:'https://map.naver.com/p/search/'+encodeURIComponent(`${r.sido_name} ${r.gu_name} ${r.dong_name} ${b.building_name}`)},key(item)]);
    }
    const detailColumns=[column('번호',7),column('이름',28),column('위치',30),column('평형',26),column('전용면적(㎡)',15,2),column('집계연도',12),column('거래수',10),column('연간 중앙가(억)',18,2),column('최근90일 중앙가(억)',20,2),column('현재 기준가(억)',18,2),column('가격 비교점수',15,2),column('신뢰도',10),column('참고범위 하단(억)',20,2),column('참고범위 상단(억)',20,2),column('대지지분(㎡)',16,2),column('대지지분 출처',45),column('최근90일 건수',16),column('비교 기간',29),column('기준가 산출월',16),column('평형 식별키',58)];
    const details=items.map((item,i)=>{const b=item.building,r=item.region,rec=recFor(item),c=rec?.valuation_comparison?.status==='available'?rec.valuation_comparison:null,v=rec?.current_valuation?.status==='available'?rec.current_valuation:null,q=v?.confidence?.status==='available'?v.confidence:null,f=facts(profile,item),share=f.land_shares?.[b.key];
      const shareValid=share&&positive(share.area_m2)&&/^https:\/\//.test(share.source_url??'');
      const area=Number(b.area_type?.match(/([\d.]+)\s*㎡/)?.[1]);
      return [i+1,b.building_name,`${r.sido_name} ${r.gu_name} ${r.dong_name}`,b.area_type,positive(area),year,b.count,b.metrics?.price_billion?.median??null,c?.comparison_price_billion??null,v?.price_billion??null,c?.score??null,q?.grade??null,q?.lower_price_billion??null,q?.upper_price_billion??null,shareValid?share.area_m2:null,shareValid?{value:share.source_url,href:share.source_url}:null,c?.comparison_trade_count??null,c?.comparison_period??null,v?.month??null,`${r.code}|${b.key}`];});
    const notes=[['작성 기준 연도',currentYear],['실거래 집계 연도',year],['자료 마감일',options.dataThrough??'미확인'],['내려받은 시각',new Date().toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})+' (한국시간)'],['이동 기준','평일 오전 08:00 출발 · 대중교통 · 아파트에서 목적지까지'],...profile.destinations.map(d=>[d.label,d.name||'미설정']),['출처','https://rt.molit.go.kr/'],['필터 조건',options.filterText??'현재 화면 필터'],['결과 범위',`${comparison.length}개 단지 · ${details.length}개 평형 · 페이지 제한 없이 현재 필터 결과 전체`],['호가','20·30·40평대 호가는 직접 입력하는 빈 칸입니다. 공급면적대 기준이며 실거래가를 호가로 대체하지 않습니다.'],['실거래 가격','평형 상세 시트의 면적은 전용면적입니다. 연간 중앙가와 최근90일 중앙가는 다른 기간의 값입니다.'],['초품아','현 모델의 초등학교 500m 내 지표입니다. 단지 내 학교·통학구역·안전한 통학로를 확인한 뜻은 아닙니다.'],['이동 시간','네이버 지도에서 평일 08:00 대중교통 조건의 추천 최적 경로를 확인한 시간만 기록합니다. 도착 시각이 있으면 08:00부터 도착까지 최초 대기를 포함합니다. 네이버 표시시간은 근거 시트에서 따로 확인합니다. 빈 칸은 미조회이며 0분이 아닙니다.'],['역 도보','확인된 보행 경로 시간이 없으면 비웁니다. 직선거리를 이동 시간으로 바꾸지 않습니다.'],['용적률·주차·대지지분','출처와 확인일이 있는 자료만 사용합니다. 대지지분은 동일 평형의 확인값이며 대지면적÷세대수로 대체하지 않습니다.'],['임장','사용자가 직접 체크할 수 있도록 모든 행을 비워 둡니다.'],['가격 범위','현재 배포 모델의 오차 보정 참고 범위입니다. 직접 학습한 P10/P50/P90 퀀타일로 표시하지 않습니다.'],['개인 설정','목적지와 개인 확인 자료는 현재 브라우저에 저장되며 공개 사이트 데이터에 포함되지 않습니다.']];
    const routeRows=[];for(const {item} of groups.values())for(const [id,label] of roles){const v=route(profile,item,id);routeRows.push([item.building.building_name,label,profile.destinations.find(d=>d.id===id).name,'평일 08:00 출발','대중교통',v?.minutes??null,v?.naver_minutes??v?.minutes??null,v?.arrival_time??null,v?.observed_at??null,v?{value:v.source_url,href:v.source_url}:null,v?'조건 확인됨':'미조회',key(item)]);}
    return [{name:'단지 비교',columns,rows:comparison},{name:'평형 상세',columns:detailColumns,rows:details},{name:'이동 시간 근거',columns:[column('이름',28),column('목적',16),column('목적지',40),column('출발 기준',24),column('교통수단',14),column('08시부터 도착까지(분)',22,4),column('네이버 표시시간(분)',22,4),column('도착 시각',14),column('조회일시',25),column('네이버 출처',50),column('확인 상태',16),column('단지 식별키',55)],rows:routeRows},{name:'기준 및 출처',freeze:1,columns:[column('항목',25),column('값·설명',105,7)],rows:notes}];
  }
  function panel(){const p=load();return `<details class="export-settings"><summary>엑셀 비교표 설정 · 평일 08:00 대중교통</summary><p>목적지는 이 브라우저에만 저장됩니다. 출처가 없는 이동 시간·용적률·대지지분은 엑셀에서 빈 칸으로 표시합니다.</p><div class="export-destinations">${p.destinations.map(d=>`<label><span>${d.label}</span><input id="export-${d.id}" value="${ResultPages.escape(d.name)}" placeholder="목적지 이름 또는 주소"/></label>`).join('')}</div><button type="button" id="export-save-profile">목적지 저장</button><label class="export-import">확인 자료 불러오기<input id="export-profile-file" type="file" accept="application/json,.json"/></label><p id="export-profile-status" role="status"></p></details>`;}
  function wire(){
    const host=document.getElementById('export-settings');host.innerHTML=panel();
    document.getElementById('export-save-profile').addEventListener('click',()=>{try{const p=load();for(const d of p.destinations)d.name=document.getElementById('export-'+d.id).value;save(p);document.getElementById('export-profile-status').textContent='목적지를 저장했습니다. 이동 시간은 조건이 일치하는 확인 자료만 사용합니다.';}catch(_){document.getElementById('export-profile-status').textContent='브라우저 저장 공간을 사용할 수 없습니다.';}});
    document.getElementById('export-profile-file').addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;if(file.size>5000000)throw Error('설정 파일은 5MB 이하여야 합니다.');save(JSON.parse(await file.text()));wire();document.getElementById('export-profile-status').textContent='개인 목적지와 확인 자료를 불러왔습니다.';}catch(error){document.getElementById('export-profile-status').textContent=error.message;}finally{e.target.value='';}});
  }
  return {sheets,load,save,validate,route,key,wire,empty};
})();
