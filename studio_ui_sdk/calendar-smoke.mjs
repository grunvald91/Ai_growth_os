// Browser-level end-to-end UI SDK test for the calendar transfer form.
// Uses the real vendored bundle; never touches a company, network API or publication.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { chromium } from "playwright";

const sdk = await readFile(resolve("../app/api/dispatcher_assets/ui-sdk-pilot.bundle.js"));
const css = await readFile(resolve("../app/api/dispatcher_assets/calendar-ui-sdk.css"));
function markup(disabled) {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet" href="/calendar-ui-sdk.css"></head><body>
  <main><div class="sg-calendar-entry">
  <p>Тема: Как работает онлайн-запись</p>
  <details class="sg-calendar-move" data-plan-timezone="Europe/Moscow">
  <summary>🕒 Перенести публикацию</summary>
  <form class="sg-calendar-form" method="post" action="/studio/products/owner/plans/plan/slots/1/move">
  <input type="hidden" name="expected_publish_at" value="2026-11-20T11:00:00+03:00">
  <label>Новое время · часовой пояс плана
    <input type="datetime-local" name="when" required value="2026-11-20T11:00"></label>
  <button class="btn secondary" type="submit">Сохранить дату</button>
  </form></details></div></main>
  <script>
    window.submits = 0;
    document.addEventListener("submit", event => {
      if (event.defaultPrevented) return;
      event.preventDefault();
      window.submits += 1;
      window.submission = Object.fromEntries(new FormData(event.target));
    });
  </script>
  ${disabled ? "" : '<script type="module" src="/ui-sdk-pilot.bundle.js"></script>'}
  </body></html>`;
}
const server = createServer((req, res) => {
  const send = (type, data) => {res.writeHead(200, {"Content-Type":type});res.end(data);};
  if (req.url.startsWith("/ui-sdk-pilot.bundle.js")) return send("application/javascript", sdk);
  if (req.url.startsWith("/calendar-ui-sdk.css")) return send("text/css", css);
  return send("text/html", markup(req.url.includes("ui_sdk=0")));
});
await new Promise((ok, fail) => {server.once("error",fail);server.listen(0,"127.0.0.1",ok);});
const browser = await chromium.launch({
  headless: true, executablePath: process.env.SERVGO_CHROME_PATH || "/usr/bin/google-chrome"
});
try {
  for (const width of [390, 1280]) {
    const page = await browser.newPage({ viewport: {width,height:850} });
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    const url = `http://127.0.0.1:${server.address().port}/`;
    await page.goto(url, {waitUntil:"networkidle"});
    const trigger = page.locator(".sg-calendar-sdk-trigger");
    await trigger.waitFor();
    assert.equal(await page.locator(".sg-calendar-move").count(), 0);
    const panel = page.locator(".sg-calendar-sdk-panel");
    assert.equal(await panel.isVisible(), false);
    await trigger.click();
    assert.equal(await panel.isVisible(), true);
    const input = panel.locator('input[name="when"]');
    const previewButton = panel.getByRole("button", {name:"Проверить перенос"});
    const confirm = panel.getByRole("button", {name:"Подтвердить перенос"});
    assert.equal(await confirm.isVisible(), false);
    await previewButton.click();
    assert.match(await panel.locator(".sg-calendar-sdk-warning").innerText(), /Дата не изменилась/);
    assert.equal(await page.evaluate(() => window.submits), 0);
    await input.fill("2026-11-21T14:00");
    await previewButton.click();
    assert.match(await panel.locator(".sg-calendar-sdk-preview").innerText(),
      /Было: 2026-11-20 11:00.*Станет: 2026-11-21 14:00.*Europe\/Moscow/);
    assert.equal(await confirm.isVisible(), true);
    await input.fill("2026-11-22T16:00");
    assert.equal(await confirm.isVisible(), false, "the preview expires when the date changes");
    await previewButton.click();
    await confirm.click();
    assert.equal(await page.evaluate(() => window.submits), 1);
    const payload = await page.evaluate(() => window.submission);
    assert.equal(payload.when, "2026-11-22T16:00");
    assert.equal(payload.expected_publish_at, "2026-11-20T11:00:00+03:00");
    assert.deepEqual(errors, []);
    await page.screenshot({path:`../studio-calendar-sdk-${width}.png`,fullPage:true});
    await page.close();

    // ?ui_sdk=0 preserves original markup and a normal working form.
    const legacy = await browser.newPage({ viewport: {width,height:850} });
    await legacy.goto(url + "?ui_sdk=0", {waitUntil:"networkidle"});
    assert.equal(await legacy.locator("ui-collapsible").count(), 0);
    await legacy.locator(".sg-calendar-move summary").click();
    await legacy.locator('input[name="when"]').fill("2026-11-21T14:00");
    await legacy.getByRole("button",{name:"Сохранить дату"}).click();
    assert.equal(await legacy.evaluate(() => window.submits), 1);
    await legacy.close();
  }
  console.log("Calendar UI SDK smoke passed: mobile/desktop, preview, confirmation, stale form payload, legacy fallback");
} finally {
  await browser.close();
  await new Promise(ok=>server.close(ok));
}
