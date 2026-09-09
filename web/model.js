async function loadGuide(){
 const response=await fetch('data/dashboard_manifest.json',{cache:'no-cache'});if(!response.ok)throw Error('자료를 불러오지 못했습니다.');
 const m=await response.json(),f=n=>n==null?'—':Number(n).toLocaleString('ko-KR'),pct=n=>n==null?'—':`${(n*100).toFixed(1)}%`,esc=ResultPages.escape;
 const retraining=m.retraining_research;
 if(retraining){
  document.getElementById('capital-retraining-status').textContent=retraining.headline;
  const rows=retraining.price_rows.map(r=>`<tr><th>${esc(r.period)}</th><td>${f(r.trades)}</td><td>${r.original.toFixed(4)}</td><td>${r.recent.toFixed(4)}</td><td>${r.all_history.toFixed(4)}</td><td>${r.rolling_five_years.toFixed(4)}</td></tr>`).join('');
  const boundaries=retraining.boundary_rows.map(r=>`<tr><th>${esc(r.label)}</th><td>${f(r.rows)}</td></tr>`).join('');
  const adaptation=retraining.full_history_followup;
  let followup='';
  if(adaptation){
   const evaluation=adaptation.price_rows.map(r=>`<tr><th>${esc(r.period)}</th><td>${r.original.toFixed(4)}</td><td>${r.uniform.toFixed(4)}</td><td>${r.adapted.toFixed(4)}</td><td>${r.complex_mape.toFixed(3)}%</td></tr>`).join('');
   const development=adaptation.development_rows.map(r=>`<tr><th>${r.year}</th><td>${esc(r.method)}</td><td>${r.mae.toFixed(4)}</td><td>${r.complex_mape.toFixed(3)}%</td></tr>`).join('');
   followup=`<h3>전체 과거를 유지하는 개선 비교</h3><p><b>${esc(adaptation.headline)}</b></p><p>${esc(adaptation.detail)}</p><p>${esc(adaptation.selection_note)}</p><p>선택 사양: <b>${esc(adaptation.selected_method)}</b></p><div class="table-scroll"><table><thead><tr><th>평가 기간</th><th>기존 고정 MAE 억</th><th>단순 전체 과거 MAE 억</th><th>개선 사양 MAE 억</th><th>개선 사양 단지 균등 오차</th></tr></thead><tbody>${evaluation}</tbody></table></div><p>${esc(adaptation.conclusion)}</p><details><summary>개발 기간의 모든 비교 결과</summary><div class="table-scroll"><table><thead><tr><th>개발 연도</th><th>사양</th><th>MAE 억</th><th>단지 균등 MAPE</th></tr></thead><tbody>${development}</tbody></table></div></details><p>${esc(adaptation.data_note)}</p><p class="score-note">${esc(adaptation.limitations)}</p><p><a href="${esc(adaptation.report_url)}">지역별 결과와 전체 과거 사용 기록 →</a></p>`;
  }
  document.getElementById('capital-retraining-results').innerHTML=`<p>${esc(retraining.data_note)}</p><h3>같은 거래에서 비교한 가격 오차</h3><p>거래당 총액 평균 절대오차(억원). 낮을수록 정확합니다.</p><div class="table-scroll"><table><thead><tr><th>평가 기간</th><th>거래 수</th><th>기존 고정</th><th>최근 자료 재학습</th><th>전체 과거 재학습</th><th>최근 5년 재학습</th></tr></thead><tbody>${rows}</tbody></table></div><p>${esc(retraining.price_note)}</p><p>${esc(retraining.input_refresh_note)}</p>${followup}<h3>수집일과 가격 정규화</h3><p>${esc(retraining.normalization_note)}</p><div class="table-scroll"><table><thead><tr><th>과거 경계 확인이 필요한 표기</th><th>해당 거래 수</th></tr></thead><tbody>${boundaries}</tbody></table></div><p>${esc(retraining.boundary_note)}</p><h3>18~24개월 잠재력의 지역 확대</h3><p>${esc(retraining.potential.headline)}</p><p>${esc(retraining.potential.detail)}</p><p><a href="potential.html#capital-expansion-validation">잠재력 진단과 현재 후보 →</a></p><p class="score-note">${esc(retraining.limitations)}</p>`;
 }else document.getElementById('capital-retraining-status').textContent='이 배포본에는 확장 재학습 결과가 아직 포함되지 않았습니다.';
 const followup=m.access_confidence_research;
 if(followup){
  document.getElementById('access-confidence-status').textContent=followup.headline;
  const gradeRows=followup.confidence_rows.filter(r=>r.scope==='grade').map(r=>`<tr><th>${esc(r.group)}</th><td>${f(r.transactions)}</td><td>${r.mape_pct.toFixed(1)}%</td><td>${r.coverage_pct.toFixed(1)}%</td></tr>`).join('');
  const accessRows=[2025,2026].map(year=>{const rows=followup.access_rows.filter(r=>r.year===year),get=method=>rows.find(r=>r.method===method).mae_oku.toFixed(4);return `<tr><th>${year}</th><td>${get('price_activity_floor')}</td><td>${get('base')}</td><td>${get('controls')}</td><td>${get('access')}</td></tr>`;}).join('');
  document.getElementById('access-confidence-results').innerHTML=`<h3>가격 추정 신뢰도 A~D</h3><p>최근 거래수만으로 정하지 않습니다. 과거에 비슷한 거래 이력·경과일·가격 분산·주변 비교 근거·면적·연식·층 조건에서 실제로 얼마나 틀렸는지를 학습했습니다. 가격과 할인 점수는 등급 때문에 바꾸지 않습니다.</p><p>A는 참고 범위 상단이 기준가 +10% 이내, B는 +20% 이내, C는 +35% 이내, D는 그보다 넓은 범위입니다. 아래는 2026년 3~7월 진단입니다.</p><div class="table-scroll"><table><thead><tr><th>등급</th><th>검증 거래</th><th>평균 절대오차율</th><th>범위 실제 포함률</th></tr></thead><tbody>${gradeRows}</tbody></table></div><p class="score-note">${esc(followup.confidence_note)}</p><h3>인천: 업무지구·일자리 접근성 1차 실험</h3><p>${esc(followup.access_note)}</p><div class="table-scroll"><table><thead><tr><th>기간</th><th>기존 인천 MAE 억</th><th>전체 과거 새 사양</th><th>기존 입력 보정</th><th>거리·일자리 추가 보정</th></tr></thead><tbody>${accessRows}</tbody></table></div><p>보정으로 새 사양의 오차는 줄었지만, 거리·일자리만의 추가 효과는 작았고 기존 인천 모델보다 정확해지지는 않았습니다. 다음 비교에는 지역별 전체 일자리와 실제 대중교통·도로 소요시간이 필요합니다.</p><p class="score-note">${esc(followup.research_note)}</p><p><a href="${esc(followup.report_url)}">입력 범위·비교 결과·재현 기록 →</a></p>`;
 }else document.getElementById('access-confidence-status').textContent='이 배포본에는 후속 신뢰도·통근 실험이 아직 포함되지 않았습니다.';
 const research=m.market_research;
 if(research){
  document.getElementById('market-research-status').textContent=research.headline;
  const rows=research.price_comparison.map(r=>`<tr><th>${esc(r.period)}</th><td>${f(r.trades)}</td><td>${r.base.toFixed(4)}</td><td>${r.kb.toFixed(4)}</td><td>${r.lease.toFixed(4)}</td><td>${r.both.toFixed(4)}</td></tr>`).join('');
  const cadence=research.cadence.map(r=>`<tr><th>${esc(r.period)}</th><td>${f(r.trades)}</td><td>${r.monthly.toFixed(4)}</td><td>${r.quarter.toFixed(4)}</td><td>${r.half.toFixed(4)}</td></tr>`).join('');
  document.getElementById('market-research-results').innerHTML=`<p>${esc(research.data_note)}</p><h3>현재 가격 오차 · 수도권</h3><p>거래당 총액 평균 절대오차(억). 작을수록 이후 실거래가격에 가깝습니다.</p><div class="table-scroll"><table><thead><tr><th>시험 기간</th><th>거래 수</th><th>기존 월간</th><th>KB 추가</th><th>전세 추가</th><th>둘 다 추가</th></tr></thead><tbody>${rows}</tbody></table></div><h3>가격 갱신 주기 · 서울</h3><p>같은 거래·층의 가격을 비교했습니다. 3월부터 시작하는 3/6개월 동결 구간이며, 아래 단위도 억입니다.</p><div class="table-scroll"><table><thead><tr><th>시험 기간</th><th>거래 수</th><th>매월 갱신</th><th>3개월 고정</th><th>6개월 고정</th></tr></thead><tbody>${cadence}</tbody></table></div><p>${esc(research.potential_note)}</p><p>${esc(research.limitations)}</p>`;
 }else document.getElementById('market-research-status').textContent='추가 실험 결과가 아직 이 배포본에 포함되지 않았습니다.';
 document.getElementById('model-status').textContent=`${m.model.model_version} · 모델 ${m.model.model_month} · 학습 ${m.model.trained_through}까지 · 거래자료 ${m.generated_at}`;
 if(m.model.nowcast){
  const n=m.model.nowcast;
  document.getElementById('model-status').textContent=`현재 가격 ${n.version} · ${n.month}월 · 거래자료 ${m.generated_at} · 이전 연간 기록 ${m.model.model_version}`;
  const regions=n.validation?.regional;
  const validation=regions?`<div class="table-scroll"><table><thead><tr><th>기간</th><th>지역</th><th>기존 MAE 억</th><th>현재 적용 MAE 억</th></tr></thead><tbody>${regions.map(r=>`<tr><th>${r.year}</th><td>${esc(r.region)}</td><td>${r.legacy_mae_oku.toFixed(4)}</td><td>${r.active_mae_oku.toFixed(4)}</td></tr>`).join('')}</tbody></table></div><p class="score-note">${esc(n.validation.note)}</p>`:`<p class="score-note">325,084건 별도 시계열 검증에서 총액 평균 절대오차: 2025년 0.4335억, 2026년 3~7월 0.5142억. 실제 거래 층 기준입니다.</p>`;
  document.getElementById('nowcast-info').innerHTML=`<p><b>${esc(n.release_note??'기존 월별 가격 모델')}</b></p><p class="score-note">월별 기준가 ${f(n.available_types)} / ${f(n.total_types)}개 평형 산출. 미산출 평형은 점수를 비워 둡니다. ${esc(n.note??'')}</p>${validation}`;
  if(n.regional_models){
   document.getElementById('capital-retraining-status').textContent=n.release_note+' · 검색·지도·상세·CSV의 기준가에 반영';
   document.getElementById('capital-retraining-results').innerHTML='<p><b>현재 적용:</b> 9월 9일 사용자 요청에 따라 서울·경기는 전체 과거 재학습 사양을 채택했습니다. 인천은 기존 모델을 유지합니다. 아래 표는 적용 결정 전 실험 기록이며, 당시의 전체 지역 일괄 교체 기준과 현재의 지역별 채택을 구분합니다. 희소 단지의 오차 악화는 후속 검증 대상입니다.</p>'+document.getElementById('capital-retraining-results').innerHTML;
  }
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
 document.getElementById('model-comparison').textContent=`${folds.length}개 시험 연도 중 ${better}개에서 학습 보정을 적용하지 않은 비교 기준보다 MAE가 낮았습니다. 해당 연간 모델의 ML 혼합 비중은 ${pct(m.model.ml_weight)}입니다. 미래 성능의 보장은 아닙니다.`;
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
