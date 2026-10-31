"""Isolated browser regression: hold-to-talk, retry, and aligned mobile navigation.

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

OUT = Path("/tmp/servgo-growth-voice-ui")
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
                # Real touch emulation catches Android selection/pointercancel
                # which synthetic PointerEvents never exercise.
                context = browser.new_context(viewport={"width": 390, "height": 844},
                                              is_mobile=True, has_touch=True, device_scale_factor=1)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append("js: " + str(error)))
                page.on("console", lambda event: errors.append("console: " + event.text[:240]) if event.type == "error" else None)
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
                  const originalFetch = window.fetch.bind(window);
                  window.fetch = (url, init) => {
                    const response = originalFetch(url, init);
                    return String(url).includes('/studio/voice/dictate')
                      ? response.then(data => new Promise(done => setTimeout(() => done(data), 330)))
                      : response;
                  };
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
                assert page.locator('#dp-press-hint').count()==0, 'hint belongs inside composer'
                assert page.locator('#dp-input').get_attribute('placeholder')=='Удерживайте, чтобы говорить'
                assert page.locator('#dp-voice-overlay').count()==1
                assert page.locator('#dp-voice-hitbox').count()==1, 'Android touch guard missing'
                guard = page.locator('#dp-voice-hitbox').evaluate("""element => ({
                  touchAction:getComputedStyle(element).touchAction,
                  userSelect:getComputedStyle(element).userSelect,
                  callout:getComputedStyle(element).webkitTouchCallout
                })""")
                assert guard['touchAction']=='none' and guard['userSelect']=='none', guard
                composer=page.locator('#dp-form').evaluate("""element=>{
                  const s=getComputedStyle(element),r=element.getBoundingClientRect();
                  return {radius:parseFloat(s.borderTopLeftRadius),height:r.height}
                }""")
                assert composer['radius']<=24 and composer['height']<=70,composer
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
                page.wait_for_timeout(180)
                active_overlay=page.locator('#dp-voice-overlay').evaluate("""element=>({
                  opacity:parseFloat(getComputedStyle(element).opacity),
                  position:getComputedStyle(element).position,
                  animation:getComputedStyle(element,'::after').animationDuration,
                  label:element.querySelector('#dp-voice-overlay-text').textContent
                })""")
                assert active_overlay['opacity']>.9 and active_overlay['position']=='fixed',active_overlay
                assert active_overlay['animation']=='3s',active_overlay
                assert 'Отпустите' in active_overlay['label']
                page.screenshot(path=str(OUT/'voice-hold-active-390.png'),animations='allow')
                pointer('pointercancel',12)
                page.wait_for_timeout(220)
                assert page.locator('#dp-voice-overlay').evaluate("e=>parseFloat(getComputedStyle(e).opacity)")<.1
                assert not voice_calls, 'cancelled hold uploaded audio'
                pointer('pointerdown',13)
                page.wait_for_function("() => window.__mockMicStarts===2 && document.querySelector('#dp-form').classList.contains('voice-recording')")
                pointer('pointerup',13)
                page.wait_for_function("() => document.querySelector('#dp-form').classList.contains('voice-processing')")
                assert page.locator('#dp-voice-status').is_visible()
                assert 'Распознаю и редактирую' in page.locator('#dp-voice-status').inner_text()
                page.wait_for_timeout(200)
                assert page.locator('#dp-voice-overlay').evaluate("e=>parseFloat(getComputedStyle(e).opacity)")<.1
                page.screenshot(path=str(OUT/'voice-processing-390.png'),animations='disabled')
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

                # Actual touchscreen long-press on the visible placeholder:
                # must not select text, raise context menu or cancel audio.
                page.locator('#dp-input').evaluate("el=>el.blur()")
                page.wait_for_function("() => !document.querySelector('#dp-form').classList.contains('voice-text-editing')")
                guard_box=page.locator('#dp-voice-hitbox').bounding_box()
                assert guard_box is not None
                hit_x=guard_box['x']+guard_box['width']*.55
                hit_y=guard_box['y']+guard_box['height']*.5
                cdp=context.new_cdp_session(page)
                page.evaluate("() => { document.getSelection()?.removeAllRanges(); window.__voiceContextMenus=0; document.addEventListener('contextmenu', () => window.__voiceContextMenus++); }")
                cdp.send('Input.dispatchTouchEvent',{'type':'touchStart',
                         'touchPoints':[{'x':hit_x,'y':hit_y,'id':41}]})
                page.wait_for_function("() => window.__mockMicStarts===3 && document.querySelector('#dp-form').classList.contains('voice-recording')")
                page.wait_for_timeout(950)  # past Android's normal text-selection threshold
                assert page.locator('#dp-form').evaluate("e=>e.classList.contains('voice-recording')"), 'Android long press cancelled recording'
                assert page.evaluate("() => document.getSelection()?.toString() || ''") == '', 'Android selected placeholder text'
                assert page.evaluate("() => window.__voiceContextMenus")==0, 'Context menu opened during hold'
                cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
                page.wait_for_function("() => !document.querySelector('#dp-form').classList.contains('voice-recording')")
                page.wait_for_function("() => !document.querySelector('#dp-form').classList.contains('voice-processing')")
                assert len(voice_calls)==2,voice_calls
                assert len(dispatch_calls)==3,dispatch_calls
                assert not [error for error in errors if error.startswith('js:')],errors
                result['checks'].append({'page':'voice-hold-mock','width':390,
                    'short_tap':True,'cancel':True,'automatic_send':True,'retry_idempotent':True,
                    'preserved_typed_draft':True,'nav_aligned':True,
                    'overlay_enter_exit':True,'processing_inside_composer':True,
                    'compact_composer':True,'real_touch_long_press':True,
                    'no_native_text_selection':True})
                page.screenshot(path=str(OUT/'voice-hold-390.png'),animations='disabled')
                # The conversation must not retain the centered empty-home
                # layout, or reserve a separate band for New Topic above chat.
                assert page.locator('body').evaluate("el=>!el.classList.contains('home-empty')")
                assert page.locator('#dp-new').is_visible()
                assert page.locator('#dp-new').evaluate(
                    "el=>el.parentElement.classList.contains('command-tools')")
                geometry=page.evaluate("""() => {
                  const main=document.querySelector('#main').getBoundingClientRect(),
                    feed=document.querySelector('#dp-feed').getBoundingClientRect(),
                    composer=document.querySelector('#dp-form').getBoundingClientRect(),
                    topic=document.querySelector('#dp-new').getBoundingClientRect(),
                    toolbar=document.querySelector('.command-tools').getBoundingClientRect();
                  return {gap:feed.top-main.top,topicTop:topic.top,
                          composerBottom:composer.bottom,toolbarTop:toolbar.top};
                }""")
                assert geometry['gap']<=45, f"Separate header band still visible: {geometry}"
                assert geometry['topicTop']>=geometry['composerBottom'], geometry
                assert geometry['topicTop']>=geometry['toolbarTop'], geometry
                # A genuinely transparent dock overlaps the scrollable history;
                # its children retain their own surfaces and remain clickable.
                page.evaluate("""() => {
                  const feed=document.querySelector('#dp-feed');
                  for(let i=0;i<14;i++){
                    const m=document.createElement('div');
                    m.className='dp-msg bot'; m.textContent='Проверка истории '+i+' — сообщение в ленте.';
                    feed.appendChild(m);
                  }
                }""")
                page.wait_for_function("""() => {
                  const dock=document.querySelector('.command-dock');
                  return parseFloat(getComputedStyle(document.querySelector('#dp-feed')).paddingBottom)
                    >=dock.getBoundingClientRect().height+8;
                }""")
                overlap=page.evaluate("""() => {
                  const feed=document.querySelector('#dp-feed'),
                        dock=document.querySelector('.command-dock'),
                        tools=document.querySelector('.command-tools'),
                        hints=document.querySelector('.command-hints');
                  const fr=feed.getBoundingClientRect(), dr=dock.getBoundingClientRect();
                  const transparent=el=>getComputedStyle(el).backgroundColor;
                  const reserved=parseFloat(getComputedStyle(feed).paddingBottom);
                  feed.scrollTop=feed.scrollHeight;
                  return {feedBottom:fr.bottom,dockTop:dr.top,dockBottom:dr.bottom,
                    feedScrollHeight:feed.scrollHeight,feedClientHeight:feed.clientHeight,
                    dockHeight:dr.height,reserved,
                    dockBg:transparent(dock),toolsBg:transparent(tools),hintsBg:transparent(hints),
                    composerBg:transparent(document.querySelector('#dp-form')),
                    lastBottom:feed.lastElementChild.getBoundingClientRect().bottom,
                    scrollTop:feed.scrollTop};
                }""")
                assert overlap['dockBg']=='rgba(0, 0, 0, 0)',overlap
                assert overlap['toolsBg']=='rgba(0, 0, 0, 0)',overlap
                assert overlap['hintsBg']=='rgba(0, 0, 0, 0)',overlap
                assert overlap['composerBg']!='rgba(0, 0, 0, 0)',overlap
                assert overlap['feedBottom']>=overlap['dockBottom']-12,overlap
                assert overlap['dockTop']<overlap['feedBottom']-45,overlap
                assert overlap['reserved']>=overlap['dockHeight']+8,overlap
                assert overlap['feedScrollHeight']>overlap['feedClientHeight'],overlap
                assert overlap['lastBottom']<=overlap['dockTop']+3,overlap
                page.screenshot(path=str(OUT/'transparent-floating-composer-390.png'),animations='disabled')
                result['checks'].append({'page':'transparent-floating-composer-390',
                    'dock_transparent':True,'shortcuts_transparent':True,
                    'feed_scrolls_behind_dock':True,'last_message_readable':True})
                page.route('**/studio/products/*/dispatch/new',
                           lambda route:route.fulfill(status=200,content_type='application/json',
                               body=json.dumps({'hints':[]})))
                page.locator('#dp-new').click()
                page.wait_for_function("() => document.body.classList.contains('home-empty') && !document.querySelector('.dp-home').classList.contains('has-feed')")
                assert page.locator('#dp-new').is_hidden()
                result['checks'].append({'page':'topic-toolbar-390','no_blank_header_band':True,
                    'new_topic_in_shortcuts':True,'empty_state_restored':True})
                browser.close()
        finally:
            server.should_exit=True
            thread.join(timeout=15)
            sock.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
    engine.dispose()
    (OUT/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("SERVGO_GROWTH_VOICE_BROWSER="+json.dumps(result,ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
