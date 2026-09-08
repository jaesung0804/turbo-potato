// Exercise a real release's data layer and pure listing/ranking functions.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');
const site=path.resolve(process.argv[2]||'.work/site'),origin=process.argv[3];
const read=p=>fs.readFileSync(path.join(site,p),'utf8');
const context=vm.createContext({console,window:{addEventListener(){}},setTimeout,URL,Map,Set,Blob,Response,DecompressionStream,crypto:webcrypto,
 fetch:async (url,options)=>origin ? fetch(new URL(url,origin.endsWith('/')?origin:origin+'/'),{...options,signal:AbortSignal.timeout(45000)}) : new Response(fs.readFileSync(path.join(site,url)))});
vm.runInContext(read('result-pages.js')+'\n'+read('data-store.js')+'\n'+read('app.js').replace(/init\(\)\.catch\([\s\S]*$/, ''),context);
const run=s=>vm.runInContext(s,context);
(async()=>{
 await run(`DashboardData.open().then(s=>{state.dataStore=s;state.summary=s.summary;state.recommendations=s.recommendations;
 state.recommendationByType=new Map(s.recommendations.recommendations.map(r=>[recommendationKey(r.region_code,r.building_key),r]));})`);
 const manifest=JSON.parse(read('data/dashboard_manifest.json'));
 const scored=run('state.recommendations.recommendations.filter(r=>r.valuation_comparison?.status==="available")');
 for(const rec of scored){
  context.rec=rec;
  assert.equal(run('reviewScoreAtPrice(rec,rec.valuation_comparison.comparison_price_billion)'),rec.valuation_comparison.score,
    'The displayed transaction price must reproduce the list score');
  assert.equal(run('reviewScoreAtPrice(rec,rec.valuation_comparison.neutral_price_billion)'),50,
    'Every displayed neutral price must produce exactly 50');
 }
 console.log(`Canonical price/score contract verified for ${scored.length} types.`);
 if(manifest.transaction_valuation){
  await run('state.dataStore.ensureTransactionValuations()');
  const replayed=run('state.recommendations.recommendations.filter(r=>r.transaction_valuation?.status==="available")');
  assert.ok(replayed.length>0,'The published contract replay cannot be empty');
  assert.ok(replayed.every(r=>Array.isArray(r.transaction_valuation.monthly)&&Array.isArray(r.transaction_valuation.recent_transactions)),
    'Every available contract summary must resolve to its lazy detail');
 }
 if(manifest.potential){
  context.potentialAsset=manifest.potential;
  const potential=await run('DashboardData.compressed(potentialAsset)');
  assert.equal(potential.rows.length,potential.cohort_size,'Potential cohort must be complete');
  assert.equal(new Set(potential.rows.map(r=>r.key)).size,potential.cohort_size,'Potential identities must be unique');
  assert.equal(Math.max(...potential.rows.map(r=>r.research_rank)),potential.cohort_size);
  console.log(`Separate potential cohort verified: ${potential.cohort_size} types.`);
 }
 await run('state.dataStore.ensureHistory()');
 context.latestPeriod=manifest.default_year;
 await run('state.dataStore.loadPeriod(latestPeriod)');
 run('state.year=latestPeriod;state.regionValueCache.clear();');
 assert.equal(run('typeItems().length'),manifest.model.valuation.recent_evidence_types,
   'The default view must include exactly the types with recent price and model evidence');
 assert.ok(run('typeItems().every(x=>{const r=aiRecommendationForItem(x);return r.current_valuation.recent_trade_count>=3&&r.recent_price_comparison.trade_count>=3;})'),
   'Default candidates must have at least three recent observations on both sides');
 run('state.recentEvidenceOnly=false;state.regionValueCache.clear();');
 for(const year of (origin?[manifest.default_year]:Object.keys(manifest.periods))){
  context.period=year;await run('state.dataStore.loadPeriod(period)');run('state.year=period;state.regionValueCache.clear();');
  for(const metric of ['price_billion','ai_score','yoy_rate']){
   context.metric=metric;run('state.metric=metric;state.regionValueCache.clear();');
   assert.equal(run('typeItems().length'),manifest.coverage[year].available_types,`Hidden results: ${year}/${metric}`);
   assert.equal(run('typeItems().reduce((s,x)=>s+x.building.count,0)'),manifest.coverage[year].represented_trades);
   assert.ok(run('groupedBuildings().length')>0);
  }
  run('aggregateRegionRates("gu",regionPeriodRate);aggregateRegionRates("dong",officialRegionAiScore);');
  console.log(`All ${year} results reachable: ${manifest.coverage[year].available_types} types / ${manifest.coverage[year].represented_trades} trades`);
 }
 run('state.search="";state.metric="price_billion";');
 const items=run('typeItems()');assert.ok(items.length);
 context.lastName=items.at(-1).building.building_name;
 assert.ok(run('state.search=lastName;typeItems().length')>0,'The final uncapped result must remain searchable');
 console.log(origin?'Public client data decoding and reachability verified.':'Real release data and ranking verification passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
