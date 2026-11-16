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


// Use the same UI SDK Collapsible primitive for the generated post itself.
// The original body, quote selection, revision command and approval controls
// are kept exactly as implemented by the dispatcher.
function enhancePostPreview(card) {
  if (!card?.__sgPostReview || card.dataset.sgPostUiEnhanced === "1") return;
  const preview = card.querySelector(".dp-post-preview");
  if (!preview || !preview.querySelector(".dp-post-body")) return;
  card.dataset.sgPostUiEnhanced = "1";

  const section = document.createElement("ui-collapsible");
  section.className = "dp-post-review-section";
  section.setAttribute("default-open", "");

  const trigger = document.createElement("ui-collapsible-trigger");
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Полный текст поста · версия " + String(card.__sgPostReview.version);
  trigger.appendChild(button);

  const panel = document.createElement("ui-collapsible-panel");
  panel.className = "dp-post-review-panel";
  while (preview.firstChild) panel.appendChild(preview.firstChild);
  section.append(trigger, panel);
  preview.appendChild(section);
}
document.addEventListener("sg:dispatcher:post-ready", event => {
  enhancePostPreview(event.detail?.card);
});
document.querySelectorAll(".dp-material").forEach(enhancePostPreview);


// Calendar pilot: progressively enhance the existing, company-scoped POST form.
// A separate preview action is required before changing a publication date.
// If JS/SDK is disabled, the original native <details> form remains usable.
function enhanceCalendarMove(details) {
  if (!(details instanceof HTMLDetailsElement) || details.dataset.sgCalendarEnhanced === "1") return;
  const form = details.querySelector("form.sg-calendar-form");
  const dateInput = form?.querySelector('input[type="datetime-local"][name="when"]');
  const originalInput = form?.querySelector('input[name="expected_publish_at"]');
  const existingSubmit = form?.querySelector('button[type="submit"]');
  if (!form || !dateInput || !originalInput || !existingSubmit) return;
  details.dataset.sgCalendarEnhanced = "1";
  const original = dateInput.value;
  const timezone = details.dataset.planTimezone || "Europe/Moscow";
  const sourceLabel = details.querySelector("summary")?.textContent?.trim() || "Перенести публикацию";

  const section = document.createElement("ui-collapsible");
  section.className = "sg-calendar-sdk-move";
  const trigger = document.createElement("ui-collapsible-trigger");
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "sg-calendar-sdk-trigger";
  toggle.textContent = sourceLabel;
  trigger.appendChild(toggle);
  const panel = document.createElement("ui-collapsible-panel");
  panel.className = "sg-calendar-sdk-panel";
  panel.hidden = true;

  const caption = document.createElement("p");
  caption.className = "sg-calendar-sdk-caption";
  caption.textContent = "Время указывается по часовому поясу плана: " + timezone;

  const preview = document.createElement("output");
  preview.className = "sg-calendar-sdk-preview";
  preview.hidden = true;
  preview.setAttribute("aria-live", "polite");
  const confirm = document.createElement("button");
  confirm.type = "submit";
  confirm.className = "btn primary sg-calendar-sdk-confirm";
  confirm.textContent = "Подтвердить перенос";
  confirm.hidden = true;
  const warning = document.createElement("p");
  warning.className = "sg-calendar-sdk-warning";
  warning.hidden = true;
  warning.setAttribute("role", "status");

  existingSubmit.type = "button";
  existingSubmit.textContent = "Проверить перенос";
  let reviewedValue = "";
  const resetPreview = () => {
    reviewedValue = "";
    preview.hidden = true;
    confirm.hidden = true;
    warning.hidden = true;
  };
  dateInput.addEventListener("input", resetPreview);
  existingSubmit.addEventListener("click", () => {
    resetPreview();
    if (!dateInput.reportValidity()) return;
    if (dateInput.value === original) {
      warning.textContent = "Дата не изменилась. Укажите новое время.";
      warning.hidden = false;
      return;
    }
    reviewedValue = dateInput.value;
    preview.textContent = "Было: " + original.replace("T", " ") +
      " → Станет: " + reviewedValue.replace("T", " ") +
      " (" + timezone + ")";
    preview.hidden = false;
    confirm.hidden = false;
  });
  form.addEventListener("submit", event => {
    if (!reviewedValue || reviewedValue !== dateInput.value) {
      event.preventDefault();
      resetPreview();
      warning.textContent = "Проверьте новую дату перед подтверждением.";
      warning.hidden = false;
    }
  });
  // Do not modify the form action, CSRF/owner checks or hidden expected state.
  form.insertBefore(caption, form.firstChild);
  form.append(preview, confirm, warning);
  panel.appendChild(form);
  section.append(trigger, panel);
  details.replaceWith(section);
}
if (new URLSearchParams(window.location.search).get("ui_sdk") !== "0") {
  document.querySelectorAll(".sg-calendar-move").forEach(enhanceCalendarMove);
}


// Phase 5: enhance confirmed plan-change previews; with the SDK disabled,
// the native before/after rows stay visible, with no dependence on JavaScript.
function enhancePlanChange(card) {
  if (!card?.__sgPlanChange || card.dataset.sgPlanChangeEnhanced === "1") return;
  const host = card.querySelector(".dp-plan-change-preview");
  if (!host) return;
  card.dataset.sgPlanChangeEnhanced = "1";
  const section = document.createElement("ui-collapsible");
  section.className = "dp-plan-change-collapsible";
  section.setAttribute("default-open", "");
  const trigger = document.createElement("ui-collapsible-trigger");
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Изменения · было → станет";
  trigger.appendChild(button);
  const panel = document.createElement("ui-collapsible-panel");
  panel.className = "dp-plan-change-panel";
  while (host.firstChild) panel.appendChild(host.firstChild);
  section.append(trigger, panel);
  host.appendChild(section);
}
if (new URLSearchParams(window.location.search).get("ui_sdk") !== "0") {
  document.addEventListener("sg:dispatcher:plan-change-ready", event => {
    enhancePlanChange(event.detail?.card);
  });
  document.querySelectorAll(".dp-confirm").forEach(card => {
    if (card.querySelector(".dp-plan-change-preview")) {
      card.__sgPlanChange = true;
      enhancePlanChange(card);
    }
  });
}
