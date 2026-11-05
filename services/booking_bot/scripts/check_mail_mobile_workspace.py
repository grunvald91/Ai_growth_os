"""Real mobile-browser regression for both ServGo Mail HTML workspaces.

Uses repository HTML and CSS with script/network requests removed. No credentials,
no mail traffic, no server write. Tests actual computed browser geometry.
"""
from pathlib import Path
import re

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "web/api/routes"
TOKENS = (ROOT / "servgo-air-tokens.css").read_text(encoding="utf-8")
MAIL_CSS = (ROOT / "servgo-mail-air.css").read_text(encoding="utf-8")
DESIGN = (ROOT.parent.parent / "frontend/src/styles/design-system.css").read_text(encoding="utf-8")

PAGES = (
    ("mail_campaign_dashboard.html", True),
    ("owner_mail_ui.html", False),
)
LONG_STATUS = ("Выключена: ни одно письмо не уйдёт, пока вы не включите "
               "рассылку. Подготовка и проверка сообщений продолжается отдельно.")


def isolated_html(name: str, theme: str) -> str:
    html = (ROOT / name).read_text(encoding="utf-8")
    html = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", html,
                  flags=re.IGNORECASE | re.DOTALL)
    html = re.sub(r"<link\b[^>]*>", "", html, flags=re.IGNORECASE)
    html = html.replace("<html lang=\"ru\">",
                        f'<html lang="ru" data-theme="{theme}">', 1)
    html = html.replace("</head>",
                        "<style>" + TOKENS + "</style><style>" + MAIL_CSS + "</style></head>", 1)
    return html


def bounds(page, selector):
    return page.locator(selector).first.bounding_box()


def run():
    total = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            for name, has_switch in PAGES:
                for width, height in ((320, 740), (360, 800), (390, 844), (768, 900)):
                    for theme in ("dark", "light"):
                        context = browser.new_context(
                            viewport={"width": width, "height": height},
                            is_mobile=width <= 390, device_scale_factor=1, has_touch=width <= 390,
                        )
                        page = context.new_page()
                        page.set_content(isolated_html(name, theme), wait_until="domcontentloaded")
                        nav = page.locator(".mail-workspace-links")
                        assert nav.is_visible(), (name, width, theme, "nav hidden")
                        links = nav.locator("a")
                        assert links.count() == 3, (name, "missing destinations")
                        for href in (
                            "https://servgo.ru/admin",
                            "https://servgo.ru/workspace/growth",
                            "/api/logout",
                        ):
                            assert nav.locator(f'a[href="{href}"]').is_visible(), (name, href)
                        nb = bounds(page, ".mail-workspace-links")
                        assert 0 <= nb["x"] and nb["x"] + nb["width"] <= width + 2, (name, width, nb)
                        assert nb["height"] <= 94, (name, width, "nav too tall", nb)
                        assert page.locator(".mail-v3-dock").is_visible()
                        assert page.locator(".mail-v3-nav svg").count() == 3
                        assert not page.locator(".mail-v3-popover-content").is_visible()
                        # Compare REAL computed active Mail pseudo-element with
                        # the authoritative .ds-bn indicator in the same browser.
                        reference = page.evaluate("""(css) => {
                            const style = document.createElement('style');
                            style.textContent = css;
                            document.head.appendChild(style);
                            const shell = document.createElement('nav');
                            shell.className = 'ds-bottomnav';
                            shell.style.cssText = 'position:fixed;left:-200px;width:120px';
                            const tab = document.createElement('a');
                            tab.className = 'ds-bn active';
                            tab.textContent = 'Раздел';
                            shell.append(tab);
                            document.body.append(shell);
                            const pseudo = getComputedStyle(tab, '::after');
                            const values = {
                                clipPath:pseudo.clipPath, backgroundImage:pseudo.backgroundImage,
                                backgroundSize:pseudo.backgroundSize, height:pseudo.height,
                                animationDuration:pseudo.animationDuration,
                                animationName:pseudo.animationName,
                                color:getComputedStyle(tab).color
                            };
                            shell.remove();
                            style.remove();
                            return values;
                        }""", DESIGN)
                        active = page.locator('.mail-v3-nav a[aria-current]')
                        if active.count() == 0:
                            active = page.locator(".mail-v3-nav a").nth(1)
                            active.evaluate("(element) => element.setAttribute('aria-current', 'page')")
                        actual = active.evaluate("""element => {
                            const pseudo = getComputedStyle(element, '::after');
                            return {
                                clipPath:pseudo.clipPath, backgroundImage:pseudo.backgroundImage,
                                backgroundSize:pseudo.backgroundSize, height:pseudo.height,
                                animationDuration:pseudo.animationDuration,
                                animationName:pseudo.animationName,
                                color:getComputedStyle(element).color
                            }
                        }""")
                        assert actual == reference, (name, width, theme, "design system line differs", actual, reference)
                        assert "Onest" in page.locator("body").evaluate(
                            "element => getComputedStyle(element).fontFamily"
                        ), (name, theme, "incorrect font family")
                        assert page.locator(".mail-v3-nav svg").first.evaluate(
                            "el => getComputedStyle(el).width"
                        ) == "21px", (name, "noncanonical bottom icon size")
                        page.locator(".mail-v3-more > summary").click()
                        pop = page.locator(".mail-v3-more .mail-v3-popover-content")
                        assert pop.is_visible(), (name, width, "menu did not open")
                        rb = pop.bounding_box()
                        assert rb["x"] >= -2 and rb["x"] + rb["width"] <= width + 2, (
                            name, width, "popover escapes viewport", rb)
                        assert rb["y"] >= -2 and rb["y"] + rb["height"] <= height + 2, (
                            name, width, "popover escapes vertically", rb)
                        if has_switch:
                            page.locator("#auto-text").evaluate(
                                "(element, text) => element.textContent = text", LONG_STATUS)
                            sb = bounds(page, "#auto-switch")
                            tx = bounds(page, ".mail-v3-popover-content .auto > div")
                            assert 49 <= sb["width"] <= 55 and 29 <= sb["height"] <= 33, (
                                name, width, "switch stretched", sb)
                            assert tx["width"] >= 145 and tx["height"] <= 170, (
                                name, width, "automation text squeezed", tx)
                            assert tx["x"] + tx["width"] <= sb["x"] - 4, (
                                name, width, "switch overlaps text", tx, sb)
                        if not has_switch and width <= 390:
                            page.locator(".mail-v3-more > summary").click()
                            page.locator(".mail-v3-folders > summary").click()
                            folder = bounds(page, ".mail-v3-folders > nav")
                            assert folder["x"] >= 0 and folder["x"] + folder["width"] <= width + 2, (
                                name, width, "folder menu escapes viewport", folder)
                        overflow = page.evaluate(
                            "() => document.documentElement.scrollWidth - innerWidth")
                        assert overflow <= 2, (name, width, theme, "horizontal overflow", overflow)
                        total += 1
                        print("MAIL_BROWSER_OK", name, width, theme,
                              "popover_width", round(rb["width"]), flush=True)
                        context.close()
        finally:
            browser.close()
    print("MAIL_BROWSER_PASS cases=" + str(total))


if __name__ == "__main__":
    run()
