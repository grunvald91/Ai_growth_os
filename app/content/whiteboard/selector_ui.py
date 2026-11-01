"""Accessible, company-scoped video-topic picker for the existing Studio form."""


def topic_picker_html(product_id) -> str:
    base = f"/studio/products/{product_id}/whiteboard"
    html = r"""
<dialog id="wb-topic-dialog" aria-labelledby="wb-topic-title" style="width:min(94vw,660px);max-height:86vh;border:1px solid var(--border,#ddd);border-radius:18px;padding:20px;background:var(--card,#fff);color:inherit">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
    <h3 id="wb-topic-title">Темы для вашей компании</h3>
    <button type="button" class="btn secondary" id="wb-topic-close" aria-label="Закрыть окно">Закрыть</button>
  </div>
  <p>Темы составляются по нише, услугам и запросам клиентов из профиля компании. Выберите одну и подтвердите запуск.</p>
  <p id="wb-topic-status" role="status" aria-live="polite"></p>
  <div id="wb-topic-options" role="radiogroup" aria-label="Предложенные темы" style="display:grid;gap:10px;max-height:36vh;overflow-y:auto"></div>
  <div id="wb-topic-quote" aria-live="polite" style="padding:12px 0"></div>
  <label style="display:flex;gap:10px;align-items:flex-start">
    <input id="wb-paid-consent" type="checkbox" disabled>
    <span id="wb-paid-caption">Разрешаю платную генерацию выбранной темы в пределах указанного лимита API-вызовов.</span>
  </label>
  <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px">
    <button class="btn secondary" type="button" id="wb-topic-cancel">Отмена</button>
    <button class="btn primary" type="button" id="wb-topic-launch" disabled>Выбрать и начать генерацию</button>
  </div>
</dialog>
<script>
(() => {
  const base = "__BASE__";
  const form = document.getElementById("wb-create-form");
  const button = document.getElementById("wb-suggest-btn");
  const dialog = document.getElementById("wb-topic-dialog");
  if (!form || !button || !dialog) return;
  const options = document.getElementById("wb-topic-options");
  const status = document.getElementById("wb-topic-status");
  const quote = document.getElementById("wb-topic-quote");
  const consent = document.getElementById("wb-paid-consent");
  const caption = document.getElementById("wb-paid-caption");
  const launch = document.getElementById("wb-topic-launch");
  const field = name => form.elements.namedItem(name);
  let chosen = "";
  let quoteReady = false;
  let quoteVersion = 0;
  function resetChoice() {
    quoteVersion += 1;
    chosen = ""; quoteReady = false; consent.checked = false; consent.disabled = true;
    launch.disabled = true; quote.textContent = "";
  }
  const close = () => dialog.close();
  dialog.addEventListener("close", () => { quoteVersion += 1; });
  document.getElementById("wb-topic-close").addEventListener("click", close);
  document.getElementById("wb-topic-cancel").addEventListener("click", close);
  consent.addEventListener("change", () => { launch.disabled = !(consent.checked && quoteReady && chosen); });
  async function showQuote() {
    const version = ++quoteVersion;
    const selected = chosen;
    quoteReady = false; consent.checked = false; consent.disabled = true; launch.disabled = true;
    if (!chosen) return;
    quote.textContent = "Рассчитываем лимит API-вызовов…";
    const params = new URLSearchParams({
      topic: chosen, duration_seconds: field("duration_seconds").value,
      science_mode: field("science_mode").value,
      visual_review: field("visual_review").value,
      max_api_calls: field("max_api_calls").value,
      source_script: field("source_script").value
    });
    try {
      const result = await fetch(base + "/quote.json", {
        method:"POST",credentials:"same-origin",cache:"no-store",
        headers:{"Content-Type":"application/x-www-form-urlencoded;charset=UTF-8"},
        body:params.toString()
      });
      if (!result.ok) throw new Error("Не удалось получить расчёт");
      const data = await result.json();
      if (version !== quoteVersion || chosen !== selected || !dialog.open) return;
      if (!data.allowed) {
        quote.textContent = "Выбранный лимит слишком мал: нужно не меньше " + data.required_calls + " вызовов. Измените настройки формы.";
        return;
      }
      quote.textContent = "Выбранная тема: " + chosen + ". Максимум " + data.limit + " API-вызовов. " +
        (data.science_review ? "Перед созданием рисунков будет проведена проверка научных утверждений." :
          "Стандартное видео без отдельной научной проверки.") +
        " Точная денежная стоимость не известна.";
      caption.textContent = "Разрешаю платную генерацию выбранной темы: до " + data.limit + " API-вызовов.";
      quoteReady = true; consent.disabled = false;
    } catch (_) {
      if (version !== quoteVersion || chosen !== selected || !dialog.open) return;
      quote.textContent = "Не удалось проверить лимит. Запуск недоступен до повторной проверки.";
    }
  }
  button.addEventListener("click", async () => {
    resetChoice(); options.replaceChildren();
    status.textContent = "Ищем темы по профилю компании…";
    dialog.showModal();
    try {
      const result = await fetch(base + "/topics.json", {credentials:"same-origin",cache:"no-store"});
      if (!result.ok) throw new Error("Ошибка чтения профиля");
      const data = await result.json();
      status.textContent = data.message || "";
      for (const item of data.topics || []) {
        const label = document.createElement("label");
        label.style.cssText = "display:flex;gap:12px;align-items:flex-start;border:1px solid #aaa5;border-radius:12px;padding:12px;cursor:pointer";
        const radio = document.createElement("input");
        radio.type = "radio"; radio.name = "wb-topic-proposal"; radio.value = item.topic;
        const description = document.createElement("span");
        const title = document.createElement("strong"); title.textContent = item.topic;
        const note = document.createElement("small"); note.textContent = " — " + item.basis;
        description.append(title, note); label.append(radio, description); options.append(label);
        radio.addEventListener("change", () => { chosen = item.topic; void showQuote(); });
      }
      if (!(data.topics || []).length) {
        const link = document.createElement("a");
        link.href = "/studio/products/__PRODUCT_ID__/profile";
        link.textContent = "Заполнить профиль компании";
        options.append(link);
      }
    } catch (_) { status.textContent = "Не удалось загрузить темы. Попробуйте снова."; }
  });
  for (const name of ["duration_seconds","science_mode","visual_review","max_api_calls","source_script"]) {
    field(name).addEventListener("change", () => { if (chosen) void showQuote(); });
  }
  launch.addEventListener("click", () => {
    if (!chosen || !quoteReady || !consent.checked) return;
    field("topic").value = chosen;
    field("wb_topic_choice").value = chosen;
    field("wb_paid_start").value = "yes";
    field("wb_paid_consent").value = "yes";
    form.requestSubmit();
  });
})();
</script>
"""
    return html.replace("__BASE__", base).replace("__PRODUCT_ID__", str(product_id))
