const Potential = (() => {
  const esc=ResultPages.escape;
  const state={data:null,search:'',district:'all',min:null,max:null,area:'all',top:100,trades:3,sort:'rank',lag:'all',selected:null};
  const number=value=>value===''?null:Number.isFinite(Number(value))?Number(value):null;
  const signed=value=>Number.isFinite(value)?`${value>0?'+':''}${value.toFixed(2)}%`:'표본 부족';
  const price=value=>Number.isFinite(value)?`${value.toLocaleString('ko-KR',{maximumFractionDigits:4})}억`:'미확인';
  function filterRows(rows,filters) {
    const query=filters.search.trim().toLocaleLowerCase();
    return rows.filter(r=>(!query||`${r.key} ${r.district} ${r.dong} ${r.name}`.toLocaleLowerCase().includes(query))
      && (filters.district==='all'||r.gu===filters.district)
      && (filters.min==null||r.entry_reference_oku>=filters.min) && (filters.max==null||r.entry_reference_oku<=filters.max)
      && (filters.area==='all'||filters.area==='small'&&r.area<=60||filters.area==='medium'&&r.area>60&&r.area<=85||filters.area==='large'&&r.area>85)
      && r.top_percent<=filters.top && r.entry_n>=(filters.trades??3) && (filters.lag!=='stable'||r.lag_sensitivity?.both_top_decile===true))
      .sort((a,b)=>(filters.sort==='price'?a.entry_reference_oku-b.entry_reference_oku:filters.sort==='trades'?b.entry_n-a.entry_n:a.research_rank-b.research_rank)||a.research_rank-b.research_rank);
  }
  function evidenceHtml(row,data) {
    const labels=Object.fromEntries(data.features.map(f=>[f.key,f.label]));
    const lag=row.lag_sensitivity;
    const drivers=(row.drivers??[]).map(d=>`<span class="driver-chip">${esc(labels[d.key]??d.key)} <b>${d.direction==='positive'?'↑':'↓'}</b></span>`).join('');
    const facts=[['반기 가격 변화',signed(row.momentum_pct)],['주변 대비 반기 추세',signed(row.relative_momentum_pct)],['최근 90일 / 365일 거래',Number.isFinite(row.n90)?`${row.n90} / ${row.n365}건`:'미확인'],['마지막 계약 경과',Number.isFinite(row.last_age)?`${row.last_age}일`:'미확인']];
    return `<div class="potential-detail-grid">${facts.map(([label,value])=>`<div><span>${label}</span><strong>${value}</strong></div>`).join('')}</div>${drivers?`<p><b>이 후보의 예상 상대 변화를 움직인 주요 지표</b></p><div class="driver-chips">${drivers}</div><p>↑는 모델의 예상 상대 변화를 높이는 방향, ↓는 낮추는 방향입니다. 지표 간 관계를 함께 학습한 결과입니다.</p>`:''}${lag?`<div class="lag-comparison"><b>공시 시차를 61일로 늘려 보면</b><p>${lag.alternate_eligible?`전체 ${data.lag_sensitivity.cohort_size.toLocaleString('ko-KR')}개 중 ${lag.alternate_rank.toLocaleString('ko-KR')}위 · 상위 ${lag.alternate_top_percent.toFixed(1)}%`:'더 오래된 자료만 사용하면 거래 이력 조건을 충족하지 못합니다.'}</p><p>현재 순위와 나란히 비교해 시차 가정에 따라 후보가 얼마나 달라지는지 확인합니다.</p></div>`:''}`;
  }
  function rowHtml(row,data,expanded=false) {
    const link=`index.html?search=${encodeURIComponent(row.key)}`;
    return `<li class="potential-card"><button class="potential-card-toggle" type="button" data-potential-key="${esc(row.key)}" aria-expanded="${expanded}"><span class="potential-card-main"><span class="potential-location">${esc(row.district)} ${esc(row.dong)} · 전용 ${row.area}㎡</span><strong>${esc(row.name)}</strong><span class="potential-card-facts">판단 전 대표가격 <b>${price(row.entry_reference_oku)}</b><span>180일 거래 ${row.entry_n}건</span></span></span><span class="potential-rank"><small>전체 ${data.cohort_size.toLocaleString('ko-KR')}개 중</small><b>${row.research_rank.toLocaleString('ko-KR')}위</b><span>상위 ${row.top_percent<.1?row.top_percent.toFixed(2):row.top_percent.toFixed(1)}%</span></span></button>${row.lag_sensitivity?.both_top_decile?'<p class="lag-badge">31일·61일 공시 지연 가정에서 모두 상위 10%</p>':''}${expanded?`<div class="potential-card-detail"><div class="potential-detail-grid"><div><span>예상 상대 가격 변화</span><strong>${signed(row.predicted_relative_change_pct)}</strong></div><div><span>결과를 비교할 기간</span><strong>${esc(data.outcome_start.slice(0,7))} ~ ${esc(data.outcome_end.slice(0,7))}</strong></div></div><p>${esc(data.target_meaning)} 절대 수익률과 구분해 읽으세요.</p>${evidenceHtml(row,data)}<p>${esc(data.entry_reference_meaning)}</p><a class="action-link" href="${esc(link)}">이 평형의 현재 가격 비교 →</a></div>`:''}</li>`;
  }
  function render(reset=false) {
    if(!state.data)return;
    if(reset)ResultPages.reset();
    const rows=filterRows(state.data.rows,state),page=ResultPages.view('potential-list',rows,30);
    document.getElementById('potential-result-count').textContent=`조건에 맞는 ${rows.length.toLocaleString('ko-KR')}개 평형 · 전체 ${state.data.cohort_size.toLocaleString('ko-KR')}개`;
    document.getElementById('potential-list').innerHTML=page.rows.length?page.rows.map(r=>rowHtml(r,state.data,state.selected===r.key)).join(''):'<li class="empty-state">현재 조건에 맞는 후보가 없습니다. 지역·가격 또는 순위 범위를 넓혀 보세요.</li>';
  }
  function theme(enabled){document.body.classList.toggle('dark-mode',enabled);document.getElementById('potential-theme').textContent=enabled?'Light':'Dark';document.getElementById('potential-theme').setAttribute('aria-pressed',String(enabled));}
  function fiveYearHtml(result) {
    if(!result)return '';
    return `<p>${Number(result.raw_rows_added).toLocaleString('ko-KR')}건의 과거 매매를 추가해 ${Number(result.model_test_origins)}개 판단 시점에서 5년 모형을 시험했습니다. 모델과 단순 소외 후보의 관측 조건을 모두 충족한 시점은 ${Number(result.common_eligible_origins)}개로, 채택에 필요한 ${Number(result.required_common_origins)}개보다 적었습니다.</p><div class="potential-detail-grid"><div><span>공통 유효 시점 · 모델 상대 변화 중앙값</span><strong>${signed(result.model_median_excess_pct)}</strong></div><div><span>같은 시점 · 단순 소외 후보</span><strong>${signed(result.benchmark_median_excess_pct)}</strong></div></div><p>${esc(result.outcome_window)}로 비교한 결과입니다. 현재 후보는 검증 범위가 더 넓은 18~24개월 모델을 유지합니다.</p><p>${esc(result.meaning??'')} ${esc(result.next_direction??'')}</p>`;
  }
  function renderInfo(data){
    document.getElementById('potential-origin').textContent=data.origin;
    document.getElementById('five-year-validation').hidden=!data.five_year_validation;
    document.getElementById('five-year-results').innerHTML=fiveYearHtml(data.five_year_validation);
    document.getElementById('potential-cutoff').textContent=data.feature_cutoff;
    document.getElementById('potential-total').textContent=data.cohort_size.toLocaleString('ko-KR');
    document.getElementById('potential-status').textContent=`판단 기준일 ${data.origin} · 실제 산출 ${new Date(data.created_at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})} (한국시간). 서울·광명, 같은 면적의 180일 매매 3건 이상인 후보를 고정해 표시합니다.`;
    document.getElementById('potential-district').innerHTML='<option value="all">전체 지역</option>'+[...new Map(data.rows.map(r=>[r.gu,r.district])).entries()].sort((a,b)=>a[1].localeCompare(b[1],'ko')).map(([code,name])=>`<option value="${esc(code)}">${esc(name)}</option>`).join('');
    document.getElementById('potential-weighting').textContent=data.weighting;
    const names=Object.fromEntries(data.features.map(f=>[f.key,f.label]));
    document.getElementById('potential-importance').innerHTML=(data.feature_importance??[]).slice().sort((a,b)=>b.mean_abs_shap_share_pct-a.mean_abs_shap_share_pct).map(f=>`<div class="importance-row"><span>${esc(names[f.key]??f.key)}</span><b>${f.mean_abs_shap_share_pct.toFixed(1)}%</b><meter min="0" max="100" value="${f.mean_abs_shap_share_pct}" aria-label="${esc(names[f.key]??f.key)} 모델 의존도"></meter></div>`).join('');
    document.getElementById('potential-importance-note').textContent=data.importance_meaning??'';
    document.getElementById('potential-features').innerHTML=data.features.map(f=>`<p><b>${esc(f.label)}</b><br>${esc(f.description)}</p>`).join('');
    document.getElementById('potential-validation-rows').innerHTML=data.validation.map(v=>`<tr><th>${esc(v.origin.slice(0,7))}</th><td>${v.selected}</td><td>${v.observed}</td><td>${v.missing}</td><td>${signed(v.median_excess_pct)}</td></tr>`).join('');
    document.getElementById('potential-lag-assumption').textContent=data.availability_assumption??'';
    const audit=data.reporting_lag_audit;
    document.getElementById('potential-reporting-audit').textContent=audit?`실제 공개 시차 관측: ${audit.window_days.toFixed(1)}일 동안 새 공개 기록 ${audit.new_public_signatures.toLocaleString('ko-KR')}개를 확보했습니다. 현재는 31·61일 가정을 비교하며, 실제 지연 분포로 학습한 보정치는 아직 적용하지 않습니다.`:'';
    document.getElementById('potential-scope').textContent=`${data.scope}. 거래가 없는 후보의 최종 성과는 확인되지 않았으며, 관측된 후보의 중앙값입니다. 5년 실험 결과는 아래 별도 표에 구분합니다. 실제 공시 시차의 분포 보정은 관측 자료가 더 필요합니다.`;
  }
  function exportRows(){const rows=filterRows(state.data.rows,state);ResultPages.downloadCsv(`apartment-potential-${state.data.origin}.csv`,['판단 기준일','실거래 반영 마감','전체 순위','전체 중 상위(%)','지역','동','단지','전용면적(㎡)','판단 전 대표가격(억)','180일 거래수','예상 상대 가격 변화(%)','61일 지연 가정 순위','31·61일 모두 상위10%','결과 시작','결과 종료','식별키'],rows.map(r=>[state.data.origin,state.data.feature_cutoff,r.research_rank,r.top_percent,r.district,r.dong,r.name,r.area,r.entry_reference_oku,r.entry_n,r.predicted_relative_change_pct,r.lag_sensitivity?.alternate_rank,r.lag_sensitivity?.both_top_decile,state.data.outcome_start,state.data.outcome_end,r.key]));}
  function wire(){
    const controls={search:'search',district:'district','price-min':'min','price-max':'max',area:'area',top:'top',trades:'trades',sort:'sort',lag:'lag'};let timer;
    for(const [id,key] of Object.entries(controls))document.getElementById(`potential-${id}`).addEventListener('input',event=>{state[key]=['min','max','top','trades'].includes(key)?number(event.target.value):event.target.value;state.selected=null;clearTimeout(timer);timer=setTimeout(()=>render(true),120);});
    document.getElementById('potential-reset').addEventListener('click',()=>{Object.assign(state,{search:'',district:'all',min:null,max:null,area:'all',top:100,trades:3,sort:'rank',lag:'all',selected:null});for(const [id,key]of Object.entries(controls))document.getElementById(`potential-${id}`).value=state[key]??'';render(true);});
    document.getElementById('potential-export').addEventListener('click',exportRows);
    document.getElementById('potential-theme').addEventListener('click',()=>{const enabled=!document.body.classList.contains('dark-mode');theme(enabled);try{localStorage.setItem('realEstateDashboardDarkMode',enabled?'1':'0');}catch(_){}});
    document.body.addEventListener('click',event=>{const page=event.target.closest('[data-page-list]');if(page){ResultPages.set(page.dataset.pageList,page.dataset.page);render();document.getElementById('potential-list').scrollIntoView({block:'start'});return;}const row=event.target.closest('[data-potential-key]');if(row){state.selected=state.selected===row.dataset.potentialKey?null:row.dataset.potentialKey;render();}});
    document.body.addEventListener('change',event=>{const input=event.target.closest('[data-page-input]');if(input){ResultPages.set(input.dataset.pageInput,input.value);render();document.getElementById('potential-list').scrollIntoView({block:'start'});}});
  }
  async function init(){
    try{theme(localStorage.getItem('realEstateDashboardDarkMode')==='1');}catch(_){theme(false);}
    wire();
    const response=await fetch('data/dashboard_manifest.json',{cache:'no-cache'});if(!response.ok)throw Error('후보 자료 목록을 불러오지 못했습니다. 새로고침해 주세요.');
    const manifest=await response.json();if(!manifest.potential)throw Error('이 배포본에는 잠재력 후보 자료가 아직 없습니다.');
    const data=await DashboardData.compressed(manifest.potential);if(data.schema_version!==1||!Array.isArray(data.rows)||data.rows.length!==data.cohort_size)throw Error('후보 자료 건수가 일치하지 않습니다.');
    state.data=data;renderInfo(data);render(true);
  }
  return {init,filterRows,rowHtml,number,signed,fiveYearHtml};
})();
Potential.init().catch(error=>{document.getElementById('potential-status').textContent=error.message;});
