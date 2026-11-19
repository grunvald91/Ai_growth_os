(() => {
  "use strict";

  const bootEl = document.getElementById("ai-chat-boot");
  const boot = bootEl ? JSON.parse(bootEl.textContent || "{}") : {};
  const API_BASE = boot.apiBase || "/studio/api/ai-chat";
  const PRODUCT_ID = boot.productId || null;
  const COMPANY_CTX = boot.companyContext || null;
  const COMPANIES = Array.isArray(boot.companies) ? boot.companies : [];
  const VOICE_URL = boot.voiceUrl || "/studio/voice/dictate";
  const VOICE_PREVIEW_URL = boot.voicePreviewUrl || "/studio/voice/preview";
  const VOICE_PROJECT = boot.voiceProject || "";
  const VOICE_MAX_SECONDS = Number(boot.voiceMaxSeconds) || 300;

  const MAX_FILES_PER_MESSAGE = 5;
  const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
  const ALLOWED_EXTENSIONS = new Set([
    ".txt", ".md", ".json", ".csv", ".pdf", ".docx", ".xlsx", ".xls",
    ".jpg", ".jpeg", ".png", ".webp"
  ]);
  const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp"]);

  const state = {
    catalog: {
      models: [],
      default_text_model: "",
      default_image_model: "",
      has_image_models: false,
      split_categories: false,
      stale: false,
      warning: null,
    },
    conversations: [],
    specialists: [],
    specialistId: null,
    activeConversationId: null,
    messages: [],
    selectedModelId: "",
    modelTab: "text",
    mode: "text",
    pendingAttachments: [],
    quote: null, // { messageId, text }
    activeRequestId: null,
    activeStreamController: null,
    autoScrollPinned: true,
    isSending: false,
    searchQuery: "",
    renameTargetId: null,
    deleteTargetId: null,
    selectionDraftText: "",
    selectionDraftMessageId: null,
  };

  // ---------- DOM refs ----------
  const el = {
    app: document.getElementById("app"),
    backdrop: document.getElementById("drawer-backdrop"),
    sidebar: document.getElementById("sidebar"),
    sidebarCollapseBtn: document.getElementById("sidebar-collapse-btn"),
    sidebarOpenBtn: document.getElementById("sidebar-open-btn"),
    newChatBtn: document.getElementById("new-chat-btn"),
    historySearch: document.getElementById("history-search"),
    convList: document.getElementById("conversation-list"),
    modelPickerBtn: document.getElementById("model-picker-btn"),
    modelPickerName: document.getElementById("model-picker-name"),
    modelPickerMeta: document.getElementById("model-picker-meta"),
    modelPopover: document.getElementById("model-popover"),
    modelTabs: document.getElementById("model-tabs"),
    modelList: document.getElementById("model-list"),
    chatTitle: document.getElementById("chat-title"),
    specialistBadge: document.getElementById("specialist-badge"),
    specialistPicker: document.getElementById("specialist-picker"),
    specialistStarters: document.getElementById("specialist-starters"),
    emptyTitle: document.getElementById("empty-state-title"),
    emptyDescription: document.getElementById("empty-state-desc"),
    companyBadge: document.getElementById("company-badge"),
    companySelect: document.getElementById("company-select"),
    modePill: document.getElementById("mode-pill"),
    catalogWarning: document.getElementById("catalog-warning"),
    contextBanner: document.getElementById("context-banner"),
    chatScroll: document.getElementById("chat-scroll"),
    emptyState: document.getElementById("empty-state"),
    messageList: document.getElementById("message-list"),
    scrollBottomBtn: document.getElementById("scroll-bottom-btn"),
    selectionMenu: document.getElementById("selection-menu"),
    selCopyBtn: document.getElementById("sel-copy-btn"),
    selAskBtn: document.getElementById("sel-ask-btn"),
    composerDropzone: document.getElementById("composer-dropzone"),
    dropOverlay: document.getElementById("drop-overlay"),
    composerWarning: document.getElementById("composer-warning"),
    contextChips: document.getElementById("context-chips"),
    quotePreview: document.getElementById("quote-preview"),
    quotePreviewText: document.getElementById("quote-preview-text"),
    quoteClearBtn: document.getElementById("quote-clear-btn"),
    attachmentsStrip: document.getElementById("attachments-strip"),
    composerBox: document.getElementById("composer-box"),
    composerInput: document.getElementById("composer-input"),
    voiceOverlay: document.getElementById("dp-voice-overlay"),
    voiceHitbox: document.getElementById("dp-voice-hitbox"),
    voiceStatus: document.getElementById("dp-voice-status"),
    modeMicBtn: document.getElementById("dp-mode-mic"),
    liveBox: document.getElementById("dp-live"),
    liveLabel: document.getElementById("dp-live-label"),
    liveText: document.getElementById("dp-live-text"),
    voiceNote: document.getElementById("dp-voice-note"),
    fileInput: document.getElementById("file-input"),
    attachBtn: document.getElementById("attach-btn"),
    uploadStatus: document.getElementById("upload-status"),
    sendBtn: document.getElementById("send-btn"),
    stopBtn: document.getElementById("stop-btn"),
    renameModal: document.getElementById("rename-modal"),
    renameInput: document.getElementById("rename-input"),
    renameCancelBtn: document.getElementById("rename-cancel-btn"),
    renameSaveBtn: document.getElementById("rename-save-btn"),
    deleteModal: document.getElementById("delete-modal"),
    deleteModalDesc: document.getElementById("delete-modal-desc"),
    deleteCancelBtn: document.getElementById("delete-cancel-btn"),
    deleteConfirmBtn: document.getElementById("delete-confirm-btn"),
    lightboxModal: document.getElementById("lightbox-modal"),
    lightboxImg: document.getElementById("lightbox-img"),
    lightboxCaption: document.getElementById("lightbox-caption"),
    lightboxDownload: document.getElementById("lightbox-download"),
    lightboxCopy: document.getElementById("lightbox-copy"),
    lightboxClose: document.getElementById("lightbox-close"),
    toast: document.getElementById("toast"),
  };

  // ---------- Helpers ----------
  function draftKey(convId, specialistId = state.specialistId) {
    // Isolate unsent drafts by role rather than sharing a global new-chat draft.
    const draftId = convId || (specialistId ? `new_${specialistId}` : "new");
    return `servgo_ai_chat_draft_${PRODUCT_ID || "global"}_${draftId}`;
  }

  function pendingSpecialistKey() {
    return `servgo_ai_chat_pending_specialist_${PRODUCT_ID || "global"}`;
  }

  function activeConvStorageKey() {
    return `servgo_ai_chat_active_${PRODUCT_ID || "global"}`;
  }

  function showToast(text) {
    if (!el.toast) return;
    el.toast.textContent = text;
    el.toast.hidden = false;
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => {
      el.toast.hidden = true;
    }, 2600);
  }

  function getModel(modelId) {
    return state.catalog.models.find((m) => m.id === modelId) || null;
  }

  function formatBytes(bytes) {
    if (!bytes) return "0 Б";
    if (bytes < 1024) return `${bytes} Б`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  }

  function genClientKey() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return `ck_${window.crypto.randomUUID()}`;
    }
    return `ck_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  }

  async function apiFetch(path, options = {}) {
    const resp = await fetch(`${API_BASE}${path}`, {
      credentials: "same-origin",
      ...options,
    });
    if (resp.status === 204) return null;
    const text = await resp.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { detail: text };
      }
    }
    if (!resp.ok) {
      const msg = (data && (data.detail || data.message)) || `Ошибка запроса (${resp.status})`;
      const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      err.status = resp.status;
      throw err;
    }
    return data;
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        showToast("Скопировано в буфер обмена");
        return true;
      }
    } catch {
      // Fallback below
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    if (ok) {
      showToast("Скопировано в буфер обмена");
    } else {
      showToast("Не удалось скопировать текст");
    }
    return ok;
  }

  const ICON_SPECS = {
    copy: [
      { tag: "rect", attrs: { x: "9", y: "9", width: "13", height: "13", rx: "2" } },
      { tag: "path", attrs: { d: "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" } },
    ],
    check: [
      { tag: "polyline", attrs: { points: "20 6 9 17 4 12" } },
    ],
    refresh: [
      { tag: "path", attrs: { d: "M3 12a9 9 0 1 0 3-6.7L3 8" } },
      { tag: "path", attrs: { d: "M3 3v5h5" } },
    ],
    continue: [
      { tag: "polyline", attrs: { points: "15 10 20 15 15 20" } },
      { tag: "path", attrs: { d: "M4 4v7a4 4 0 0 0 4 4h12" } },
    ],
    download: [
      { tag: "path", attrs: { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" } },
      { tag: "polyline", attrs: { points: "7 10 12 15 17 10" } },
      { tag: "line", attrs: { x1: "12", y1: "15", x2: "12", y2: "3" } },
    ],
    pencil: [
      { tag: "path", attrs: { d: "M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" } },
    ],
    trash: [
      { tag: "path", attrs: { d: "M3 6h18" } },
      { tag: "path", attrs: { d: "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" } },
      { tag: "path", attrs: { d: "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" } },
    ],
  };

  function createIcon(name, size = 15) {
    const svgNs = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.9");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    (ICON_SPECS[name] || []).forEach((spec) => {
      const node = document.createElementNS(svgNs, spec.tag);
      Object.entries(spec.attrs).forEach(([k, v]) => node.setAttribute(k, v));
      svg.appendChild(node);
    });
    return svg;
  }

  function flashButtonCopied(btn, origIcon = "copy", size = 15) {
    if (!btn) return;
    btn.replaceChildren(createIcon("check", size));
    btn.classList.add("is-copied");
    clearTimeout(btn._copyResetTimer);
    btn._copyResetTimer = setTimeout(() => {
      btn.replaceChildren(createIcon(origIcon, size));
      btn.classList.remove("is-copied");
    }, 1500);
  }

  // ---------- Safe DOM-only Markdown Renderer ----------
  function safeUrl(raw) {
    const trimmed = String(raw || "").trim();
    if (/^https?:\/\//i.test(trimmed) || /^mailto:/i.test(trimmed)) {
      return trimmed;
    }
    return null;
  }

  function appendInlineNodes(parent, text) {
    const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|\[([^\]]+)\]\(([^)\s]+)\))/g;
    let last = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) {
        parent.appendChild(document.createTextNode(text.slice(last, match.index)));
      }
      const token = match[0];
      if (token.startsWith("`") && token.endsWith("`")) {
        const code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        parent.appendChild(code);
      } else if ((token.startsWith("**") && token.endsWith("**")) || (token.startsWith("__") && token.endsWith("__"))) {
        const strong = document.createElement("strong");
        appendInlineNodes(strong, token.slice(2, -2));
        parent.appendChild(strong);
      } else if (token.startsWith("*") && token.endsWith("*")) {
        const em = document.createElement("em");
        appendInlineNodes(em, token.slice(1, -1));
        parent.appendChild(em);
      } else if (match[2] !== undefined && match[3] !== undefined) {
        const url = safeUrl(match[3]);
        if (url) {
          const a = document.createElement("a");
          a.href = url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = match[2];
          parent.appendChild(a);
        } else {
          parent.appendChild(document.createTextNode(match[2]));
        }
      }
      last = match.index + token.length;
    }
    if (last < text.length) {
      parent.appendChild(document.createTextNode(text.slice(last)));
    }
  }

  function isTableRow(line) {
    return /^\|.*\|$/.test(line.trim());
  }

  function isTableSeparator(line) {
    return /^\|(?:\s*:?-+:?\s*\|)+$/.test(line.trim());
  }

  function splitTableCells(line) {
    return line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());
  }

  function renderMarkdownInto(container, markdownText) {
    container.textContent = "";
    const text = String(markdownText || "").replace(/\r\n/g, "\n");
    if (!text.trim()) return;

    const lines = text.split("\n");
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      // Fenced code block
      const fenceMatch = line.match(/^```([\w.+-]*)\s*$/);
      if (fenceMatch) {
        const lang = fenceMatch[1] || "code";
        const codeLines = [];
        i += 1;
        while (i < lines.length && !/^```\s*$/.test(lines[i])) {
          codeLines.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) i += 1;
        const codeText = codeLines.join("\n");

        const wrap = document.createElement("div");
        wrap.className = "ai-code-block";

        const head = document.createElement("div");
        head.className = "ai-code-head";
        const langSpan = document.createElement("span");
        langSpan.textContent = lang;
        const copyBtn = document.createElement("button");
        copyBtn.type = "button";
        copyBtn.className = "ai-code-copy ai-icon-action-btn";
        copyBtn.title = "Копировать код";
        copyBtn.setAttribute("aria-label", "Копировать код");
        copyBtn.appendChild(createIcon("copy", 14));
        copyBtn.addEventListener("click", async () => {
          if (await copyText(codeText)) flashButtonCopied(copyBtn, "copy", 14);
        });
        head.append(langSpan, copyBtn);

        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = codeText;
        pre.appendChild(code);

        wrap.append(head, pre);
        container.appendChild(wrap);
        continue;
      }

      // Blank line
      if (!line.trim()) {
        i += 1;
        continue;
      }

      // Headings
      const headingMatch = line.match(/^(#{1,4})\s+(.+)$/);
      if (headingMatch) {
        const level = Math.min(4, headingMatch[1].length);
        const h = document.createElement(`h${level}`);
        appendInlineNodes(h, headingMatch[2]);
        container.appendChild(h);
        i += 1;
        continue;
      }

      // Table
      if (i + 1 < lines.length && isTableRow(line) && isTableSeparator(lines[i + 1])) {
        const headers = splitTableCells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && isTableRow(lines[i])) {
          rows.push(splitTableCells(lines[i]));
          i += 1;
        }
        const wrap = document.createElement("div");
        wrap.className = "ai-table-wrap";
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const trHead = document.createElement("tr");
        headers.forEach((hText) => {
          const th = document.createElement("th");
          appendInlineNodes(th, hText);
          trHead.appendChild(th);
        });
        thead.appendChild(trHead);
        table.appendChild(thead);

        const tbody = document.createElement("tbody");
        rows.forEach((row) => {
          const tr = document.createElement("tr");
          headers.forEach((_, idx) => {
            const td = document.createElement("td");
            appendInlineNodes(td, row[idx] || "");
            tr.appendChild(td);
          });
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        container.appendChild(wrap);
        continue;
      }

      // Blockquote
      if (/^>\s?/.test(line)) {
        const quoteLines = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          quoteLines.push(lines[i].replace(/^>\s?/, ""));
          i += 1;
        }
        const bq = document.createElement("blockquote");
        appendInlineNodes(bq, quoteLines.join(" "));
        container.appendChild(bq);
        continue;
      }

      // Unordered list
      if (/^[-*+]\s+/.test(line)) {
        const ul = document.createElement("ul");
        while (i < lines.length && /^[-*+]\s+/.test(lines[i])) {
          const li = document.createElement("li");
          appendInlineNodes(li, lines[i].replace(/^[-*+]\s+/, ""));
          ul.appendChild(li);
          i += 1;
        }
        container.appendChild(ul);
        continue;
      }

      // Ordered list
      if (/^\d+\.\s+/.test(line)) {
        const ol = document.createElement("ol");
        while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
          const li = document.createElement("li");
          appendInlineNodes(li, lines[i].replace(/^\d+\.\s+/, ""));
          ol.appendChild(li);
          i += 1;
        }
        container.appendChild(ol);
        continue;
      }

      // Paragraph
      const paraLines = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^```/.test(lines[i]) &&
        !/^#{1,4}\s+/.test(lines[i]) &&
        !/^[-*+]\s+/.test(lines[i]) &&
        !/^\d+\.\s+/.test(lines[i]) &&
        !/^>\s?/.test(lines[i])
      ) {
        paraLines.push(lines[i]);
        i += 1;
      }
      const p = document.createElement("p");
      appendInlineNodes(p, paraLines.join("\n"));
      container.appendChild(p);
    }
  }

  // ---------- Theme Sync ----------
  function syncThemeButtons() {
    const pref = window.ServGoTheme ? window.ServGoTheme.getPreference() : "auto";
    document.querySelectorAll(".ai-theme-btn").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.themePref === pref);
    });
  }

  // ---------- Model Picker & Validation ----------
  function renderCatalog() {
    if (state.catalog.warning) {
      el.catalogWarning.textContent = state.catalog.warning;
      el.catalogWarning.hidden = false;
    } else {
      el.catalogWarning.hidden = true;
    }

    el.modelTabs.hidden = !state.catalog.split_categories;
    document.querySelectorAll(".ai-model-tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.modelTab === state.modelTab);
    });

    const filtered = state.catalog.models.filter((m) => {
      if (!state.catalog.split_categories) return true;
      return state.modelTab === "image" ? m.supports_image_generation : m.supports_text;
    });

    el.modelList.textContent = "";
    if (filtered.length === 0) {
      const empty = document.createElement("div");
      empty.className = "ai-conv-empty";
      empty.textContent = "Нет моделей в этой категории.";
      el.modelList.appendChild(empty);
    } else {
      filtered.forEach((m) => {
        const opt = document.createElement("button");
        opt.type = "button";
        opt.className = "ai-model-option";
        if (m.id === state.selectedModelId) opt.classList.add("is-selected");
        opt.disabled = !m.available || m.access_enabled !== true;

        const head = document.createElement("div");
        head.className = "ai-model-opt-head";
        const name = document.createElement("span");
        name.className = "ai-model-opt-title";
        name.textContent = m.name;
        const prov = document.createElement("span");
        prov.className = "ai-model-opt-provider";
        prov.textContent = m.provider;
        head.append(name, prov);

        const badges = document.createElement("div");
        badges.className = "ai-model-badges";
        const tags = [];
        if (m.supports_text) tags.push({ label: "Текст", ok: true });
        if (m.supports_image_generation) tags.push({ label: "Изображения", ok: true });
        if (m.supports_vision) tags.push({ label: "Входящие фото", ok: true });
        if (m.supports_files) tags.push({ label: "Файлы", ok: true });
        if (m.access_enabled !== true) {
          tags.push({ label: m.access_label || "Недоступна по тарифу", ok: false, off: true });
        } else {
          tags.push({ label: m.available ? "Доступна" : "Недоступна", ok: m.available, off: !m.available });
        }

        tags.forEach((t) => {
          const span = document.createElement("span");
          span.className = `ai-cap-tag${t.ok ? " is-ok" : ""}${t.off ? " is-off" : ""}`;
          span.textContent = t.label;
          badges.appendChild(span);
        });

        opt.append(head, badges);
        opt.addEventListener("click", async () => {
          if (!m.available || m.access_enabled !== true) return;
          state.selectedModelId = m.id;
          if (state.mode === "image" && !m.supports_image_generation && m.supports_text) {
            setMode("text");
          } else if (state.mode === "text" && m.supports_image_generation && !m.supports_text) {
            setMode("image");
          }
          el.modelPopover.hidden = true;
          el.modelPickerBtn.setAttribute("aria-expanded", "false");
          updateSelectedModelDisplay();
          validateComposerCapabilities();
          if (state.activeConversationId) {
            try {
              await apiFetch(`/conversations/${state.activeConversationId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ selected_model: m.id }),
              });
            } catch {
              // Non-fatal; model_id is also sent per message.
            }
          }
        });
        el.modelList.appendChild(opt);
      });
    }

    updateSelectedModelDisplay();
    validateComposerCapabilities();
  }

  function updateSelectedModelDisplay() {
    const model = getModel(state.selectedModelId);
    if (!model) {
      el.modelPickerName.textContent = state.selectedModelId || "Выберите модель";
      el.modelPickerMeta.hidden = true;
      return;
    }
    el.modelPickerName.textContent = model.name;
    el.modelPickerMeta.textContent = model.supports_image_generation && !model.supports_text ? "Фото" : model.provider;
    el.modelPickerMeta.hidden = false;
  }

  function validateComposerCapabilities() {
    const model = getModel(state.selectedModelId);
    const warnings = [];
    let blocked = false;

    if (!model) {
      warnings.push("Модель не выбрана.");
      blocked = true;
    } else if (model.access_enabled !== true) {
      warnings.push(model.access_status === "paid"
        ? `Модель «${model.name}» доступна за отдельную оплату. Пока она отключена.`
        : `Модель «${model.name}» требует проверки тарифа. Выберите другую модель.`);
      blocked = true;
    } else if (!model.available) {
      warnings.push(`Модель «${model.name}» сейчас недоступна.`);
      blocked = true;
    } else {
      if (state.mode === "image" && !model.supports_image_generation) {
        warnings.push(`Модель «${model.name}» не поддерживает генерацию изображений. Выберите модель из вкладки «Изображения» или переключитесь на «Текст».`);
        blocked = true;
      }
      if (state.mode === "text" && !model.supports_text) {
        warnings.push(`Модель «${model.name}» предназначена только для генерации изображений.`);
        blocked = true;
      }
      const hasImages = state.pendingAttachments.some((a) => a.is_image);
      if (hasImages && !model.supports_vision) {
        warnings.push(`Модель «${model.name}» не поддерживает входящие изображения. Удалите изображение или выберите модель с пометкой «Входящие фото».`);
        blocked = true;
      }
      if (state.mode === "image" && state.pendingAttachments.length > 0) {
        warnings.push("В режиме генерации изображений прикрепление файлов не поддерживается.");
        blocked = true;
      }
    }

    if (warnings.length === 0 && state.mode === "image" && state.specialistId) {
      warnings.push("Инструкции специалиста действуют только для текста. Изображение создаётся по вашему описанию.");
    }

    if (warnings.length > 0) {
      el.composerWarning.textContent = warnings[0];
      el.composerWarning.hidden = false;
    } else {
      el.composerWarning.hidden = true;
    }
    el.sendBtn.disabled = blocked || state.isSending;
    return !blocked;
  }

  const VOICE_HOLD_PLACEHOLDER = "Нажмите — текст · удерживайте — голос";
  const VOICE_TYPING_PLACEHOLDER = "Напишите сообщение... (Enter — отправить, Shift+Enter — новая строка)";
  const VOICE_IMAGE_PLACEHOLDER = "Опишите изображение или удерживайте для голоса";

  function voiceDefaultPlaceholder() {
    if (state.mode === "image") return VOICE_IMAGE_PLACEHOLDER;
    return voiceSupported() ? VOICE_HOLD_PLACEHOLDER : VOICE_TYPING_PLACEHOLDER;
  }

  function voiceSupported() {
    return Boolean(
      navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder && VOICE_URL
    );
  }

  function setMode(nextMode) {
    state.mode = nextMode === "image" ? "image" : "text";
    document.querySelectorAll(".ai-mode-btn").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.mode === state.mode);
    });
    el.modePill.textContent = `Режим: ${state.mode === "image" ? "Изображение" : "Текст"}`;
    const isEditing = el.composerBox && el.composerBox.classList.contains("voice-text-editing");
    el.composerInput.placeholder = isEditing
      ? (state.mode === "image" ? "Опишите изображение для генерации... (Enter — отправить)" : VOICE_TYPING_PLACEHOLDER)
      : voiceDefaultPlaceholder();

    const current = getModel(state.selectedModelId);
    if (state.mode === "image") {
      state.modelTab = "image";
      if (!current || !current.supports_image_generation) {
        const fallbackImg = state.catalog.default_image_model || (state.catalog.models.find((m) => m.supports_image_generation && m.available && m.access_enabled === true) || {}).id;
        if (fallbackImg) state.selectedModelId = fallbackImg;
      }
    } else {
      state.modelTab = "text";
      if (!current || !current.supports_text) {
        const fallbackTxt = state.catalog.default_text_model || (state.catalog.models.find((m) => m.supports_text && m.available && m.access_enabled === true) || {}).id;
        if (fallbackTxt) state.selectedModelId = fallbackTxt;
      }
    }
    renderCatalog();
    renderContextChips();
  }

  // ---------- Contextual Company Chips Always Above Input ----------
  function setupCompanyContextUI() {
    if (COMPANIES.length > 1 && el.companySelect) {
      el.companySelect.textContent = "";
      COMPANIES.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.name;
        if (PRODUCT_ID && c.id === PRODUCT_ID) opt.selected = true;
        el.companySelect.appendChild(opt);
      });
      el.companySelect.hidden = false;
      if (el.companyBadge) el.companyBadge.hidden = true;
      el.companySelect.addEventListener("change", () => {
        const nextPid = el.companySelect.value;
        if (!nextPid) return;
        window.location.href = `/studio/chat?product_id=${encodeURIComponent(nextPid)}`;
      });
    } else if (COMPANY_CTX && COMPANY_CTX.name && el.companyBadge) {
      el.companyBadge.textContent = COMPANY_CTX.name;
      el.companyBadge.hidden = false;
      if (el.companySelect) el.companySelect.hidden = true;
    }
  }

  function buildContextChips() {
    const companyName = (COMPANY_CTX && COMPANY_CTX.name) || "";
    const hasCompany = Boolean(companyName);
    const labelRef = hasCompany ? `«${companyName}»` : "нашего бизнеса";
    const categoryNote = COMPANY_CTX && COMPANY_CTX.category ? ` (${COMPANY_CTX.category})` : "";
    const services = (COMPANY_CTX && Array.isArray(COMPANY_CTX.services)) ? COMPANY_CTX.services : [];
    const topics = (COMPANY_CTX && Array.isArray(COMPANY_CTX.topics)) ? COMPANY_CTX.topics : [];
    const primaryService = services[0] || "";
    const servicePhrase = primaryService ? ` с фокусом на услугу «${primaryService}»` : "";

    if (state.quote && state.quote.text) {
      return [
        { label: "Объясни проще", prompt: "Объясни выделенный фрагмент проще и с конкретным примером." },
        { label: `Примени к ${labelRef}`, prompt: `Как лучше применить это на практике для ${labelRef}${categoryNote}?` },
        { label: "План по шагам", prompt: `Разбей эту идею на пошаговый план внедрения для ${labelRef}.` },
      ];
    }

    if (state.pendingAttachments.length > 0) {
      return [
        { label: "5 главных выводов", prompt: "Разбери прикреплённые материалы и выдели 5 главных выводов." },
        { label: `Внедрить в ${labelRef}`, prompt: `Какие шаги из прикреплённого файла стоит внедрить в ${labelRef}${categoryNote}?` },
        { label: "Таблица задач и рисков", prompt: "Сформируй по прикреплённому материалу таблицу: задача, срок, ожидаемый эффект." },
      ];
    }

    if (state.mode === "image") {
      const chips = [
        {
          label: `Баннер акции ${hasCompany ? `«${companyName}»` : ""}`.trim(),
          prompt: `Создай современный рекламный баннер для ${labelRef}${categoryNote}${servicePhrase}: чистая композиция, выразительный свет, без мелкого текста на изображении.`,
        },
        {
          label: "Обложка для соцсетей",
          prompt: `Сгенерируй стильную минималистичную обложку для канала ${labelRef}${categoryNote}, гармоничные цвета, без текста.`,
        },
        {
          label: "Сцена для Stories 9:16",
          prompt: `Вертикальный визуал 9:16 для сторис ${labelRef}${categoryNote}: чистый верхний край под будущий заголовок, фотореализм, без надписей.`,
        },
      ];
      if (primaryService) {
        chips.push({
          label: `Визуал: ${primaryService}`,
          prompt: `Рекламное изображение по услуге «${primaryService}» для ${labelRef}${categoryNote}, современный премиальный стиль, без текста.`,
        });
      }
      return chips;
    }

    const hasAssistantMessages = state.messages.some((m) => m.role === "assistant" && (m.content || "").trim());
    if (hasAssistantMessages) {
      return [
        { label: "Сократи и конкретизируй", prompt: `Сократи последний ответ для ${labelRef} вдвое — оставь только ключевые шаги и цифры.` },
        { label: `Пост для ${labelRef}`, prompt: `Преврати эти идеи в готовый пост для Telegram-канала ${labelRef}${servicePhrase}.` },
        { label: "Уточни под клиентов", prompt: `Адаптируй эти рекомендации под целевых клиентов ${labelRef}${categoryNote} и оформи в таблицу.` },
        { label: "3 заголовка и оффера", prompt: `Предложи 3 сильных заголовка и оффера для ${labelRef} по итогам обсуждения.` },
      ];
    }

    const starter = [];
    topics.slice(0, 2).forEach((t) => {
      starter.push({
        label: t.label,
        prompt: `Подготовь материал для ${labelRef}${categoryNote} по теме «${t.topic}»: тезисы, структура и готовый текст без штампов.`,
      });
    });
    starter.push(
      {
        label: `План акции ${hasCompany ? `«${companyName}»` : "бизнеса"}`,
        prompt: `Составь краткий план запуска акции для ${labelRef}${categoryNote}${servicePhrase} на 2 недели с таблицей KPI.`,
      },
      {
        label: primaryService ? `Пост про «${primaryService}»` : `Пост в Telegram`,
        prompt: `Напиши сильный пост для Telegram от имени ${labelRef}${categoryNote}${servicePhrase}: заголовок, польза клиенту и мягкий призыв к записи.`,
      },
      {
        label: "Ответ клиенту на отзыв",
        prompt: `Напиши вежливый и уверенный шаблон ответа клиенту ${labelRef} на критический отзыв с предложением решить вопрос.`,
      },
      {
        label: "Идеи привлечения клиентов",
        prompt: `Предложи 5 практических гипотез привлечения новых и возврата постоянных клиентов для ${labelRef}${categoryNote}.`,
      },
    );
    return starter.slice(0, 5);
  }

  function renderContextChips() {
    if (!el.contextChips) return;
    const chips = buildContextChips();
    el.contextChips.textContent = "";
    chips.forEach((chip) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ai-context-chip";
      btn.textContent = chip.label;
      btn.title = chip.prompt;
      btn.addEventListener("click", () => {
        el.composerInput.value = chip.prompt;
        localStorage.setItem(draftKey(state.activeConversationId), el.composerInput.value);
        autoResizeTextarea();
        el.composerInput.focus();
      });
      el.contextChips.appendChild(btn);
    });
  }

  // ---------- Specialist picker (the role is persisted per conversation) ----------
  function getSpecialist(specialistId) {
    return state.specialists.find((item) => item.id === specialistId) || null;
  }

  function renderSpecialistPicker() {
    if (!el.specialistPicker) return;
    const chosen = getSpecialist(state.specialistId);
    el.specialistBadge.hidden = !state.specialistId;
    if (state.specialistId) {
      el.specialistBadge.textContent = chosen ? chosen.name : "Специалист недоступен";
    }
    if (el.emptyTitle) el.emptyTitle.textContent = chosen ? chosen.name : "Чем помочь сегодня?";
    if (el.emptyDescription) {
      el.emptyDescription.textContent = chosen
        ? chosen.description + ". Начните с готового вопроса или напишите свой."
        : "Выберите специалиста или задайте вопрос обычному ИИ. Модель можно поменять независимо от роли.";
    }

    const options = [
      { id: null, name: "Обычный чат", description: "Любые вопросы без специализации" },
      ...state.specialists,
    ];
    el.specialistPicker.textContent = "";
    options.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ai-specialist-choice";
      if (item.id === state.specialistId) btn.classList.add("is-selected");
      btn.setAttribute("aria-pressed", String(item.id === state.specialistId));
      const glyph = document.createElement("span");
      glyph.className = "ai-specialist-choice-icon";
      glyph.textContent = ({ business_consultant: "▦", marketer: "◎", smm_specialist: "✦" })[item.id] || "◌";
      const content = document.createElement("span");
      content.className = "ai-specialist-choice-text";
      const name = document.createElement("strong");
      name.textContent = item.name;
      const description = document.createElement("small");
      description.textContent = item.description;
      content.append(name, description);
      btn.append(glyph, content);
      btn.addEventListener("click", async () => {
        if (state.isSending || item.id === state.specialistId) return;
        // Changing a role starts a new conversation and never borrows its draft.
        if (!state.activeConversationId) {
          localStorage.setItem(draftKey(null), el.composerInput.value);
        } else {
          await startNewChat();
        }
        state.specialistId = item.id;
        if (item.id) localStorage.setItem(pendingSpecialistKey(), item.id);
        else localStorage.removeItem(pendingSpecialistKey());
        el.composerInput.value = localStorage.getItem(draftKey(null)) || "";
        autoResizeTextarea();
        if (state.mode !== "text") setMode("text");
        renderSpecialistPicker();
        validateComposerCapabilities();
        el.composerInput.focus();
      });
      el.specialistPicker.appendChild(btn);
    });
    if (el.specialistStarters) {
      el.specialistStarters.textContent = "";
      (chosen ? chosen.starter_prompts : []).forEach((prompt) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ai-suggestion-chip";
        btn.textContent = prompt;
        btn.addEventListener("click", () => {
          el.composerInput.value = prompt;
          localStorage.setItem(draftKey(state.activeConversationId), prompt);
          autoResizeTextarea();
          el.composerInput.focus();
        });
        el.specialistStarters.appendChild(btn);
      });
    }
  }

  // ---------- Conversations Sidebar ----------
  function renderConversations() {
    el.convList.textContent = "";
    if (state.conversations.length === 0) {
      const empty = document.createElement("div");
      empty.className = "ai-conv-empty";
      empty.textContent = state.searchQuery ? "Совпадений не найдено" : "Пока нет диалогов";
      el.convList.appendChild(empty);
      return;
    }

    state.conversations.forEach((conv) => {
      const item = document.createElement("div");
      item.className = "ai-conv-item";
      if (conv.id === state.activeConversationId) item.classList.add("is-active");

      const mainBtn = document.createElement("button");
      mainBtn.type = "button";
      mainBtn.className = "ai-conv-item-main";
      const title = document.createElement("span");
      title.className = "ai-conv-title";
      title.textContent = conv.title;
      const meta = document.createElement("span");
      meta.className = "ai-conv-meta";
      const specialist = getSpecialist(conv.specialist_id);
      meta.textContent = (specialist ? specialist.name + " · " : "") + (conv.last_message_preview || "Пустой диалог");
      mainBtn.append(title, meta);
      mainBtn.addEventListener("click", () => {
        closeMobileDrawer();
        selectConversation(conv.id);
      });

      const actions = document.createElement("div");
      actions.className = "ai-conv-actions";

      const renameBtn = document.createElement("button");
      renameBtn.type = "button";
      renameBtn.className = "ai-conv-act-btn";
      renameBtn.title = "Переименовать";
      renameBtn.setAttribute("aria-label", "Переименовать");
      renameBtn.appendChild(createIcon("pencil", 13));
      renameBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openRenameModal(conv.id, conv.title);
      });

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "ai-conv-act-btn is-danger";
      deleteBtn.title = "Удалить";
      deleteBtn.setAttribute("aria-label", "Удалить");
      deleteBtn.appendChild(createIcon("trash", 13));
      deleteBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openDeleteModal(conv.id, conv.title);
      });

      actions.append(renameBtn, deleteBtn);
      item.append(mainBtn, actions);
      el.convList.appendChild(item);
    });
  }

  async function loadConversations() {
    const qs = new URLSearchParams();
    if (state.searchQuery.trim()) qs.set("q", state.searchQuery.trim());
    if (PRODUCT_ID) qs.set("product_id", PRODUCT_ID);
    const list = await apiFetch(`/conversations?${qs.toString()}`);
    state.conversations = Array.isArray(list) ? list : [];
    renderConversations();
  }

  // ---------- Messages Rendering ----------
  function statusLabel(status) {
    switch (status) {
      case "pending": return "Ожидание ответа";
      case "streaming": return "Генерация";
      case "completed":
      case "done": return "Готово";
      case "error":
      case "outcome_unknown": return "Ошибка";
      case "stopped": return "Остановлено";
      default: return status;
    }
  }

  function updateContextBanner() {
    const truncatedMsg = [...state.messages].reverse().find((m) => m.context_truncated);
    if (truncatedMsg) {
      el.contextBanner.textContent = `В контекст модели переданы последние сообщения; более ранних сообщений не вошло: ${truncatedMsg.context_omitted_count}. Полная история сохранена в диалоге.`;
      el.contextBanner.hidden = false;
    } else {
      el.contextBanner.hidden = true;
    }
  }

  function renderMessages() {
    el.messageList.textContent = "";
    el.emptyState.hidden = state.messages.length > 0;
    updateContextBanner();

    state.messages.forEach((msg) => {
      el.messageList.appendChild(buildMessageElement(msg));
    });
    renderContextChips();
    renderSpecialistPicker();
    if (state.autoScrollPinned) {
      scrollToBottom(false);
    }
  }

  function buildMessageElement(msg) {
    const wrapper = document.createElement("article");
    wrapper.className = `ai-msg ai-msg-${msg.role}`;
    wrapper.dataset.messageId = msg.id;

    const card = document.createElement("div");
    card.className = "ai-msg-card";

    // Meta row
    const meta = document.createElement("div");
    meta.className = "ai-msg-meta";
    const roleSpan = document.createElement("span");
    roleSpan.className = "ai-msg-role";
    roleSpan.textContent = msg.role === "user" ? "Вы" : "ServGo AI";
    meta.appendChild(roleSpan);

    if (msg.model_id) {
      const mSpan = document.createElement("span");
      mSpan.className = "ai-msg-model";
      const modelObj = getModel(msg.model_id);
      mSpan.textContent = modelObj ? modelObj.name : msg.model_id;
      meta.appendChild(mSpan);
    }

    if (msg.role === "assistant") {
      const st = document.createElement("span");
      st.className = `ai-status-badge ai-status-${msg.status}`;
      st.textContent = statusLabel(msg.status);
      meta.appendChild(st);
    }
    card.appendChild(meta);

    // Quote block (user question asked about selected snippet)
    if (msg.quote_text) {
      const qBox = document.createElement("div");
      qBox.className = "ai-msg-quote";
      const qLabel = document.createElement("strong");
      qLabel.textContent = "Цитата";
      const qText = document.createElement("span");
      qText.textContent = msg.quote_text;
      qBox.append(qLabel, qText);
      card.appendChild(qBox);
    }

    // User attachments
    const nonGeneratedAtts = msg.attachments || [];
    if (nonGeneratedAtts.length > 0) {
      const attWrap = document.createElement("div");
      attWrap.className = "ai-msg-attachments";
      nonGeneratedAtts.forEach((att) => {
        const link = document.createElement("a");
        link.className = "ai-att-chip";
        link.href = `${att.url}?download=1`;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        if (att.is_image) {
          const img = document.createElement("img");
          img.className = "ai-att-thumb";
          img.src = att.url;
          img.alt = att.filename;
          link.appendChild(img);
        }
        const name = document.createElement("span");
        name.className = "ai-att-name";
        name.textContent = `${att.filename} (${formatBytes(att.byte_size)})`;
        link.appendChild(name);
        attWrap.appendChild(link);
      });
      card.appendChild(attWrap);
    }

    // Content
    if (msg.role === "user") {
      const body = document.createElement("div");
      body.className = "ai-msg-user-text";
      body.textContent = msg.content || "";
      card.appendChild(body);
    } else {
      const generatedImgs = msg.generated_images || [];
      if ((msg.status === "pending" || msg.status === "streaming") && !msg.content && generatedImgs.length === 0) {
        const dots = document.createElement("div");
        dots.className = "ai-typing-dots";
        dots.textContent = msg.status === "pending" ? "Ожидание ответа модели..." : "Генерация ответа...";
        card.appendChild(dots);
      }

      if (msg.content) {
        const body = document.createElement("div");
        body.className = "ai-msg-body";
        body.dataset.messageId = msg.id;
        renderMarkdownInto(body, msg.content);
        card.appendChild(body);
      }

      // Generated images
      if (generatedImgs.length > 0) {
        const gallery = document.createElement("div");
        gallery.className = "ai-gen-gallery";
        generatedImgs.forEach((imgAtt) => {
          const gCard = document.createElement("div");
          gCard.className = "ai-gen-card";
          const img = document.createElement("img");
          img.className = "ai-gen-img";
          img.src = imgAtt.url;
          img.alt = imgAtt.filename || "Изображение";
          img.addEventListener("click", () => openLightbox(imgAtt));

          const bar = document.createElement("div");
          bar.className = "ai-gen-bar";
          const dl = document.createElement("a");
          dl.className = "ai-icon-action-btn";
          dl.href = `${imgAtt.url}?download=1`;
          dl.download = imgAtt.filename || "image.png";
          dl.title = "Скачать изображение";
          dl.setAttribute("aria-label", "Скачать изображение");
          dl.appendChild(createIcon("download", 15));
          bar.appendChild(dl);

          if (navigator.clipboard && window.ClipboardItem) {
            const cpBtn = document.createElement("button");
            cpBtn.type = "button";
            cpBtn.className = "ai-icon-action-btn";
            cpBtn.title = "Копировать изображение";
            cpBtn.setAttribute("aria-label", "Копировать изображение");
            cpBtn.appendChild(createIcon("copy", 15));
            cpBtn.addEventListener("click", async () => {
              await copyImageFromUrl(imgAtt.url);
              flashButtonCopied(cpBtn, "copy", 15);
            });
            bar.appendChild(cpBtn);
          }

          gCard.append(img, bar);
          gallery.appendChild(gCard);
        });
        card.appendChild(gallery);
      }

      if (msg.error_message) {
        const errBox = document.createElement("div");
        errBox.className = "ai-error-box";
        errBox.textContent = msg.error_message;
        card.appendChild(errBox);
      }

      // Action bar on completed/error/stopped assistant messages
      if (msg.status !== "pending" && msg.status !== "streaming") {
        const actBar = document.createElement("div");
        actBar.className = "ai-msg-actions";

        if (msg.content) {
          const copyBtn = document.createElement("button");
          copyBtn.type = "button";
          copyBtn.className = "ai-icon-action-btn";
          copyBtn.title = "Копировать ответ";
          copyBtn.setAttribute("aria-label", "Копировать ответ");
          copyBtn.appendChild(createIcon("copy", 15));
          copyBtn.addEventListener("click", async () => {
            if (await copyText(msg.content)) flashButtonCopied(copyBtn, "copy", 15);
          });
          actBar.appendChild(copyBtn);
        }

        const retryLabel = msg.mode === "image" ? "Перегенерировать" : "Повторить";
        const retryBtn = document.createElement("button");
        retryBtn.type = "button";
        retryBtn.className = "ai-icon-action-btn";
        retryBtn.title = retryLabel;
        retryBtn.setAttribute("aria-label", retryLabel);
        retryBtn.appendChild(createIcon("refresh", 15));
        retryBtn.addEventListener("click", () => retryAssistantMessage(msg.id));
        actBar.appendChild(retryBtn);

        if (msg.mode !== "image") {
          const contBtn = document.createElement("button");
          contBtn.type = "button";
          contBtn.className = "ai-icon-action-btn";
          contBtn.title = "Продолжить";
          contBtn.setAttribute("aria-label", "Продолжить");
          contBtn.appendChild(createIcon("continue", 15));
          contBtn.addEventListener("click", () => {
            el.composerInput.focus();
            scrollToBottom(true);
          });
          actBar.appendChild(contBtn);
        }

        card.appendChild(actBar);
      }
    }

    wrapper.appendChild(card);
    return wrapper;
  }

  function updateMessageInPlace(msgId) {
    const msg = state.messages.find((m) => m.id === msgId);
    if (!msg) return;
    const oldEl = el.messageList.querySelector(`article[data-message-id="${msgId}"]`);
    const freshEl = buildMessageElement(msg);
    if (oldEl) {
      oldEl.replaceWith(freshEl);
    } else {
      el.messageList.appendChild(freshEl);
    }
    if (state.autoScrollPinned) {
      scrollToBottom(false);
    }
  }

  // ---------- Smart Auto-scroll ----------
  function isNearBottom() {
    const s = el.chatScroll;
    return s.scrollHeight - s.scrollTop - s.clientHeight < 90;
  }

  function scrollToBottom(smooth = true) {
    el.chatScroll.scrollTo({
      top: el.chatScroll.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
    state.autoScrollPinned = true;
    el.scrollBottomBtn.hidden = true;
  }

  // ---------- Quote & Selection Context Menu ----------
  function hideSelectionMenu() {
    el.selectionMenu.hidden = true;
  }

  function updateSelectionMenu() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) {
      hideSelectionMenu();
      return;
    }
    const text = sel.toString().trim();
    if (!text) {
      hideSelectionMenu();
      return;
    }
    const range = sel.getRangeAt(0);
    const commonEl = range.commonAncestorContainer.nodeType === 3
      ? range.commonAncestorContainer.parentElement
      : range.commonAncestorContainer;
    const msgBody = commonEl && commonEl.closest ? commonEl.closest(".ai-msg-body") : null;
    if (!msgBody || !el.messageList.contains(msgBody)) {
      hideSelectionMenu();
      return;
    }

    state.selectionDraftText = text;
    state.selectionDraftMessageId = msgBody.dataset.messageId || null;

    const rect = range.getBoundingClientRect();
    el.selectionMenu.hidden = false;
    const menuWidth = el.selectionMenu.offsetWidth || 180;
    const menuHeight = el.selectionMenu.offsetHeight || 38;
    const left = Math.max(10, Math.min(window.innerWidth - menuWidth - 10, rect.left + rect.width / 2 - menuWidth / 2));
    const top = rect.top > menuHeight + 14 ? rect.top - menuHeight - 8 : rect.bottom + 8;

    el.selectionMenu.style.left = `${left}px`;
    el.selectionMenu.style.top = `${Math.min(window.innerHeight - menuHeight - 10, Math.max(8, top))}px`;
  }

  function setQuote(messageId, text) {
    state.quote = { messageId, text: text.slice(0, 4000) };
    el.quotePreviewText.textContent = state.quote.text;
    el.quotePreview.hidden = false;
    renderContextChips();
    el.composerInput.focus();
  }

  function clearQuote() {
    state.quote = null;
    el.quotePreview.hidden = true;
    el.quotePreviewText.textContent = "";
    renderContextChips();
  }

  // ---------- Attachments ----------
  function renderPendingAttachments() {
    el.attachmentsStrip.textContent = "";
    el.attachmentsStrip.hidden = state.pendingAttachments.length === 0;
    renderContextChips();
    state.pendingAttachments.forEach((att) => {
      const chip = document.createElement("div");
      chip.className = "ai-draft-att";
      if (att.is_image) {
        const img = document.createElement("img");
        img.src = att.url;
        img.alt = att.filename;
        chip.appendChild(img);
      }
      const label = document.createElement("span");
      label.className = "ai-att-name";
      label.textContent = `${att.filename} (${formatBytes(att.byte_size)})`;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "ai-draft-att-remove";
      rm.title = "Убрать файл";
      rm.textContent = "✕";
      rm.addEventListener("click", () => {
        state.pendingAttachments = state.pendingAttachments.filter((a) => a.id !== att.id);
        renderPendingAttachments();
        validateComposerCapabilities();
      });
      chip.append(label, rm);
      el.attachmentsStrip.appendChild(chip);
    });
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (files.length === 0) return;

    if (state.pendingAttachments.length + files.length > MAX_FILES_PER_MESSAGE) {
      showToast(`Можно прикрепить не более ${MAX_FILES_PER_MESSAGE} файлов к одному сообщению`);
      return;
    }

    const currentBytes = state.pendingAttachments.reduce((acc, a) => acc + (a.byte_size || 0), 0);
    const addedBytes = files.reduce((acc, f) => acc + (f.size || 0), 0);
    if (currentBytes + addedBytes > MAX_TOTAL_BYTES) {
      showToast("Суммарный размер файлов не должен превышать 20 МБ");
      return;
    }

    const model = getModel(state.selectedModelId);
    for (const file of files) {
      const extMatch = file.name.toLowerCase().match(/(\.[a-z0-9]+)$/);
      const ext = extMatch ? extMatch[1] : "";
      if (!ALLOWED_EXTENSIONS.has(ext)) {
        showToast(`Неподдерживаемый формат файла: ${file.name}`);
        return;
      }
      if (IMAGE_EXTENSIONS.has(ext) && model && !model.supports_vision) {
        showToast(`Модель «${model.name}» не поддерживает изображения`);
        return;
      }
    }

    el.uploadStatus.hidden = false;
    try {
      for (const file of files) {
        const fd = new FormData();
        fd.append("file", file);
        if (PRODUCT_ID) fd.append("product_id", PRODUCT_ID);
        const uploaded = await apiFetch("/files", { method: "POST", body: fd });
        state.pendingAttachments.push(uploaded);
        renderPendingAttachments();
        validateComposerCapabilities();
      }
    } catch (err) {
      showToast(err.message || "Ошибка загрузки файла");
    } finally {
      el.uploadStatus.hidden = true;
      el.fileInput.value = "";
    }
  }

  // ---------- Streaming SSE Consumption ----------
  function setSendingUI(sending, requestId = null) {
    state.isSending = sending;
    state.activeRequestId = requestId;
    el.stopBtn.hidden = !(sending && requestId);
    el.sendBtn.hidden = sending && Boolean(requestId);
    validateComposerCapabilities();
  }

  async function consumeRequestStream(requestId, assistantMessageId) {
    if (state.activeStreamController) {
      state.activeStreamController.abort();
    }
    const controller = new AbortController();
    state.activeStreamController = controller;
    setSendingUI(true, requestId);

    try {
      const resp = await fetch(`${API_BASE}/requests/${requestId}/events`, {
        credentials: "same-origin",
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        throw new Error(`Ошибка подключения к потоку (${resp.status})`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sepIdx;
        while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
          const rawBlock = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + 2);
          handleSseBlock(rawBlock, assistantMessageId);
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        const msg = state.messages.find((m) => m.id === assistantMessageId);
        if (msg && (msg.status === "pending" || msg.status === "streaming")) {
          msg.status = "error";
          msg.error_message = err.message || "Соединение прервано";
          updateMessageInPlace(assistantMessageId);
        }
      }
    } finally {
      if (state.activeStreamController === controller) {
        state.activeStreamController = null;
        setSendingUI(false, null);
      }
      loadConversations().catch(() => {});
    }
  }

  function handleSseBlock(block, assistantMessageId) {
    let eventName = "message";
    const dataLines = [];
    block.split("\n").forEach((line) => {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    });
    if (dataLines.length === 0) return;
    let payload = {};
    try {
      payload = JSON.parse(dataLines.join("\n"));
    } catch {
      return;
    }

    const msg = state.messages.find((m) => m.id === assistantMessageId);
    if (!msg) return;

    if (eventName === "status") {
      msg.status = payload.status || msg.status;
      updateMessageInPlace(assistantMessageId);
    } else if (eventName === "meta") {
      msg.context_truncated = Boolean(payload.context_truncated);
      msg.context_omitted_count = Number(payload.context_omitted_count || 0);
      updateContextBanner();
    } else if (eventName === "delta") {
      msg.status = "streaming";
      msg.content = (msg.content || "") + (payload.text || "");
      updateMessageInPlace(assistantMessageId);
    } else if (eventName === "image") {
      if (Array.isArray(payload.images)) {
        msg.generated_images = payload.images;
        updateMessageInPlace(assistantMessageId);
      }
    } else if (eventName === "done") {
      const doneMsg = payload.message;
      if (doneMsg && typeof doneMsg === "object") {
        Object.assign(msg, doneMsg);
      } else {
        msg.status = payload.status || "completed";
        if (typeof payload.content === "string" && payload.content) {
          msg.content = payload.content;
        }
      }
      updateMessageInPlace(assistantMessageId);
      setSendingUI(false, null);
      renderContextChips();
    } else if (eventName === "error") {
      msg.status = payload.status || "error";
      msg.error_message = payload.error || payload.message || "Не удалось получить ответ";
      updateMessageInPlace(assistantMessageId);
      setSendingUI(false, null);
    }
  }

  // ---------- Send / Retry / Stop ----------
  async function ensureConversation() {
    if (state.activeConversationId) return state.activeConversationId;
    const conv = await apiFetch("/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model_id: state.selectedModelId || null,
        product_id: PRODUCT_ID,
        specialist_id: state.specialistId,
      }),
    });
    state.activeConversationId = conv.id;
    state.specialistId = conv.specialist_id || null;
    localStorage.removeItem(pendingSpecialistKey());
    localStorage.setItem(activeConvStorageKey(), conv.id);
    renderSpecialistPicker();
    await loadConversations();
    return conv.id;
  }

  async function sendMessage(overrideText = null) {
    if (state.isSending) return;
    const isExplicitVoiceText = typeof overrideText === "string";
    const content = isExplicitVoiceText ? overrideText.trim() : el.composerInput.value.trim();
    if (!content && state.pendingAttachments.length === 0) {
      showToast("Введите текст сообщения или прикрепите файл");
      return;
    }
    if (!validateComposerCapabilities()) return;

    const currentDraftConv = state.activeConversationId;
    setSendingUI(true, null);

    try {
      const convId = await ensureConversation();
      const resp = await apiFetch(`/conversations/${convId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          mode: state.mode,
          model_id: state.selectedModelId,
          attachment_ids: state.pendingAttachments.map((a) => a.id),
          quote_message_id: state.quote ? state.quote.messageId : null,
          quote_text: state.quote ? state.quote.text : null,
          client_key: genClientKey(),
        }),
      });

      if (!isExplicitVoiceText) {
        // Clear typed draft only when the typed message itself was sent.
        localStorage.removeItem(draftKey(currentDraftConv));
        localStorage.removeItem(draftKey(convId));
        el.composerInput.value = "";
        autoResizeTextarea();
      }
      state.pendingAttachments = [];
      renderPendingAttachments();
      clearQuote();

      state.messages.push(resp.user_message, resp.assistant_message);
      el.chatTitle.textContent = resp.conversation.title;
      state.autoScrollPinned = true;
      renderMessages();
      scrollToBottom(true);

      await loadConversations();
      await consumeRequestStream(resp.request.id, resp.assistant_message.id);
    } catch (err) {
      showToast(err.message || "Ошибка отправки сообщения");
      setSendingUI(false, null);
    }
  }

  async function retryAssistantMessage(messageId) {
    if (!state.activeConversationId || state.isSending) return;
    if (!validateComposerCapabilities()) return;
    try {
      const resp = await apiFetch(`/conversations/${state.activeConversationId}/messages/${messageId}/retry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: state.selectedModelId }),
      });
      state.messages.push(resp.assistant_message);
      state.autoScrollPinned = true;
      renderMessages();
      scrollToBottom(true);
      await consumeRequestStream(resp.request.id, resp.assistant_message.id);
    } catch (err) {
      showToast(err.message || "Не удалось повторить запрос");
    }
  }

  async function stopGeneration() {
    if (!state.activeRequestId) return;
    const reqId = state.activeRequestId;
    try {
      await apiFetch(`/requests/${reqId}/stop`, { method: "POST" });
      showToast("Остановка генерации...");
    } catch (err) {
      showToast(err.message || "Не удалось остановить генерацию");
    }
  }

  // ---------- Conversation Actions ----------
  async function selectConversation(convId) {
    if (state.activeStreamController) {
      state.activeStreamController.abort();
      state.activeStreamController = null;
    }
    setSendingUI(false, null);
    state.activeConversationId = convId;
    localStorage.removeItem(pendingSpecialistKey());
    localStorage.setItem(activeConvStorageKey(), convId);
    renderConversations();

    const detail = await apiFetch(`/conversations/${convId}`);
    el.chatTitle.textContent = detail.conversation.title;
    state.messages = detail.messages || [];
    state.specialistId = detail.conversation.specialist_id || null;
    if (detail.conversation.selected_model && getModel(detail.conversation.selected_model)) {
      state.selectedModelId = detail.conversation.selected_model;
    }
    renderCatalog();
    state.autoScrollPinned = true;
    renderMessages();
    scrollToBottom(false);

    // Restore saved draft for this conversation
    const savedDraft = localStorage.getItem(draftKey(convId)) || "";
    el.composerInput.value = savedDraft;
    autoResizeTextarea();

    // If an assistant message is still pending/streaming after page reload, re-attach
    const activeAssistant = [...state.messages].reverse().find(
      (m) => m.role === "assistant" && (m.status === "pending" || m.status === "streaming") && m.request_id
    );
    if (activeAssistant) {
      await consumeRequestStream(activeAssistant.request_id, activeAssistant.id);
    }
  }

  async function startNewChat() {
    if (state.activeStreamController) {
      state.activeStreamController.abort();
      state.activeStreamController = null;
    }
    setSendingUI(false, null);
    state.activeConversationId = null;
    state.specialistId = null;
    localStorage.removeItem(activeConvStorageKey());
    localStorage.removeItem(pendingSpecialistKey());
    state.messages = [];
    el.chatTitle.textContent = "Новый диалог";
    clearQuote();
    state.pendingAttachments = [];
    renderPendingAttachments();
    renderConversations();
    renderMessages();
    renderSpecialistPicker();
    el.composerInput.value = localStorage.getItem(draftKey(null)) || "";
    autoResizeTextarea();
    el.composerInput.focus();
    closeMobileDrawer();
  }

  function openRenameModal(convId, currentTitle) {
    state.renameTargetId = convId;
    el.renameInput.value = currentTitle;
    el.renameModal.hidden = false;
    el.renameInput.focus();
    el.renameInput.select();
  }

  async function submitRename() {
    const title = el.renameInput.value.trim();
    if (!title || !state.renameTargetId) return;
    try {
      const updated = await apiFetch(`/conversations/${state.renameTargetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (state.activeConversationId === updated.id) {
        el.chatTitle.textContent = updated.title;
      }
      el.renameModal.hidden = true;
      await loadConversations();
    } catch (err) {
      showToast(err.message || "Не удалось переименовать диалог");
    }
  }

  function openDeleteModal(convId, title) {
    state.deleteTargetId = convId;
    el.deleteModalDesc.textContent = `Удалить диалог «${title}» и все его сообщения и файлы?`;
    el.deleteModal.hidden = false;
  }

  async function submitDelete() {
    if (!state.deleteTargetId) return;
    const id = state.deleteTargetId;
    try {
      await apiFetch(`/conversations/${id}`, { method: "DELETE" });
      el.deleteModal.hidden = true;
      localStorage.removeItem(draftKey(id));
      if (state.activeConversationId === id) {
        await startNewChat();
      }
      await loadConversations();
      showToast("Диалог удалён");
    } catch (err) {
      showToast(err.message || "Не удалось удалить диалог");
    }
  }

  // ---------- Lightbox & Clipboard Image ----------
  function openLightbox(att) {
    el.lightboxImg.src = att.url;
    el.lightboxCaption.textContent = att.filename;
    el.lightboxDownload.href = `${att.url}?download=1`;
    el.lightboxDownload.download = att.filename;
    el.lightboxCopy.hidden = !(navigator.clipboard && window.ClipboardItem);
    el.lightboxCopy.onclick = () => copyImageFromUrl(att.url);
    el.lightboxModal.hidden = false;
  }

  async function copyImageFromUrl(url) {
    try {
      const resp = await fetch(url, { credentials: "same-origin" });
      const blob = await resp.blob();
      await navigator.clipboard.write([new ClipboardItem({ [blob.type || "image/png"]: blob })]);
      showToast("Изображение скопировано в буфер обмена");
    } catch {
      showToast("Браузер не позволяет скопировать это изображение — используйте «Скачать»");
    }
  }

  // ---------- UI Events & Init ----------
  function autoResizeTextarea() {
    el.composerInput.style.height = "auto";
    el.composerInput.style.height = `${Math.min(180, el.composerInput.scrollHeight)}px`;
  }

  function openMobileDrawer() {
    el.app.classList.add("is-drawer-open");
    el.backdrop.hidden = false;
  }

  function closeMobileDrawer() {
    el.app.classList.remove("is-drawer-open");
    el.backdrop.hidden = true;
  }

  function bindEvents() {
    el.sidebarCollapseBtn.addEventListener("click", () => {
      if (window.innerWidth <= 860) {
        closeMobileDrawer();
      } else {
        el.app.classList.toggle("is-sidebar-collapsed");
      }
    });

    el.sidebarOpenBtn.addEventListener("click", () => {
      if (window.innerWidth <= 860) {
        openMobileDrawer();
      } else {
        el.app.classList.remove("is-sidebar-collapsed");
      }
    });

    el.backdrop.addEventListener("click", closeMobileDrawer);
    el.newChatBtn.addEventListener("click", startNewChat);

    let searchTimer = null;
    el.historySearch.addEventListener("input", () => {
      state.searchQuery = el.historySearch.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadConversations().catch(() => {}), 220);
    });

    document.querySelectorAll(".ai-theme-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (window.ServGoTheme) {
          window.ServGoTheme.setPreference(btn.dataset.themePref);
        }
        syncThemeButtons();
      });
    });

    el.modelPickerBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const willOpen = el.modelPopover.hidden;
      el.modelPopover.hidden = !willOpen;
      el.modelPickerBtn.setAttribute("aria-expanded", String(willOpen));
    });

    document.querySelectorAll(".ai-model-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        state.modelTab = tab.dataset.modelTab;
        renderCatalog();
      });
    });

    document.addEventListener("click", (ev) => {
      if (!el.modelPopover.hidden && !el.modelPopover.contains(ev.target) && !el.modelPickerBtn.contains(ev.target)) {
        el.modelPopover.hidden = true;
        el.modelPickerBtn.setAttribute("aria-expanded", "false");
      }
      if (!el.selectionMenu.hidden && !el.selectionMenu.contains(ev.target)) {
        setTimeout(() => {
          const sel = window.getSelection();
          if (!sel || sel.isCollapsed) hideSelectionMenu();
        }, 40);
      }
    });

    document.querySelectorAll(".ai-mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => setMode(btn.dataset.mode));
    });

    el.composerInput.addEventListener("input", () => {
      autoResizeTextarea();
      localStorage.setItem(draftKey(state.activeConversationId), el.composerInput.value);
    });

    el.composerInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        sendMessage();
      }
    });

    el.sendBtn.addEventListener("click", sendMessage);
    el.stopBtn.addEventListener("click", stopGeneration);

    el.attachBtn.addEventListener("click", () => el.fileInput.click());
    el.fileInput.addEventListener("change", () => uploadFiles(el.fileInput.files));

    el.composerDropzone.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      el.dropOverlay.hidden = false;
    });
    el.composerDropzone.addEventListener("dragleave", (ev) => {
      if (!el.composerDropzone.contains(ev.relatedTarget)) {
        el.dropOverlay.hidden = true;
      }
    });
    el.composerDropzone.addEventListener("drop", (ev) => {
      ev.preventDefault();
      el.dropOverlay.hidden = true;
      if (ev.dataTransfer && ev.dataTransfer.files) {
        uploadFiles(ev.dataTransfer.files);
      }
    });

    el.quoteClearBtn.addEventListener("click", clearQuote);

    el.chatScroll.addEventListener("scroll", () => {
      const near = isNearBottom();
      state.autoScrollPinned = near;
      el.scrollBottomBtn.hidden = near;
      hideSelectionMenu();
    });

    el.scrollBottomBtn.addEventListener("click", () => scrollToBottom(true));

    document.addEventListener("mouseup", () => setTimeout(updateSelectionMenu, 10));
    document.addEventListener("touchend", () => setTimeout(updateSelectionMenu, 60));
    document.addEventListener("selectionchange", () => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) {
        hideSelectionMenu();
      }
    });

    el.selCopyBtn.addEventListener("click", () => {
      if (state.selectionDraftText) copyText(state.selectionDraftText);
      hideSelectionMenu();
    });

    el.selAskBtn.addEventListener("click", () => {
      if (state.selectionDraftText) {
        setQuote(state.selectionDraftMessageId, state.selectionDraftText);
      }
      hideSelectionMenu();
      const sel = window.getSelection();
      if (sel) sel.removeAllRanges();
    });

    el.renameCancelBtn.addEventListener("click", () => { el.renameModal.hidden = true; });
    el.renameSaveBtn.addEventListener("click", submitRename);
    el.renameInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") submitRename();
      if (ev.key === "Escape") el.renameModal.hidden = true;
    });

    el.deleteCancelBtn.addEventListener("click", () => { el.deleteModal.hidden = true; });
    el.deleteConfirmBtn.addEventListener("click", submitDelete);

    el.lightboxClose.addEventListener("click", () => { el.lightboxModal.hidden = true; });
    el.lightboxModal.addEventListener("click", (ev) => {
      if (ev.target === el.lightboxModal) el.lightboxModal.hidden = true;
    });
  }

  // ---------- Voice Input & Corrector (parity with Home) ----------
  function setupVoiceInput() {
    const form = el.composerBox;
    const input = el.composerInput;
    if (!form || !input) return;

    const voiceSupportedNow = voiceSupported();
    if (el.voiceOverlay) {
      document.body.appendChild(el.voiceOverlay);
    }

    if (!voiceSupportedNow) {
      form.classList.add("voice-unavailable");
      input.placeholder = voiceDefaultPlaceholder();
      return;
    }

    input.placeholder = voiceDefaultPlaceholder();

    let activeHold = null;
    let holdTimer = null;
    let recorder = null;
    let recordStream = null;
    let recordChunks = [];
    let recordStarted = 0;
    let recordTimer = null;
    let previewTimer = null;
    let previewAbort = null;
    let previewOff = false;
    let lastLive = "";
    let recordingId = 0;
    let voiceWorking = false;
    let micOpening = false;
    let discardRecording = false;
    let lastInsert = null;

    const emptyReasons = {
      too_short: "Запись слишком короткая. Попробуйте ещё раз.",
      silence: "Речи не слышно. Попробуйте ещё раз.",
      too_long: `Запись длиннее ${Math.max(1, Math.round(VOICE_MAX_SECONDS / 60))} минут. Надиктуйте по частям.`,
      stt_error: "Не получилось распознать. Попробуйте ещё раз.",
    };

    function mimeCandidate() {
      const list = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
      for (let i = 0; i < list.length; i += 1) {
        if (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(list[i])) {
          return list[i];
        }
      }
      return "";
    }

    function extensionFor(type) {
      if (type.indexOf("mp4") >= 0) return "m4a";
      if (type.indexOf("ogg") >= 0) return "ogg";
      return "webm";
    }

    function clock(seconds) {
      const m = Math.floor(seconds / 60);
      const s = seconds % 60;
      return `${m}:${s < 10 ? "0" : ""}${s}`;
    }

    function recentLines() {
      const lines = [];
      const doneMsgs = state.messages.filter((m) => m.content && (m.role === "user" || m.role === "assistant"));
      for (let i = Math.max(0, doneMsgs.length - 3); i < doneMsgs.length; i += 1) {
        const text = String(doneMsgs[i].content || "").trim();
        if (text) lines.push(text.slice(0, 600));
      }
      return lines;
    }

    function showNote(text, bad = false, undoBtn = null) {
      if (!el.voiceNote) return;
      el.voiceNote.textContent = "";
      el.voiceNote.classList.toggle("bad", Boolean(bad));
      if (!text) {
        el.voiceNote.hidden = true;
        return;
      }
      const span = document.createElement("span");
      span.textContent = text;
      el.voiceNote.appendChild(span);
      if (undoBtn) el.voiceNote.appendChild(undoBtn);
      el.voiceNote.hidden = false;
    }

    function showLive(label, text, working) {
      if (!el.liveBox) return;
      el.liveBox.hidden = false;
      el.liveBox.classList.toggle("working", Boolean(working));
      if (el.liveLabel) el.liveLabel.textContent = label;
      if (el.liveText) el.liveText.textContent = text || "";
      el.liveBox.scrollTop = el.liveBox.scrollHeight;
    }

    function hideLive() {
      if (!el.liveBox) return;
      el.liveBox.hidden = true;
      if (el.liveText) el.liveText.textContent = "";
      lastLive = "";
    }

    function setVoiceState(mode) {
      const recording = mode === "recording";
      form.classList.toggle("voice-connecting", mode === "connecting");
      form.classList.toggle("voice-recording", recording);
      form.classList.toggle("voice-processing", mode === "processing");
      form.classList.toggle("voice-sending", mode === "sending");

      if (window.VoiceGlow) {
        if (mode === "connecting") window.VoiceGlow.connecting();
        else if (mode === "processing") window.VoiceGlow.recognize();
        else if (mode === "sending" || mode === "idle") window.VoiceGlow.hide();
      }
      if (el.voiceStatus) {
        el.voiceStatus.hidden = mode !== "processing" && mode !== "sending";
        el.voiceStatus.textContent = mode === "processing"
          ? "Распознаю и редактирую запись…"
          : mode === "sending"
            ? "Отправляю сообщение…"
            : "";
      }
      input.readOnly = mode !== "idle";
      form.setAttribute("aria-busy", mode === "processing" || mode === "sending" ? "true" : "false");
      if (mode === "idle") {
        const isEditing = form.classList.contains("voice-text-editing");
        input.placeholder = isEditing
          ? (state.mode === "image" ? "Опишите изображение для генерации... (Enter — отправить)" : VOICE_TYPING_PLACEHOLDER)
          : voiceDefaultPlaceholder();
      }
      validateComposerCapabilities();
    }

    function releaseStream() {
      if (recordTimer) {
        clearInterval(recordTimer);
        recordTimer = null;
      }
      if (recordStream) {
        recordStream.getTracks().forEach((t) => t.stop());
        recordStream = null;
      }
    }

    function stopPreview() {
      if (previewTimer) {
        clearTimeout(previewTimer);
        previewTimer = null;
      }
      if (previewAbort) {
        previewAbort.abort();
        previewAbort = null;
      }
    }

    function schedulePreview(delay, id) {
      if (previewOff || !VOICE_PREVIEW_URL) return;
      previewTimer = setTimeout(() => runPreview(id), delay);
    }

    function runPreview(id) {
      previewTimer = null;
      if (id !== recordingId || !recorder || recorder.state === "inactive") return;
      if (!recordChunks.length) {
        schedulePreview(1000, id);
        return;
      }
      const type = recorder.mimeType || "audio/webm";
      const fd = new FormData();
      fd.append("audio", new Blob(recordChunks, { type }), `preview.${extensionFor(type)}`);
      fd.append("duration_ms", String(Date.now() - recordStarted));
      if (VOICE_PROJECT) fd.append("project", VOICE_PROJECT);
      const controller = new AbortController();
      previewAbort = controller;
      fetch(VOICE_PREVIEW_URL, {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        body: fd,
        signal: controller.signal,
      })
        .then((resp) => {
          if (resp.status === 503) previewOff = true;
          return resp.ok ? resp.json() : null;
        })
        .then((data) => {
          if (id !== recordingId || !recorder) return;
          if (data && data.text) {
            lastLive = data.text;
            showLive("Слушаю…", data.text, false);
          }
        })
        .catch(() => {})
        .finally(() => {
          if (previewAbort === controller) previewAbort = null;
          if (id === recordingId && recorder && recorder.state !== "inactive") {
            schedulePreview(1500, id);
          }
        });
    }

    function insertText(text) {
      const before = input.value;
      const glue = before && !/\s$/.test(before) ? " " : "";
      const inserted = glue + text;
      input.value = before + inserted;
      autoResizeTextarea();
      localStorage.setItem(draftKey(state.activeConversationId), input.value);
      return { before, glue, inserted };
    }

    function insertWithUndo(data) {
      const done = insertText(data.text);
      lastInsert = { before: done.before, inserted: done.inserted, raw: done.glue + (data.raw_text || data.text) };
      const changes = (data.replacements || []).map((item) => `${item.from} → ${item.to}`);
      if (data.cleaned && changes.length && data.raw_text !== data.text) {
        const undo = document.createElement("button");
        undo.type = "button";
        undo.className = "dp-voice-undo";
        undo.textContent = "Вернуть как сказано";
        undo.addEventListener("click", () => {
          if (lastInsert && input.value === lastInsert.before + lastInsert.inserted) {
            input.value = lastInsert.before + lastInsert.raw;
            autoResizeTextarea();
            localStorage.setItem(draftKey(state.activeConversationId), input.value);
          }
          showNote("");
          input.focus();
        });
        showNote(`Поправил: ${changes.join(", ")}`, false, undo);
      } else if (data.fallback_reason) {
        showNote("Вставил как распознано: правка не получилась.", false);
      } else {
        showNote("");
      }
    }

    function startRecording() {
      if (voiceWorking || recorder || micOpening || state.isSending || !activeHold) return;
      const intent = activeHold;
      discardRecording = false;
      showNote("");
      input.blur();
      micOpening = true;
      setVoiceState("connecting");
      showLive("Подключаю микрофон…", "", true);

      navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
        micOpening = false;
        if (!intent || intent !== activeHold || intent.cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          hideLive();
          setVoiceState("idle");
          return;
        }
        recordStream = stream;
        const type = mimeCandidate();
        try {
          recorder = type ? new window.MediaRecorder(stream, { mimeType: type }) : new window.MediaRecorder(stream);
        } catch {
          releaseStream();
          hideLive();
          setVoiceState("idle");
          showNote("Этот браузер не умеет записывать звук.", true);
          return;
        }
        recordChunks = [];
        recorder.addEventListener("dataavailable", (ev) => {
          if (ev.data && ev.data.size) recordChunks.push(ev.data);
        });
        recorder.addEventListener("stop", onRecorded);
        try {
          recorder.start(1000);
        } catch {
          recorder = null;
          releaseStream();
          hideLive();
          setVoiceState("idle");
          showNote("Не получилось включить запись.", true);
          return;
        }
        recordStarted = Date.now();
        if (window.VoiceGlow) window.VoiceGlow.start(stream);
        recordingId += 1;
        lastLive = "";
        showLive("Говорите · отпустите, чтобы отправить", "", false);
        schedulePreview(2500, recordingId);
        setVoiceState("recording");

        const tick = () => {
          const seconds = Math.floor((Date.now() - recordStarted) / 1000);
          input.placeholder = `Идёт запись ${clock(seconds)}`;
          if (seconds >= VOICE_MAX_SECONDS) stopRecording();
        };
        tick();
        recordTimer = setInterval(tick, 250);
      }).catch((err) => {
        micOpening = false;
        releaseStream();
        hideLive();
        setVoiceState("idle");
        const denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
        showNote(
          denied
            ? "Нет доступа к микрофону. Разрешите его для этого сайта в настройках браузера."
            : "Микрофон не найден или занят другим приложением.",
          true
        );
      });
    }

    function stopRecording() {
      if (recorder && recorder.state !== "inactive") {
        if (window.VoiceGlow) {
          if (discardRecording) window.VoiceGlow.hide();
          else window.VoiceGlow.recognize();
        }
        recorder.stop();
      }
    }

    function onRecorded() {
      const type = (recorder && recorder.mimeType) || "audio/webm";
      const millis = Date.now() - recordStarted;
      stopPreview();
      recordingId += 1;
      recorder = null;
      releaseStream();

      if (discardRecording) {
        discardRecording = false;
        recordChunks = [];
        hideLive();
        setVoiceState("idle");
        return;
      }

      showLive("Распознаю и редактирую запись…", lastLive, true);
      const blob = new Blob(recordChunks, { type });
      recordChunks = [];
      voiceWorking = true;
      setVoiceState("processing");

      const fd = new FormData();
      fd.append("audio", blob, `dictation.${extensionFor(type)}`);
      fd.append("duration_ms", String(millis));
      fd.append("context", JSON.stringify(recentLines()));
      if (VOICE_PROJECT) fd.append("project", VOICE_PROJECT);

      let dispatchedVoice = false;
      fetch(VOICE_URL, {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        body: fd,
      })
        .then((resp) => resp.json().catch(() => ({})).then((data) => {
          if (!resp.ok) {
            throw new Error((data && data.detail) || "Не получилось распознать. Попробуйте ещё раз.");
          }
          return data;
        }))
        .then(async (data) => {
          hideLive();
          if (data.empty || !data.text) {
            showNote(emptyReasons[data.empty_reason] || emptyReasons.silence, true);
            return;
          }
          const transcript = String(data.text).trim();
          const changes = (data.replacements || []).map((item) => `${item.from} → ${item.to}`);
          if (validateComposerCapabilities()) {
            if (data.fallback_reason) {
              showNote("Редактор не завершил правку: передаю распознанный текст.", false);
            } else if (data.cleaned && changes.length) {
              showNote(`Поправил: ${changes.join(", ")}`, false);
            } else {
              showNote("");
            }
            dispatchedVoice = true;
            setVoiceState("sending");
            await sendMessage(transcript);
          } else {
            insertWithUndo(data);
          }
        })
        .catch((err) => {
          const kept = lastLive;
          hideLive();
          if (kept) insertText(kept);
          showNote((err && err.message ? err.message : "Ошибка распознавания") + (kept ? " Вставил распознанное без правки." : ""), true);
        })
        .finally(() => {
          voiceWorking = false;
          dispatchedVoice = false;
          setVoiceState("idle");
        });
    }

    function enterTypingMode() {
      if (state.isSending || voiceWorking || recorder || micOpening) return;
      form.classList.add("voice-text-editing");
      if (el.modeMicBtn) el.modeMicBtn.hidden = false;
      input.placeholder = state.mode === "image"
        ? "Опишите изображение для генерации... (Enter — отправить)"
        : VOICE_TYPING_PLACEHOLDER;
    }

    function leaveTypingMode() {
      input.blur();
      form.classList.remove("voice-text-editing");
      if (el.modeMicBtn) el.modeMicBtn.hidden = true;
      input.placeholder = voiceDefaultPlaceholder();
    }

    input.addEventListener("focus", enterTypingMode);
    input.addEventListener("blur", () => {
      form.classList.remove("voice-text-editing");
      if (el.modeMicBtn) el.modeMicBtn.hidden = true;
      input.placeholder = voiceDefaultPlaceholder();
    });

    if (document.activeElement === input) enterTypingMode();

    function beginHold(ev, allowWhileEditing = false) {
      if (ev.button !== 0 || activeHold || recorder || micOpening || voiceWorking || state.isSending) return;
      if (!allowWhileEditing && form.classList.contains("voice-text-editing")) return;
      if (ev.pointerType === "touch") ev.preventDefault();
      activeHold = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, cancelled: false, started: false };
      if (window.VoiceGlow) {
        window.VoiceGlow.setKnobX(ev.clientX);
        window.VoiceGlow.arm();
      }
      if (ev.pointerType === "touch" && ev.target && ev.target.setPointerCapture) {
        try { ev.target.setPointerCapture(ev.pointerId); } catch {}
      }
      form.classList.add("voice-arming");
      holdTimer = setTimeout(() => {
        holdTimer = null;
        if (!activeHold || activeHold.cancelled) return;
        activeHold.started = true;
        startRecording();
      }, 220);
    }

    if (el.modeMicBtn) {
      el.modeMicBtn.addEventListener("pointerdown", (ev) => {
        if (ev.button !== 0) return;
        ev.preventDefault();
        ev.stopPropagation();
        beginHold(ev, true);
      });
      el.modeMicBtn.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        leaveTypingMode();
      });
    }

    if (el.voiceHitbox) {
      el.voiceHitbox.addEventListener("touchstart", (ev) => {
        if (ev.cancelable) ev.preventDefault();
      }, { passive: false });
    }

    form.addEventListener("pointerdown", (ev) => {
      if (ev.target.closest("button")) return;
      beginHold(ev, false);
    });

    function endHold(ev, cancelled) {
      if (!activeHold || (ev && ev.pointerId !== activeHold.id)) return;
      const intent = activeHold;
      intent.cancelled = true;
      activeHold = null;
      if (holdTimer) {
        clearTimeout(holdTimer);
        holdTimer = null;
      }
      form.classList.remove("voice-arming");
      if (recorder) {
        discardRecording = cancelled;
        stopRecording();
      } else if (micOpening) {
        hideLive();
        setVoiceState("idle");
        if (!cancelled) showNote("После разрешения микрофона удерживайте строку ещё раз.", false);
      } else if (!intent.started && !cancelled) {
        if (window.VoiceGlow) window.VoiceGlow.hide();
        if (intent && el.modeMicBtn && form.classList.contains("voice-text-editing")) {
          leaveTypingMode();
        } else {
          input.focus();
        }
      } else {
        hideLive();
        setVoiceState("idle");
      }
    }

    document.addEventListener("pointermove", (ev) => {
      if (activeHold && ev.pointerId === activeHold.id && window.VoiceGlow) {
        window.VoiceGlow.setKnobX(ev.clientX);
      }
    });
    document.addEventListener("pointerup", (ev) => endHold(ev, false));
    document.addEventListener("pointercancel", (ev) => endHold(ev, true));
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) endHold(null, true);
    });
    form.addEventListener("contextmenu", (ev) => {
      if (ev.target === el.voiceHitbox || activeHold || recorder) ev.preventDefault();
    });
    form.addEventListener("selectstart", (ev) => {
      if (activeHold || ev.target === el.voiceHitbox) ev.preventDefault();
    });
  }

  async function init() {
    syncThemeButtons();
    setupCompanyContextUI();
    renderContextChips();
    try {
      const specialistData = await apiFetch("/specialists");
      state.specialists = Array.isArray(specialistData.specialists) ? specialistData.specialists : [];
    } catch (_err) {
      state.specialists = [];
    }
    renderSpecialistPicker();
    bindEvents();
    setupVoiceInput();

    try {
      state.catalog = await apiFetch("/models");
      state.selectedModelId = state.catalog.default_text_model || (state.catalog.models.find((m) => m.supports_text && m.available && m.access_enabled === true) || {}).id || "";
      renderCatalog();
    } catch (err) {
      el.catalogWarning.textContent = err.message || "Не удалось загрузить каталог моделей";
      el.catalogWarning.hidden = false;
    }

    try {
      await loadConversations();
      const lastId = localStorage.getItem(activeConvStorageKey());
      if (lastId && state.conversations.some((c) => c.id === lastId)) {
        await selectConversation(lastId);
      } else {
        const remembered = localStorage.getItem(pendingSpecialistKey());
        state.specialistId = getSpecialist(remembered) ? remembered : null;
        if (!state.specialistId) localStorage.removeItem(pendingSpecialistKey());
        renderSpecialistPicker();
        el.composerInput.value = localStorage.getItem(draftKey(null)) || "";
        autoResizeTextarea();
        validateComposerCapabilities();
      }
    } catch (err) {
      showToast(err.message || "Не удалось загрузить историю диалогов");
    }
  }

  init();
})();
