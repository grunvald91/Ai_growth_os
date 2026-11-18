// Real browser check of the dispatcher workflow card using the pinned UI SDK.
// No authenticated backend, mutation endpoint or paid model is contacted.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const [dispatcher, widgets, sdk, css, widgetCss] = await Promise.all([
  readFile(resolve("../app/api/dispatcher_assets/dispatcher.js")),
  readFile(resolve("../app/api/dispatcher_assets/resource-widgets.js")),
  readFile(resolve("../app/api/dispatcher_assets/ui-sdk-pilot.bundle.js")),
  readFile(resolve("../app/api/dispatcher_assets/dispatcher.css")),
  readFile(resolve("../app/api/dispatcher_assets/resource-widgets.css")),
]);
const state = {hints:[], messages:[{
  id:"workflow-message", seq:1, role:"assistant",
  text:"Проверено 2 из 3 разделов. Остальные остановлены из-за ошибки.",
  blocks:[{type:"workflow",title:"Результаты поручения",steps:[
    {title:"Что в работе",status:"completed",detail:"В работе: 2.",
      blocks:[{type:"table",columns:["Задание","Статус"],rows:[["Текст <img src=x onerror=alert(1)>","В работе"]]}]},
    {title:"Показать контент-план",status:"failed",detail:"Нет соединения с источником.",
      blocks:[{type:"links",items:[{label:"Открыть план",href:"/studio/products/demo/plan"}]}]},
    {title:"Что ждёт проверки",status:"skipped",detail:"Не выполнялось.",
      blocks:[]},
  ]}]}]};
function html(){
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet" href="/dispatcher.css">
  <link rel="stylesheet" href="/resource-widgets.css"></head><body>
  <section class="dp-home" data-api="/fake" data-voice-url="">
    <div id="dp-feed" class="dp-feed"></div><div id="dp-chips"></div>
    <form id="dp-form"><textarea id="dp-input"></textarea>
      <button id="dp-send" type="submit">Отправить</button></form>
    <button id="dp-new" type="button">Новая тема</button>
    <button id="dp-mic" type="button" hidden>Голос</button>
    <div id="dp-live" hidden><span id="dp-live-label"></span><p id="dp-live-text"></p></div>
    <p id="dp-voice-note" hidden></p>
  </section>
  <script type="application/json" id="dp-state">${JSON.stringify(state).replace(/</g,"\\u003c")}</script>
  <script src="/dispatcher.js"></script><script src="/resource-widgets.js"></script>
  </body></html>`;
}
const server = createServer((req,res)=>{
  let type="text/html",body=html();
  if(req.url.startsWith("/dispatcher.js")){type="application/javascript";body=dispatcher;}
  else if(req.url.startsWith("/resource-widgets.js")){type="application/javascript";body=widgets;}
  else if(req.url.startsWith("/dispatcher.css")){type="text/css";body=css;}
  else if(req.url.startsWith("/resource-widgets.css")){type="text/css";body=widgetCss;}
  else if(req.url.startsWith("/studio/assets/ui-sdk-pilot.bundle.js")){
    type="application/javascript";body=sdk;
  }
  res.writeHead(200,{"content-type":type});res.end(body);
});
await new Promise((ok,fail)=>{server.once("error",fail);server.listen(0,"127.0.0.1",ok);});
const browser=await chromium.launch({headless:true,
  executablePath:process.env.SERVGO_CHROME_PATH||"/usr/bin/google-chrome"});
try{
  for(const width of [390,1280]){
    const url=`http://127.0.0.1:${server.address().port}/`;
    const page=await browser.newPage({viewport:{width,height:840}});
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    await page.goto(url,{waitUntil:"networkidle"});
    const workflow=page.locator('[data-sg-resource-widget="workflow"]');
    assert.equal(await workflow.count(),1);
    assert.deepEqual(await workflow.locator(".sg-workflow-state").allTextContents(),
      ["Проверено","Ошибка","Пропущено"]);
    await workflow.locator("ui-collapsible-trigger button").waitFor();
    await workflow.locator("ui-collapsible-trigger button").focus();
    await page.keyboard.press("Enter");
    assert.equal(await workflow.locator(".sg-workflow-result").count(),3);
    assert.match(await workflow.locator("ui-collapsible-panel").innerText(),/Нет соединения/);
    assert.equal(await workflow.locator("img").count(),0);
    assert.match(await workflow.locator(".dp-table").innerText(),/<img src=x/);
    assert.equal(await workflow.locator(".dp-link").getAttribute("href"),
      "/studio/products/demo/plan");
    assert.equal(await page.locator(".dp-confirm").count(),0);
    assert.deepEqual(errors,[]);
    await page.screenshot({path:`../studio-workflow-${width}.png`,fullPage:true});
    await page.close();
    const legacy=await browser.newPage({viewport:{width,height:840}});
    await legacy.goto(url+"?ui_sdk=0",{waitUntil:"networkidle"});
    assert.equal(await legacy.locator("ui-collapsible").count(),0);
    await legacy.locator('[data-sg-resource-widget="workflow"] details summary').click();
    assert.equal(await legacy.locator(".sg-workflow-result").count(),3);
    await legacy.close();
  }
  console.log("Dispatcher readonly workflow browser smoke: steps, keyboard, safe links, XSS, mobile and fallback OK");
}finally{
  await browser.close();
  await new Promise(ok=>server.close(ok));
}
