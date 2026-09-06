async function loadGuide(){
 const response=await fetch('data/dashboard_manifest.json',{cache:'no-cache'});if(!response.ok)throw Error('자료를 불러오지 못했습니다.');
 const m=await response.json(),f=n=>n==null?'—':Number(n).toLocaleString('ko-KR'),pct=n=>n==null?'—':`${(n*100).toFixed(1)}%`,esc=ResultPages.escape;
 document.getElementById('model-status').textContent=`모델 ${m.model.model_month} · 학습 ${m.model.trained_through}까지 · 거래자료 ${m.generated_at}`;
 const folds=m.model.validation;
 document.getElementById('validation-rows').innerHTML=folds.map(v=>`<tr><th>${v.test_year}</th><td>${f(v.model.rows)}</td><td>${f(v.model.mae_price_per_pyeong)}</td><td>${f(v.baseline.mae_price_per_pyeong)}</td><td>${f(v.model.median_absolute_pct_error)}%</td><td>${pct(v.model.within_20pct)}</td><td>${pct(v.interval_actual_coverage)}</td></tr>`).join('');
 document.getElementById('validation-regions').innerHTML=folds.map(v=>`<details><summary>${v.test_year} 지역별 오차 · 학습 비중 ${pct(v.ml_weight)}</summary><div class="table-scroll"><table><thead><tr><th>지역</th><th>표본</th><th>MAE (만원/평)</th><th>중앙 오차율</th><th>±20% 이내</th></tr></thead><tbody>${Object.entries(v.regions).map(([s,x])=>`<tr><th>${esc(s)}</th><td>${f(x.rows)}</td><td>${f(x.mae_price_per_pyeong)}</td><td>${f(x.median_absolute_pct_error)}%</td><td>${pct(x.within_20pct)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
 const better=folds.filter(v=>v.model.mae_price_per_pyeong<v.baseline.mae_price_per_pyeong).length;
 document.getElementById('model-comparison').textContent=`${folds.length}개 시험 연도 중 ${better}개에서 단순 전년 기준보다 MAE가 낮았습니다. 현재 월의 ML 혼합 비중은 ${pct(m.model.ml_weight)}입니다. 미래 성능의 보장은 아닙니다.`;
 document.getElementById('coverage-rows').innerHTML=Object.entries(m.coverage).sort(([a],[b])=>a==='all'?-1:b==='all'?1:Number(b)-Number(a)).map(([year,c])=>`<tr><th>${year==='all'?'전체':esc(year)}</th><td>${f(c.source_trades)}</td><td>${f(c.represented_trades)}</td><td>${f(c.available_types)}</td><td>${c.complete?'일치':'누락 '+f(c.unrepresented_trades)}</td></tr>`).join('');
}
loadGuide().catch(e=>{document.getElementById('model-status').textContent=e.message;});
