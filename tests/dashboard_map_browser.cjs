// Optional real-browser regression; pass a report produced by build_dashboard_demo.py.
// PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE may point at an existing installation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const htmlPath = path.resolve(process.argv[2]);

(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {})});
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sn-map-browser-'));
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1050}});
    const errors = [], network = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route(/^https?:/, route => {network.push(route.request().url()); return route.abort();});
    await page.goto(pathToFileURL(htmlPath).href);
    const svg=page.locator('#experiment-map');
    await page.waitForFunction(()=>document.querySelector('#experiment-map').hasAttribute('viewBox'));
    const frame=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const box=await svg.boundingBox();
    const before=await svg.getAttribute('viewBox');
    const inspector=await page.locator('#map-inspector h3').textContent();
    await page.mouse.move(box.x+60,box.y+box.height-50);
    await page.mouse.down(); await page.mouse.move(box.x+200,box.y+box.height-140,{steps:8}); await page.mouse.up();
    assert.notEqual(await svg.getAttribute('viewBox'),before,'drag must pan the viewport');
    assert.equal(await page.locator('#map-inspector h3').textContent(),inspector,'drag must not select a node');
    const panned=await svg.getAttribute('viewBox');
    await page.locator('#map-zoom-in').click();
    assert.ok(Number((await svg.getAttribute('viewBox')).split(' ')[2])<Number(panned.split(' ')[2]));
    await page.locator('#map-fit').click();
    const verifyFit=async()=>{
      const outside=await svg.evaluate(s=>{
        const b=s.getBoundingClientRect();
        return [...s.querySelectorAll('.map-node rect')].filter(n=>{const r=n.getBoundingClientRect();return r.left<b.left-1||r.right>b.right+1||r.top<b.top-1||r.bottom>b.bottom+1}).length;
      });
      assert.equal(outside,0,'show all must include every card');
    };
    await verifyFit();
    const nodes=page.locator('.map-node.run');
    const cardBox=await nodes.first().boundingBox();
    await page.mouse.move(cardBox.x+20,cardBox.y+20);await page.mouse.down();
    await page.mouse.move(cardBox.x+90,cardBox.y+45,{steps:6});await page.mouse.up();
    assert.equal(await page.locator('#map-inspector h3').textContent(),inspector,'dragging a card must not activate it');
    await page.locator('#map-fit').click();
    const wheelBefore=await svg.getAttribute('viewBox');
    await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,-100);await frame();
    assert.ok(Number((await svg.getAttribute('viewBox')).split(' ')[2])<Number(wheelBefore.split(' ')[2]),'wheel zoom must work in the canvas');
    await page.locator('#map-fit').click();
    await nodes.nth(1).click();
    const selected=await nodes.nth(1).getAttribute('data-node-id');
    assert.equal(await nodes.nth(1).getAttribute('aria-pressed'),'true');
    const viewAfterClick=await svg.getAttribute('viewBox');
    await page.locator('[data-view="analysis"]').click();
    await page.locator('[data-view="map"]').click(); await frame();
    assert.equal(await svg.getAttribute('viewBox'),viewAfterClick,'view switches preserve the camera');
    assert.equal(await page.locator('.map-node.selected').getAttribute('data-node-id'),selected);
    await page.locator('#map-locate').click();
    await svg.focus(); const keyBefore=await svg.getAttribute('viewBox'); await page.keyboard.press('ArrowDown');
    assert.notEqual(await svg.getAttribute('viewBox'),keyBefore,'keyboard must pan');
    await page.keyboard.press('0'); await verifyFit();
    await nodes.nth(1).focus(); await page.keyboard.press('Enter');
    assert.equal(await nodes.nth(1).getAttribute('aria-pressed'),'true','keyboard must select');
    await page.locator('#map-expand').click(); await frame();
    assert.ok((await svg.boundingBox()).width>box.width,'expanded canvas uses more screen space');
    await page.keyboard.press('Escape'); await frame();
    assert.equal(await page.locator('#map-expand').getAttribute('aria-pressed'),'false');
    await page.setViewportSize({width:390,height:844}); await frame();
    await page.locator('#map-fit').click(); await verifyFit();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile page must not overflow horizontally');
    await svg.scrollIntoViewIfNeeded();
    const touchBox=await svg.boundingBox(), touchBefore=await svg.getAttribute('viewBox');
    const cdp=await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:touchBox.x+100,y:touchBox.y+150}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:touchBox.x+160,y:touchBox.y+200}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    assert.notEqual(await svg.getAttribute('viewBox'),touchBefore,'single-finger touch must pan');
    await page.setViewportSize({width:1440,height:1050}); await frame();
    const original=fs.readFileSync(htmlPath,'utf8');
    const data=JSON.parse(original.match(/<script type="application\/json" id="dashboard-data">([\s\S]*?)<\/script>/)[1]);
    const base=structuredClone(data.graph);
    for(let i=1;i<=25;i++) {
      data.graph.nodes.push(...base.nodes.map(n=>({...n,id:n.id+'-'+i,source_id:n.source_id+'-'+i})));
      data.graph.edges.push(...base.edges.map(e=>({...e,source:e.source+'-'+i,target:e.target+'-'+i})));
    }
    Object.values(data.observations).forEach(o=>{o.detail_file=pathToFileURL(path.resolve(path.dirname(htmlPath),o.detail_file)).href});
    const large=original.replace(/(<script type="application\/json" id="dashboard-data">)[\s\S]*?(<\/script>)/,(_,a,b)=>a+JSON.stringify(data).replace(/</g,'\\u003c')+b);
    const fixture=path.join(temp,'many-runs.html');fs.writeFileSync(fixture,large);
    await page.goto(pathToFileURL(fixture).href);await frame();
    await page.locator('#map-fit').click();await verifyFit();
    assert.equal(await page.locator('.map-node').count(),base.nodes.length*26);
    await page.locator('#map-locate').click();
    assert.equal(await page.locator('#map-zoom-level').textContent(),'100%','large graphs can return to a readable selection');
    await page.locator('#map-fit').click();
    await page.locator('.map-node').last().focus();
    const last=await page.locator('.map-node').last().boundingBox(), visible=await svg.boundingBox();
    assert.ok(last.y>=visible.y&&last.y+last.height<=visible.y+visible.height,'focusing an off-screen node reveals it');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('.map-node').last().getAttribute('aria-pressed'),'true');
    assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
    console.log('Passed: canvas/card drag, wheel/button zoom, fit, selection, view persistence, keyboard, resize, expanded view, mobile width/touch, 26 copies of graph; no page errors or network requests.');
  } finally {await browser.close();fs.rmSync(temp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1});
