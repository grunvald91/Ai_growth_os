// Browser smoke for the real dispatcher and pinned UI SDK plan-confirmation card.
// Self-contained HTML fixture: no authenticated server, no companies, no writes.
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {readFile} from "node:fs/promises";
import {resolve} from "node:path";
import {chromium} from "playwright";

const dispatcher = await readFile(resolve("../app/api/dispatcher_assets/dispatcher.js"));
const sdk = await readFile(resolve("../app/api/dispatcher_assets/ui-sdk-pilot.bundle.js"));
const css = await readFile(resolve("../app/api/dispatcher_assets/dispatcher.css"));
const calls = [];
const state = {
  messages: [{
    id: "assistant-1", seq: 1, role: "assistant", text: "Подтвердите изменения.",
    blocks: [
      {type: "text", text: "Подтвердите изменения."},
      {type: "confirm", confirmation_id: "preview-1", title: "Изменить пакет?",
       details: ["Запись пока не выполнена."], state: "pending", revision: 0,
       paid: false, confirm_label: "Сохранить изменения", cancel_label: "Отмена",
       change_preview: {rows: [{label: "Тема", before: "Старая тема",
         after: "Как клиенту записаться <script>alert(1)</script>"}]}}
    ]
  }], hints: []
};
function html() {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet" href="/dispatcher.css"></head><body>
  <section class="dp-home" data-api="/fake" data-voice-url="">
    <div id="dp-feed" class="dp-feed"></div><div id="dp-chips"></div>
    <form id="dp-form"><textarea id="dp-input"></textarea>
      <button id="dp-send" type="submit">Отправить</button></form>
    <button id="dp-new" type="button">Новая тема</button>
    <button id="dp-mic" type="button" hidden>Голос</button>
    <div id="dp-live" hidden><span id="dp-live-label"></span><p id="dp-live-text"></p></div>
    <p id="dp-voice-note" hidden></p>
  </section>
  <script type="application/json" id="dp-state">${JSON.stringify(state).replace(/</g, "\\u003c")}</script>
  <script src="/dispatcher.js"></script></body></html>`;
}
const server = createServer(async (request, response) => {
  if (request.url.startsWith("/fake/confirm")) {
    let body = "";
    for await (const part of request) body += part;
    calls.push(JSON.parse(body));
    response.writeHead(200, {"content-type": "application/json"});
    response.end('{"messages":[],"hints":[]}');
    return;
  }
  let type = "text/html", data = html();
  if (request.url.startsWith("/dispatcher.js")) {type = "application/javascript"; data = dispatcher;}
  if (request.url.startsWith("/dispatcher.css")) {type = "text/css"; data = css;}
  if (request.url.startsWith("/studio/assets/ui-sdk-pilot.bundle.js")) {
    type = "application/javascript"; data = sdk;
  }
  response.writeHead(200, {"content-type": type});
  response.end(data);
});
await new Promise((ok, fail) => {server.once("error", fail);server.listen(0, "127.0.0.1", ok);});
const browser = await chromium.launch({
  headless: true, executablePath: process.env.SERVGO_CHROME_PATH || "/usr/bin/google-chrome"
});
try {
  for (const width of [390, 1280]) {
    const url = `http://127.0.0.1:${server.address().port}/`;
    const page = await browser.newPage({viewport: {width, height: 820}});
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(url, {waitUntil: "networkidle"});
    await page.waitForFunction(() => customElements.get("ui-collapsible") !== undefined);
    const section = page.locator(".dp-plan-change-collapsible");
    assert.equal(await section.count(), 1);
    const preview = page.locator(".dp-plan-change-after");
    assert.match(await preview.innerText(), /Как клиенту записаться/);
    assert.equal(await page.locator(".dp-plan-change-preview script").count(), 0);
    assert.equal(calls.length, 0, "rendering a card must never submit");
    const trigger = section.locator("ui-collapsible-trigger button");
    await trigger.click();
    assert.equal(await section.locator("ui-collapsible-panel").isVisible(), false);
    await trigger.click();
    assert.equal(await preview.isVisible(), true);
    await page.getByRole("button", {name:"Сохранить изменения"}).click();
    assert.equal(calls.length, (width === 390 ? 1 : 2));
    assert.equal(calls.at(-1).confirmation_id, "preview-1");
    assert.equal(calls.at(-1).accept, true);
    assert.deepEqual(errors, []);
    await page.screenshot({path:`../studio-plan-chat-${width}.png`,fullPage:true});
    await page.close();
    const legacy = await browser.newPage({viewport: {width,height:820}});
    await legacy.goto(url + "?ui_sdk=0", {waitUntil: "networkidle"});
    assert.equal(await legacy.locator("ui-collapsible").count(), 0);
    assert.match(await legacy.locator(".dp-plan-change-after").innerText(), /Как клиенту записаться/);
    await legacy.close();
  }
  console.log("Dispatcher plan change: SDK, desktop/mobile, preview, confirmation, XSS and legacy fallback OK");
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
