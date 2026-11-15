import "@ui-sdk/web/toggle";

// Presentation-only enhancement. The dispatcher owns its editable payload,
// revision checks, confirmation and tenant-scoped execution as before.
function enhanceCreationEditor(form) {
  if (!(form instanceof HTMLFormElement) || form.dataset.uiSdkEnhanced === "1") return;
  const fields = [...form.querySelectorAll("[data-ui-sdk-advanced]")];
  if (fields.length !== 2) return;

  const expanded = fields.some((field) => {
    const control = field.querySelector("input,textarea");
    return control && String(control.value || "").trim().length > 0;
  });

  const toggle = document.createElement("ui-toggle");
  toggle.className = "dp-ui-sdk-toggle";
  if (expanded) toggle.setAttribute("default-pressed", "");
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.textContent = "Дополнительные параметры";
  toggle.appendChild(trigger);

  const panel = document.createElement("div");
  panel.className = "dp-ui-sdk-advanced";
  panel.hidden = !expanded;

  fields[0].before(toggle, panel);
  fields.forEach((field) => panel.appendChild(field));
  toggle.addEventListener("pressedchange", (event) => {
    panel.hidden = !Boolean(event.detail?.value);
  });
  form.dataset.uiSdkEnhanced = "1";
}

document.addEventListener("sg:dispatcher:editor-ready", (event) => {
  enhanceCreationEditor(event.detail?.form);
});
// A click may have opened the editor while this lazy module was still loading.
document.querySelectorAll(".dp-create-editor").forEach(enhanceCreationEditor);
