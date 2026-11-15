// Browser-level smoke against the actual built UI SDK module, without a Studio
// server, customer data, or paid action. Run from studio_ui_sdk after npm install.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { chromium } from "playwright";

const bundle = await readFile(resolve("../app/api/dispatcher_assets/ui-sdk-pilot.bundle.js"));
const css = await readFile(resolve("../app/api/dispatcher_assets/dispatcher.css"));
function html(prefilled) {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1"></head>
  <body><main class="dp-main"><section class="dp-home has-feed">
    <div class="dp-feed"><article class="dp-msg bot">
    <form class="dp-create-editor">
      <label class="dp-create-edit-field"><span>Тема</span>
        <textarea required>Рекомендации для клиентов</textarea></label>
      <label class="dp-create-edit-field" data-ui-sdk-advanced>
        <span>Аудитория</span><input name="audience" value="${prefilled ? "Клиенты" : ""}"></label>
      <label class="dp-create-edit-field" data-ui-sdk-advanced>
        <span>Дополнительные требования</span><textarea name="brief"></textarea></label>
      <button type="submit">Сохранить</button>
    </form>
    <div class="dp-material" id="post-card">
      <p class="dp-material-text">Фрагмент</p><a class="dp-link" href="/studio/material">Открыть полностью</a>
    </div></article></div></section></main>
    <script>
      window.reviewRequests = [];
      const postCard = document.getElementById("post-card");
      postCard.__sgMaterialReview = {
        block: {artifact_id:"post-123", kind:"Пост", reviewable:true,
          body:"Первый абзац. Измени выбранный фрагмент. Последний абзац.",
          version:2},
        send(chip,key) {window.reviewRequests.push({chip,key});return true;}
      };
    </script>
    <script type="module" src="/ui-sdk-pilot.bundle.js"></script>
  </body></html>`;
}
const server = createServer((req, res) => {
  if (req.url.startsWith("/ui-sdk-pilot.bundle.js")) {
    res.writeHead(200, { "Content-Type": "application/javascript" }); res.end(bundle);
  } else if (req.url.startsWith("/dispatcher.css")) {
    res.writeHead(200, { "Content-Type": "text/css" }); res.end(css);
  } else {
    res.writeHead(200, { "Content-Type": "text/html" });
    res.end(html(req.url.includes("prefilled=1")).replace("</head>",
      '<link rel="stylesheet" href="/dispatcher.css"></head>'));
  }
});
await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
const address = server.address();
const origin = `http://127.0.0.1:${address.port}`;
const browser = await chromium.launch({ headless: true, executablePath: process.env.SERVGO_CHROME_PATH || "/usr/bin/google-chrome" });
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(origin, { waitUntil: "networkidle" });
  const trigger = page.locator(".dp-create-editor ui-collapsible-trigger button");
  const panel = page.locator(".dp-create-editor ui-collapsible-panel");
  await trigger.waitFor();
  assert.equal(await panel.isVisible(), false, "optional fields must start closed");
  assert.equal(await trigger.getAttribute("aria-expanded"), "false");
  await trigger.click();
  assert.equal(await panel.isVisible(), true, "click should reveal optional inputs");
  assert.equal(await trigger.getAttribute("aria-expanded"), "true");
  await page.locator('input[name="audience"]').fill("Новые клиенты");
  await trigger.press("Enter"); // keyboard activation should close it
  assert.equal(await panel.isVisible(), false, "keyboard should close panel");
  await trigger.press("Space");
  assert.equal(await panel.isVisible(), true, "Space should reopen panel");
  assert.equal(await page.locator('input[name="audience"]').inputValue(), "Новые клиенты",
    "collapse must not discard form values");
  // The material is populated by the server and enhanced after lazy module load.
  const postPreview = page.locator("#post-card ui-collapsible-panel");
  await postPreview.waitFor();
  assert.equal(await postPreview.isVisible(), true);
  assert.equal(await page.locator(".dp-post-review-body").innerText(),
    "Первый абзац. Измени выбранный фрагмент. Последний абзац.");
  await page.evaluate(() => {
    const body = document.querySelector(".dp-post-review-body");
    const node = body.firstChild;
    const text = node.textContent;
    const start = text.indexOf("Измени выбранный фрагмент");
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, start + "Измени выбранный фрагмент".length);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    body.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  });
  assert.equal(await page.locator(".dp-post-review-selected").isVisible(), true);
  await page.locator(".dp-post-review-form textarea").fill("Перепиши проще, не меняя смысл");
  await page.locator(".dp-post-review-form button[type=submit]").click();
  const request = await page.evaluate(() => window.reviewRequests[0]);
  assert.equal(request.chip.capability, "revise_material");
  assert.equal(request.chip.params.material_id, "post-123");
  assert.equal(request.chip.params.source_version, "2");
  assert.equal(request.chip.params.fragment, "Измени выбранный фрагмент");
  assert.equal(request.chip.params.feedback, "Перепиши проще, не меняя смысл");
  assert.equal((await page.locator(".dp-post-review-feedback").innerText()).includes("Подтвердите"), true);
  // The UI sends a request to the dispatcher, not approval or publication.
  await page.screenshot({ path: "../studio-ui-sdk-mobile-smoke.png", fullPage: true });
  await page.goto(origin + "/?prefilled=1", { waitUntil: "networkidle" });
  await page.locator(".dp-create-editor ui-collapsible-trigger button").waitFor();
  assert.equal(await page.locator(".dp-create-editor ui-collapsible-panel").isVisible(), true,
    "previously populated fields must be visible by default");
  assert.deepEqual(errors, [], "browser must not have JS errors");
  console.log("UI SDK Collapsible browser smoke passed: mouse, keyboard, data persistence, prefilled state");
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
