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

// The same canonical prices must yield the same score everywhere. Sample size
// describes evidence and must never make an equal-price comparison look cheap.
evaluate(`globalThis.priceRec={house_match_score:83.4,current_valuation:{status:'available',price_billion:10,effective_sample_size:20,score_error_scale:.2},valuation_comparison:{status:'available',neutral_price_billion:10,comparison_price_billion:10,score:50,discount_pct:0,score_error_scale:.2}};`);
assert.equal(evaluate('reviewScoreAtPrice(priceRec,10)'),50);
assert.equal(evaluate('reviewScoreAtPrice(priceRec,10*Math.exp(-.2*Math.atanh(.5)))'),70);
assert.ok(evaluate('reviewScoreAtPrice(priceRec,8)')>50);
assert.ok(evaluate('reviewScoreAtPrice(priceRec,12)')<50);
for(const invalid of ['0','-1','NaN','Infinity','null'])assert.equal(evaluate(`reviewScoreAtPrice(priceRec,${invalid})`),null);
assert.equal(evaluate('reviewScoreAtPrice({...priceRec,current_valuation:{...priceRec.current_valuation,effective_sample_size:0}},10)'),50);
assert.equal(evaluate('reviewScoreAtPrice({current_valuation:{status:"available",price_billion:10}},10)'),null);
assert.equal(evaluate('neutralPrice({fair_price_per_pyeong:4000,area_pyeong:25,neutral_price_billion:10,house_match_score:83.4})'),null);
assert.equal(evaluate('neutralPrice(null)'),null);
assert.equal(evaluate('colorFor(50,{min:10,mid:50,max:90})'),'rgb(247, 247, 247)');
evaluate(`globalThis.reportedCase={house_match_score:83.4,current_valuation:{status:'available',price_billion:.518149,effective_sample_size:2,score_error_scale:.15}};`);
assert.equal(evaluate('neutralPrice(reportedCase)'),.5181);
assert.equal(evaluate('reviewScoreAtPrice(reportedCase,.5181)'),50);
assert.ok(evaluate('reviewScoreAtPrice(reportedCase,.52)')<50);
assert.ok(evaluate('reviewScoreAtPrice(reportedCase,.52)')>48);
assert.equal(evaluate('reviewScoreAtPrice({...reportedCase,current_valuation:{...reportedCase.current_valuation,effective_sample_size:100}},.52)'),evaluate('reviewScoreAtPrice(reportedCase,.52)'));
assert.equal(evaluate("neutralPrice({neutral_price_billion:10,current_valuation:{status:'insufficient_history'}})"),null);
assert.equal(evaluate('totalPriceLabel(.603149)'),'0.6031억');
assert.equal(evaluate('totalPriceLabel(8.025)'),'8.025억');
// Display-rounding and monotonicity are shared with Python canonical pricing.
assert.equal(evaluate('canonicalPrice(.51815)'),.5182);
assert.equal(evaluate('priceBillion({metrics:{price_billion:{avg:12,median:10}}})'),10);
for(let p=1;p<20;p+=.125){context.p=p;assert.ok(evaluate('reviewScoreAtPrice(priceRec,p)>=reviewScoreAtPrice(priceRec,p+.125)'));}
assert.equal(evaluate('askingPriceResult(priceRec,"10")').includes('50점 기준가와 같습니다.'),true);
evaluate(`priceRec.current_valuation.confidence={status:'available',grade:'D',lower_price_billion:6,upper_price_billion:17,historical_grade_coverage_pct:80.97};`);
assert.equal(evaluate('reviewScoreAtPrice(priceRec,10)'),50);
assert.match(evaluate('priceConfidencePanel(priceRec)'),/가격 신뢰도 D/);
assert.match(evaluate('priceConfidencePanel(priceRec)'),/77.2%/);
assert.match(evaluate('priceConfidencePanel(priceRec)'),/상승 가능성이나 가격 점수와는 별개/);
assert.equal(evaluate('priceConfidenceLabel({})'),'가격 신뢰도 미산출');
assert.match(evaluate('householdEvidenceNote({households_verified:true,household_observed_at:"2026-09-08",household_source_url:"https://example.gov/official"})'),/공식 단지 전체 세대수/);
assert.match(evaluate('householdEvidenceNote({households_verified:false})'),/기존 시설 자료/);
evaluate(`globalThis.savedRecommendationForItem=aiRecommendationForItem;
aiRecommendationForItem=()=>({price_billion:99,current_valuation:{status:'available',price_billion:10,month:'2026-09',feature_cutoff:'2026-08-01',recent_trade_count:0,score_error_scale:.2},valuation_comparison:{status:'no_recent_transactions'},recent_price_comparison:{window_start:'2026-06-10',window_end:'2026-09-07',data_through:'2026-09-07',trade_count:0}});
globalThis.emptyWindowPanel=askingPricePanel({region:{code:'test'},building:{key:'test'}});
aiRecommendationForItem=savedRecommendationForItem;`);
assert.match(evaluate('emptyWindowPanel'),/연간 중앙가로 대체하지 않습니다/);
assert.match(evaluate('emptyWindowPanel'),/id="asking-price-input"[^>]*value=""/);
assert.match(evaluate('emptyWindowPanel'),/50점 가격 넣기/);

// Selecting B while the shared transaction ledger loads for A must refresh B.
evaluate(`globalThis.originalRenderSelectedRegion=renderSelectedRegion;globalThis.renderedSelections=[];renderSelectedRegion=()=>renderedSelections.push(state.selectedTypeId);state.selectedTypeId='A';state.transactionLoading=true;state.selectedTypeId='B';settleTransactionValuations();`);
assert.deepEqual(Array.from(evaluate('renderedSelections')),['B']);
assert.equal(evaluate('state.transactionLoading'),false);
evaluate(`state.transactionLoading=true;settleTransactionValuations({message:'temporary failure'});`);
assert.equal(evaluate('state.transactionError'),'temporary failure');
assert.deepEqual(Array.from(evaluate('renderedSelections')),['B','B']);
evaluate(`state.selectedTypeId=null;settleTransactionValuations();renderSelectedRegion=originalRenderSelectedRegion;`);
assert.equal(evaluate('renderedSelections.length'),2);

// The explicit all-evidence view preserves unscored listings and CSV rows.
assert.equal(evaluate('state.recentEvidenceOnly'),true);
evaluate('state.recentEvidenceOnly=false;');
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
// Explicit score filters are inclusive, apply to every metric/export, and never
// borrow a current score for an earlier year or a similarly named apartment.
evaluate(`state.summary.regions[0].loadedBucket.addresses.push({
 ...state.summary.regions[0].loadedBucket.addresses[0],key:'lot3|84.91',complex_key:'lot3'});
 state.recommendationByType.set('1|lot1|84.91',{house_match_score:99,valuation_comparison:{status:'available',score:64.9,neutral_price_billion:10,comparison_price_billion:9,score_error_scale:.2},quality_flags:[]});
 state.recommendationByType.set('1|lot2|84.92',{house_match_score:1,valuation_comparison:{status:'available',score:65,neutral_price_billion:10,comparison_price_billion:9,score_error_scale:.2},quality_flags:[]});
 state.view=null;state.minReviewScore=65;`);
assert.equal(evaluate('typeItems().length'),1);
assert.equal(evaluate('typeItems()[0].building.key'),'lot2|84.92');
assert.equal(evaluate('regionFilteredMetricValue(state.summary.regions[0],"count")'),5);
assert.equal(evaluate('groupedBuildings().length'),1);
evaluate(`ResultPages.downloadCsv=(name,headers,rows)=>{globalThis.exported=rows;};exportResults();`);
assert.equal(evaluate('exported.length'),1);
assert.equal(evaluate('exported[0][13]'),65);
evaluate(`state.minReviewScore=64.9;`);assert.equal(evaluate('typeItems().length'),2);
evaluate(`state.minReviewScore=70;`);assert.equal(evaluate('typeItems().length'),0);
evaluate(`state.minReviewScore=0;`);assert.equal(evaluate('typeItems().length'),2);
evaluate(`state.year='2025';`);assert.equal(evaluate('typeItems().length'),0);
evaluate(`state.minReviewScore=null;`);assert.equal(evaluate('typeItems().length'),3);
evaluate(`state.year='2026';state.minReviewScore=65;state.search='84.91';`);
assert.equal(evaluate('typeItems().length'),0);
evaluate(`state.search='';state.minReviewScore=null;state.summary.regions[0].loadedBucket.addresses.pop();state.view=null;`);
// Default evidence requires both the model history and the price window. Its
// explicit release restores all types; sparse history never changes the score.
evaluate(`state.recommendationByType.get('1|lot1|84.91').current_valuation={status:'available',price_billion:10,recent_trade_count:3,score_error_scale:.2};
state.recommendationByType.get('1|lot1|84.91').valuation_comparison.comparison_trade_count=3;
state.recommendationByType.get('1|lot2|84.92').current_valuation={status:'available',price_billion:10,recent_trade_count:0,score_error_scale:.2};
state.recommendationByType.get('1|lot2|84.92').valuation_comparison.comparison_trade_count=10;
state.recentEvidenceOnly=true;`);
assert.equal(evaluate('typeItems().length'),1);
assert.equal(evaluate('typeItems()[0].building.key'),'lot1|84.91');
assert.equal(evaluate('state.recommendationByType.get("1|lot2|84.92").valuation_comparison.score'),65);
evaluate(`state.recentEvidenceOnly=false;state.minPriceBillion=9.5;`);
assert.equal(evaluate('typeItems().length'),0,'Budget must use the same recent comparison price of 9, not annual 10/11');
evaluate(`state.minPriceBillion=8;`);
assert.equal(evaluate('typeItems().length'),2);
evaluate(`state.minPriceBillion=null;`);
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
manifest.transaction_valuation=asset('transaction-valuations',{schema_version:1,by_key:{'1|A':{status:'available',trade_count:2,recent_transactions:[],monthly:[]}}});
let delayed=null,corrupt=false,historyRequests=0,transactionRequests=0;
context.fetch=async url=>{
 if(url===manifest.history.url)historyRequests++;if(url===manifest.transaction_valuation.url)transactionRequests++;
 if(url==='data/dashboard_manifest.json')return new Response(JSON.stringify(manifest));
 if(delayed&&url===manifest.periods['2025'].url)await delayed.promise;
 return new Response(corrupt?Buffer.from('corrupt'):assets.get(url));
};
(async()=>{
 await evaluate('DashboardData.open().then(s=>globalThis.store=s)');
 assert.equal(historyRequests,0);assert.equal(transactionRequests,0);
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
 await evaluate('Promise.all([packedStore.ensureTransactionValuations(),packedStore.ensureTransactionValuations()])');
 assert.equal(transactionRequests,1);assert.equal(evaluate('packedStore.recommendations.recommendations[0].transaction_valuation.trade_count'),2);
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
   if(['type-select','asking-price-input','asking-price-result','use-neutral-price'].includes(match[1]))continue; // dynamically created only when a type is selected
   assert.ok(ids.has(match[1]),`Missing static element ${match[1]}`);
 }
 console.log('UI data checks passed: complete pagination, missing scores, search, historical metrics, hashes, race handling, static element bindings.');
})().catch(e=>{console.error(e);process.exitCode=1;});
