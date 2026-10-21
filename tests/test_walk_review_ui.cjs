const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const ui = require('../web/walk-review.js');
const raw = fs.readFileSync('web/walk-review.json'), data = JSON.parse(raw);
assert.equal(ui.validate(data), data);
assert.deepEqual(ui.counts(data.rows), {complexes:14,routes:42,estimated:31,disconnected:11,terrain_reference:27,terrain_excluded:4,route_review:30,review_estimated:25,review_disconnected:5,comparison_estimated:6});
assert.ok(fs.readFileSync('web/walk-review.js','utf8').includes(crypto.createHash('sha256').update(raw).digest('hex')));
for (const key of ['distance','time','forward','reverse']) for (const direction of ['asc','desc']) {
  const rows = data.rows.slice().sort((a,b)=>ui.compare(a,b,key,direction));
  const values = rows.map(r=>ui.sortValue(r,key));
  const firstNull = values.indexOf(null);
  assert.equal(firstNull, ['forward','reverse'].includes(key) ? 3 : 6);
  assert.ok(values.slice(firstNull).every(v=>v===null));
  for (let i=1;i<firstNull;i++) assert.ok(direction==='asc' ? values[i]>=values[i-1] : values[i]<=values[i-1]);
}
// Genuine zero remains a sortable value; current eligible subset need not contain one.
const zeroBase = data.rows.find(r=>r.terrain.status==='reference' && r.route_review_status!=='needs_review');
const zero = {...zeroBase,terrain:{...zeroBase.terrain,forward_m:0}};
assert.equal(ui.sortValue(zero,'forward'),0);
for (const r of data.rows.filter(r=>r.terrain.status==='excluded')) {
  assert.ok(r.terrain.forward_m>0);
  assert.equal(ui.sortValue(r,'forward'),null);
  assert.match(ui.rowHtml(r).split('</tr>')[0],/지형 비교 제외/);
  assert.match(ui.rowHtml(r),/검토용 계산값/);
}
const opts={purpose:'all',destination:'all',query:'',sort:'forward',direction:'asc'};
assert.deepEqual(ui.selected(data,opts).map(r=>r.id),ui.selected(data,{...opts,sort:'name'}).map(r=>r.id));
for (const purpose of ['station','school','bus_stop']) {
  assert.equal(ui.selected(data,{...opts,purpose,sort:'distance'}).length,14);
  for (const target of ui.destinationOptions(data,purpose)) {
    const rows=ui.selected(data,{...opts,purpose,destination:target.value});
    assert.ok(rows.length>0 && rows.every(r=>ui.destinationKey(r)===target.value));
  }
}
const busTargets=ui.destinationOptions(data,'bus_stop').filter(x=>x.label.includes('문래중학교'));
assert.equal(busTargets.length,2);assert.notEqual(busTargets[0].value,busTargets[1].value);assert.notEqual(busTargets[0].label,busTargets[1].label);
assert.equal(ui.selected(data,{...opts,query:'없는단지'}).length,0);
assert.equal(ui.selected(data,{...opts,query:'국화맨션'}).length,3);
for (const r of data.rows.filter(r=>r.status==='disconnected')) {
  const main=ui.rowHtml(r).split('</tr>')[0];
  assert.match(main,/계산 못함/);assert.match(main,/자료 없음/);
  assert.doesNotMatch(main,/약 0(?:\.0)?(?:m|분)/);
}
const html=ui.rowHtml({...data.rows[0],name:'<img src=x onerror=alert(1)>'});
assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'));
for (const mutation of [
  d=>d.rows.find(r=>r.status==='disconnected').distance.total_m=0,
  d=>d.rows[0].time.base_min++,
  d=>d.rows[0].terrain.is_measured_walk=true,
  d=>d.rows[0].terrain.used_in_duration=true,
  d=>d.rows.push(d.rows[0]),
  d=>d.rows.find(r=>r.terrain.status==='excluded').terrain.status='reference',
  d=>d.rows.find(r=>r.purpose==='station').station_name=null,
  d=>d.rows.find(r=>r.route_review_status==='needs_review').route_review_status='no_specific_flag',
  d=>d.rows.find(r=>r.route_review_reasons.length).route_review_reasons=[],
  d=>d.rows.find(r=>r.route_review_reasons.length).route_review_reasons[0].code='unknown',
  d=>d.quality_screening.remaining_estimates_are_measured=true
]) {const bad=structuredClone(data);mutation(bad);assert.throws(()=>ui.validate(bad));}

const groups=ui.complexGroups(data,{query:'',sort:'name',direction:'asc'});
assert.equal(groups.length,14);
for(const g of groups){
  assert.deepEqual(Object.keys(g.routes).sort(),['bus_stop','school','station']);
  assert.equal((ui.complexHtml(g).match(/data-summary-route-id=/g)||[]).length,3);
}
for(const sort of ['station','school','bus_stop'])for(const direction of ['asc','desc']){
  const sorted=ui.complexGroups(data,{sort,direction});let missing=false,last=null;
  for(const g of sorted){const v=ui.sortValue(g.routes[sort],'distance');if(v===null){missing=true;continue;}assert.equal(missing,false);if(last!==null)assert.ok(direction==='asc'?v>=last:v<=last);last=v;}
}
assert.equal(ui.complexGroups(data,{query:'문래공원한신'}).length,1);
const held=data.rows.filter(r=>r.route_review_status==='needs_review');assert.equal(held.length,30);
for(const r of held){
  assert.equal(ui.sortValue(r,'distance'),null);assert.equal(ui.sortValue(r,'time'),null);assert.equal(ui.sortValue(r,'forward'),null);
  const main=ui.rowHtml(r).split('</tr>')[0];
  assert.match(main,/실제 통행 불가 판정이 아닙니다/);
  if(r.status==='disconnected'){assert.equal(r.distance.total_m,null);assert.match(main,/계산 못함/);}
  else {assert.ok(r.distance.total_m>0);assert.match(main,/거리 비교 보류/);assert.match(ui.rowHtml(r),/기존 거리 합계/);assert.doesNotMatch(main,/약 [\d,.]+(?:m|분)/);}
  for(const reason of r.route_review_reasons)assert.ok(main.includes(reason.label));
}
const miwonBus=data.rows.find(r=>r.name==='문래미원'&&r.purpose==='bus_stop');
assert.deepEqual(miwonBus.route_review_reasons.map(x=>x.code),['building_crossing','connector_dominant']);
assert.ok(data.rows.filter(r=>r.name==='남성').every(r=>r.route_review_status==='no_specific_flag'));
assert.equal(data.rows.find(r=>r.name==='문래두산위브'&&r.purpose==='bus_stop').route_review_status,'no_specific_flag');
for(const purpose of ['station','school','bus_stop'])for(const direction of ['asc','desc']){
  const rows=ui.selected(data,{...opts,purpose,sort:'distance',direction});
  const categories=rows.map(ui.comparisonGroup);assert.deepEqual(categories,categories.slice().sort());
  const html=ui.comparisonHtml(rows);
  for(const group of new Set(categories))assert.ok(html.includes('data-comparison-group="'+group+'"'));
}
const station=data.rows.find(r=>r.station_name==='문래역');
const alternate={...station,id:'synthetic-other-exit',complex_id:'synthetic-complex',destination_id:'synthetic:exit4',destination:'문래역 4번출구'};
const multi={rows:[station,alternate]};
assert.equal(ui.destinationKey(station),ui.destinationKey(alternate));
assert.deepEqual(ui.destinationOptions(multi,'station'),[{value:'station:문래역',label:'문래역'}]);
assert.equal(ui.selected(multi,{purpose:'station',destination:'station:문래역',query:'',sort:'distance',direction:'asc'}).length,2);
assert.match(ui.rowHtml(alternate),/문래역 4번출구/);
assert.ok(ui.destinationOptions(data,'station').every(o=>!o.label.includes('출구')));
assert.equal(ui.destinationOptions(data,'station').length,4);

const page=fs.readFileSync('web/static-location.html','utf8');
assert.ok(page.includes('id="walk-review"'));
for (const entry of ['web/index.html','web/research.html']) assert.ok(fs.readFileSync(entry,'utf8').includes('static-location.html#walk-review'));
assert.ok(fs.readFileSync('estate/site/ui.py','utf8').includes("'walk-review.js', 'walk-review.json'"));
async function run() {
  global.crypto=crypto.webcrypto;
  global.fetch=async()=>({ok:true,arrayBuffer:async()=>raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)});
  assert.equal((await ui.load()).rows.length,42);
  global.fetch=async()=>({ok:true,arrayBuffer:async()=>new TextEncoder().encode('{}').buffer});
  await assert.rejects(ui.load(),/무결성/);
  global.fetch=async()=>({ok:false});
  await assert.rejects(ui.load(),/불러오지/);
  console.log('Walk review UI passed: 14 grouped complexes; 42 preserved routes; station-name keys; 6 comparison/25 held/11 missing; reason visibility; raw values preserved; numeric exclusion; genuine zero; source guard.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
