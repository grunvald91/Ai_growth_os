// Isolated browser smoke for the real dispatcher JS, with mocked task results.
// Never uses a real company, LLM, publication endpoint or external network.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { chromium } from "playwright";

const javascript = await readFile(resolve("../app/api/dispatcher_assets/dispatcher.js"));
const styles = await readFile(resolve("../app/api/dispatcher_assets/dispatcher.css"));
const sdk = await readFile(resolve("../app/api/dispatcher_assets/ui-sdk-pilot.bundle.js"));
const postId = "11111111-1111-4111-8111-111111111111";
const revisedId = "22222222-2222-4222-8222-222222222222";
const oldBody = "Первый абзац. Текст для исправления. <img src=x onerror=alert(1)>";
const newBody = "Первый абзац. Исправленная формулировка без обещаний.";
let revisionStarted = false, updateSent = false;
const calls = [];
function materialMessage(id, seq, materialId, body) {
  return { id, seq, role: "assistant", text: "Пост готов для проверки",
    blocks: [
      { type: "text", text: "Пост готов для проверки" },
      { type: "material", artifact_id: materialId, kind: "Пост",
        title: "Пост для клиента", status: "Ждёт вашего решения",
        excerpt: body.slice(0, 60), body, truncated: false, can_revise: true,
        href: "/studio/products/test/materials/" + materialId },
      { type: "chips", items: [{ label: "Утвердить", capability: "approve_material",
        params: { material_id: materialId } }] }
    ] };
}
const original = materialMessage("message-original", 1, postId, oldBody);
function pageHtml() {
  const state = JSON.stringify({messages:[original], hints:[]});
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link rel="stylesheet" href="/dispatcher.css"></head>
  <body><main class="dp-main"><section class="dp-home has-feed"
  data-api="/studio/products/test/dispatch">
  <div class="dp-hello"><h1>Studio</h1></div>
  <button id="dp-new" type="button">Новая тема</button>
  <form id="dp-form" class="dp-form"><textarea id="dp-input"
  placeholder="Команда"></textarea><button id="dp-send">Отправить</button></form>
  <div id="dp-chips"></div><div id="dp-feed" class="dp-feed"></div>
  <div id="dp-voice-note" hidden></div>
  <div id="dp-live" hidden><span id="dp-live-label"></span><span id="dp-live-text"></span></div>
  <script id="dp-state" type="application/json">${state}</script>
  <script src="/dispatcher.js"></script>
  </section></main></body></html>`;
}
const server = createServer(async (req, res) => {
  const send = (status, kind, data) => { res.writeHead(status, {"Content-Type":kind}); res.end(data); };
  if (req.url.startsWith("/dispatcher.css")) return send(200, "text/css", styles);
  if (req.url.startsWith("/dispatcher.js")) return send(200, "application/javascript", javascript);
  if (req.url.startsWith("/studio/assets/ui-sdk-pilot.bundle.js"))
    return send(200, "application/javascript", sdk);
  if (req.url.endsWith("/updates")) {
    const messages = revisionStarted && !updateSent ?
      [materialMessage("message-revised", 6, revisedId, newBody)] : [];
    if (messages.length) updateSent = true;
    return send(200, "application/json", JSON.stringify({messages}));
  }
  if (req.method === "POST") {
    let raw = ""; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw || "{}"); calls.push({path:req.url, body});
    if (req.url.endsWith("/messages")) {
      const approve = body.chip?.capability === "approve_material";
      const id = approve ? "approve-confirmation" : "revision-confirmation";
      const name = approve ? "Утвердить" : "Доработать";
      const number = approve ? 8 : 3;
      const answer = {id: approve ? "approve-prompt" : "revision-prompt", role:"assistant", seq:number,
        text:"Требуется подтверждение", blocks:[{type:"confirm",confirmation_id:id,
        title:name + " пост?", details:["Подтвердите действие"],
        confirm_label:name, cancel_label:"Отмена",paid:false,state:"pending",revision:0}]};
      return send(200, "application/json", JSON.stringify({messages:[answer]}));
    }
    if (req.url.endsWith("/confirm")) {
      if (body.confirmation_id === "revision-confirmation") revisionStarted = true;
      const answer = {id:body.confirmation_id + "-ok", role:"assistant", seq:body.confirmation_id==="revision-confirmation"?4:9,
        text:"Принято", blocks:[{type:"text",text:"Принято"}]};
      return send(200, "application/json", JSON.stringify({messages:[answer]}));
    }
  }
  return send(200, "text/html", pageHtml());
});
await new Promise((ok, fail) => { server.once("error", fail); server.listen(0, "127.0.0.1", ok); });
const address = server.address();
const browser = await chromium.launch({headless:true,
  executablePath:process.env.SERVGO_CHROME_PATH || "/usr/bin/google-chrome"});
try {
  for (const width of [390, 1280]) {
    // Each viewport tests an isolated chat. The server call log still records requests.
    revisionStarted=false; updateSent=false;
    const page = await browser.newPage({viewport:{width,height:860}});
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${address.port}/`, {waitUntil:"networkidle"});
    const body = page.locator(".dp-post-body").first();
    await body.waitFor();
    assert.equal(await body.textContent(), oldBody);
    assert.equal(await page.locator(".dp-post-body img").count(), 0, "text must not execute HTML");
    // Simulate the user selecting exactly one phrase in the first preview.
    await page.evaluate(() => {
      const element = document.querySelector(".dp-post-body");
      const text = element.firstChild;
      const start = text.textContent.indexOf("Текст для исправления");
      const selection = window.getSelection();
      const range = document.createRange();
      range.setStart(text, start);
      range.setEnd(text, start + "Текст для исправления".length);
      selection.removeAllRanges(); selection.addRange(range);
    });
    await page.locator(".dp-post-revise-button").first().click();
    assert.match(await page.locator(".dp-post-quote").first().textContent(),
      /Текст для исправления/);
    await page.locator(".dp-post-feedback-label textarea").first()
      .fill("Перепиши это предложение без обещаний.");
    await page.getByRole("button", {name:"Отправить правку"}).first().click();
    await page.locator(".dp-confirm-actions .primary").first().waitFor();
    const requested = calls.filter(c=>c.body.chip?.capability==="revise_material").at(-1);
    assert.equal(requested.body.chip.params.material_id, postId);
    assert.match(requested.body.chip.params.feedback, /Текст для исправления/);
    assert.match(requested.body.chip.params.feedback, /без обещаний/);
    assert.equal(revisionStarted, false, "revision must wait for the confirmation");
    await page.locator(".dp-confirm-actions .primary").first().click();
    assert.equal(revisionStarted, true);
    await page.evaluate(()=>document.dispatchEvent(new Event("visibilitychange")));
    await page.locator(".dp-post-body").last().getByText("Исправленная формулировка").waitFor()
      .catch(async ()=>{ await page.waitForFunction(
        ()=>document.querySelectorAll(".dp-post-body").length===2); });
    assert.equal(await page.locator(".dp-post-body").last().textContent(), newBody);
    const approveButtons = page.getByRole("button", {name:"Утвердить", exact:true});
    await approveButtons.last().click();
    const approval = calls.filter(c=>c.body.chip?.capability==="approve_material").at(-1);
    assert.equal(approval.body.chip.params.material_id, revisedId);
    assert.equal(calls.filter(c=>c.path.endsWith("/confirm") &&
      c.body.confirmation_id==="approve-confirmation").length,
      width === 390 ? 0 : 1, "approval is never automatic");
    await page.screenshot({path:`../studio-inline-review-${width}.png`, fullPage:true});
    assert.deepEqual(errors, [], "real dispatcher must not throw");
    await page.close();
  }
  console.log("Studio inline post browser smoke passed for 390px and 1280px");
} finally {
  await browser.close();
  await new Promise(ok=>server.close(ok));
}
