"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("app/api/vk_publishing.py", "utf8");
const embedded = source.match(/VK_TOKEN_DEEPLINK_SCRIPT = r"""<script>([\s\S]*?)<\/script>"""/);
assert.ok(embedded, "VK link script must exist");
const script = embedded[1];

function element(value = "") {
  return {
    value, href: "", hidden: false, textContent: "", listeners: {},
    addEventListener(type, callback) { this.listeners[type] = callback; },
    fire(type) { return this.listeners[type]?.(); },
    removeAttribute(name) { if (name === "href") this.href = ""; },
  };
}

function setup(withMain = true) {
  const nodes = {
    "sg-vk-community-link": element(),
    "sg-vk-token-link": element(),
    "sg-vk-group-link": element(),
    "sg-vk-oauth-group-link": element(),
    "sg-vk-token-copy": element(),
    "sg-vk-token-help": element(),
  };
  if (withMain) nodes["sg-vk-oauth-community-link"] = element();
  const clipboard = [];
  vm.runInNewContext(script, {
    document: { getElementById: (id) => nodes[id] || null },
    URL, encodeURIComponent, decodeURIComponent,
    navigator: { clipboard: { async writeText(text) { clipboard.push(text); } } },
  });
  return {nodes, clipboard};
}

test("without community input there is NO misleading link to all VK communities", () => {
  const {nodes} = setup(false);
  const link = nodes["sg-vk-token-link"];
  assert.equal(link.hidden, true);
  assert.equal(link.href, "");
  assert.equal(nodes["sg-vk-token-copy"].hidden, true);
  assert.equal(nodes["sg-vk-group-link"].href, "");
  assert.equal(nodes["sg-vk-group-link"].hidden, true);
  assert.match(nodes["sg-vk-token-help"].textContent, /Вставьте ссылку/);
});

test("specific community names and numeric IDs lead straight to token settings", () => {
  const {nodes} = setup();
  const input = nodes["sg-vk-community-link"];
  const link = nodes["sg-vk-token-link"];
  for (const [value, destination] of [
    ["https://vk.com/my_community", "https://vk.ru/my_community?act=tokens"],
    ["vk.ru/public123456", "https://vk.ru/public123456?act=tokens"],
    ["123456", "https://vk.ru/club123456?act=tokens"],
    ["https://vk.ru/club241947800", "https://vk.ru/club241947800?act=tokens"],
    ["https://m.vk.com/club9", "https://vk.ru/club9?act=tokens"],
  ]) {
    input.value = value;
    input.fire("input");
    assert.equal(link.hidden, false);
    assert.equal(link.href, destination);
    assert.equal(nodes["sg-vk-group-link"].href, destination.replace("?act=tokens", ""));
    assert.equal(nodes["sg-vk-token-copy"].hidden, false);
  }
});

test("invalid links never become external links", () => {
  const {nodes} = setup();
  const input = nodes["sg-vk-community-link"];
  for (const value of ["https://evil.example/group", "http://vk.com/club8",
      "https://vk.com.evil.example/club8", "vk.com/club0", "vk.com/id123",
      "https://vk.com/club8/posts"]) {
    input.value = value;
    input.fire("input");
    assert.equal(nodes["sg-vk-token-link"].href, "", value);
    assert.equal(nodes["sg-vk-token-link"].hidden, true, value);
  }
});

test("group from first authorization field is reused in key form", () => {
  const {nodes} = setup();
  const main = nodes["sg-vk-oauth-community-link"];
  const keyInput = nodes["sg-vk-community-link"];
  main.value = "vk.com/club89";
  main.fire("input");
  assert.equal(keyInput.value, "vk.com/club89");
  assert.equal(nodes["sg-vk-token-link"].href, "https://vk.ru/club89?act=tokens");
  assert.equal(nodes["sg-vk-oauth-group-link"].href, "https://vk.ru/club89");
  keyInput.value = "vk.com/club90";
  keyInput.fire("input");
  main.value = "vk.com/club91";
  main.fire("input");
  assert.equal(keyInput.value, "vk.com/club90");
});

test("user can copy URL to open it in a browser instead of the VK app", async () => {
  const {nodes, clipboard} = setup();
  const input = nodes["sg-vk-community-link"];
  input.value = "vk.ru/club7";
  input.fire("input");
  await nodes["sg-vk-token-copy"].fire("click");
  assert.deepEqual(clipboard, ["https://vk.ru/club7?act=tokens"]);
});
