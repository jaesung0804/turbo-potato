const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const read=name=>fs.readFileSync(`web/${name}`,'utf8');
const elements=new Map();
const context=vm.createContext({console,crypto:webcrypto,TextDecoder,Intl,Date,Map,Set,document:{getElementById:id=>{
  if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:'',hidden:true,classList:{toggle(){}}});
  return elements.get(id);
}}});
const code=read('research.js').replace(/ResearchReview\.init\(\)\.catch[\s\S]*$/,'');
vm.runInContext(read('release-status.js')+'\n'+code,context);
const run=source=>vm.runInContext(source,context);
context.manifest={generated_at:'2026-09-10',collection:{fetched_at:'2026-09-10T22:48:13Z'}};
let status=run('EstateReleaseStatus.describe(manifest,new Date("2026-09-15T01:00:00Z"))');
assert.equal(status.through,'2026-09-10');assert.equal(status.collected,'2026-09-11');assert.equal(status.ageDays,5);
assert.equal(status.state,'unknown');assert.ok(!status.message.includes('완료'));
context.manifest.release_status={built_at:'2026-09-15T02:00:00Z',mode:'ui_only',transaction_refresh:{status:'skipped_missing_key'}};
status=run('EstateReleaseStatus.describe(manifest,new Date("2026-09-15T03:00:00Z"))');
assert.equal(status.through,'2026-09-10');assert.equal(status.warning,true);assert.equal(status.built,'2026-09-15');
assert.equal(run('EstateReleaseStatus.describe({generated_at:"bad"}).ageDays'),null);
assert.equal(run('EstateReleaseStatus.describe({generated_at:"2099-01-01"},new Date("2026-09-15")).ageDays'),null);
const review={schema_version:1,kind:'existing_evaluation_review',production_changed:false,evaluation_date:'2026-09-09',potential:{
  comparison:[{method:'learned_price',same_origins:17,median_origin_complex_excess_pct:.8236,median_observation_rate_pct:64.02}],
  paired_origins:[{origin:'2020-01-01',model_excess_pct:1,baseline_excess_pct:2,difference_pp:-1,model_selected:100,model_observed:60,baseline_selected:100,baseline_observed:70}],
  common_origins:17,model_evaluated_origins:29,excluded_origins:Array(12).fill({}),model_beats_baseline_origins:7,paired_median_difference_pp:-.7424
},long_horizon:{five_year:{passed:false},multiple_paths:{passed:false}}};
context.review=review;
run('ResearchReview.renderReview(review)');
assert.ok(elements.get('comparison-rows').innerHTML.includes('+0.82%'));
assert.ok(elements.get('comparison-decision').textContent.includes('7/17'));
assert.ok(elements.get('comparison-decision').textContent.includes('−0.74')||elements.get('comparison-decision').textContent.includes('-0.74%p'));
assert.ok(elements.get('paired-rows').innerHTML.includes('60 / 100'));
assert.equal(elements.get('comparison-body').hidden,false);
context.review.potential.comparison[0].method='<img src=x onerror=alert(1)>';
assert.ok(!run('ResearchReview.comparisonRows(review)').includes('<img'));
const html=read('research.html'),ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
assert.equal(new Set(ids).size,ids.length);
for(const m of code.matchAll(/getElementById\('([^']+)'\)/g))assert.ok(ids.includes(m[1]),`Missing research element ${m[1]}`);
assert.ok(!code.includes('DashboardData.open('));
async function integrity(){
  const buffer=new TextEncoder().encode(JSON.stringify(review));
  context.fetch=async()=>({ok:true,arrayBuffer:async()=>buffer.buffer});
  context.asset={url:'data/model_review.json',bytes:buffer.byteLength,sha256:Buffer.from(await webcrypto.subtle.digest('SHA-256',buffer)).toString('hex')};
  assert.equal((await run('ResearchReview.loadReview(asset)')).production_changed,false);
  context.asset.sha256='0'.repeat(64);
  await assert.rejects(run('ResearchReview.loadReview(asset)'),/무결성/);
  context.asset.url='https://example.test/review.json';
  await assert.rejects(run('ResearchReview.loadReview(asset)'),/포함되지/);
}
integrity().then(()=>console.log('Research UI: dates, missing refresh evidence, paired comparisons, counts, safe rendering and integrity verified.')).catch(error=>{console.error(error);process.exitCode=1;});
