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

                # Newly revised product pages must render on the same narrow
                # Android-size layout without horizontal scroll or fake state.
                for segment in ("campaigns/new", "seo", "seo/queue"):
                    response = page.goto(
                        f"{origin}/studio/products/{product.id}/{segment}?design=v4",
                        wait_until="load",
                    )
                    assert response and response.status == 200, (
                        f"{segment} HTTP: {response.status if response else None}"
                    )
                    value = page.evaluate("""() => ({
                        viewport: innerWidth,
                        overflow: Math.max(0, document.documentElement.scrollWidth-innerWidth),
                        formFields: document.querySelectorAll('.sg-campaign-form [required]').length,
                        hasSavedRuns: !!document.querySelector('.seo-run'),
                        hasRunsHeading: document.body.innerText.includes('Запуски SEO-завода'),
                    })""")
                    result["checks"].append({"page": segment, "width": 320, **value})
                    assert value["overflow"] <= 1, f"{segment} overflow: {value}"
                    if segment == "campaigns/new":
                        assert value["formFields"] == 2, "Short draft must require only goal and name"
                        assert page.get_by_text("Дополнительные настройки (необязательно)").count() == 1
                    if segment == "seo":
                        assert value["hasRunsHeading"], "SEO run heading missing"
                    if segment == "seo/queue":
                        assert page.get_by_text("Подтвердить постановку в очередь").count() == 1
                    page.screenshot(path=str(OUT / f"{segment.replace('/', '-')}-320.png"),
                                    full_page=True, animations="disabled")

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

                # Hold-to-talk is a real browser interaction, not just a markup assertion.
                # Mock microphone and POST responses: no model tokens, recordings or live tasks.
                page.add_init_script("""(() => {
                  window.__mockMicStarts = 0;
                  Object.defineProperty(navigator, 'mediaDevices', {configurable:true, value:{
                    getUserMedia: async () => { window.__mockMicStarts++; return {
                      getTracks: () => [{stop(){}}]
                    }; }
                  }});
                  class MockRecorder {
                    constructor(){this.state='inactive';this.mimeType='audio/webm';this.handlers={};}
                    static isTypeSupported(){return true;}
                    addEventListener(kind,cb){this.handlers[kind]=cb;}
                    start(){this.state='recording';}
                    stop(){
                      if(this.state==='inactive')return;
                      this.state='inactive';
                      this.handlers.dataavailable?.({data:new Blob(['test voice'],{type:this.mimeType})});
                      this.handlers.stop?.();
                    }
                  }
                  window.MediaRecorder=MockRecorder;
                })();""")
                voice_calls, dispatch_calls = [], []
                def mock_voice(route):
                    voice_calls.append(route.request.method)
                    route.fulfill(status=200,content_type='application/json',
                        body=json.dumps({'text':'Что в работе','raw_text':'что в работе',
                                         'empty':False,'cleaned':True,'fallback_reason':None,
                                         'replacements':[]}))
                def mock_dispatch(route):
                    item=json.loads(route.request.post_data or '{}')
                    dispatch_calls.append(item)
                    route.fulfill(status=503 if len(dispatch_calls)==1 else 200,
                        content_type='application/json',
                        body=json.dumps({'detail':'Временная ошибка' } if len(dispatch_calls)==1
                                        else {'messages':[],'hints':[]}))
                page.route('**/studio/voice/dictate',mock_voice)
                page.route('**/studio/products/*/dispatch/messages',mock_dispatch)
                page.set_viewport_size({'width':390,'height':844})
                response=page.goto(
                    f'{origin}/studio/products/{product.id}?design=v4',wait_until='load')
                assert response and response.status==200
                assert page.locator('#dp-mic').count()==0, 'v4 must have no microphone button'
                # All bottom-nav labels, including the details/summary "Ещё", share a baseline.
                nav_y=page.locator('#mobileNav').evaluate("""nav => [...nav.children]
                  .map(cell => cell.querySelector('span').getBoundingClientRect().top)""")
                assert max(nav_y)-min(nav_y)<=3, f'Bottom navigation is misaligned: {nav_y}'
                def pointer(kind, pointer_id):
                    page.evaluate("""([kind,id]) => {
                      const where=kind==='pointerdown' ? document.querySelector('#dp-input') : document;
                      where.dispatchEvent(new PointerEvent(kind,{
                        bubbles:true,cancelable:true,button:0,pointerType:'touch',pointerId:id,clientX:120,clientY:630
                      }));
                    }""",[kind,pointer_id])
                pointer('pointerdown',11)
                pointer('pointerup',11)
                page.wait_for_timeout(270)
                assert page.evaluate('window.__mockMicStarts')==0, 'short tap started audio'
                assert page.locator('#dp-input').evaluate("el=>document.activeElement===el")
                page.locator('#dp-input').fill('Мой незавершённый текст')
                pointer('pointerdown',12)
                page.wait_for_function("() => window.__mockMicStarts===1 && document.querySelector('#dp-form').classList.contains('voice-recording')")
                pointer('pointercancel',12)
                page.wait_for_timeout(100)
                assert not voice_calls, 'cancelled hold uploaded audio'
                pointer('pointerdown',13)
                page.wait_for_function("() => window.__mockMicStarts===2 && document.querySelector('#dp-form').classList.contains('voice-recording')")
                pointer('pointerup',13)
                page.wait_for_function("() => document.querySelector('#dp-voice-note button.dp-voice-undo')")
                assert len(voice_calls)==1 and len(dispatch_calls)==1, (voice_calls,dispatch_calls)
                assert dispatch_calls[0]['mode']=='text'
                assert dispatch_calls[0]['text']=='Что в работе'
                assert page.locator('#dp-input').input_value()=='Мой незавершённый текст', 'dictation overwrote draft'
                page.get_by_role('button',name='Отправить ещё раз').click()
                page.wait_for_function("() => !document.querySelector('.dp-home').classList.contains('busy')")
                assert len(dispatch_calls)==2,dispatch_calls
                assert dispatch_calls[0]['client_key']==dispatch_calls[1]['client_key'], 'retry lost idempotency key'
                assert page.locator('#dp-voice-note').is_hidden()
                assert not errors,errors
                result['checks'].append({'page':'voice-hold-mock','width':390,
                    'short_tap':True,'cancel':True,'automatic_send':True,'retry_idempotent':True,
                    'preserved_typed_draft':True,'nav_aligned':True})
                page.screenshot(path=str(OUT/'voice-hold-390.png'),animations='disabled')
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
