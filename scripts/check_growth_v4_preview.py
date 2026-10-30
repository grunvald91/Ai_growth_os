"""Browser regression checks for the standalone v4 preview (no API or credentials).

Install playwright and its Chromium, then run this script. Alternatively pass
--browser /usr/bin/chromium. A temporary loopback-only server serves the assets.
"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright, expect


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check(page, screenshots: Path | None):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.reload()
    pages = page.evaluate("tabs.map(t=>t[0])")
    # All screen layouts; table interiors deliberately scroll inside their wrapper.
    for width in (320, 390, 768, 1024, 1280, 1440):
        page.set_viewport_size({"width": width, "height": 844 if width <= 780 else 900})
        for name in pages:
            page.evaluate("name=>go(name)", name)
            overflow = page.evaluate("""() => [...document.querySelectorAll('#main *')]
                .filter(e=>{const r=e.getBoundingClientRect();return r.width &&
                    r.right>innerWidth+2 && !e.closest('.table-wrap')})
                .map(e=>e.className)""")
            assert not overflow, (width, name, overflow)
    print("PASS: 90 screen/viewport combinations")

    page.evaluate("go('create')")
    page.locator("#taskTopic").fill("Тема для сохранения")
    page.locator("#taskChannel").select_option("Email")
    page.locator('[data-format="Простое видео"]').click()
    expect(page.locator("#taskTopic")).to_have_value("Тема для сохранения")
    expect(page.locator("#taskChannel")).to_have_value("Email")
    page.locator("#themeTop").click()
    expect(page.locator("#taskTopic")).to_have_value("Тема для сохранения")
    page.evaluate("go('work');go('create')")
    expect(page.locator("#taskTopic")).to_have_value("Тема для сохранения")

    page.evaluate("go('seo')")
    page.wait_for_timeout(500)
    page.evaluate("""() => {window.entrances=0;window.seoHeading=document.querySelector('h1');
        document.addEventListener('animationstart',e=>{if(e.animationName==='riseIn')window.entrances++})} """)
    page.locator('[data-action="seo-start"]').click()
    page.locator('[data-action="next-stage"]').focus()
    page.wait_for_timeout(1900)
    assert page.evaluate("window.entrances===0 && document.querySelector('h1')===window.seoHeading")
    expect(page.locator('[data-action="next-stage"]')).to_be_focused()
    page.evaluate("go('create')")
    page.locator("#taskTopic").fill("Тема во время фонового SEO")
    page.wait_for_function("!state.seoActive", timeout=15000)
    expect(page.locator("#taskTopic")).to_have_value("Тема во время фонового SEO")
    print("PASS: drafts, background completion, incremental motion and focus")

    page.evaluate("go('campaigns')")
    before = page.locator('[data-v4-action="campaign-detail"]').count()
    page.locator('[data-v4-action="campaign-detail"]').first.click()
    page.locator("#campaignAudience").fill("Постоянные клиенты")
    page.locator("#campaignBudget").fill("1200")
    page.locator("#campaignDate").fill("2026-11-15")
    page.locator('[data-v4-action="save-campaign"]').click()
    expect(page.locator(".modal")).to_have_count(0)
    assert page.locator('[data-v4-action="campaign-detail"]').count() == before
    page.locator('[data-v4-action="campaign-detail"]').first.click()
    expect(page.locator("#campaignAudience")).to_have_value("Постоянные клиенты")
    expect(page.locator("#campaignBudget")).to_have_value("1200")
    expect(page.locator("#campaignDate")).to_have_value("2026-11-15")
    page.keyboard.press("Escape")
    expect(page.locator(".modal")).to_have_count(0)

    page.evaluate("go('mail')")
    page.locator('[data-v4-action="compose"]').first.click()
    page.locator("#composeTo").fill("demo@example.com")
    page.locator("#composeSubject").fill("Тестовый черновик")
    page.locator("#composeBody").fill("Текст письма")
    page.locator('[data-v4-action="compose-save"]').click()
    expect(page.locator(".modal")).to_have_count(0)
    expect(page.locator("#mailDraftTo")).to_have_value("demo@example.com")
    page.locator('[data-v4-mail="contacts"]').click()
    page.locator('[data-v4-mail="drafts"]').click()
    expect(page.locator("#mailDraftEdit")).to_have_value("Тестовый черновик\nТекст письма")
    page.evaluate("go('settings')")
    company = page.get_by_label("Название", exact=True)
    company.fill("Новая компания")
    page.locator('[data-v4-action="save-settings"]').click()
    page.locator('[data-v4-settings="brand"]').click()
    page.locator('[data-v4-settings="business"]').click()
    expect(page.get_by_label("Название", exact=True)).to_have_value("Новая компания")

    page.evaluate("go('review')")
    page.locator('[data-v4-action="confirm-review"]').first.click()
    page.evaluate("go('work')")
    expect(page.locator('[data-task="1"]')).to_contain_text("Пост для Telegram")
    page.locator('[data-task="1"]').focus()
    page.keyboard.press("Space")
    expect(page.locator(".modal")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".modal")).to_have_count(0)
    expect(page.locator('[data-task="1"]')).to_be_focused()
    page.evaluate("sendCommand('Создай простое видео')")
    page.get_by_role("button", name="Создать простое видео").click()
    expect(page.locator('[data-format="Простое видео"]')).to_have_class("format-option active")
    expect(page.locator("#taskTopic")).to_have_value("Создай простое видео")
    expect(page.locator("#taskChannel")).to_have_value("Видео MP4")
    print("PASS: campaigns, recipient, settings, approval and contextual video CTA")

    page.locator("#openSearch").click()
    for _ in range(16):
        page.keyboard.press("Tab")
        assert page.evaluate("!!document.activeElement.closest('.modal')")
    for _ in range(16):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("!!document.activeElement.closest('.modal')")
    page.keyboard.press("Escape")
    expect(page.locator(".modal")).to_have_count(0)
    expect(page.locator("#openSearch")).to_be_focused()
    assert not page.evaluate("document.querySelector('.shell').inert")

    # Same two-column desktop shell before and after a command.
    page.evaluate("state.homeResult=null;go('home')")
    before = page.locator(".workspace").bounding_box()["width"]
    page.locator("#voiceInput").fill("Покажи SEO")
    page.keyboard.press("Enter")
    assert page.locator(".workspace").bounding_box()["width"] == before
    assert page.locator("#rail").bounding_box() is None
    for width in (1024, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("go('seo')")
        assert page.evaluate("""() => {const c=document.querySelector('.crumbs').getBoundingClientRect();
            const a=document.querySelector('.top-actions').getBoundingClientRect();
            return c.right<=a.left && a.right<=innerWidth;}""")

    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("state.homeResult=null;go('home');toast('Сообщение о сохранении')")
    page.wait_for_timeout(400)
    nav = page.locator("#mobileNav").bounding_box()
    toast = page.locator("#toast").bounding_box()
    command = page.locator(".command-bar").bounding_box()
    assert toast["y"] + toast["height"] < nav["y"]
    assert command["y"] > 844 * .6
    assert command["y"] + command["height"] < nav["y"]
    if screenshots:
        page.screenshot(path=str(screenshots / "mobile-home.png"), full_page=True)
    # Every desktop destination is accessible in the mobile More menu.
    page.locator('#mobileNav [data-page="more"]').click()
    for name in [n for n in pages if n not in ("home", "more")]:
        expect(page.locator(f'#main [data-page="{name}"]')).to_be_visible()

    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("state.theme='dark';go('team')")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(13, 23, 39)"
    assert page.evaluate("getComputedStyle(document.querySelector('.mini-table td b')).color") == "rgb(237, 243, 251)"
    page.wait_for_timeout(400)
    if screenshots:
        page.screenshot(path=str(screenshots / "desktop-dark.png"), full_page=True)
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("go('seo');startSeoDemo()")
    assert page.evaluate("document.getAnimations().length") == 0
    page.locator("#openSearch").click()
    page.keyboard.press("Escape")
    expect(page.locator(".modal")).to_have_count(0)
    expect(page.locator("#openSearch")).to_be_focused()
    # Voice demo never starts a real microphone/remote SpeechRecognition session.
    page.evaluate("() => {go('home');window.SpeechRecognition=function(){throw Error('Remote voice called')}}")
    page.locator("#micBtn").click()
    expect(page.locator("#voiceFallback")).to_be_visible()
    assert not errors, errors
    print("PASS: modal keyboard, desktop/mobile geometry, dark theme, reduced motion, offline voice")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Optional Chromium executable path")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    assets = Path(__file__).resolve().parents[1] / "app/api/assets"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(assets)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=args.browser)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{server.server_port}/growth_os_v4_preview.html")
            check(page, args.screenshots)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
