/* Shared read-only resource widgets for dispatcher, work and material library.
 * Existing authenticated routes own all links, scope checks and mutations.
 * Native <details> remains interactive with JS, SDK or network unavailable.
 */
(function () {
  "use strict";
  if (new URLSearchParams(window.location.search).get("ui_sdk") === "0") return;
  if (!window.customElements || !window.MutationObserver) return;
  // A rewritten Growth v4 page may include an older asset as well; install
  // exactly one observer even if the script appears twice.
  if (window.__servgoResourceWidgetsInitialized) return;
  window.__servgoResourceWidgetsInitialized = true;

  // Pages without a dispatcher must not load the SDK before this guard:
  // ?ui_sdk=0 means a real opt-out, not merely a visual HTML fallback.
  // On dispatcher pages, reuse the module already requested by dispatcher.js.
  if (!customElements.get("ui-collapsible") &&
      !document.querySelector('script[src*="/studio/assets/ui-sdk-pilot.bundle.js"]')) {
    import("/studio/assets/ui-sdk-pilot.bundle.js?v=4").catch(function () {
      // Keep the original native <details> when the optional bundle is unavailable.
    });
  }

  function enhance(widget) {
    if (!(widget instanceof HTMLElement) || widget.dataset.sgResourceEnhanced === "1") return;
    const details = widget.querySelector(":scope > details[data-sg-resource-details]");
    if (!details) return;
    const summary = details.querySelector(":scope > summary");
    const body = details.querySelector(":scope > .sg-resource-details-body");
    if (!summary || !body) return;

    const section = document.createElement("ui-collapsible");
    section.className = "sg-resource-sdk";
    if (details.open) section.setAttribute("default-open", "");
    const trigger = document.createElement("ui-collapsible-trigger");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sg-resource-trigger";
    button.textContent = summary.textContent.trim() || "Подробнее";
    trigger.appendChild(button);
    const panel = document.createElement("ui-collapsible-panel");
    panel.className = "sg-resource-panel";
    panel.hidden = !details.open;
    panel.appendChild(body);
    section.append(trigger, panel);
    details.replaceWith(section);
    widget.dataset.sgResourceEnhanced = "1";
  }

  function scan(root) {
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root instanceof HTMLElement && root.hasAttribute("data-sg-resource-widget")) enhance(root);
    root.querySelectorAll?.("[data-sg-resource-widget]").forEach(enhance);
  }

  // Do not replace the accessible fallback until the actual pinned SDK loads.
  customElements.whenDefined("ui-collapsible").then(function () {
    scan(document);
    const observer = new MutationObserver(function (records) {
      for (const record of records) {
        for (const node of record.addedNodes) scan(node);
      }
    });
    observer.observe(document.body, {childList: true, subtree: true});
  });
})();
