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

function setup(format, options = {}) {
  // Run actual browser functions including creationHints, against Create DOM IDs.
  const platformByFormat = {
    post: "vk", story: "vk", static_image: "instagram_reels",
    short_video: "vk_clips", whiteboard: "", package: ""
  };
  const fieldValues = {
    "sg-create-platform": platformByFormat[format] || "",
    "sg-create-count": "",
    "sg-create-audience": "",
    "sg-create-brief": "",
    "sg-create-image-model": "auto",
    "sg-create-aspect": ""
  };
  Object.assign(fieldValues, options);
  const sandbox = {
    root: {
      classList: { contains: value => value === "sg-create-dispatch" },
      getAttribute: name => name === "data-create-format" ? format : null
    },
    document: {
      getElementById: id => id in fieldValues ? {value: fieldValues[id]} : null
    }
  };
  vm.createContext(sandbox);
  vm.runInContext([
    extract("isExplicitInstruction"),
    extract("createCommand"),
    extract("creationHints"),
    extract("creationRequest")
  ].join("\n"), sandbox);
  return sandbox;
}

test("post selected + CRM question transmits exact topic, not an unsupported prompt", () => {
  const request = setup("post").creationRequest("Зачем мне CRM?");
  assert.equal(request.text, "Зачем мне CRM?");
  assert.equal(request.creation.format, "post");
  assert.equal(request.creation.platform, "vk");
  assert.equal(request.creation.topic, "Зачем мне CRM?");
});

test("all six selectable creation formats dispatch a bare subject", () => {
  const expected = {
    post: "vk", story: "vk", static_image: "instagram_reels",
    short_video: "vk_clips", whiteboard: null, package: null
  };
  for (const [format, platform] of Object.entries(expected)) {
    const request = setup(format).creationRequest("Как сократить потери заявок");
    assert.equal(request.text, "Как сократить потери заявок", format);
    assert.equal(request.creation.topic, "Как сократить потери заявок", format);
    assert.equal(request.creation.format, format);
    assert.equal(request.creation.platform, platform, format);
  }
});

test("Stories keeps count, platform, and image model for confirmation", () => {
  const request = setup("story", {
    "sg-create-count": "5",
    "sg-create-image-model": "gemini",
    "sg-create-audience": "Владельцы салонов"
  }).creationRequest("Как возвращать клиентов");
  assert.equal(request.creation.frame_count, 5);
  assert.equal(request.creation.platform, "vk");
  assert.equal(request.creation.image_model, "gemini");
  assert.equal(request.creation.aspect_ratio, "9:16");
  assert.equal(request.creation.audience, "Владельцы салонов");
});

test("static image keeps aspect ratio, model, and destination", () => {
  const request = setup("static_image", {
    "sg-create-aspect": "4:5",
    "sg-create-image-model": "openai",
    "sg-create-brief": "Не использовать неподтверждённые цифры"
  }).creationRequest("Почему клиенты не возвращаются");
  assert.equal(request.creation.aspect_ratio, "4:5");
  assert.equal(request.creation.image_model, "openai");
  assert.equal(request.creation.platform, "instagram_reels");
  assert.equal(request.creation.editorial_brief, "Не использовать неподтверждённые цифры");
});

test("simple video never inherits invisible image/audience settings", () => {
  const request = setup("whiteboard", {
    "sg-create-count": "5", "sg-create-platform": "vk",
    "sg-create-audience": "Старая аудитория",
    "sg-create-brief": "Старый бриф",
    "sg-create-image-model": "openai",
    "sg-create-aspect": "4:5"
  }).creationRequest("Как работает CRM для бизнеса");
  assert.equal(request.creation.format, "whiteboard");
  assert.equal(request.creation.topic, "Как работает CRM для бизнеса");
  assert.equal(request.creation.platform, null);
  assert.equal(request.creation.frame_count, null);
  assert.equal(request.creation.audience, null);
  assert.equal(request.creation.editorial_brief, null);
  assert.equal(request.creation.image_model, null);
  assert.equal(request.creation.aspect_ratio, null);
});

test("package has no platform but keeps editorial brief", () => {
  const request = setup("package", {
    "sg-create-brief": "Сохранять маркетинговую подачу",
    "sg-create-audience": "Малый бизнес"
  }).creationRequest("Зачем предпринимателю CRM");
  assert.equal(request.creation.platform, null);
  assert.equal(request.creation.editorial_brief, "Сохранять маркетинговую подачу");
  assert.equal(request.creation.audience, "Малый бизнес");
});

test("explicit instructions outrank any previously selected format", () => {
  for (const format of ["post", "story", "static_image", "short_video", "whiteboard", "package"]) {
    const ui = setup(format);
    for (const message of [
      "Сделай пост про CRM",
      "Покажи мои материалы",
      "Опубликуй пост",
      "Не публикуй это",
      "Создай 4 сторис про запись"
    ]) {
      const request = ui.creationRequest(message);
      assert.equal(request.text, message, format + ": " + message);
      assert.equal(request.creation?.topic, undefined, format + ": " + message);
    }
  }
});

test("no selected format is not a create operation", () => {
  const request = setup(null).creationRequest("Зачем мне CRM?");
  assert.equal(request.text, "Зачем мне CRM?");
  assert.equal(request.creation, null);
});

test("too-long topics cannot bypass server model validation", () => {
  const request = setup("post").creationRequest("а".repeat(301));
  assert.equal(request.creation.topic, undefined);
});
