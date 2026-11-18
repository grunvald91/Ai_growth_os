// Real browser smoke: common task/material disclosure across Studio + dispatcher.
// No live company APIs and no mutation endpoints are contacted.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const [sdk, shared, css] = await Promise.all([
  readFile(resolve("../app/api/dispatcher_assets/ui-sdk-pilot.bundle.js")),
  readFile(resolve("../app/api/dispatcher_assets/resource-widgets.js")),
  readFile(resolve("../app/api/dispatcher_assets/resource-widgets.css")),
]);
function html() {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet" href="/resource-widgets.css">
  <style>body{--card:#fff;--text:#14263e;--muted:#52647c;--line:#e5ecf4;--tint:#f3f7fc}
  body.dark{--card:#17253a;--text:#edf3fb;--muted:#a4b6cd;--line:#304158;--tint:#1b2b43}</style>
  </head><body data-growth-design="v4">
  <main>
    <article class="sg-resource-widget" data-sg-resource-widget="task" id="task">
      <a class="sg-work-item" href="/studio/products/demo/tasks/task1">Задача · подготовка</a>
      <details class="sg-resource-details" data-sg-resource-details>
        <summary>Сведения о задании</summary>
        <div class="sg-resource-details-body"><p class="sg-resource-description">Задание выполняется.</p></div>
      </details>
    </article>
    <article class="sg-resource-widget" data-sg-resource-widget="material" data-material data-kind="post" data-search="тест пост" id="material">
      <a class="material-card" href="/studio/products/demo/materials/abc">Пост · реальный результат</a>
      <details class="sg-resource-details" data-sg-resource-details>
        <summary>Сведения о материале</summary>
        <div class="sg-resource-details-body"><p class="sg-resource-description">Проверенный текст.</p></div>
      </details>
    </article>
    <section class="sg-review-page"><div class="sg-review-list">
      <article class="sg-resource-widget" data-sg-resource-widget="material" id="review">
        <div class="sg-review-item"><h3>SEO-статья ждёт согласования</h3>
          <p>Предпросмотр сокращён, проверка по полной версии.</p>
          <a class="btn secondary" href="/studio/products/demo/materials/review1">Открыть и проверить</a>
        </div>
        <details class="sg-resource-details" data-sg-resource-details>
          <summary>Что проверить</summary>
          <div class="sg-resource-details-body">
            <p class="sg-resource-description">Проверьте факты перед решением.</p>
          </div>
        </details>
      </article>
    </div></section>
  </main>
  <script src="/resource-widgets.js"></script>
  <script src="/resource-widgets.js"></script>
  </body></html>`;
}
const sdkRequests = [];
const server = createServer((req, res) => {
  const response = (type, content) => {
    res.writeHead(200, {"Content-Type":type, "Cache-Control":"no-store"}); res.end(content);
  };
  if (req.url.startsWith("/resource-widgets.js")) return response("application/javascript", shared);
  if (req.url.startsWith("/resource-widgets.css")) return response("text/css", css);
  if (req.url.startsWith("/studio/assets/ui-sdk-pilot.bundle.js")) {
    sdkRequests.push(req.url);
    return response("application/javascript", sdk);
  }
  return response("text/html", html());
});
await new Promise((ok, fail) => {server.once("error",fail);server.listen(0,"127.0.0.1",ok);});
const browser = await chromium.launch({
  headless: true, executablePath: process.env.SERVGO_CHROME_PATH || "/usr/bin/google-chrome",
});
try {
  for (const width of [390, 1280]) {
    const url = `http://127.0.0.1:${server.address().port}/`;
    const page = await browser.newPage({viewport: {width, height: 820}});
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const startRequests = sdkRequests.length;
    await page.goto(url, {waitUntil:"networkidle"});
    await page.waitForFunction(() => document.querySelectorAll("ui-collapsible.sg-resource-sdk").length === 3);
    assert.equal(sdkRequests.length, startRequests + 1, "one SDK load per page");
    assert.equal(await page.evaluate(() => window.__servgoResourceWidgetsInitialized), true);
    assert.equal(await page.locator("[data-sg-resource-widget]").count(), 3);
    assert.equal(await page.locator("#task > .sg-work-item").getAttribute("href"), "/studio/products/demo/tasks/task1");
    assert.equal(await page.locator("#material").getAttribute("data-search"), "тест пост");
    assert.equal(await page.locator("#task > details").count(), 0);
    const task = page.locator("#task");
    const taskPanel = task.locator("ui-collapsible-panel");
    assert.equal(await taskPanel.isVisible(), false);
    await task.locator("ui-collapsible-trigger button").click();
    assert.equal(await taskPanel.isVisible(), true);
    assert.match(await taskPanel.innerText(), /Задание выполняется/);
    await task.locator("ui-collapsible-trigger button").click();
    assert.equal(await taskPanel.isVisible(), false);
    const material = page.locator("#material");
    await material.locator("ui-collapsible-trigger button").focus();
    await page.keyboard.press("Enter");
    assert.equal(await material.locator("ui-collapsible-panel").isVisible(), true);
    assert.equal(page.url(), url);
    const review = page.locator("#review");
    assert.equal(await review.getAttribute("data-sg-resource-widget"), "material");
    assert.equal(await review.locator('a.btn').getAttribute("href"),
      "/studio/products/demo/materials/review1");
    assert.equal(await review.locator("form").count(), 0, "no inline approval bypass");
    await review.locator("ui-collapsible-trigger button").focus();
    await page.keyboard.press("Enter");
    assert.equal(await review.locator("ui-collapsible-panel").isVisible(), true);
    assert.match(await review.locator("ui-collapsible-panel").innerText(), /Проверьте факты/);
    await page.evaluate(() => document.body.classList.add("dark"));
    const darkBg = await page.locator("#task .sg-resource-trigger").evaluate(el =>
      getComputedStyle(el).backgroundColor);
    const darkText = await page.locator("#task .sg-resource-trigger").evaluate(el =>
      getComputedStyle(el).color);
    assert.equal(darkBg, "rgb(27, 43, 67)", "dark trigger uses Growth v4 --tint");
    assert.equal(darkText, "rgb(237, 243, 251)", "dark trigger uses Growth v4 --text");
    await page.evaluate(() => document.body.classList.remove("dark"));
    const lightBg = await page.locator("#task .sg-resource-trigger").evaluate(el =>
      getComputedStyle(el).backgroundColor);
    assert.equal(lightBg, "rgb(243, 247, 252)", "theme switches without a reload");
    await page.evaluate(() => {
      const item = document.createElement("article");
      item.className = "sg-resource-widget";
      item.id = "new-task";
      item.setAttribute("data-sg-resource-widget", "task");
      const link = document.createElement("a");
      link.href = "/studio/products/demo/tasks/new";
      link.textContent = "Новая задача";
      const details = document.createElement("details");
      details.className = "sg-resource-details";
      details.setAttribute("data-sg-resource-details", "");
      const summary = document.createElement("summary");
      summary.textContent = "Сведения о задании";
      const body = document.createElement("div");
      body.className = "sg-resource-details-body";
      body.textContent = "<img src=x onerror=alert(1)>";
      details.append(summary,body); item.append(link,details);
      document.querySelector("main").appendChild(item);
    });
    await page.locator("#new-task ui-collapsible").waitFor();
    assert.equal(await page.locator("#new-task img").count(), 0);
    await page.locator("#new-task ui-collapsible-trigger button").click();
    assert.match(await page.locator("#new-task ui-collapsible-panel").innerText(), /<img src=x/);
    assert.deepEqual(errors, []);
    await page.screenshot({path:`../studio-resource-widgets-${width}.png`,fullPage:true});
    await page.close();

    const legacy = await browser.newPage({viewport: {width,height: 820}});
    const beforeLegacy = sdkRequests.length;
    await legacy.goto(url + "?ui_sdk=0", {waitUntil:"networkidle"});
    assert.equal(sdkRequests.length, beforeLegacy, "SDK bundle must not load on opt-out");
    assert.equal(await legacy.locator("ui-collapsible").count(), 0);
    await legacy.locator("#task details summary").click();
    assert.equal(await legacy.locator("#task details .sg-resource-description").isVisible(), true);
    await legacy.locator("#material details summary").click();
    assert.equal(await legacy.locator("#material details .sg-resource-description").isVisible(), true);
    await legacy.locator("#review details summary").click();
    assert.equal(await legacy.locator("#review details .sg-resource-description").isVisible(), true);
    assert.equal(await legacy.locator("#material > .material-card").getAttribute("href"),
      "/studio/products/demo/materials/abc");
    await legacy.close();
    if (width === 390) {
      const noJs = await browser.newPage({viewport: {width, height: 820}, javaScriptEnabled:false});
      await noJs.goto(url, {waitUntil:"load"});
      await noJs.locator("#task details summary").click();
      assert.equal(await noJs.locator("#task details .sg-resource-description").isVisible(), true);
      await noJs.close();
    }
  }
  console.log("Resource widget smoke: shared SDK, mobile/desktop, keyboard, dynamic, escaped text, native fallback OK");
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
