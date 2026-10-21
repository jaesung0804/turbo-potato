/* Real browser checks against retained local assets; all external URLs blocked. */
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const site=path.resolve(process.argv[2]||'web'),output=path.resolve(process.argv[3]||'.work/mullae-ui-review-20261021/browser');
fs.mkdirSync(output,{recursive:true});
const data=JSON.parse(fs.readFileSync(path.join(site,'walk-review.json')));
const byid=new Map(data.rows.map(r=>[r.id,r]));
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png'};
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
  const origin=(process.argv[4]||'http://127.0.0.1:'+server.address().port).replace(/\/$/,'');
  browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--disable-dev-shm-usage','--renderer-process-limit=2']});
  context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});
  await context.route('**/*',route=>{if(route.request().url().startsWith(origin+'/'))return route.continue();external.push(route.request().url());return route.abort();});
  await context.addInitScript(()=>{localStorage.setItem('qa-walk-note','preserved');localStorage.setItem('estate-comparison-profile-v1','preserved-private-profile');});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  const ready=async()=>{await page.waitForSelector('#walk-body:not([hidden])');};
  await page.goto(origin+'/static-location.html#walk-review',{waitUntil:'networkidle'});await ready();
  const rows=()=>page.locator('#walk-rows tr[data-route-id]');
  assert.equal(await rows().count(),42);
  assert.equal(await page.locator('#walk-rows tr[data-terrain-status="excluded"]').count(),4);
  assert.equal(await page.locator('#walk-rows tr[data-status="disconnected"]').count(),11);
  assert.match(await page.locator('#walk-selected').innerText(),/31.*11.*27.*4/);
  for(const r of data.rows){
    const row=page.locator('#walk-rows tr[data-route-id="'+r.id+'"]');
    if(r.status==='disconnected'){
      assert.match(await row.locator('.walk-distance').innerText(),/계산 못함/);
      assert.match(await row.locator('.walk-height').innerText(),/자료 없음/);
      assert.doesNotMatch(await row.locator('.walk-distance').innerText(),/약 0/);
    }else{
      assert.ok((await row.locator('.walk-distance').innerText()).includes(Math.round(r.distance.total_m).toLocaleString('ko-KR')+'m'));
      if(r.terrain.status==='excluded')assert.match(await row.locator('.walk-height').innerText(),/비교 제외/);
    }
  }
  checks.push('42 real rows;31 computed;11 null;27 terrain references;4 exclusions visible');
  async function sortedValues(key,direction){
    await page.selectOption('#walk-sort',key);await page.selectOption('#walk-direction',direction);
    const ids=await rows().evaluateAll(elements=>elements.map(e=>e.dataset.routeId));
    const values=ids.map(id=>{const r=byid.get(id);return key==='distance'?r.distance.total_m:key==='time'?r.time.base_min:r.terrain.status==='reference'?r.terrain[key+'_m']:null;});
    let missing=false,last=null;
    for(const v of values){if(v===null){missing=true;continue;}assert.equal(missing,false,'null sorted before a usable value');if(last!==null)assert.ok(direction==='asc'?v>=last:v<=last);last=v;}
    return values;
  }
  for(const key of ['distance','time'])for(const direction of ['asc','desc']){
    const values=await sortedValues(key,direction);assert.equal(values.filter(v=>v===null).length,11);
  }
  assert.equal(await page.locator('#walk-sort option[value="forward"]').evaluate(el=>el.disabled),true);
  checks.push('distance/time asc+desc:missing always last;terrain comparison guarded by exact destination');
  await page.click('#walk-reset');await page.locator('#walk-review').evaluate(el=>el.scrollIntoView({block:'start'}));
  await page.screenshot({path:path.join(output,'desktop-overview-1440.png')});screenshots.push('desktop-overview-1440.png');
  await page.selectOption('#walk-purpose','school');await page.selectOption('#walk-destination','B000002002');
  for(const key of ['forward','reverse'])for(const direction of ['asc','desc'])await sortedValues(key,direction);
  assert.ok(await rows().count()>2);assert.equal(await page.locator('#walk-sort option[value="forward"]').evaluate(el=>el.disabled),false);
  checks.push('both terrain directions sort correctly within same school destination');
  await page.selectOption('#walk-purpose','station');
  assert.equal(await page.inputValue('#walk-destination'),'all');assert.equal(await page.inputValue('#walk-sort'),'name');
  await page.selectOption('#walk-destination','osm:node:5344219250');
  await page.selectOption('#walk-sort','forward');assert.equal(await rows().count(),2);
  assert.equal(await page.locator('#walk-rows tr[data-terrain-status="excluded"]').count(),2);
  const toggle=rows().first().locator('button[data-walk-detail]');
  await toggle.focus();await page.keyboard.press('Enter');
  assert.equal(await toggle.getAttribute('aria-expanded'),'true');
  const detailId=await toggle.getAttribute('aria-controls');
  assert.match(await page.locator('[id="'+detailId+'"]').innerText(),/비교 제외 · 검토용 계산값/);
  await page.locator('#walk-selected').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'desktop-excluded-evidence-1440.png')});screenshots.push('desktop-excluded-evidence-1440.png');
  checks.push('excluded terrain visible,raw values remain review-only;keyboard evidence toggle works');
  await page.click('#walk-reset');
  await page.fill('#walk-query','없는단지');
  assert.equal(await rows().count(),0);assert.equal(await page.locator('#walk-empty').isVisible(),true);
  await page.click('#walk-reset');assert.equal(await rows().count(),42);
  const sizes=[{width:390,height:844},{width:360,height:800},{width:768,height:1024}];
  for(const viewport of sizes){
    await page.setViewportSize(viewport);await page.click('#walk-reset');await page.locator('#walk-review').evaluate(el=>el.scrollIntoView({block:'start'}));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'horizontal page overflow');
    assert.ok(await page.locator('#walk-reset').evaluate(el=>el.scrollWidth<=el.clientWidth),'reset button text clipped');
    for(const id of ['purpose','destination','query','sort','direction']){
      const box=await page.locator('#walk-'+id).boundingBox();assert.ok(box.width>80&&box.height>=40);
    }
    if(viewport.width===390){
      await page.screenshot({path:path.join(output,'mobile-overview-390.png')});screenshots.push('mobile-overview-390.png');
      await page.selectOption('#walk-purpose','school');await page.selectOption('#walk-destination','B000002002');
      await page.selectOption('#walk-sort','distance');await page.locator('#walk-selected').evaluate(el=>el.scrollIntoView({block:'start'}));
      await page.screenshot({path:path.join(output,'mobile-comparison-390.png')});screenshots.push('mobile-comparison-390.png');
      await page.selectOption('#walk-purpose','station');await page.selectOption('#walk-destination','osm:node:3404807927');
      assert.equal(await rows().count(),8);
      await page.locator('#walk-selected').evaluate(el=>el.scrollIntoView({block:'start'}));
      await page.screenshot({path:path.join(output,'mobile-missing-390.png')});screenshots.push('mobile-missing-390.png');
    }
  }
  checks.push('390/360/768px:no horizontal page overflow;controls>=40px;empty/reset works');
  assert.equal(await page.evaluate(()=>localStorage.getItem('qa-walk-note')),'preserved');
  assert.equal(await page.evaluate(()=>localStorage.getItem('estate-comparison-profile-v1')),'preserved-private-profile');
  await page.route('**/walk-review.json',r=>r.fulfill({status:200,contentType:'application/json',body:'{}'}));
  await page.reload({waitUntil:'networkidle'});await page.waitForSelector('#walk-load-status.error');
  assert.equal(await page.locator('#walk-body').isHidden(),true);
  assert.match(await page.locator('#walk-load-status').innerText(),/무결성/);
  await page.unroute('**/walk-review.json');
  await page.route('**/walk-review.json',r=>r.fulfill({status:404,body:'missing'}));
  await page.reload({waitUntil:'networkidle'});await page.waitForSelector('#walk-load-status.error');
  assert.equal(await page.locator('#walk-body').isHidden(),true);
  await page.unroute('**/walk-review.json');
  await page.reload({waitUntil:'networkidle'});await ready();
  assert.equal(await rows().count(),42);
  checks.push('tampered/missing payload fails closed;personal storage unchanged');
  for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
    await page.setViewportSize(viewport);
    await page.goto(origin+'/index.html',{waitUntil:'networkidle'});
    await page.waitForFunction(()=>Number(document.querySelector('#total-buildings').textContent.replaceAll(',',''))>0);
    assert.equal(await page.locator('#workspace-alert').isHidden(),true);
    await page.click('#tab-all');assert.ok(await page.locator('#all-list>li').count()>0);
    assert.ok(await page.locator('a[href="static-location.html#walk-review"]').isVisible());
    await page.goto(origin+'/research.html',{waitUntil:'networkidle'});
    await page.waitForSelector('#comparison-body:not([hidden])');
    assert.ok(await page.locator('#comparison-rows tr').count()>0);
    assert.ok(await page.locator('a[href="static-location.html#walk-review"]').isVisible());
  }
  checks.push('existing price list and research comparison load on desktop/mobile;new navigation links visible');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  fs.writeFileSync(path.join(output,'browser-results.json'),JSON.stringify({status:'pass',checks,viewports:[1440,390,360,768],screenshots,page_errors:errors,external_requests:external,tested_origin:origin,live_verification:Boolean(process.argv[4])},null,2)+'\n');
  console.log(JSON.stringify({status:'pass',checks:checks.length,screenshots,page_errors:errors,external_requests:external}));
}
run().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(context)await context.close();if(browser)await browser.close();server.close();});
