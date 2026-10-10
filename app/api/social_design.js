/* Growth Studio social publishing: owner authenticated edge handles access. */
(() => {
  const root = document.getElementById("social-app");
  if (!root) return;
  const apiBase = root.dataset.api;
  const status = document.getElementById("social-feedback");
  const say = (text) => { status.textContent = text; };
  const send = async (path, value, multipart = false) => {
    const response = await fetch(apiBase + path, {
      method: "POST",
      headers: multipart ? undefined : {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: multipart ? value : JSON.stringify(value || {})
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Не удалось выполнить операцию");
    return body;
  };
  const run = async (button, fn) => {
    if (button) button.disabled = true;
    try { await fn(); }
    catch (error) { say(error.message || "Неизвестная ошибка"); }
    finally { if (button) button.disabled = false; }
  };
  document.getElementById("social-connect")?.addEventListener("submit", event => {
    event.preventDefault();
    const form = event.target;
    run(form.querySelector("button[type=submit]"), async () => {
      await send("/channels", {chat: form.elements.chat.value.trim()});
      location.reload();
    });
  });
  document.getElementById("social-post")?.addEventListener("submit", event => {
    event.preventDefault();
    const form = event.target;
    run(form.querySelector("button[type=submit]"), async () => {
      const channels = Array.from(form.querySelectorAll('input[name="channels"]:checked'), el => el.value);
      if (!channels.length) throw new Error("Выберите хотя бы один канал");
      const date = new Date(form.elements.when.value);
      if (!Number.isFinite(date.getTime())) throw new Error("Укажите дату и время");
      say("Сохраняем вложения…");
      const media = [];
      for (const file of form.elements.files.files) {
        const data = new FormData();
        data.append("file", file, file.name);
        const uploaded = await send("/upload", data, true);
        media.push(uploaded.id);
      }
      const result = await send("/posts", {
        channels, text: form.elements.text.value, media, scheduled_at: date.toISOString()
      });
      say("Создано заданий на публикацию: " + result.scheduled + ".");
      form.reset();
    });
  });
  root.addEventListener("click", event => {
    const button = event.target.closest("button[data-verify],button[data-remove]");
    if (!button) return;
    if (button.dataset.verify) run(button, async () => {
      await send("/channels/" + button.dataset.verify + "/verify");
      location.reload();
    });
    if (button.dataset.remove) {
      if (!confirm("Отключить этот канал и отменить все его будущие публикации?")) return;
      run(button, async () => {
        await send("/channels/" + button.dataset.remove + "/disconnect");
        location.reload();
      });
    }
  });
  root.addEventListener("change", event => {
    const element = event.target.closest("input[data-auto]");
    if (!element) return;
    const original = !element.checked;
    run(element, async () => {
      try {
        await send("/channels/" + element.dataset.auto + "/options", {auto_plan: element.checked});
        say("Режим автопубликации обновлён.");
      } catch (error) {
        element.checked = original;
        throw error;
      }
    });
  });
})();
