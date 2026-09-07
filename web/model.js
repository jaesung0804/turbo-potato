async function loadGuide(){
 const response=await fetch('data/dashboard_manifest.json',{cache:'no-cache'});if(!response.ok)throw Error('자료를 불러오지 못했습니다.');
 const m=await response.json(),f=n=>n==null?'—':Number(n).toLocaleString('ko-KR'),pct=n=>n==null?'—':`${(n*100).toFixed(1)}%`,esc=ResultPages.escape;
 document.getElementById('model-status').textContent=`${m.model.model_version} · 모델 ${m.model.model_month} · 학습 ${m.model.trained_through}까지 · 거래자료 ${m.generated_at}`;
 if(m.model.nowcast){
  const n=m.model.nowcast,section=document.createElement('section');
  section.className='analysis-panel';
  const heading=document.createElement('h2');heading.textContent='최근 실거래 기반 호가 비교';
  const detail=document.createElement('p');detail.textContent=`${n.version} · ${n.month}월 · ${n.available_types.toLocaleString('ko-KR')} / ${n.total_types.toLocaleString('ko-KR')}개 평형 산출. ${n.note} 목록 점수·기존 검증표는 연간 모델이며, 최근 50점 가격은 호가 비교에 사용합니다.`;
  const validation=document.createElement('p');validation.textContent='별도 시계열 검증 325,084건에서 거래당 총액 평균 절대오차: 2025년 0.8714억 → 0.4335억, 2026년 3~7월 0.7521억 → 0.5142억. 실제 거래 층을 입력한 검증이며 대표 층 가격의 정확도나 미래 수익률을 보장하지 않습니다. 호가는 학습 정답으로 쓰지 않았고, 세대수 대비 회전율은 검증된 이력이 없어 제외했습니다. 1~2월과 2027년 이후는 재검증 전 연간 가격으로 대체합니다.';
  section.append(heading,detail,validation);document.getElementById('model-status').after(section);
 }
 if(m.model.model_version==='estate-reference-v4')document.getElementById('reference-method').textContent='v4 모델입니다. 동일 평형 전년도 가격 → 최근 3년 이력 → 유사 면적의 동·구·시도 가격 → 기존 주변 가격 순으로 비교 기준을 보완합니다. LightGBM은 이 기준에서의 가격 차이를 학습하고, 이전 연도에서 혼합 비중을 선택합니다. 모델은 버전·월별로 고정됩니다.';
 const folds=m.model.validation;
 if(m.model.model_version==='estate-reference-v5')document.getElementById('reference-method').textContent='v5 모델입니다. 동일 면적 전년도 가격 → 동일 면적 최근 3년 이력 → 같은 단지의 비슷한 다른 면적 과거 거래 → 유사 면적 주변 단지 가격 순으로 비교 기준을 보완합니다. LightGBM은 이 기준과의 가격 차이를 학습합니다. 단지 상세에서 실제 사용한 비교 기준을 확인할 수 있습니다.';
 const sibling=m.sibling_comparison;
 if(sibling){
  const a=sibling.results.v4,b=sibling.results.v5;
  document.getElementById('sibling-status').textContent=`${sibling.data_through} 자료로 과거 3개 연도 비교 · 합산 MAE ${f(a.pooled_mae.toFixed(2))} → ${f(b.pooled_mae.toFixed(2))}만원/평. 2026년 비슷한 면적 간 총액 역전(큰 면적이 5% 이상 저렴) ${f(a.current_inversions.larger_total_at_least_5pct_lower)} → ${f(b.current_inversions.larger_total_at_least_5pct_lower)}쌍. 역전 자체가 모두 오류라는 뜻은 아닙니다.`;
  document.getElementById('sibling-rows').innerHTML=b.folds.map((v,i)=>`<tr><th>${v.test_year}</th><td>${f(a.folds[i].model.mae_price_per_pyeong)}</td><td>${f(v.model.mae_price_per_pyeong)}</td><td>${f(a.folds[i].sibling_fallback_segment.mae_price_per_pyeong)}</td><td>${f(v.sibling_fallback_segment.mae_price_per_pyeong)}</td></tr>`).join('');
 }
 document.getElementById('validation-rows').innerHTML=folds.map(v=>`<tr><th>${v.test_year}</th><td>${f(v.model.rows)}</td><td>${f(v.model.mae_price_per_pyeong)}</td><td>${f(v.baseline.mae_price_per_pyeong)}</td><td>${f(v.model.median_absolute_pct_error)}%</td><td>${pct(v.model.within_20pct)}</td><td>${pct(v.interval_actual_coverage)}</td></tr>`).join('');
 document.getElementById('validation-regions').innerHTML=folds.map(v=>`<details><summary>${v.test_year} 지역별 오차 · 학습 비중 ${pct(v.ml_weight)}</summary><div class="table-scroll"><table><thead><tr><th>지역</th><th>표본</th><th>MAE (만원/평)</th><th>중앙 오차율</th><th>±20% 이내</th></tr></thead><tbody>${Object.entries(v.regions).map(([s,x])=>`<tr><th>${esc(s)}</th><td>${f(x.rows)}</td><td>${f(x.mae_price_per_pyeong)}</td><td>${f(x.median_absolute_pct_error)}%</td><td>${pct(x.within_20pct)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
 const better=folds.filter(v=>v.model.mae_price_per_pyeong<v.baseline.mae_price_per_pyeong).length;
 document.getElementById('model-comparison').textContent=`${folds.length}개 시험 연도 중 ${better}개에서 학습 보정을 적용하지 않은 비교 기준보다 MAE가 낮았습니다. 현재 월의 ML 혼합 비중은 ${pct(m.model.ml_weight)}입니다. 미래 성능의 보장은 아닙니다.`;
 document.getElementById('coverage-rows').innerHTML=Object.entries(m.coverage).sort(([a],[b])=>a==='all'?-1:b==='all'?1:Number(b)-Number(a)).map(([year,c])=>`<tr><th>${year==='all'?'전체':esc(year)}</th><td>${f(c.source_trades)}</td><td>${f(c.represented_trades)}</td><td>${f(c.available_types)}</td><td>${c.complete?'일치':'누락 '+f(c.unrepresented_trades)}</td></tr>`).join('');
 const comparison=m.model_comparison;
 const importance=m.model_importance;
 if(importance){
  const names={price_history:'과거 가격·주변 가격·기준가격',location:'시도·시군구·동',area:'전용면적·면적 구간',age:'연식',past_sample_counts:'과거 거래·비교 표본 수',year:'대상 연도'};
  document.getElementById('importance-status').textContent=`${importance.model_version} · ${importance.test_year}년 ${f(importance.rows)}개 평형 · 원래 MAE ${f(importance.baseline_mae)}만원/평 · ML 혼합 비중 ${pct(importance.ml_weight)}`;
  document.getElementById('importance-rows').innerHTML=importance.groups.map(r=>`<tr><th>${esc(names[r.name]||r.name)}</th><td>${f(r.mae_increase)}</td><td>${f(r.repeat_std)}</td></tr>`).join('');
 }
 if(m.data_quality){
  document.getElementById('quality-status').textContent=`원본 수집 ${m.collection?.fetched_at||'미확인'} · 계약자료 ${m.data_through||m.generated_at}까지. 9월을 포함한 현재 연도는 진행 중이며 미신고·정정으로 달라집니다.`;
  document.getElementById('quality-rows').innerHTML=Object.entries(m.data_quality.monthly).filter(([month])=>month.startsWith('2026')).sort().map(([month,q])=>`<tr><th>${esc(month)}</th>${['raw','used','direct','cancelled','unknown_deal_type','floor_observed'].map(k=>`<td>${f(q[k])}</td>`).join('')}</tr>`).join('');
 }
 if(comparison){
  document.getElementById('comparison-status').textContent=`${comparison.data_through}까지의 거래자료로 비교 · 전체 MAE ${f(comparison.pooled_mae.v3)} → ${f(comparison.pooled_mae.v4)}만원/평 · ${comparison.runtime.platform}, LightGBM ${comparison.runtime.lightgbm}. 공개 운영 모델의 자동 교체 조건은 아닙니다.`;
  document.getElementById('comparison-rows').innerHTML=comparison.folds.map(r=>`<tr><th>${r.test_year}</th><td>${f(r.rows)}</td><td>${f(r.v3.model.mae_price_per_pyeong)}</td><td>${f(r.v4.model.mae_price_per_pyeong)}</td><td>${f(r.mae_change_pct)}%</td><td>${pct(r.v4.interval_actual_coverage)}</td></tr>`).join('');
  const names={with_prior:'전년도 동일 평형 거래 있음',without_prior:'전년도 동일 평형 거래 없음',under_3_trades:'거래 3건 미만'};
  document.getElementById('comparison-segments').innerHTML=comparison.folds.map(r=>`<details><summary>${r.test_year} 거래 이력·표본 수별 결과</summary><div class="table-scroll"><table><thead><tr><th>구분</th><th>표본</th><th>v3 MAE</th><th>v4 MAE</th><th>v4 포함률</th></tr></thead><tbody>${Object.entries(r.v4.segments).map(([key,v])=>`<tr><th>${esc(names[key]||key)}</th><td>${f(v.model.rows)}</td><td>${f(r.v3.segments[key]?.model.mae_price_per_pyeong)}</td><td>${f(v.model.mae_price_per_pyeong)}</td><td>${pct(v.interval_actual_coverage)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
 }
}
loadGuide().catch(e=>{document.getElementById('model-status').textContent=e.message;});
