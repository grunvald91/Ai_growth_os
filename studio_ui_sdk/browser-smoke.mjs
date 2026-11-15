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
    </form></article></div></section></main>
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
  const trigger = page.locator("ui-collapsible-trigger button");
  const panel = page.locator("ui-collapsible-panel");
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
  await page.screenshot({ path: "../studio-ui-sdk-mobile-smoke.png", fullPage: true });
  await page.goto(origin + "/?prefilled=1", { waitUntil: "networkidle" });
  await page.locator("ui-collapsible-trigger button").waitFor();
  assert.equal(await page.locator("ui-collapsible-panel").isVisible(), true,
    "previously populated fields must be visible by default");
  assert.deepEqual(errors, [], "browser must not have JS errors");
  console.log("UI SDK Collapsible browser smoke passed: mouse, keyboard, data persistence, prefilled state");
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
