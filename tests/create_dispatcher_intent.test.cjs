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
    "sg-create-aspect": "",
    "sg-create-vk-model-choice": "auto",
    "sg-create-vk-image-ratio": "4:5"
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

test("topic validation keeps the voice hint in accessible descriptions", () => {
  const attributes={};
  const sandbox={
    root:{classList:{contains: value=>value==="sg-create-dispatch"}},
    input:{setAttribute:(name,value)=>{attributes[name]=value;}},
    document:{getElementById:id=>id==="sg-create-voice-hint" ? {} : null}
  };
  vm.createContext(sandbox);
  vm.runInContext(extract("markCreateTopicInvalid"),sandbox);
  sandbox.markCreateTopicInvalid(true);
  assert.equal(attributes["aria-invalid"],"true");
  assert.equal(attributes["aria-describedby"],"sg-create-context sg-create-voice-hint dp-voice-note");
  sandbox.markCreateTopicInvalid(false);
  assert.equal(attributes["aria-invalid"],"false");
  assert.equal(attributes["aria-describedby"],"sg-create-context sg-create-voice-hint");
});

test("VK post reads the one model selector (including text-only)", () => {
  assert.equal(setup("post", {"sg-create-vk-model-choice": "none"})
    .creationRequest("Тема поста").creation.image_model, "none");
  assert.equal(setup("post", {"sg-create-vk-model-choice": "gemini"})
    .creationRequest("Тема поста").creation.image_model, "gemini");
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

test("selected material format always takes precedence over imperative words in topic", () => {
  for (const format of ["post", "story", "static_image", "short_video", "whiteboard", "package"]) {
    const ui = setup(format);
    for (const topic of [
      "Сделай рекламу для владельцев CRM",
      "Создай понятную инструкцию",
      "Как можно улучшить запись клиентов"
    ]) {
      const request = ui.creationRequest(topic);
      assert.equal(request.text, topic, format + ": " + topic);
      assert.equal(request.creation.format, format, topic);
      assert.equal(request.creation.topic, topic, format + ": " + topic);
    }
  }
});

test("no selected format is not a create operation", () => {
  const request = setup(null).creationRequest("Зачем мне CRM?");
  assert.equal(request.text, "Зачем мне CRM?");
  assert.equal(request.creation, null);
});

test("short and overlong topics return clear local validation instead of classifier fallback", () => {
  for (const format of ["post", "story", "short_video", "static_image", "whiteboard", "package"]) {
    for (const invalid of ["CRM".slice(0, 2), "а".repeat(301)]) {
      const request = setup(format).creationRequest(invalid);
      assert.equal(typeof request.validation_error, "string", format);
      assert.equal(request.creation, undefined, format);
      assert.equal(request.text, undefined, format);
    }
  }
  assert.match(setup("whiteboard").creationRequest("CRM?").validation_error, /8 символов/);
  const longVideoError = setup("whiteboard").creationRequest("а".repeat(301)).validation_error;
  assert.match(longVideoError, /разделе «Видео»/);
  assert.doesNotMatch(longVideoError, /дополнительных требованиях/);
  assert.equal(setup("post").creationRequest("CRM?").creation.topic, "CRM?");
  assert.equal(setup("whiteboard").creationRequest("Как CRM?").creation.topic, "Как CRM?");
});

test("Unicode emoji count follows Python server topic length", () => {
  assert.equal(setup("post").creationRequest("😀😀😀").creation.topic, "😀😀😀");
  assert.match(setup("post").creationRequest("😀".repeat(301)).validation_error, /300 символов/);
});

test("typed validation retains the composer draft and never sends a request", () => {
  const ui = setup("whiteboard");
  const events = [];
  ui.input = {value: "CRM?"};
  ui.busy = false;
  ui.grow = () => events.push("grow");
  ui.markCreateTopicInvalid = () => {};
  ui.showNote = (text, bad) => events.push({text, bad});
  ui.input.focus = () => {};
  ui.send = (...args) => events.push({sent: args});
  vm.runInContext(extract("sendText"), ui);
  ui.sendText();
  assert.equal(ui.input.value, "CRM?");
  assert.equal(events.length, 1);
  assert.match(events[0].text, /8 символов/);
  assert.equal(events[0].bad, true);

  ui.input.value = "Зачем компании CRM?";
  ui.sendText();
  assert.equal(ui.input.value, "");
  assert.equal(events.filter(event => event.sent).length, 1);
  assert.equal(events.filter(event => event.sent)[0].sent[0].creation.topic, "Зачем компании CRM?");
});

test("voice validation preserves recognized text instead of sending invalid selected subject", () => {
  assert.match(source, /var request = creationRequest\(transcript\)/);
  assert.match(source, /if \(request.validation_error\) \{[\s\S]*?insertText\(transcript\);[\s\S]*?return;/);
});

test("VK post creation sends image model and selected square format", () => {
  const request = setup("post", {
    "sg-create-platform": "vk",
    "sg-create-vk-model-choice": "gemini",
    "sg-create-vk-image-ratio": "1:1"
  }).creationRequest("Зачем нужна CRM?");
  assert.equal(request.creation.format, "post");
  assert.equal(request.creation.platform, "vk");
  assert.equal(request.creation.image_model, "gemini");
  assert.equal(request.creation.aspect_ratio, "1:1");
});

test("other channels and text-only VK never accidentally request paid media", () => {
  const telegram = setup("post", {
    "sg-create-platform": "telegram",
    "sg-create-vk-model-choice": "gemini",
    "sg-create-vk-image-ratio": "1:1"
  }).creationRequest("Как сделать запись проще");
  assert.equal(telegram.creation.image_model, null);
  assert.equal(telegram.creation.aspect_ratio, null);
  const noImage = setup("post", {
    "sg-create-platform": "vk",
    "sg-create-vk-model-choice": "none"
  }).creationRequest("Как сделать запись проще");
  assert.equal(noImage.creation.image_model, "none");
});

test("simple video sends its own explicit settings, never the other format's fields", () => {
  const fields = {
    "sg-create-video-length": "60", "sg-create-video-profile": "configured",
    "sg-create-video-image": "gpt-image-2.5-sunburst",
    "sg-create-video-review": "once", "sg-create-video-budget": "45",
    "sg-create-video-science": "scientific", "sg-create-video-cta": "Подпишитесь на канал",
    "sg-create-video-script": "Мой сценарий", "sg-create-video-notes": "Контекст",
    "sg-create-video-references": "Источник"
  };
  const video=setup("whiteboard",fields).creationRequest("Как работает CRM").creation.video;
  assert.equal(video.length,"60");
  assert.equal(video.profile,"configured");
  assert.equal(video.image_model,"gpt-image-2.5-sunburst");
  assert.equal(setup("whiteboard").creationRequest("Как работает CRM").creation.video.image_model,"gemini-3.1-flash-image");
  assert.equal(video.visual_review,true);
  assert.equal(video.max_api_calls,45);
  assert.equal(video.science_mode,"scientific");
  assert.equal(video.source_script,"Мой сценарий");
  assert.equal(video.reference_materials,"Источник");
  assert.equal(setup("post",fields).creationRequest("Как работает CRM").creation.video,undefined);
});
