"""Isolated browser regression: AI-team strict CSP and 320px create page.

No production requests, mail, AI generation, publication or real credentials.
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

OUT = Path("/tmp/servgo-growth-targeted-ui")
OUT.mkdir(exist_ok=True)


def main():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    previous = dict(app.dependency_overrides)
    result = {"checks": []}
    with Session(engine, expire_on_commit=False) as session:
        product = Product(name="ServGo isolated fixture", slug="servgo-ci-check")
        session.add(product)
        session.commit()
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_settings] = lambda: Settings(app_env="development", _env_file=None)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError("Fixture server unavailable")
                time.sleep(0.05)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 320, "height": 844})
                errors = []
                page.on("pageerror", lambda error: errors.append("js: " + str(error)))
                page.on("console", lambda event: errors.append("console: " + event.text[:240]) if event.type == "error" else None)
                create_url = f"{origin}/studio/products/{product.id}/create?design=v4"
                response = page.goto(create_url, wait_until="load")
                assert response and response.status == 200, f"create HTTP: {response.status if response else None}"
                page.wait_for_timeout(100)
                measurements = page.evaluate("""() => ({
                    viewport: innerWidth, scroll: document.documentElement.scrollWidth,
                    overflow: Math.max(0, document.documentElement.scrollWidth-innerWidth),
                    wider: [...document.querySelectorAll('main *')].filter(e=>{
                       const r=e.getBoundingClientRect();return r.width>0&&r.right>innerWidth+1;
                    }).slice(0,10).map(e=>({
                       className: String(e.className).slice(0,70),
                       width: Math.round(e.getBoundingClientRect().width),
                       right: Math.round(e.getBoundingClientRect().right)
                    }))
                })""")
                result["checks"].append({"page":"create","width":320,**measurements})
                page.screenshot(path=str(OUT/"create-320.png"),full_page=True,animations="disabled")
                assert measurements["overflow"] <= 1, f"320px form overflows: {measurements}"

                for width in (390, 1440):
                    page.set_viewport_size({"width":width,"height":844 if width==390 else 900})
                    errors.clear()
                    url = origin + "/studio/team?design=v4"
                    response = page.goto(url, wait_until="load")
                    page.wait_for_timeout(150)
                    assert response and response.status==200, f"team HTTP: {response.status if response else None}"
                    csp = response.headers.get("content-security-policy","")
                    assert "style-src 'self'" in csp and "script-src 'self'" in csp, csp
                    assert "'unsafe-inline'" not in csp, csp
                    value = page.evaluate("""() => ({
                      title:document.title,
                      hasTeam:!!document.querySelector('#saveButton'),
                      stylesheet:[...document.styleSheets].map(x=>x.href||'').filter(Boolean),
                      theme:document.documentElement.dataset.theme||'',
                      width:document.documentElement.scrollWidth,
                      viewport:innerWidth
                    })""")
                    result["checks"].append({"page":"team","viewport":width,**value})
                    assert value["hasTeam"], "team controls missing"
                    assert any("servgo-team-air.css" in x for x in value["stylesheet"]), "team theme CSS missing"
                    assert any("servgo-air-tokens.css" in x for x in value["stylesheet"]), "tokens CSS missing"
                    assert value["theme"] in ("light","dark"), "theme not initialized"
                    blocked=[x for x in errors if "content security policy" in x.lower() or "refused to apply inline" in x.lower()]
                    assert not blocked, "CSP blocked team presentation: " + str(blocked)
                    page.screenshot(path=str(OUT/f"team-{width}.png"),full_page=True,animations="disabled")
                browser.close()
        finally:
            server.should_exit=True
            thread.join(timeout=15)
            sock.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
    engine.dispose()
    (OUT/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("SERVGO_GROWTH_TARGETED_BROWSER="+json.dumps(result,ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
