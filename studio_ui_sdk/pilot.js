import "@ui-sdk/web/collapsible";

// Presentation-only enhancement. Dispatcher server controls revisions, approvals,
// permissions and paid actions. No new executor or API path is introduced.
function enhanceCreationEditor(form) {
  if (!(form instanceof HTMLFormElement) || form.dataset.uiSdkEnhanced === "1") return;
  const fields = [...form.querySelectorAll("[data-ui-sdk-advanced]")];
  if (fields.length !== 2) return;

  // Never hide prefilled fields: the owner must see parameters before approval.
  const expanded = fields.some((field) => {
    const control = field.querySelector("input,textarea");
    return control && String(control.value || "").trim().length > 0;
  });

  const section = document.createElement("ui-collapsible");
  section.className = "dp-ui-sdk-collapsible";
  if (expanded) section.setAttribute("default-open", "");

  const trigger = document.createElement("ui-collapsible-trigger");
  trigger.className = "dp-ui-sdk-trigger";
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Дополнительные параметры";
  trigger.appendChild(button);

  const panel = document.createElement("ui-collapsible-panel");
  panel.className = "dp-ui-sdk-advanced";
  panel.hidden = !expanded;

  fields[0].before(section);
  section.append(trigger, panel);
  fields.forEach((field) => panel.appendChild(field));
  form.dataset.uiSdkEnhanced = "1";
}

document.addEventListener("sg:dispatcher:editor-ready", (event) => {
  enhanceCreationEditor(event.detail?.form);
});
// The lazy module can finish loading after the user has already opened an editor.
document.querySelectorAll(".dp-create-editor").forEach(enhanceCreationEditor);


// The dispatcher emits already-authorized, server-originated material blocks. The
// UI SDK only renders the text and collects feedback; the existing dispatcher
// capability still requires a separate confirmation before any AI edit is queued.
function enhanceMaterial(card) {
  const binding = card?.__sgMaterialReview;
  if (!binding || card.dataset.sgReviewEnhanced === "1") return;
  const { block, send } = binding;
  if (!block.reviewable || typeof block.body !== "string" || !block.body ||
      !block.artifact_id || typeof send !== "function") return;
  card.dataset.sgReviewEnhanced = "1";

  const oldExcerpt = card.querySelector(".dp-material-text");
  if (oldExcerpt) oldExcerpt.hidden = true;
  const fullLink = card.querySelector(".dp-link");

  const section = document.createElement("ui-collapsible");
  section.className = "dp-post-review";
  section.setAttribute("default-open", "");
  const toggle = document.createElement("ui-collapsible-trigger");
  const toggleButton = document.createElement("button");
  toggleButton.type = "button";
  toggleButton.textContent = "Полный текст поста · версия " + String(block.version || 1);
  toggle.appendChild(toggleButton);
  const panel = document.createElement("ui-collapsible-panel");
  panel.className = "dp-post-review-panel";

  const body = document.createElement("div");
  body.className = "dp-post-review-body";
  body.textContent = block.body; // never inject model-produced HTML
  body.setAttribute("aria-label", "Готовый текст поста. Можно выделить фрагмент для правки");
  const help = document.createElement("p");
  help.className = "dp-post-review-help";
  help.textContent = "Выделите нужные слова в тексте или оставьте замечание ко всему посту.";
  const chosen = document.createElement("output");
  chosen.className = "dp-post-review-selected";
  chosen.hidden = true;
  chosen.setAttribute("aria-live", "polite");
  let selectedText = "";
  function captureSelection() {
    const selection = window.getSelection?.();
    if (!selection || selection.isCollapsed ||
        !body.contains(selection.anchorNode) || !body.contains(selection.focusNode)) return;
    const text = selection.toString().trim();
    if (!text || !block.body.includes(text)) return;
    if (text.length > 420) {
      chosen.textContent = "Выделено слишком много: выберите фрагмент до 420 символов.";
      chosen.hidden = false;
      selectedText = "";
      return;
    }
    selectedText = text;
    requestKey = null;
    chosen.textContent = "Выделено: «" + text + "»";
    chosen.hidden = false;
  }
  body.addEventListener("pointerup", captureSelection);
  body.addEventListener("keyup", captureSelection);
  // On touch devices the native text selection can finish just after touchend.
  body.addEventListener("touchend", () => setTimeout(captureSelection, 100));
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "dp-post-review-clear";
  clear.textContent = "Сбросить выделение";
  clear.addEventListener("click", () => {
    selectedText = "";
    requestKey = null;
    chosen.hidden = true;
    window.getSelection?.()?.removeAllRanges();
  });

  const editor = document.createElement("form");
  editor.className = "dp-post-review-form";
  const label = document.createElement("label");
  label.textContent = "Что изменить?";
  const feedback = document.createElement("textarea");
  feedback.rows = 3;
  feedback.maxLength = 850;
  feedback.minLength = 8;
  feedback.required = true;
  feedback.placeholder = "Например: сократи этот абзац, сохрани цифры и смысл";
  label.appendChild(feedback);
  const action = document.createElement("button");
  action.type = "submit";
  action.className = "dp-btn primary";
  action.textContent = "Предложить правку";
  const status = document.createElement("p");
  status.className = "dp-post-review-feedback";
  status.setAttribute("role", "status");
  status.hidden = true;
  let requestKey = null;
  feedback.addEventListener("input", () => { requestKey = null; });
  editor.addEventListener("submit", event => {
    event.preventDefault();
    const instruction = feedback.value.trim();
    if (instruction.length < 8) {
      status.hidden = false;
      status.textContent = "Напишите замечание не короче 8 символов.";
      return;
    }
    if (!requestKey) requestKey = crypto.randomUUID?.() ||
      ("post-review-" + Date.now() + "-" + Math.random().toString(36).slice(2));
    const sent = send({
      label: "Доработать пост: " + instruction.slice(0, 55),
      capability: "revise_material",
      params: {
        material_id: String(block.artifact_id),
        source_version: String(block.version),
        feedback: instruction,
        ...(selectedText ? { fragment: selectedText } : {})
      }
    }, requestKey);
    if (sent) {
      status.hidden = false;
      status.textContent = "Замечание отправлено. Подтвердите доработку в новой карточке ниже.";
    } else {
      status.hidden = false;
      status.textContent = "Диспетчер занят. Повторите отправку.";
    }
  });
  editor.append(label, action, status);
  panel.append(body, help, chosen, clear, editor);
  section.append(toggle, panel);
  card.insertBefore(section, fullLink);
}

document.addEventListener("sg:dispatcher:material-ready", event => {
  enhanceMaterial(event.detail?.card);
});
// The async module might arrive after the dispatcher restored prior messages.
document.querySelectorAll(".dp-material").forEach(enhanceMaterial);
