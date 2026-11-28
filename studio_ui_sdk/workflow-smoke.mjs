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
  ]}]},{id:"clarification-message",seq:2,role:"assistant",text:"Какой результат нужен?",blocks:[{type:"text",text:"Какой результат нужен?"},{type:"clarification",options:["Привлечь новых клиентов","<img src=x onerror=alert(1)>"]}]}]};
state.messages.push({
  id:"form-message",seq:3,role:"assistant",text:"Выберите параметры",
  blocks:[{type:"tool_form",capability:"show_insights",title:"Итоги бизнеса по записям",
    submit_label:"Показать результат",fields:[
      {name:"topic",label:"Раздел аналитики",kind:"select",value:"all",required:false,
       options:[{value:"all",label:"Все"},{value:"clients",label:"Клиенты <img src=x>"}]},
      {name:"days",label:"Количество дней",kind:"integer",value:"",required:false,minimum:7,maximum:365}
    ]}]
});
state.messages.push({id:"used-form",seq:4,role:"assistant",blocks:[{
    type:"tool_form",capability:"create_post",title:"Пост",submit_label:"Подготовить подтверждение",
    fields:[{name:"topic",label:"Тема",kind:"text",required:true,value:"Тема поста"}]}]});
state.messages.push({id:"used-form-request",seq:5,role:"user",text:"Пост",submitted_form_id:"used-form"});
state.messages.push({id:"chart-message",seq:6,role:"assistant",text:"Данные записей",
  blocks:[{type:"chart",title:"Записи по дням",source:"BookingBot",period:"01.11.2026–24.11.2026",
    unit:"шт",note:"Тестовые данные браузера",points:[
      {label:"Пн <img src=x>",value:0},{label:"Вт",value:4},{label:"Ср",value:2}
    ]}]});
state.messages.push({id:"trend-message",seq:7,role:"assistant",text:"Динамика",
  blocks:[{type:"chart",kind:"line",title:"Динамика записей",source:"BookingBot",period:"01.11.2026–03.11.2026",
    unit:"шт",note:"Тестовый ряд",points:[{label:"01.11.2026",value:0},{label:"02.11.2026",value:4},{label:"03.11.2026",value:2}]}]});
state.messages.push({id:"distribution-message",seq:8,role:"assistant",text:"Статусы",
  blocks:[{type:"chart",kind:"donut",title:"Статусы записей",source:"BookingBot",period:"01.11.2026–03.11.2026",
    unit:"шт",points:[{label:"Выполнены",value:8},{label:"Отменены",value:2}]},
    {type:"metrics_summary",title:"Записи за период",source:"BookingBot",period:"01.11.2026–03.11.2026",
    unit:"шт",points:[{label:"Записей без отмен",value:8},{label:"Отменено",value:2}]}]});
state.messages.push({id:"calendar-message",seq:9,role:"assistant",text:"Расписание",blocks:[{
  type:"calendar",title:"Календарь записей",source:"BookingBot",timezone:"Europe/Moscow",
  columns:["Время","Клиент","Услуга","Мастер","Статус"],
  days:[{date:"2026-11-28",label:"Сб 28.11.2026",summary:"Записей: 1",rows:[["09:00","<img src=x>","Услуга","Мастер","подтверждена"]]},
        {date:"2026-11-29",label:"Вс 29.11.2026",summary:"Записей: 0",rows:[]}]
}]});
state.messages.push({id:"list-message",seq:10,role:"assistant",text:"Материалы",blocks:[{
  type:"resource_list",title:"Публикации",filterable:true,items:[
    {kind:"publication",title:"Пост <img src=x>",status:"Запланировано",detail:"ВКонтакте · 09:00",href:"/studio/products/demo/materials/one",link_label:"Открыть исходный материал"},
    {kind:"task",title:"Видео",status:"Ошибка",detail:"Этап озвучки",href:"/studio/products/demo/tasks/two"}
  ]
}]});
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
    const clarification=page.locator('[data-sg-resource-widget="clarification"]');
    await clarification.locator("ui-collapsible").waitFor();
    const posted=[];page.on("request",req=>{if(req.method()==="POST")posted.push(req.url());});
    await page.locator("#dp-input").fill("Мой черновик");
    await clarification.getByRole("button",{name:"Привлечь новых клиентов"}).click();
    assert.equal(await page.locator("#dp-input").inputValue(),"Мой черновик\nПривлечь новых клиентов");
    assert.equal(await clarification.locator("img").count(),0);
    assert.equal(posted.length,0);
    const used=page.locator('[data-id="used-form"]');
    assert.equal(await used.getByLabel("Тема").isDisabled(),true);
    assert.equal(await used.getByRole("button",{name:"Форма отправлена"}).isDisabled(),true);
    const form=page.locator('[data-id="form-message"] [data-sg-resource-widget="tool_form"]');
    await form.locator("ui-collapsible").waitFor();
    const formCalls=[];
    await page.route("**/fake/messages", async route=>{
      formCalls.push(route.request().postDataJSON());
      await route.fulfill({json:{messages:[],hints:[]}});
    });
    await form.getByLabel("Раздел аналитики").selectOption("clients");
    await form.getByLabel("Количество дней").fill("366");
    await form.getByRole("button",{name:"Показать результат"}).click();
    assert.equal(formCalls.length,0);
    await form.getByLabel("Количество дней").fill("30");
    assert.equal(formCalls.length,0);
    assert.equal(await form.locator("img").count(),0);
    await form.getByRole("button",{name:"Показать результат"}).click();
    await page.waitForFunction(()=>!document.querySelector(".dp-home").classList.contains("busy"));
    assert.equal(formCalls.length,1);
    assert.equal(formCalls[0].mode,"chip");
    assert.equal(formCalls[0].form_submission.message_id,"form-message");
    assert.equal(formCalls[0].chip,undefined);
    assert.deepEqual(formCalls[0].form_submission.params,{topic:"clients",days:"30"});
    assert.equal(await page.locator("#dp-input").inputValue(),"Мой черновик\nПривлечь новых клиентов");
    const chart=page.locator('[data-id="chart-message"] .dp-chart');
    assert.equal(await chart.count(),1);
    assert.match(await chart.innerText(),/BookingBot/);
    assert.equal(await chart.locator("img").count(),0);
    assert.deepEqual(await chart.locator(".dp-chart-bar").evaluateAll(nodes=>nodes.map(n=>n.style.width)),["0%","100%","50%"]);
    const trend=page.locator(".dp-line-chart");
    assert.equal(await trend.locator("circle").count(),3);
    const data=page.locator(".dp-chart-data");
    await data.locator("summary").click();
    assert.match(await data.innerText(),/02.11.2026/);
    assert.equal(await page.locator(".dp-donut-ring").count(),1);
    assert.match(await page.locator(".dp-donut-ring").evaluate(n=>n.style.background),/conic-gradient/);
    assert.equal(await page.locator(".dp-metrics-grid .dp-metric").count(),2);
    const calendar=page.locator('[data-id="calendar-message"] .dp-calendar');
    assert.equal(await calendar.count(),1);
    assert.equal(await calendar.locator("details[open]").count(),1);
    assert.equal(await calendar.locator("img").count(),0);
    assert.match(await calendar.innerText(),/09:00/);
    const requestsBeforeDay=posted.length;
    await calendar.locator("summary").nth(1).focus();
    await page.keyboard.press("Enter");
    assert.equal(await calendar.locator("details[open]").count(),2);
    assert.match(await calendar.innerText(),/Нет записей для отображения/);
    assert.equal(posted.length,requestsBeforeDay);
    const resources=page.locator('[data-id="list-message"] .dp-resource-filter');
    assert.equal(await resources.locator(".dp-resource:visible").count(),2);
    assert.equal(await resources.locator("img").count(),0);
    const beforeFilter=posted.length;
    await resources.getByLabel("Поиск на этой странице").fill("озвучки");
    assert.equal(await resources.locator(".dp-resource:visible").count(),1);
    assert.match(await resources.locator(".dp-resource:visible").innerText(),/Видео/);
    await resources.getByLabel("Поиск на этой странице").fill("нет совпадений");
    assert.equal(await resources.locator(".dp-resource:visible").count(),0);
    assert.match(await resources.innerText(),/совпадений нет/);
    await resources.getByLabel("Поиск на этой странице").fill("");
    assert.equal(await resources.locator(".dp-resource:visible").count(),2);
    assert.equal(posted.length,beforeFilter);
    assert.deepEqual(errors,[]);
    await page.screenshot({path:`../studio-workflow-${width}.png`,fullPage:true});
    await page.close();
    const legacy=await browser.newPage({viewport:{width,height:840}});
    await legacy.goto(url+"?ui_sdk=0",{waitUntil:"networkidle"});
    assert.equal(await legacy.locator("ui-collapsible").count(),0);
    await legacy.locator('[data-sg-resource-widget="workflow"] details summary').click();
    assert.equal(await legacy.locator(".sg-workflow-result").count(),3);
    await legacy.locator('[data-sg-resource-widget="clarification"]').getByRole("button",{name:"Привлечь новых клиентов"}).click();
    assert.equal(await legacy.locator("#dp-input").inputValue(),"Привлечь новых клиентов");
    assert.equal(await legacy.locator('[data-id="used-form"]').getByLabel("Тема").isDisabled(),true);
    const nativeForm=legacy.locator('[data-id="form-message"] [data-sg-resource-widget="tool_form"]');
    assert.equal(await nativeForm.locator("details[open]").count(),1);
    await nativeForm.getByLabel("Количество дней").fill("7");
    assert.equal(await nativeForm.getByLabel("Количество дней").inputValue(),"7");
    assert.equal(await legacy.locator(".dp-chart-row").count(),3);
    assert.equal(await legacy.locator(".dp-line-chart circle").count(),3);
    assert.equal(await legacy.locator(".dp-donut-ring").count(),1);
    assert.equal(await legacy.locator(".dp-calendar-day").count(),2);
    await legacy.locator(".dp-calendar-day summary").nth(1).click();
    assert.equal(await legacy.locator(".dp-calendar-day[open]").count(),2);
    await legacy.getByLabel("Поиск на этой странице").fill("ВКонтакте");
    assert.equal(await legacy.locator('[data-id="list-message"] .dp-resource:visible').count(),1);
    await legacy.close();
  }
  console.log("Dispatcher readonly workflow browser smoke: steps, keyboard, safe links, XSS, mobile and fallback OK");
}finally{
  await browser.close();
  await new Promise(ok=>server.close(ok));
}
