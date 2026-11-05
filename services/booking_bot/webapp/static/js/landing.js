const ATTRIBUTION_STORAGE_KEY = "servgo_growth_attribution_v1";

function readGrowthAttribution() {
    const params = new URLSearchParams(window.location.search);
    const current = {
        experiment_id: params.get("experiment_id") || params.get("exp") || "",
        utm_source: params.get("utm_source") || "",
        utm_medium: params.get("utm_medium") || "",
        utm_campaign: params.get("utm_campaign") || "",
        utm_content: params.get("utm_content") || "",
        utm_term: params.get("utm_term") || "",
        seo_module: ["platform", "booking", "growth", "mail", "shifts", "seo"].includes(params.get("seo_module"))
            ? params.get("seo_module") : "",
        referrer: document.referrer || "",
        landing_path: window.location.pathname || "/",
    };

    const hasCampaignData = Boolean(
        current.experiment_id ||
        current.utm_source ||
        current.utm_medium ||
        current.utm_campaign ||
        current.utm_content ||
        current.utm_term ||
        current.seo_module
    );

    if (hasCampaignData) {
        try {
            sessionStorage.setItem(ATTRIBUTION_STORAGE_KEY, JSON.stringify(current));
        } catch (_) {}
        return current;
    }

    try {
        const stored = JSON.parse(sessionStorage.getItem(ATTRIBUTION_STORAGE_KEY) || "null");
        if (stored && typeof stored === "object") {
            return {
                ...stored,
                landing_path: current.landing_path,
                referrer: stored.referrer || current.referrer,
            };
        }
    } catch (_) {}

    return current;
}

document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener("click", (event) => {
        const target = document.querySelector(link.getAttribute("href"));
        if (!target) {
            return;
        }
        event.preventDefault();
        target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
});

const topbar = document.querySelector(".topbar");
window.addEventListener("scroll", () => {
    topbar.classList.toggle("is-scrolled", window.scrollY > 12);
});

function applyDesktopLinks() {
    const isTelegramWebApp = Boolean(window.Telegram?.WebApp?.initData);
    const isDesktop = window.matchMedia("(min-width: 721px)").matches;

    document.querySelectorAll("[data-desktop-href]").forEach((link) => {
        if (!link.dataset.telegramHref) {
            link.dataset.telegramHref = link.getAttribute("href") || "";
        }

        link.setAttribute(
            "href",
            isDesktop && !isTelegramWebApp ? link.dataset.desktopHref : link.dataset.telegramHref
        );
    });
}

applyDesktopLinks();
window.addEventListener("resize", applyDesktopLinks);

const connectModal = document.querySelector("[data-connect-modal]");
const connectForm = document.querySelector("[data-connect-form]");
const connectStatus = document.querySelector("[data-connect-form-status]");
const openConnectButtons = document.querySelectorAll("[data-open-connect-form]");
const closeConnectButtons = document.querySelectorAll("[data-close-connect-form]");

function setConnectStatus(message, type = "") {
    if (!connectStatus) {
        return;
    }

    connectStatus.textContent = message;
    connectStatus.classList.toggle("is-error", type === "error");
    connectStatus.classList.toggle("is-success", type === "success");
}

function openConnectModal(event) {
    if (!connectModal || !connectForm) {
        return;
    }

    // Кнопка «Занять бесплатное место» помечает заявку и меняет заголовок формы.
    const freeSeat = event?.currentTarget?.dataset.connectIntent === "free_seat";
    if (connectForm.elements.intent) {
        connectForm.elements.intent.value = freeSeat ? "free_seat" : "";
    }
    const fromBlog = readGrowthAttribution();
    if (connectForm.elements.seo_module && fromBlog.utm_source === "servgo_blog") {
        connectForm.elements.seo_module.value = fromBlog.seo_module || "platform";
    }
    const title = connectModal.querySelector("[data-connect-title]");
    if (title) {
        title.textContent = freeSeat ? title.dataset.freeSeatText : title.dataset.defaultText;
    }

    connectModal.hidden = false;
    document.body.classList.add("modal-open");
    setConnectStatus("");
    connectForm.elements.name?.focus();
}

function closeConnectModal() {
    if (!connectModal) {
        return;
    }

    connectModal.hidden = true;
    document.body.classList.remove("modal-open");
}

openConnectButtons.forEach((button) => {
    button.addEventListener("click", openConnectModal);
});
if (new URLSearchParams(window.location.search).get("utm_source") === "servgo_blog") {
    // A public SEO CTA leads directly to the existing real connection form.
    openConnectModal();
}

closeConnectButtons.forEach((button) => {
    button.addEventListener("click", closeConnectModal);
});

connectModal?.addEventListener("click", (event) => {
    if (event.target === connectModal) {
        closeConnectModal();
    }
});

window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && connectModal && !connectModal.hidden) {
        closeConnectModal();
    }
});

connectForm?.addEventListener("submit", async (event) => {
    event.preventDefault();

    const formData = new FormData(connectForm);
    const attribution = readGrowthAttribution();
    const payload = {
        name: String(formData.get("name") || "").trim(),
        phone: String(formData.get("phone") || "").trim(),
        email: String(formData.get("email") || "").trim(),
        comment: String(formData.get("comment") || "").trim(),
        self_reported_source: String(formData.get("self_reported_source") || "").trim(),
        niche: String(formData.get("niche") || "").trim(),
        intent: String(formData.get("intent") || "").trim(),
        requested_module: String(formData.get("seo_module") || "platform").trim(),
        consent: formData.get("consent") === "yes",
        ...attribution,
    };

    if (!payload.name) {
        setConnectStatus("Укажите имя", "error");
        connectForm.elements.name?.focus();
        return;
    }

    if (!payload.phone && !payload.email) {
        setConnectStatus("Укажите телефон или email", "error");
        connectForm.elements.phone?.focus();
        return;
    }

    if (!payload.consent) {
        setConnectStatus("Подтвердите согласие на использование контактных данных для ответа", "error");
        connectForm.elements.consent?.focus();
        return;
    }

    const submitButton = connectForm.querySelector('button[type="submit"]');
    submitButton.disabled = true;
    setConnectStatus("Отправляем заявку...");

    try {
        const response = await fetch("/api/landing/connect-request", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));

        if (!response.ok || !result.success) {
            throw new Error(result.error || "Не удалось отправить заявку");
        }

        connectForm.reset();
        setConnectStatus("Заявка отправлена. Мы скоро свяжемся с вами.", "success");
    } catch (error) {
        setConnectStatus(error.message || "Не удалось отправить заявку", "error");
    } finally {
        submitButton.disabled = false;
    }
});


// Калькулятор «Сколько стоит ваша база»: считает только из введённых цифр.
(function () {
    const calc = document.querySelector("[data-base-calc]");
    if (!calc) return;
    const total = calc.querySelector("[data-base-calc-total]");
    const formula = calc.querySelector("[data-base-calc-formula]");
    const fmt = (n) => Math.round(n).toLocaleString("ru-RU").replace(/\u00a0/g, " ");
    const read = (name, max) => {
        const v = parseFloat(calc.elements[name].value);
        if (!isFinite(v) || v < 0) return 0;
        return max ? Math.min(v, max) : v;
    };
    const update = () => {
        const clients = read("clients");
        const check = read("check");
        const share = read("share", 100);
        total.textContent = fmt(clients * (share / 100) * check) + " ₽";
        formula.textContent = fmt(clients) + " клиентов × " + fmt(share) + "% × " + fmt(check) + " ₽, если каждый вернувшийся придёт один раз";
    };
    calc.addEventListener("input", update);
    calc.addEventListener("submit", (e) => e.preventDefault());
    update();
})();
