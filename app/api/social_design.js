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
  document.getElementById("social-plan-media")?.addEventListener("submit", event => {
    event.preventDefault();
    const form = event.target;
    run(form.querySelector("button[type=submit]"), async () => {
      const selector = form.elements.plan_slot.value;
      const delimiter = selector.indexOf(":");
      if (delimiter < 0) throw new Error("Выберите публикацию из утверждённого контент-плана");
      const plan_id = selector.slice(0, delimiter);
      const slot_id = selector.slice(delimiter + 1);
      const selected = Array.from(form.elements.files.files);
      if (!selected.length || selected.length > 10) throw new Error("Выберите от 1 до 10 файлов");
      say("Загружаем файлы к контент-плану…");
      const media = [];
      for (const file of selected) {
        const data = new FormData();
        data.append("file", file, file.name);
        media.push((await send("/upload", data, true)).id);
      }
      await send("/plan-media", {plan_id, slot_id, media});
      say("Медиа привязаны к публикации контент-плана.");
      location.reload();
    });
  });
  root.addEventListener("click", event => {
    const copyButton = event.target.closest("button[data-copy-id]");
    if (!copyButton) return;
    run(copyButton, async () => {
      const textarea = document.getElementById(copyButton.dataset.copyId);
      if (!textarea) throw new Error("Материал недоступен");
      await navigator.clipboard.writeText(textarea.value);
      say("Материал скопирован. Опубликуйте его через редактор площадки.");
    });
  });
  root.addEventListener("click", event => {
    const button = event.target.closest("button[data-remove],button[data-post-action]");
    if (!button) return;
    if (button.dataset.postAction) {
      const messages = {
        cancel: "Закрыть эту публикацию без автоматического повтора? Если Telegram уже получил сообщение, оно останется в канале.",
        confirm_delivered: "Вы лично проверили канал и действительно видите сообщение? Подтверждение будет отмечено как ручное, без Telegram API ID."
      };
      if (!confirm(messages[button.dataset.postAction])) return;
      run(button, async () => {
        await send("/posts/" + button.dataset.postId + "/resolve", {action: button.dataset.postAction});
        location.reload();
      });
      return;
    }
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
