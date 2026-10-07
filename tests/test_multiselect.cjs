// User scenarios: OR within each field, AND across fields, hierarchy and map parity.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const read = name => fs.readFileSync(`web/${name}`, 'utf8');
const context = vm.createContext({console, window:{addEventListener(){}}, Map, Set, URL, setTimeout});
vm.runInContext(read('result-pages.js')+'\n'+read('filter-select.js')+'\n'+read('app.js').replace(/init\(\)\.catch\([\s\S]*$/, ''), context);
const run = code => vm.runInContext(code, context);
const ids = () => Array.from(run('typeItems().map(x=>x.building.key)'));
run(`
const fixture = (key,year,area,lines) => ({key,building_name:key,complex_key:key,built_year:year,count:2,
  metrics:{area_pyeong:{avg:area},price_billion:{median:8}},subway_lines:lines,subway_distance_m:300});
state.year='2026';state.recentEvidenceOnly=false;
state.summary={generated_at:'2026-10-06',years:['2026'],regions:[
 {code:'s1',gu_code:'s',sido_name:'서울특별시',gu_name:'강남구',dong_name:'역삼동',loadedBucket:{addresses:[fixture('S-new',2021,14,['2호선']),fixture('S-old',2000,34,['3호선'])]}},
 {code:'g1',gu_code:'g',sido_name:'경기도',gu_name:'성남시 분당구',dong_name:'정자동',loadedBucket:{addresses:[fixture('G-semi',2011,26,['신분당선']),fixture('G-large',2016,35,['2호선','신분당선'])]}},
 {code:'i1',gu_code:'i',sido_name:'인천광역시',gu_name:'남동구',dong_name:'구월동',loadedBucket:{addresses:[fixture('I-new',2023,20,[])]}}
]};state.regionByCode=new Map(state.summary.regions.map(r=>[r.code,r]));
`);
assert.equal(ids().length,5);
run(`state.selectedSido=['서울특별시','경기도'];state.ageRange=['new','semi_new'];`);
assert.deepEqual(ids(),['S-new','G-semi','G-large']);
run(`state.areaRange=['lte14','gt14_lte26'];`);
assert.deepEqual(ids(),['S-new','G-semi']);
run(`state.subwayLine=['2호선','신분당선'];`);
assert.deepEqual(ids(),['S-new','G-semi']);
run(`state.subwayLine=['3호선'];`);
assert.deepEqual(ids(),[]);
run(`state.subwayLine=[];state.ageRange=[];state.areaRange=[];state.selectedGu=['s','g'];state.selectedDong=['s1','g1'];`);
assert.equal(ids().length,4);
run(`state.selectedSido=['경기도'];reconcileRegionSelections();`);
assert.deepEqual(Array.from(run('state.selectedGu')),['g']);
assert.deepEqual(Array.from(run('state.selectedDong')),['g1']);
assert.deepEqual(ids(),['G-semi','G-large']);
// Removing the final parent selection restores all provinces but keeps valid child constraints.
run(`state.selectedSido=[];reconcileRegionSelections();`);
assert.deepEqual(ids(),['G-semi','G-large']);
// Replacing the parent with an unrelated province clears incompatible children.
run(`state.selectedSido=['인천광역시'];reconcileRegionSelections();`);
assert.deepEqual(Array.from(run('state.selectedGu')),[]);
assert.deepEqual(Array.from(run('state.selectedDong')),[]);
assert.deepEqual(ids(),['I-new']);
run(`state.selectedSido=['서울특별시','경기도'];state.mapMode='sido';`);
assert.deepEqual(Array.from(run('state.summary.regions.filter(regionInMapScope).map(r=>r.code)')),['s1','g1']);
assert.match(run('selectedSidoLabel()'),/서울특별시.*경기도/);
run(`state.regionByMapCode.set('legacy',[state.summary.regions[2],state.summary.regions[1]]);state.metric='count';state.mapDist={min:0,mid:1,max:4};`);
assert.equal(run(`styleFeature({properties:{ADM_CD:'legacy'}}).fillOpacity`),.94,'A geometry shared by changed districts must highlight every selected location');
// Exact boundaries, unknown ages/areas, duplicate line memberships and clearing all.
run(`state.selectedSido=[];state.ageRange=['new','semi_new'];`);
for (const [year,expected] of [[2021,true],[2020,true],[2011,true],[2010,false],[null,false]]) {
  assert.equal(run(`ageMatches({built_year:${year}})`),expected);
}
run(`state.areaRange=['lte14','gt26_lte34'];`);
for (const [area,expected] of [[14,true],[14.01,false],[26,false],[26.01,true],[34,true],[34.01,false],[null,false]]) {
  assert.equal(run(`areaMatches({metrics:{area_pyeong:{avg:${area}}}})`),expected);
}
run(`state.ageRange=[];state.areaRange=[];state.subwayLine=['2호선','신분당선'];`);
assert.equal(ids().length,3,'A building served by both chosen lines appears only once');
run(`state.subwayLine=[];`);assert.equal(ids().length,5);
assert.equal(run(`ageMatches({built_year:null}) && areaMatches({})`),true);
console.log('Multi-select scenarios passed: unions, intersections, boundaries, hierarchy, map and reset.');
