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
 await run('state.dataStore.ensureHistory()');
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
