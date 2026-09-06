async function loadGuide(){
 const response=await fetch('data/dashboard_manifest.json',{cache:'no-cache'});if(!response.ok)throw Error('자료를 불러오지 못했습니다.');
 const m=await response.json(),f=n=>n==null?'—':Number(n).toLocaleString('ko-KR'),pct=n=>n==null?'—':`${(n*100).toFixed(1)}%`,esc=ResultPages.escape;
 document.getElementById('model-status').textContent=`${m.model.model_version} · 모델 ${m.model.model_month} · 학습 ${m.model.trained_through}까지 · 거래자료 ${m.generated_at}`;
 if(m.model.model_version==='estate-reference-v4')document.getElementById('reference-method').textContent='v4 후보 모델입니다. 동일 평형 전년도 가격 → 최근 3년 이력 → 유사 면적의 동·구·시도 가격 → 기존 주변 가격 순으로 비교 기준을 보완합니다. LightGBM은 이 기준에서의 가격 차이를 학습하고, 이전 연도에서 혼합 비중을 선택합니다. 모델은 버전·월별로 고정됩니다.';
 const folds=m.model.validation;
 document.getElementById('validation-rows').innerHTML=folds.map(v=>`<tr><th>${v.test_year}</th><td>${f(v.model.rows)}</td><td>${f(v.model.mae_price_per_pyeong)}</td><td>${f(v.baseline.mae_price_per_pyeong)}</td><td>${f(v.model.median_absolute_pct_error)}%</td><td>${pct(v.model.within_20pct)}</td><td>${pct(v.interval_actual_coverage)}</td></tr>`).join('');
 document.getElementById('validation-regions').innerHTML=folds.map(v=>`<details><summary>${v.test_year} 지역별 오차 · 학습 비중 ${pct(v.ml_weight)}</summary><div class="table-scroll"><table><thead><tr><th>지역</th><th>표본</th><th>MAE (만원/평)</th><th>중앙 오차율</th><th>±20% 이내</th></tr></thead><tbody>${Object.entries(v.regions).map(([s,x])=>`<tr><th>${esc(s)}</th><td>${f(x.rows)}</td><td>${f(x.mae_price_per_pyeong)}</td><td>${f(x.median_absolute_pct_error)}%</td><td>${pct(x.within_20pct)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
 const better=folds.filter(v=>v.model.mae_price_per_pyeong<v.baseline.mae_price_per_pyeong).length;
 document.getElementById('model-comparison').textContent=`${folds.length}개 시험 연도 중 ${better}개에서 학습 보정을 적용하지 않은 비교 기준보다 MAE가 낮았습니다. 현재 월의 ML 혼합 비중은 ${pct(m.model.ml_weight)}입니다. 미래 성능의 보장은 아닙니다.`;
 document.getElementById('coverage-rows').innerHTML=Object.entries(m.coverage).sort(([a],[b])=>a==='all'?-1:b==='all'?1:Number(b)-Number(a)).map(([year,c])=>`<tr><th>${year==='all'?'전체':esc(year)}</th><td>${f(c.source_trades)}</td><td>${f(c.represented_trades)}</td><td>${f(c.available_types)}</td><td>${c.complete?'일치':'누락 '+f(c.unrepresented_trades)}</td></tr>`).join('');
 const comparison=m.model_comparison;
 if(comparison){
  document.getElementById('comparison-status').textContent=`${comparison.data_through}까지의 거래자료로 비교 · 전체 MAE ${f(comparison.pooled_mae.v3)} → ${f(comparison.pooled_mae.v4)}만원/평 · ${comparison.runtime.platform}, LightGBM ${comparison.runtime.lightgbm}. 공개 운영 모델의 자동 교체 조건은 아닙니다.`;
  document.getElementById('comparison-rows').innerHTML=comparison.folds.map(r=>`<tr><th>${r.test_year}</th><td>${f(r.rows)}</td><td>${f(r.v3.model.mae_price_per_pyeong)}</td><td>${f(r.v4.model.mae_price_per_pyeong)}</td><td>${f(r.mae_change_pct)}%</td><td>${pct(r.v4.interval_actual_coverage)}</td></tr>`).join('');
  const names={with_prior:'전년도 동일 평형 거래 있음',without_prior:'전년도 동일 평형 거래 없음',under_3_trades:'거래 3건 미만'};
  document.getElementById('comparison-segments').innerHTML=comparison.folds.map(r=>`<details><summary>${r.test_year} 거래 이력·표본 수별 결과</summary><div class="table-scroll"><table><thead><tr><th>구분</th><th>표본</th><th>v3 MAE</th><th>v4 MAE</th><th>v4 포함률</th></tr></thead><tbody>${Object.entries(r.v4.segments).map(([key,v])=>`<tr><th>${esc(names[key]||key)}</th><td>${f(v.model.rows)}</td><td>${f(r.v3.segments[key]?.model.mae_price_per_pyeong)}</td><td>${f(v.model.mae_price_per_pyeong)}</td><td>${pct(v.interval_actual_coverage)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
 }
}
loadGuide().catch(e=>{document.getElementById('model-status').textContent=e.message;});
