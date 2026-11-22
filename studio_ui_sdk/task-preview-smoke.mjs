// Real Chromium smoke for the actual Tasks JS/CSS, no live backend requests.
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {chromium} from "playwright";

const source=await readFile(resolve("../app/api/content_studio_work_ui.py"),"utf8");
const js=source.match(/JS = r"""([\s\S]*?)"""/)?.[1];
const css=source.match(/CSS = r"""([\s\S]*?)"""/)?.[1];
assert.ok(js && css, "Tasks assets must exist as the real source strings");
const html=String.raw`<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/studio/assets/work-ui.css">
<style>body{--s-surface:#fff;--s-text:#24364f;--s-line:#dae3ed;--s-accent:#2f5beb;font-family:system-ui}
.rail{max-width:280px}@media(max-width:1080px){.rail{display:none}}</style>
</head><body>
<details data-sg-notifications data-feed-url="/studio/products/11111111-1111-4111-8111-111111111111/activity.json">
<summary>Уведомления <span data-sg-count hidden>0</span></summary>
<div data-sg-notify-list></div></details>
<section class="sg-surface sg-work-board" data-sg-work-board>
<div class="sg-work-list">
<article class="sg-resource-widget" data-sg-resource-widget="task" id="job">
<a class="sg-work-item" href="/studio/products/11111111-1111-4111-8111-111111111111/tasks/t1">
<span class="sg-work-item-head"><span class="sg-work-kind">SEO-завод</span>
<span class="sg-work-status">Планирование завершено</span></span>
<span class="sg-work-item-main"><b>SEO-завод: поисковые запросы</b>
<span class="sg-work-summary">Поставлено статей в очередь: 3</span></span>
<span class="sg-work-item-foot"><time datetime="2026-11-22T05:00:00+00:00">Создано 22.11.2026</time>
<span class="sg-work-open">Открыть</span></span></a></article>
</div></section>
<aside class="rail"><section data-sg-work-preview>
<p data-sg-preview-hint>Выберите задание.</p>
<div id="sgWorkPreviewDetail" data-sg-preview-detail hidden>
<span data-sg-preview-kind></span><h3 data-sg-preview-title></h3>
<span data-sg-preview-status></span><p data-sg-preview-summary></p>
<p data-sg-preview-date></p><a data-sg-preview-link href="#">Открыть задание</a>
</div></section></aside>
<script src="/studio/assets/work-ui.js"></script>
</body></html>`;
const server=createServer((req,res)=>{
  const answer=(type,body)=>{
    res.writeHead(200,{"Content-Type":type,"Cache-Control":"no-store"});
    res.end(body);
  };
  if(req.url==="/studio/assets/work-ui.js")return answer("application/javascript",js);
  if(req.url==="/studio/assets/work-ui.css")return answer("text/css",css);
  if(req.url.endsWith("/activity.json"))return answer("application/json",'{"jobs":[],"notices":[]}');
  if(req.url.includes("/tasks/t1"))return answer("text/html","<h1>Задание открыто</h1>");
  return answer("text/html",html);
});
await new Promise((ok,fail)=>{server.once("error",fail);server.listen(0,"127.0.0.1",ok);});
const browser=await chromium.launch({
  headless:true,executablePath:process.env.SERVGO_CHROME_PATH||"/usr/bin/google-chrome"
});
try{
  for(const width of [360,390,768,1024,1081,1280,1440]){
    const page=await browser.newPage({viewport:{width,height:820}});
    const errors=[];
    page.on("pageerror",err=>errors.push(err.message));
    await page.goto("http://127.0.0.1:"+server.address().port+
      "/studio/products/11111111-1111-4111-8111-111111111111/work");
    const button=page.locator("[data-sg-task-peek]");
    if(width<=1080){
      assert.equal(await button.count(),0,"mobile must not add an extra button");
      assert.equal(await page.locator(".rail").isVisible(),false);
    }else{
      await button.waitFor();
      assert.equal(await button.count(),1,"one contextual control per card");
      assert.equal(await button.getAttribute("aria-controls"),"sgWorkPreviewDetail");
      assert.equal(await button.getAttribute("aria-pressed"),"false");
      await button.focus();
      await page.keyboard.press("Enter");
      assert.equal(await button.getAttribute("aria-pressed"),"true");
      const detail=page.locator("[data-sg-preview-detail]");
      assert.equal(await detail.isVisible(),true);
      assert.match(await detail.innerText(),/Планирование завершено/);
      assert.match(await detail.innerText(),/Поставлено статей в очередь: 3/);
      assert.equal(await page.locator("[data-sg-preview-link]").getAttribute("href"),
        "/studio/products/11111111-1111-4111-8111-111111111111/tasks/t1");
      if(width===1280)await page.screenshot({path:resolve("../studio-resource-widgets-tasks-desktop.png")});
    }
    const main=page.locator("#job > a.sg-work-item");
    assert.ok((await main.getAttribute("href")).endsWith("/tasks/t1"));
    await main.click();
    assert.match(page.url(),/\/tasks\/t1$/,"primary card link must still navigate");
    assert.deepEqual(errors,[],"no page errors at width "+width);
    await page.close();
  }
}finally{
  await browser.close();
  await new Promise(ok=>server.close(ok));
}
console.log("Tasks contextual preview: 360/390/768/1024/1081/1280/1440px OK");