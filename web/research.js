const ResearchReview = (() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names={learned_price:'잠재력 모델',laggard:'단순 소외 규칙',cheap_peer:'주변 대비 저가 규칙',momentum:'상대 추세 규칙'};
  const signed=(value,unit='%')=>typeof value==='number'&&Number.isFinite(value)?`${value>0?'+':''}${value.toFixed(2)}${unit}`:'자료 부족';
  function comparisonRows(review) {
    return review.potential.comparison.map(r=>`<tr><th>${esc(names[r.method]??r.method)}</th><td>${signed(r.median_origin_complex_excess_pct)}</td><td>${r.median_observation_rate_pct==null?'—':r.median_observation_rate_pct.toFixed(1)+'%'}</td><td>${r.same_origins}개</td></tr>`).join('');
  }
  function pairedRows(review) {
    return review.potential.paired_origins.map(r=>`<tr><th>${esc(r.origin.slice(0,7))}</th><td>${signed(r.model_excess_pct)}</td><td>${signed(r.baseline_excess_pct)}</td><td>${signed(r.difference_pp,'%p')}</td><td>${r.model_observed} / ${r.model_selected}</td><td>${r.baseline_observed} / ${r.baseline_selected}</td></tr>`).join('');
  }
  async function loadReview(asset) {
    if (!asset || asset.url!=='data/model_review.json' || !Number.isInteger(asset.bytes) || asset.bytes<=0 || asset.bytes>131072) throw Error('검증 요약 자료가 포함되지 않은 배포본입니다.');
    const response=await fetch(asset.url,{cache:'no-cache'});
    if(!response.ok)throw Error('검증 요약을 불러오지 못했습니다.');
    const body=await response.arrayBuffer();
    if(body.byteLength!==asset.bytes)throw Error('검증 요약 크기가 일치하지 않습니다.');
    const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',body))].map(x=>x.toString(16).padStart(2,'0')).join('');
    if(hash!==asset.sha256)throw Error('검증 요약 무결성을 확인하지 못했습니다. 새로고침해 주세요.');
    const data=JSON.parse(new TextDecoder().decode(body));
    if(data.schema_version!==1 || data.kind!=='existing_evaluation_review' || data.production_changed!==false)throw Error('지원하지 않는 검증 요약입니다.');
    return data;
  }
  function renderReview(review) {
    const p=review.potential;
    document.getElementById('comparison-rows').innerHTML=comparisonRows(review);
    document.getElementById('paired-rows').innerHTML=pairedRows(review);
    document.getElementById('comparison-status').textContent=`${review.evaluation_date} 기존 진단 · ${p.model_evaluated_origins}개 모델 평가 시점 중 ${p.common_origins}개를 동일 조건으로 비교했습니다. ${p.excluded_origins.length}개는 비교방법 또는 결과 관측량 조건이 부족해 제외했습니다.`;
    document.getElementById('comparison-decision').textContent=`모델이 단순 소외 규칙보다 나았던 시점은 ${p.model_beats_baseline_origins}/${p.common_origins}개입니다. 시점별 두 방식 차이의 중앙값은 ${signed(p.paired_median_difference_pp,'%p')}입니다. 겹치는 기간이 있어 독립 시행의 승률로 해석하지 않습니다.`;
    document.getElementById('long-horizon-status').textContent=`5년 사양: ${review.long_horizon.five_year.passed?'기존 연구 기준 통과':'기존 연구 기준 미통과'} · 여러 재평가 시점 사양: ${review.long_horizon.multiple_paths.passed?'기존 연구 기준 통과':'기존 연구 기준 미통과'}. 현재 18~24개월 후보와 구분합니다.`;
    document.getElementById('comparison-body').hidden=false;
  }
  async function init() {
    const response=await fetch('data/dashboard_manifest.json',{cache:'no-cache'});
    if(!response.ok)throw Error('공개 자료 상태를 불러오지 못했습니다.');
    const manifest=await response.json();
    if(manifest.schema_version!==1)throw Error('지원하지 않는 공개 자료 버전입니다.');
    const status=EstateReleaseStatus.describe(manifest);
    document.getElementById('snapshot-date').textContent=status.through;
    document.getElementById('snapshot-age').textContent=status.ageDays==null?'자료 기준일 확인 필요':`자료 기준일로부터 ${status.ageDays}일 경과`;
    document.getElementById('collection-date').textContent=status.collected;
    document.getElementById('collection-message').textContent=status.message;
    document.getElementById('price-version').textContent=manifest.model?.nowcast?.month??manifest.model?.model_month??'미확인';
    document.getElementById('price-version-note').textContent=manifest.model?.nowcast?.release_note??'현재 배포본에서 적용 범위를 확인해 주세요.';
    document.getElementById('release-note').textContent=`공개 배포 ${manifest.release_id??'미확인'} · 화면 생성 ${status.built} · 수집 시각은 한국시간 기준입니다. 화면 배포일과 거래자료 기준일은 다릅니다.`;
    EstateReleaseStatus.render(manifest);
    try { renderReview(await loadReview(manifest.model_review)); }
    catch(error) { document.getElementById('comparison-status').textContent=error.message; }
  }
  return {init, comparisonRows, pairedRows, renderReview, loadReview, signed};
})();
ResearchReview.init().catch(error=>{document.getElementById('release-status').textContent=error.message;});
