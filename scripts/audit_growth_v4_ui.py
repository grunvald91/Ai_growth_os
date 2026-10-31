"""Read-only desktop/mobile audit of real Growth v4 templates against an isolated fixture.

Does not contact production services, invoke AI providers, publish, or send mail.
Run with: python scripts/audit_growth_v4_ui.py
"""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.models.domain import Product

ROOT = Path("/tmp/growth-v4-audit")
ROOT.mkdir(exist_ok=True)
WIDTHS = (320, 390, 768, 1024, 1440)
PAGES = (
    "", "/create", "/work", "/review", "/materials", "/plan", "/calendar",
    "/channels", "/analytics", "/whiteboard", "/seo", "/campaigns",
    "/campaigns/new", "/profile", "/profile/edit", "/accounts", "/comments",
    "/speed",
)
GLOBAL = (
    "/studio?choose=1", "/studio/team", "/studio/tune",
    "/studio/tune/work",
)
CAPTURE = {
    "", "/create", "/work", "/review", "/materials", "/plan",
    "/calendar", "/channels", "/analytics", "/whiteboard", "/seo",
    "/campaigns", "/campaigns/new", "/profile", "/accounts", "/comments",
    "/speed",
}

METRICS = r"""() => {
 const root = document.documentElement;
 const main = document.querySelector('main');
 const mobile = document.querySelector('#mobileNav');
 const sidebar = document.querySelector('#leftNav');
 const visible = el => !!el && el.getBoundingClientRect().width > 0 &&
    getComputedStyle(el).display !== 'none';
 const text = main?.innerText || '';
 return {
  title: document.title,
  v4: document.body.getAttribute('data-growth-design') === 'v4',
  overflowPx: Math.max(0, root.scrollWidth - innerWidth),
  mobileNav: visible(mobile),
  desktopNav: visible(sidebar),
  navLinks: document.querySelectorAll('#leftNav a').length,
  forms: main?.querySelectorAll('form').length || 0,
  fields: main?.querySelectorAll('input,select,textarea').length || 0,
  buttons: main?.querySelectorAll('button').length || 0,
  oldFormMarkers: main?.querySelectorAll('.sg-surface,.sg-form,.sg-layout-two,.sg-intake,.sg-guide,.sg-table').length || 0,
  stillCallsVideoDrawn: /рисованн(?:ое|ого|ые|ый|ым|ыми|ом)/i.test(text),
  voiceInput: !!document.querySelector('#dp-input'),
  sample: text.slice(0,160),
 };
}"""


def main():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    old_overrides = dict(app.dependency_overrides)
    cases = []
    errors = []

    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="Макет проверки ServGo", slug="servgo")
        db.add(product)
        db.commit()

        def session():
            yield db

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_settings] = (
            lambda: Settings(app_env="development", _env_file=None)
        )
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(
            target=lambda: server.run(sockets=[sock]), daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Fixture web server did not start")
                time.sleep(0.05)

            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.on(
                    "pageerror",
                    lambda exc: errors.append(
                        {"page": page.url.split("?")[0], "error": str(exc)[:240]}
                    ),
                )
                base = f"/studio/products/{product.id}"
                # Make the v4 preference persistent through the tested navigation.
                page.goto(origin + base + "?design=v4")
                for width in WIDTHS:
                    page.set_viewport_size(
                        {"width": width, "height": 844 if width <= 768 else 900}
                    )
                    for suffix in PAGES:
                        target = base + suffix
                        response = page.goto(origin + target, wait_until="load")
                        page.wait_for_timeout(35)
                        item = {
                            "width": width,
                            "route": target.replace(str(product.id), "{product_id}"),
                            "status": response.status if response else None,
                            **page.evaluate(METRICS),
                        }
                        cases.append(item)
                        if width in (390, 1440) and suffix in CAPTURE and item["status"] == 200:
                            name = "home" if not suffix else suffix.strip("/").replace("/", "-")
                            page.screenshot(
                                path=str(ROOT / f"{width}-{name}.png"),
                                full_page=True,
                                animations="disabled",
                            )
                for width in (390, 1440):
                    page.set_viewport_size({"width": width, "height": 900})
                    for index, route in enumerate(GLOBAL):
                        response = page.goto(origin + route + (
                            "&design=v4" if "?" in route else "?design=v4"
                        ), wait_until="load")
                        item = {
                            "width": width,
                            "route": route,
                            "status": response.status if response else None,
                            **page.evaluate(METRICS),
                        }
                        cases.append(item)
                        if item["status"] == 200:
                            page.screenshot(
                                path=str(ROOT / f"{width}-global-{index}.png"),
                                full_page=True,
                                animations="disabled",
                            )
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            sock.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old_overrides)

    engine.dispose()
    bad = [v for v in cases if v["status"] != 200 or not v["v4"] or v["overflowPx"] > 1]
    old_forms = [v for v in cases if v["width"] == 1440 and v["oldFormMarkers"] > 0]
    old_labels = [v for v in cases if v["width"] == 1440 and v["stillCallsVideoDrawn"]]
    result = {
        "source": "main branch at audit time",
        "test_environment": "isolated SQLite fixture, real FastAPI HTML/JS/CSS, headless Chromium; no live production login",
        "cases": len(cases),
        "breakdowns": {
            str(w): len([x for x in cases if x["width"] == w])
            for w in WIDTHS
        },
        "problemCases": bad,
        "errors": errors,
        "legacyFormPagesDesktop": [
            {"route": v["route"], "markers": v["oldFormMarkers"], "fields": v["fields"]}
            for v in old_forms
        ],
        "oldVideoLabelPagesDesktop": [
            {"route": v["route"], "sample": v["sample"]} for v in old_labels
        ],
        "pagesDesktop": [v for v in cases if v["width"] == 1440],
        "pagesMobile": [v for v in cases if v["width"] == 390],
    }
    (ROOT / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "V4_AUDIT", json.dumps({
            "cases": len(cases),
            "invalid": len(bad),
            "js_errors": len(errors),
            "desktop_old_form_pages": len(old_forms),
            "desktop_old_video_label_pages": len(old_labels),
        }, ensure_ascii=False),
        flush=True,
    )
    for x in bad:
        print("V4_PROBLEM", json.dumps(x, ensure_ascii=False), flush=True)
    for x in old_forms:
        print("V4_LEGACY_FORM", x["route"], x["oldFormMarkers"], flush=True)
    for x in old_labels:
        print("V4_OLD_LABEL", x["route"], flush=True)
    for x in errors:
        print("V4_JS_ERROR", json.dumps(x, ensure_ascii=False), flush=True)
    # This script is an audit, not a gate. Review the report before fixing issues.


if __name__ == "__main__":
    main()
