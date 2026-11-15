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
