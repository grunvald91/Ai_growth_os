"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("app/api/dispatcher_assets/dispatcher.js", "utf8");
function extract(name) {
  const begin = source.indexOf("  function " + name + "(");
  assert.notEqual(begin, -1, "Missing " + name);
  const end = source.indexOf("\n  }", begin);
  assert.notEqual(end, -1, "Unterminated " + name);
  return source.slice(begin, end + "\n  }".length);
}

function setup(format) {
  const sandbox = {
    root: {
      classList: { contains: value => value === "sg-create-dispatch" },
      getAttribute: name => name === "data-create-format" ? format : null
    },
    document: { getElementById: () => null },
    creationHints: () => format ? {format, platform: format === "post" ? "vk" : null} : null
  };
  vm.createContext(sandbox);
  vm.runInContext([
    extract("isExplicitInstruction"),
    extract("createCommand"),
    extract("creationRequest")
  ].join("\n"), sandbox);
  return sandbox;
}

test("post selected + CRM question transmits exact topic, not an unsupported prompt", () => {
  const ui = setup("post");
  const request = ui.creationRequest("Зачем мне CRM?");
  assert.equal(request.text, "Зачем мне CRM?");
  assert.equal(request.creation.format, "post");
  assert.equal(request.creation.platform, "vk");
  assert.equal(request.creation.topic, "Зачем мне CRM?");
});

test("selected Stories and image accept a plain subject directly", () => {
  for (const format of ["story", "static_image", "short_video", "package"]) {
    const request = setup(format).creationRequest("Как сократить потери заявок");
    assert.equal(request.text, "Как сократить потери заявок");
    assert.equal(request.creation.topic, "Как сократить потери заявок");
  }
});

test("explicit instructions always outrank a previously selected format", () => {
  const ui = setup("story");
  for (const message of [
    "Сделай пост про CRM",
    "Покажи мои материалы",
    "Опубликуй пост",
    "Не публикуй это",
    "Создай 4 сторис про запись"
  ]) {
    const request = ui.creationRequest(message);
    assert.equal(request.text, message, message);
    assert.equal(request.creation.topic, undefined, message);
  }
});

test("no selected format is not a create operation", () => {
  const request = setup(null).creationRequest("Зачем мне CRM?");
  assert.equal(request.text, "Зачем мне CRM?");
  assert.equal(request.creation, null);
});

test("too-long topics cannot silently bypass server model validation", () => {
  const request = setup("post").creationRequest("а".repeat(301));
  assert.equal(request.creation.topic, undefined);
});
