const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('web/model.html','utf8');
const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]);
const comparison=JSON.parse(fs.readFileSync('reports/estate_model_comparison.json','utf8'));
async function check(version,includeComparison){
 const elements=Object.fromEntries(ids.map(id=>[id,{textContent:'',innerHTML:''}]));
 const manifest={generated_at:'2026-09-05',coverage:{all:{source_trades:10,represented_trades:10,available_types:3,complete:true}},
  model:{model_version:version,model_month:'2026-09',trained_through:'2025-12-31',ml_weight:.25,validation:comparison.folds.map(f=>f.v3)}};
 if(includeComparison)manifest.model_comparison=comparison;
 const context=vm.createContext({document:{getElementById(id){assert.ok(elements[id],`Missing guide element ${id}`);return elements[id];}},
  fetch:async()=>({ok:true,json:async()=>manifest})});
 vm.runInContext(fs.readFileSync('web/result-pages.js','utf8'),context);
 const code=fs.readFileSync('web/model.js','utf8').replace(/loadGuide\(\)\.catch[\s\S]*$/,'');
 vm.runInContext(code,context);await vm.runInContext('loadGuide()',context);
 assert.match(elements['model-status'].textContent,new RegExp(version));
 assert.ok(elements['validation-rows'].innerHTML.includes('2025'));
 if(includeComparison){
  assert.equal((elements['comparison-rows'].innerHTML.match(/<tr>/g)||[]).length,3);
  assert.match(elements['comparison-segments'].innerHTML,/전년도 동일 평형 거래 없음/);
  assert.ok(elements['comparison-status'].textContent.includes(comparison.pooled_mae.v4.toLocaleString('ko-KR')));
 }
 if(version==='estate-reference-v4')assert.match(elements['reference-method'].textContent,/최근 3년/);
}
(async()=>{await check('estate-reference-v3',false);await check('estate-reference-v3',true);await check('estate-reference-v4',true);
 console.log('Model guide renders existing releases and candidate comparisons.');})().catch(e=>{console.error(e);process.exitCode=1;});
