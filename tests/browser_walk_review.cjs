/* Browser checks of grouped complexes and facility-specific comparisons. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const site=path.resolve(process.argv[2]||'web'),output=path.resolve(process.argv[3]||'.work/ui-revision-20261021/browser');
fs.mkdirSync(output,{recursive:true});
const data=JSON.parse(fs.readFileSync(path.join(site,'walk-review.json')));
const byid=new Map(data.rows.map(r=>[r.id,r]));
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.css':'text/css; charset=utf-8'};
const server=http.createServer((req,res)=>{
  const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  const target=path.resolve(site,'.'+(pathname==='/'?'/index.html':pathname));
  if(!target.startsWith(site+path.sep)||!fs.existsSync(target)||!fs.statSync(target).isFile()){res.writeHead(404);res.end('Not found');return;}
  res.setHeader('Content-Type',mime[path.extname(target)]||'application/octet-stream');fs.createReadStream(target).pipe(res);
});
const external=[],errors=[],screenshots=[],checks=[];
let browser,context;
async function run(){
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--disable-dev-shm-usage','--renderer-process-limit=2']});
  context=await browser.newContext({viewport:{width:1440,height:1100},deviceScaleFactor:1});
  await context.route('**/*',route=>{if(route.request().url().startsWith(origin+'/'))return route.continue();external.push(route.request().url());return route.abort();});
  await context.addInitScript(()=>{localStorage.setItem('qa-walk-note','preserved');});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  const ready=()=>page.waitForSelector('#walk-body:not([hidden])');
  const cards=()=>page.locator('#walk-complex-cards .walk-complex-card');
  const rows=p=>page.locator('#walk-'+p+'-rows tr[data-route-id]');
  const shot=async name=>{await page.screenshot({path:path.join(output,name)});screenshots.push(name);};
  await page.goto(origin+'/static-location.html#walk-review',{waitUntil:'networkidle'});await ready();
  assert.equal(await cards().count(),14);
  assert.equal(await page.locator('#walk-complexes').isVisible(),true);
  assert.equal(await page.locator('#walk-destinations').isHidden(),true);
  for(const card of await cards().all()){
    assert.equal(await card.locator('[data-summary-route-id]').count(),3);
    for(const p of ['station','school','bus_stop'])assert.equal(await card.locator('[data-purpose="'+p+'"]').count(),1);
  }
  const allIds=await page.locator('[id]').evaluateAll(els=>els.map(e=>e.id));
  assert.equal(new Set(allIds).size,allIds.length);
  await page.locator('#walk-review').evaluate(el=>el.scrollIntoView({block:'start'}));await shot('desktop-complex-overview-1440.png');
  checks.push('default shows 14 complexes, each with station/school/bus once; comparison hidden; no duplicate DOM IDs');
  for(const sort of ['station','school','bus_stop'])for(const direction of ['asc','desc']){
    await page.selectOption('#walk-summary-sort',sort);await page.selectOption('#walk-summary-direction',direction);
    const ids=await cards().evaluateAll(els=>els.map(e=>e.dataset.complexId));
    let last=null,missing=false;
    for(const id of ids){
      const r=data.rows.find(r=>r.complex_id===id&&r.purpose===sort);
      const v=r.route_review_status==='needs_review'?null:r.distance.total_m;
      if(v===null){missing=true;continue;}assert.equal(missing,false);
      if(last!==null)assert.ok(direction==='asc'?v>=last:v<=last);last=v;
    }
  }
  await page.click('#walk-reset');
  await page.fill('#walk-query','문래공원한신');assert.equal(await cards().count(),1);
  assert.equal(await cards().first().locator('details:not([open])').count(),3);
  assert.equal((await cards().first().innerText()).includes('982'),false);
  await cards().first().locator('[data-purpose="station"] summary').click();
  assert.match(await cards().first().innerText(),/기존 거리 합계 982\.4m · 시간 12\.3분/);
  assert.match(await cards().first().innerText(),/문래역 1번출구/);
  assert.match(await cards().first().innerText(),/거리 비교 보류/);
  await page.click('#walk-reset');
  checks.push('summary distance sorts null/review last both directions; Hanshin raw values preserved in explicit review detail');
  await page.click('#walk-tab-destinations');assert.equal(await page.locator('#walk-complexes').isHidden(),true);
  assert.equal(await page.locator('#walk-panel-station').isVisible(),true);
  assert.equal(await page.locator('#walk-panel-school').isHidden(),true);
  assert.equal(await page.locator('#walk-panel-bus_stop').isHidden(),true);
  const stationLabels=await page.locator('#walk-station-destination option').allTextContents();
  assert.equal(stationLabels.length,5);assert.ok(stationLabels.includes('문래역'));assert.ok(stationLabels.every(x=>!x.includes('출구')));
  async function sortRows(p,key,direction){
    await page.selectOption('#walk-'+p+'-sort',key);await page.selectOption('#walk-'+p+'-direction',direction);
    const ids=await rows(p).evaluateAll(els=>els.map(e=>e.dataset.routeId));
    let missing=false,last=null;
    for(const id of ids){
      const r=byid.get(id);let v=key==='distance'?r.distance.total_m:key==='time'?r.time.base_min:r.terrain.status==='reference'?r.terrain[key+'_m']:null;
      if(r.route_review_status==='needs_review')v=null;
      if(v===null){missing=true;continue;}assert.equal(missing,false);
      if(last!==null)assert.ok(direction==='asc'?v>=last:v<=last);last=v;
    }
  }
  for(const p of ['station','school','bus_stop']){
    await page.click('#walk-tab-'+p);assert.equal(await rows(p).count(),14);
    for(const key of ['distance','time'])for(const direction of ['asc','desc'])await sortRows(p,key,direction);
    for(const row of await rows(p).all()){
      const r=byid.get(await row.getAttribute('data-route-id'));
      if(r.route_review_status==='needs_review'){
        for(const reason of r.route_review_reasons)assert.ok((await row.innerText()).includes(reason.label));
        assert.match(await row.innerText(),/실제 통행 불가 판정이 아닙니다/);
        if(r.status!=='disconnected'){assert.match(await row.innerText(),/거리 비교 보류/);assert.doesNotMatch(await row.locator('.walk-distance').innerText(),/약 [\d,.]+/);}
      }
    }
  }
  await page.click('#walk-tab-station');await page.selectOption('#walk-station-destination','station:문래역');
  assert.equal(await rows('station').count(),2);
  assert.ok((await rows('station').locator('.walk-destination').allTextContents()).every(x=>!x.includes('출구')));
  const heldRow=page.locator('#walk-station-rows tr[data-route-id="'+data.rows.find(r=>r.name==='문래공원한신'&&r.purpose==='station').id+'"]');
  await heldRow.locator('button').focus();await page.keyboard.press('Enter');
  const detailId=await heldRow.locator('button').getAttribute('aria-controls');
  assert.match(await page.locator('[id="'+detailId+'"]').innerText(),/문래역 1번출구/);
  await page.locator('#walk-destinations').evaluate(el=>el.scrollIntoView({block:'start'}));await shot('desktop-station-comparison-1440.png');
  await page.locator('#walk-tab-station').focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#walk-panel-school').isVisible(),true);
  await page.selectOption('#walk-school-destination','B000002002');
  for(const key of ['forward','reverse'])for(const direction of ['asc','desc'])await sortRows('school',key,direction);
  await page.click('#walk-tab-station');assert.equal(await page.inputValue('#walk-station-destination'),'station:문래역');
  await page.click('#walk-tab-school');assert.equal(await page.inputValue('#walk-school-destination'),'B000002002');
  checks.push('separate station/school/bus panels; station-name selector retains exits in details; independent filters; keyboard tabs; numeric sorts');
  await page.click('#walk-tab-bus_stop');
  assert.match(await page.locator('#walk-panel-bus_stop').innerText(),/확인되지 않은 버스 노선·방향은 채우지 않습니다/);
  await page.selectOption('#walk-bus_stop-direction','asc');
  const qualityGroups=await page.locator('#walk-bus_stop-rows [data-comparison-group]').evaluateAll(els=>els.map(e=>e.dataset.comparisonGroup));
  assert.deepEqual(qualityGroups,['0','1']);
  await page.locator('#walk-destinations').evaluate(el=>el.scrollIntoView({block:'start'}));await shot('desktop-quality-groups-1440.png');
  const miwon=data.rows.find(r=>r.name==='문래미원'&&r.purpose==='bus_stop');
  const miwonRow=page.locator('#walk-bus_stop-rows tr[data-route-id="'+miwon.id+'"]');
  assert.match(await miwonRow.innerText(),/건물 윤곽 통과/);assert.match(await miwonRow.innerText(),/직선 연결 비중 큼/);
  assert.doesNotMatch(await miwonRow.locator('.walk-distance').innerText(),/85/);
  await miwonRow.locator('button').click();
  const miwonDetail=await miwonRow.locator('button').getAttribute('aria-controls');
  assert.match(await page.locator('[id="'+miwonDetail+'"]').innerText(),/기존 거리 합계 85\.6m/);
  checks.push('quality reasons visible for every flagged route; estimated/review/missing sections separated; Miwon short raw distance only in diagnosis detail');
  await page.fill('#walk-query','없는단지');assert.equal(await rows('bus_stop').count(),0);assert.equal(await page.locator('#walk-bus_stop-empty').isVisible(),true);
  await page.click('#walk-reset');await page.click('#walk-tab-complexes');
  const viewports=[{width:390,height:1100},{width:360,height:900},{width:768,height:1024}];
  for(const viewport of viewports){
    await page.setViewportSize(viewport);await page.click('#walk-tab-complexes');await page.click('#walk-reset');
    await page.locator('#walk-complexes').evaluate(el=>el.scrollIntoView({block:'start'}));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    if(viewport.width===390){
      await cards().first().screenshot({path:path.join(output,'mobile-complex-card-390.png')});screenshots.push('mobile-complex-card-390.png');
      await page.fill('#walk-query','문래미원');
      assert.match(await cards().first().innerText(),/계산 못함/);assert.match(await cards().first().innerText(),/건물 윤곽 통과/);assert.match(await cards().first().innerText(),/직선 연결 비중 큼/);
      await cards().first().screenshot({path:path.join(output,'mobile-miwon-quality-390.png')});screenshots.push('mobile-miwon-quality-390.png');await page.click('#walk-reset');
    }
    await page.click('#walk-tab-destinations');await page.click('#walk-tab-station');
    await page.selectOption('#walk-station-destination','station:도림천역');assert.equal(await rows('station').count(),8);
    for(const r of await rows('station').all()){assert.match(await r.innerText(),/계산 못함/);assert.doesNotMatch(await r.innerText(),/약 0/);}
    await page.locator('#walk-destinations').evaluate(el=>el.scrollIntoView({block:'start'}));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    for(const el of await page.locator('#walk-body input,#walk-body select,#walk-body button').all()){
      if(await el.isVisible()){const box=await el.boundingBox();assert.ok(box.height>=40);}
    }
    if(viewport.width===390)await shot('mobile-station-missing-390.png');
    await page.click('#walk-tab-school');await page.locator('#walk-destinations').evaluate(el=>el.scrollIntoView({block:'start'}));
    if(viewport.width===390)await shot('mobile-school-comparison-390.png');
  }
  checks.push('390/360/768px grouped cards and comparison layouts: no horizontal overflow; controls>=40px; 8 disconnected stations remain unknown');
  assert.equal(await page.evaluate(()=>localStorage.getItem('qa-walk-note')),'preserved');
  await page.route('**/walk-review.json',r=>r.fulfill({status:200,contentType:'application/json',body:'{}'}));
  await page.reload({waitUntil:'networkidle'});await page.waitForSelector('#walk-load-status.error');assert.equal(await page.locator('#walk-body').isHidden(),true);
  await page.unroute('**/walk-review.json');
  await page.route('**/walk-review.json',r=>r.fulfill({status:404,body:'missing'}));
  await page.reload({waitUntil:'networkidle'});await page.waitForSelector('#walk-load-status.error');
  await page.unroute('**/walk-review.json');
  for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
    await page.setViewportSize(viewport);await page.goto(origin+'/index.html',{waitUntil:'networkidle'});
    await page.waitForFunction(()=>Number(document.querySelector('#total-buildings').textContent.replaceAll(',',''))>0);
    assert.equal(await page.locator('#workspace-alert').isHidden(),true);
    await page.click('#tab-all');assert.ok(await page.locator('#all-list>li').count()>0);
    await page.goto(origin+'/research.html',{waitUntil:'networkidle'});await page.waitForSelector('#comparison-body:not([hidden])');
    assert.ok(await page.locator('#comparison-rows tr').count()>0);
  }
  checks.push('missing/tampered data fail closed; personal storage preserved; existing price/research pages load on desktop/mobile');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify({status:'pass',checks,viewports:[1440,390,360,768],screenshots,page_errors:errors,external_requests:external,published:false},null,2)+'\n');
  console.log(JSON.stringify({status:'pass',checks:checks.length,screenshots,page_errors:errors,external_requests:external}));
}
run().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(context)await context.close();if(browser)await browser.close();server.close();});
