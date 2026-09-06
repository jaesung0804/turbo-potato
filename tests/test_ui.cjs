// Pure data and application-function tests; no DOM or browser simulation.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {gzipSync}=require('node:zlib'),{createHash,webcrypto}=require('node:crypto');
const root=process.cwd(),read=name=>fs.readFileSync(`${root}/web/${name}`,'utf8');
const app=read('app.js').replace(/init\(\)\.catch\([\s\S]*$/, '');
const context=vm.createContext({console,window:{addEventListener(){}},setTimeout,URL,Map,Set,Blob,Response,DecompressionStream,crypto:webcrypto});
vm.runInContext(read('result-pages.js')+'\n'+read('data-store.js')+'\n'+app,context);
function evaluate(code){return vm.runInContext(code,context);}

const all=Array.from({length:1007},(_,i)=>i);context.all=all;
assert.equal(evaluate('ResultPages.paginate(all,999).page'),21);
assert.equal(evaluate('ResultPages.paginate(all,21).rows.at(-1)'),1006);
assert.deepEqual(Array.from(evaluate('Array.from({length:21},(_,i)=>ResultPages.paginate(all,i+1).rows).flat()')),all);
assert.equal(evaluate('ResultPages.csvCell("=HYPERLINK(1)")'),'"\'=HYPERLINK(1)"');
assert.equal(evaluate('ResultPages.escape("<img onerror=x>")'),'&lt;img onerror=x&gt;');

// Changing the sorting metric cannot hide unscored listings or CSV rows.
evaluate(`state.summary={years:['2026','2025','2024'],generated_at:'2026-09-04',regions:[
 {code:'1',sido_name:'서울특별시',gu_code:'a',gu_name:'구',dong_name:'동',loadedBucket:{addresses:[
 {key:'lot1|84.91',complex_key:'lot1',building_name:'같은이름',area_type:'84.91㎡',count:1,metrics:{price_billion:{avg:10},area_pyeong:{avg:25},price_per_pyeong:{avg:4000}}},
 {key:'lot2|84.92',complex_key:'lot2',building_name:'같은이름',area_type:'84.92㎡',count:5,metrics:{price_billion:{avg:11},area_pyeong:{avg:25},price_per_pyeong:{avg:4400}}}]}}]};
 state.year='2026';state.metric='ai_score';state.recommendations={target_year:'2026'};`);
assert.equal(evaluate('typeItems().length'),2);
assert.equal(evaluate('groupedBuildings().length'),2);
assert.equal(evaluate('aiScoreForItem(typeItems()[0])'),null);
evaluate(`state.search='84.92';`);assert.equal(evaluate('typeItems().length'),1);
evaluate(`state.search='서울';`);assert.equal(evaluate('typeItems().length'),2);
evaluate(`state.search='';state.metric='count';`);assert.equal(evaluate('featureMetricValue(state.summary.regions)'),6);
assert.equal(evaluate('typeYoyRateForYears(state.summary.regions[0],typeItems()[0].building,"2026","2025")'),null);
evaluate(`state.year='2025';state.metric='ai_score';state.regionValueCache.clear();`);
assert.equal(evaluate('groupedBuildings().length'),2);
assert.equal(evaluate('buildingAge({built_year:2000})'),25);
evaluate(`state.summary.regions.push({...state.summary.regions[0],code:'2',gu_code:'b'});`);
assert.equal(evaluate('aggregateRegionRates("gu",()=>1).length'),2);

// Asset checks, exact complete counts, stale async requests, and rollback on corruption.
const assets=new Map();
function asset(name,value){const body=gzipSync(JSON.stringify(value));const url=`data/bundle/${name}.json.gz`;assets.set(url,body);return {url,bytes:body.length,sha256:createHash('sha256').update(body).digest('hex')};}
const values=Array.from({length:12},(_,i)=>i+1);
const manifest={schema_version:1,years:['2025','2026'],coverage:{'2025':{available_types:1,source_trades:5,represented_trades:5},'2026':{available_types:1,source_trades:7,represented_trades:7}},
 catalog:asset('catalog',{regions:[{code:'1'}],addresses:[{key:'A',building_name:'끝 단지'}]}),history:asset('history',{}),recommendations:asset('recs',{recommendations:[]}),
 periods:{'2025':asset('2025',[['1',5,values,[[0,5,values]],[]]]),'2026':asset('2026',[['1',7,values,[[0,7,values]],[]]])}};
let delayed=null,corrupt=false,historyRequests=0;
context.fetch=async url=>{
 if(url===manifest.history.url)historyRequests++;
 if(url==='data/dashboard_manifest.json')return new Response(JSON.stringify(manifest));
 if(delayed&&url===manifest.periods['2025'].url)await delayed.promise;
 return new Response(corrupt?Buffer.from('corrupt'):assets.get(url));
};
(async()=>{
 await evaluate('DashboardData.open().then(s=>globalThis.store=s)');
 assert.equal(historyRequests,0);
 await evaluate('Promise.all([store.ensureHistory(),store.ensureHistory()])');
 assert.equal(historyRequests,1);
 await evaluate('store.loadPeriod("2025")');
 assert.equal(evaluate('store.summary.regions[0].loadedBucket.addresses[0].count'),5);
 delayed={};delayed.promise=new Promise(r=>delayed.resolve=r);
 const slow=evaluate('store.loadPeriod("2025")');await evaluate('store.loadPeriod("2026")');delayed.resolve();await slow;delayed=null;
 assert.equal(evaluate('store.summary.regions[0].loadedBucket.count'),7);
 corrupt=false;
 manifest.recommendations=asset('packed',{encoding:'catalog-v1',metadata:{target_year:'2026'},fields:['house_match_score','quality_flags'],rows:[[0,'1',72,['few trades']]]});
 await evaluate('DashboardData.open().then(s=>globalThis.packedStore=s)');
 assert.equal(evaluate('packedStore.recommendations.recommendations[0].building_name'),'끝 단지');
 assert.equal(evaluate('packedStore.recommendations.recommendations[0].building_key'),'A');
 assert.equal(evaluate('packedStore.recommendations.recommendations[0].house_match_score'),72);
 corrupt=true;await assert.rejects(evaluate('store.loadPeriod("2025")'),/무결성/);
 assert.equal(evaluate('store.summary.regions[0].loadedBucket.count'),7);
 // Every statically referenced app element exists in the shipped HTML.
 const html=read('index.html');const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]));
 assert.equal(ids.size,[...html.matchAll(/\bid="([^"]+)"/g)].length,'Duplicate IDs');
 for(const name of ['leaflet.css','leaflet.js']){
  const tag=html.match(new RegExp(`<[^>]+(?:href|src)="vendor/${name}"[^>]*>`))[0];
  const hash=tag.match(/integrity="sha256-([^"]+)"/)[1];
  assert.equal(createHash('sha256').update(fs.readFileSync(`${root}/web/vendor/${name}`)).digest('base64'),hash,'Vendored Leaflet SRI mismatch');
 }
 for(const match of read('app.js').matchAll(/getElementById\("([^"]+)"\)/g)){
   if(match[1]==='type-select')continue; // dynamically created only when a type is selected
   assert.ok(ids.has(match[1]),`Missing static element ${match[1]}`);
 }
 console.log('UI data checks passed: complete pagination, missing scores, search, historical metrics, hashes, race handling, static element bindings.');
})().catch(e=>{console.error(e);process.exitCode=1;});
