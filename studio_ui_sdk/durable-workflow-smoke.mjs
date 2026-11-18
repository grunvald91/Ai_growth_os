// Mock-only browser smoke: actual dispatcher workflow buttons and SDK disclosure.
// No production requests, model calls or queued tasks.
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {chromium} from "playwright";

const [dispatcher, widgets, sdk, css, widgetCss] = await Promise.all([
  readFile(resolve("../app/api/dispatcher_assets/dispatcher.js")),
  readFile(resolve("../app/api/dispatcher_assets/resource-widgets.js")),
  readFile(resolve("../app/api/dispatcher_assets/ui-sdk-pilot.bundle.js")),
  readFile(resolve("../app/api/dispatcher_assets/dispatcher.css")),
  readFile(resolve("../app/api/dispatcher_assets/resource-widgets.css")),
]);
const id = "a5286c0b-d887-43f6-a903-2f7a00d8d321";
const approvals = [];
function block(position = 0, revision = 1, done = false) {
  return {type:"workflow",workflow_id:id,title:"Поручение из нескольких шагов",
    revision,status:done?"completed":"waiting_approval",
    can_retry_read:false,
    approval:done?null:{
      title:"Подготовить пост?",position,paid:false,confirm_label:"Создать",
      details:["Тема: Пример "+(position+1),"<img src=x onerror=alert(1)>"]
    },
    steps:[
      {title:"Создать пост",status:done||position>0?"completed":"waiting_approval",
        detail:position>0||done?"Задание поставлено в очередь.":"Нужно подтверждение",
        blocks:[]},
      {title:"Создать пост",status:done?"completed":position===1?"waiting_approval":"pending",
        detail:done?"Задание в очереди.":"",blocks:[]},
      {title:"Что в работе",status:done?"completed":"pending",
        detail:done?"В работе: 2.":"",blocks:[]}
    ]
  };
}
let live = block();
function html() {
  const state = {messages:[{
    id:"workflow-message",seq:1,role:"assistant",text:"Поручение сохранено.",
    blocks:[{type:"text",text:"Поручение сохранено."},block()]
  }],hints:[],workflows:[]};
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
const server=createServer(async (req,res)=>{
  const url=new URL(req.url,"http://localhost");
  let type="text/html", body=html(), status=200;
  if(url.pathname==="/fake/workflows/"+id) {
    type="application/json";body=JSON.stringify({workflow:live});
  } else if(url.pathname==="/fake/workflows/"+id+"/decision") {
    let raw="";for await(const chunk of req)raw+=chunk;
    let data=JSON.parse(raw);approvals.push(data);
    if(data.revision!==live.revision || data.position!==live.approval?.position) {
      status=409;type="application/json";body=JSON.stringify({detail:"Устаревшая карточка"});
    } else {
      live=block(data.position+1,live.revision+1,data.position===1);
      type="application/json";body=JSON.stringify({workflow:live});
    }
  } else if(url.pathname==="/dispatcher.js"){type="application/javascript";body=dispatcher;}
  else if(url.pathname==="/resource-widgets.js"){type="application/javascript";body=widgets;}
  else if(url.pathname==="/dispatcher.css"){type="text/css";body=css;}
  else if(url.pathname==="/resource-widgets.css"){type="text/css";body=widgetCss;}
  else if(url.pathname==="/studio/assets/ui-sdk-pilot.bundle.js"){
    type="application/javascript";body=sdk;
  }
  res.writeHead(status,{"content-type":type});res.end(body);
});
await new Promise((ok,fail)=>{server.once("error",fail);server.listen(0,"127.0.0.1",ok);});
const browser=await chromium.launch({headless:true,
  executablePath:process.env.SERVGO_CHROME_PATH||"/usr/bin/google-chrome"});
try{
 for(const width of [390,1280]) {
  live=block();approvals.length=0;
  const url=`http://127.0.0.1:${server.address().port}/`;
  const page=await browser.newPage({viewport:{width,height:820}});
  const errors=[];page.on("pageerror",e=>errors.push(e.message));
  await page.goto(url,{waitUntil:"networkidle"});
  const widget=page.locator('[data-workflow-id="'+id+'"]');
  await page.getByRole("button",{name:"Создать"}).waitFor();
  assert.equal(await widget.count(),1);
  assert.equal(await page.locator('img').count(),0);
  assert.equal(await page.getByRole("button",{name:"Создать"}).count(),1);
  await page.getByRole("button",{name:"Создать"}).click();
  await page.waitForFunction(() =>
    document.querySelector('[data-workflow-id] .sg-workflow-approval')?.textContent?.includes("Пример 2"));
  assert.equal(approvals.length,1);
  assert.equal(approvals[0].position,0);
  assert.equal(approvals[0].revision,1);
  await page.reload({waitUntil:"networkidle"});
  await page.getByRole("button",{name:"Создать"}).waitFor();
  assert.match(await page.locator(".sg-workflow-approval").innerText(),/Пример 2/);
  await page.getByRole("button",{name:"Создать"}).click();
  await page.waitForFunction(() => document.querySelector(".sg-workflow-state:last-child")!==null);
  await page.waitForFunction(() => document.querySelectorAll(".sg-workflow-completed").length===3);
  assert.equal(approvals.length,2);
  assert.equal(approvals[1].position,1);
  assert.equal(approvals[1].revision,2);
  assert.equal(await page.getByRole("button",{name:"Создать"}).count(),0);
  assert.deepEqual(errors,[]);
  await page.close();
  const fallback=await browser.newPage({viewport:{width,height:820}});
  await fallback.goto(url+"?ui_sdk=0",{waitUntil:"networkidle"});
  assert.equal(await fallback.locator("ui-collapsible").count(),0);
  await fallback.locator('[data-workflow-id] details summary').click();
  assert.equal(await fallback.locator(".sg-workflow-result").count(),3);
  await fallback.close();
 }
 console.log("Durable workflows: two distinct approvals, reload reconciliation, XSS, mobile/desktop and native fallback OK");
}finally{
 await browser.close();
 await new Promise(resolve=>server.close(resolve));
}
