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
from app.models.domain import Product, Artifact

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
        # Real server-rendered preview fixtures: the original CSS used a
        # light background while the v4 override switched its ink to white.
        script_material = Artifact(
            product_id=product.id, kind="video_script", title="Contrast test video",
            content=json.dumps({"production_brief": {
                "objective": "Readable script", "target_platform": "instagram_reels",
                "voiceover_or_dialogue_script": "Readable voiceover",
                "editing_rhythm": "Readable editing",
                "shots": [{"start_second": 0, "end_second": 5,
                           "purpose": "Shot title", "visual_description": "Shot description",
                           "generation_prompt": "Shot prompt"}]}}))
        post_material = Artifact(
            product_id=product.id, kind="channel_text", title="Contrast test post",
            content=json.dumps({"body": "Readable publication", "channel": "telegram"}))
        creative_material = Artifact(
            product_id=product.id, kind="creative_brief", title="Contrast test frames",
            content=json.dumps({"platform": "instagram_reels", "frames": [{
                "number": 1, "on_screen_text": "Screen text",
                "purpose": "Frame title", "action_or_animation": "Frame description",
                "visual_prompt": "Frame prompt", "spoken_text": "Frame speech"}]}))
        session.add_all([script_material, post_material, creative_material])
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
                  window.__mockLevel = 0;
                  window.__mockAudioContexts = 0;
                  window.__mockAudioResumes = 0;
                  window.__mockAudioCloses = 0;
                  class MockAudioContext {
                    constructor(){
                      this.state='suspended';
                      window.__mockAudioContexts++;
                    }
                    createAnalyser(){ return {
                      fftSize:512,
                      getByteTimeDomainData(array){
                        for(let i=0;i<array.length;i++)
                          array[i]=128+Math.round(window.__mockLevel*Math.sin(i*.31));
                      }
                    }; }
                    createMediaStreamSource(){ return {connect(){},disconnect(){}}; }
                    close(){
                      this.state='closed';
                      window.__mockAudioCloses++;
                      return Promise.resolve();
                    }
                    resume(){
                      this.state='running';
                      window.__mockAudioResumes++;
                      return Promise.resolve();
                    }
                  }
                  window.AudioContext=MockAudioContext;
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
                # The morning briefing appears once and must not pollute the chat.
                page.locator('#sgDailyDrawer').wait_for(state='visible')
                page.locator('#sgDailyContent .sg-daily-stats').wait_for(state='visible')
                assert page.locator('body').evaluate("e => e.classList.contains('sg-daily-open')")
                page.mouse.click(382, 500)  # backdrop, outside the 358px drawer
                assert page.locator('#sgDailyHandle').is_visible()
                page.locator('#sgDailyDrawer').wait_for(state='hidden', timeout=3000)
                page.locator('#sgDailyHandle').click()
                page.locator('#sgDailyClose').click()
                assert page.locator('#sgDailyHandle').is_visible()
                # Computed visibility catches hidden/oversized header controls.
                for width in (360, 390, 780, 1280):
                    page.set_viewport_size({'width': width, 'height': 844})
                    switcher = page.locator('#bizBtn')
                    assert switcher.is_visible(), f'Company switcher hidden at {width}px'
                    assert switcher.locator('strong').is_visible()
                    assert switcher.get_attribute('href') is None
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('#bizBtn').click()
                assert page.locator('#sgOrgMenu').is_visible()
                page.locator('#sgOrgMenu .sg-org-option').first.wait_for(state='visible', timeout=10000)
                assert page.locator('#sgOrgMenu').get_by_text(product.name, exact=True).count() >= 1
                assert page.locator('#bizBtn').get_attribute('aria-expanded') == 'true'
                page.mouse.click(52, 250)  # outside the dropdown
                assert page.locator('#sgOrgMenu').is_hidden()
                assert page.locator('#bizBtn').get_attribute('aria-expanded') == 'false'
                page.goto(f'{origin}/studio/products/{product.id}?design=v4', wait_until='load')
                page.locator('#sgDailyDrawer').wait_for(state='hidden', timeout=3000), 'Daily briefing re-opened on the same day'
                result['checks'].append({'page': 'company-switcher-and-daily-briefing',
                    'widths': [360, 390, 780, 1280], 'visible': True, 'dropdown_opens': True,
                    'opens_once_per_day': True, 'close_on_outside_click': True})
                # Navigation must work by clicking inside the mobile drawer;
                # testing page.goto() alone would miss WebView link handling.
                page.locator('#mobileNav details>summary').click()
                seo_link=page.locator('#mobileNav .v4-more a[href$="/seo"]')
                assert seo_link.is_visible(), 'SEO-zavod link missing from mobile drawer'
                with page.expect_navigation() as seo_navigation:
                    seo_link.click()
                assert seo_navigation.value.status==200
                assert page.url.split('?')[0]==f'{origin}/studio/products/{product.id}/seo'
                assert page.locator('.seo-activation').count()==1
                # The same link must work with a fresh embedded-style browser page,
                # without any target=_blank popup or page.close() side-effect.
                page.goto(f'{origin}/studio/products/{product.id}?design=v4', wait_until='load')
                from app.content.whiteboard.contracts import WhiteboardRequest
                from app.content.whiteboard.runtime import configured_models
                from app.content.whiteboard.jobs import queue_job
                video=queue_job(session,WhiteboardRequest(product_id=product.id,
                    topic='Проверка открытия последнего простого видео в студии',
                    duration_seconds=30,scenes=3,call_to_action='Подпишитесь на канал'),
                    configured_models(Settings(_env_file=None,
                      whiteboard_storyboard_model='fixture/story',
                      whiteboard_image_model='gemini-3.1-flash-image',
                      whiteboard_speech_model='fixture/speech',
                      whiteboard_regions_model='fixture/regions')),
                    max_api_calls=34,visual_review=True,image_provider='claudehub')
                session.commit()
                page.goto(f'{origin}/studio/products/{product.id}?design=v4', wait_until='load')
                page.locator('[data-sg-notifications] summary').click()
                notification=page.locator(
                    f'[data-sg-notify-list] a[href$="/whiteboard/{video.id}"]')
                notification.wait_for(state='visible',timeout=10000)
                assert notification.get_attribute('target') is None
                with page.expect_navigation() as video_navigation:
                    notification.click()
                assert video_navigation.value.status==200
                assert page.url.split('?')[0]==(
                    f'{origin}/studio/products/{product.id}/whiteboard/{video.id}')
                assert page.locator('.wb-approval').count()==1
                assert page.locator('body').evaluate(
                    "e=>e.getAttribute('data-growth-design')")=='v4'
                result['checks'].append({'page':'mobile-seo-and-video-links',
                    'width':390,'seo_click_works':True,
                    'video_notification_click_works':True,'same_tab_navigation':True,
                    'no_paid_provider_calls':True})
                page.goto(f'{origin}/studio/products/{product.id}?design=v4', wait_until='load')
                assert page.locator('#dp-mic').count()==0, 'v4 must have no microphone button'
                assert page.locator('#dp-press-hint').count()==0, 'hint belongs inside composer'
                assert page.locator('#dp-input').get_attribute('placeholder')=='Нажмите — текст · удерживайте — голос'
                assert page.locator('#dp-mode-mic').is_hidden(), 'mic should appear only in text mode'
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
                # The Web Audio context must be activated during the user gesture,
                # not from the later getUserMedia promise.
                assert page.evaluate('window.__mockAudioResumes')==1, 'AudioContext not armed on pointerdown'
                pointer('pointerup',11)
                page.wait_for_timeout(270)
                assert page.evaluate('window.__mockMicStarts')==0, 'short tap started audio'
                assert page.evaluate('window.__mockAudioCloses')==1, 'short tap leaked an armed AudioContext'
                assert page.locator('#dp-input').evaluate("el=>document.activeElement===el")
                assert page.locator('#dp-mode-mic').is_visible(), 'typing must reveal mic switch'
                assert page.locator('#dp-input').get_attribute('placeholder')=='Введите команду…'
                assert page.locator('#dp-voice-hitbox').evaluate(
                  "e=>getComputedStyle(e).pointerEvents")=='none', 'typing must allow cursor placement'
                page.locator('#dp-input').fill('Мой незавершённый текст')
                # Holding the editable textarea does not trigger voice recording.
                pointer('pointerdown',20)
                page.wait_for_timeout(270)
                pointer('pointerup',20)
                assert page.evaluate('window.__mockMicStarts')==0, 'typing long press started recording'
                # Tap the small mic once: hide keyboard, preserve text, restore hold gesture.
                page.locator('#dp-mode-mic').dispatch_event(
                    'pointerdown',{'bubbles':True,'cancelable':True,'button':0,'pointerType':'touch','pointerId':51})
                assert page.locator('#dp-mode-mic').is_hidden()
                assert not page.locator('#dp-input').evaluate("el=>document.activeElement===el"), 'keyboard was not dismissed'
                assert page.locator('#dp-voice-hitbox').evaluate(
                  "e=>getComputedStyle(e).pointerEvents")=='auto', 'voice hitbox not restored'
                assert page.locator('#dp-input').input_value()=='Мой незавершённый текст'
                pointer('pointerdown',12)
                assert page.evaluate('window.__mockAudioResumes')==2, 'hold did not arm audio in gesture'
                page.wait_for_function("() => window.__mockMicStarts===1 && document.querySelector('#dp-form').classList.contains('voice-recording')")
                assert page.evaluate('window.__mockAudioContexts')==2, 'recorder opened a second AudioContext'
                page.wait_for_timeout(180)
                active_overlay=page.locator('#dp-voice-overlay').evaluate("""element=>({
                  opacity:parseFloat(getComputedStyle(element).opacity),
                  position:getComputedStyle(element).position,
                  animation:getComputedStyle(element.querySelector('.vg__glow')).animationDuration,
                  label:element.querySelector('#dp-voice-overlay-text').textContent
                })""")
                assert active_overlay['opacity']>.75 and active_overlay['position']=='fixed',active_overlay
                assert active_overlay['animation']=='4.6s',active_overlay
                assert 'Отпустите' in active_overlay['label']
                overlay_geometry=page.locator('#dp-voice-overlay').evaluate("""element=>{
                  const r=element.getBoundingClientRect(),sty=getComputedStyle(element);
                  return {parent:element.parentElement.tagName,top:r.top,left:r.left,
                    width:r.width,height:r.height,viewportWidth:innerWidth,viewportHeight:innerHeight,
                    bg:sty.backgroundColor,dim:getComputedStyle(element,'::before').content,
                    overlayActive:element.classList.contains('is-active')};
                }""")
                assert overlay_geometry['parent']=='BODY',overlay_geometry
                assert abs(overlay_geometry['left'])<2,overlay_geometry
                assert abs(overlay_geometry['top']-(overlay_geometry['viewportHeight']-300))<2,overlay_geometry
                assert abs(overlay_geometry['width']-overlay_geometry['viewportWidth'])<2,overlay_geometry
                assert abs(overlay_geometry['height']-300)<2,overlay_geometry
                assert overlay_geometry['bg']=='rgba(0, 0, 0, 0)',overlay_geometry
                assert overlay_geometry['dim']=='none' and overlay_geometry['overlayActive'],overlay_geometry
                # Mic samples, not randomly animated placeholders, control the bars.
                def waveform_pixels():
                    return page.locator('#vgBars').evaluate("""canvas => {
                      const ctx=canvas.getContext('2d');
                      const data=ctx.getImageData(0,0,canvas.width,canvas.height).data;
                      let count=0;
                      for(let i=3;i<data.length;i+=4) if(data[i]>10) count++;
                      return count;
                    }""")
                quiet=waveform_pixels()
                page.evaluate('window.__mockLevel=3')  # quiet speech was invisible before the fix
                page.wait_for_timeout(220)
                soft=waveform_pixels()
                assert soft>quiet+15,(quiet,soft)
                page.evaluate('window.__mockLevel=36')
                page.wait_for_timeout(260)
                loud=waveform_pixels()
                assert loud>soft+65,(quiet,soft,loud)
                assert page.evaluate('window.__mockMicStarts')==1, 'VoiceGlow opened a second mic'
                # The small knob tracks the real active pointer, not an unrelated animation.
                page.evaluate("""() => document.dispatchEvent(new PointerEvent('pointermove',{
                  bubbles:true,cancelable:true,button:0,pointerType:'touch',pointerId:12,clientX:212,clientY:630
                }))""")
                assert 210<=page.locator('#vgKnob').evaluate("e=>parseFloat(e.style.left)")<=214
                page.screenshot(path=str(OUT/'voice-hold-active-390.png'),animations='allow')
                pointer('pointercancel',12)
                page.wait_for_timeout(220)
                assert page.locator('#dp-voice-overlay').evaluate("e=>parseFloat(getComputedStyle(e).opacity)")<.1
                assert not voice_calls, 'cancelled hold uploaded audio'
                pointer('pointerdown',13)
                page.wait_for_function("() => window.__mockMicStarts===2 && document.querySelector('#dp-form').classList.contains('voice-recording')")
                pointer('pointerup',13)
                page.wait_for_function("() => document.querySelector('#dp-form').classList.contains('voice-processing')")
                assert page.locator('#dp-voice-overlay').evaluate("e=>e.classList.contains('rec')")
                assert 'Распознавание' in page.locator('#dp-voice-overlay-text').inner_text()
                assert page.locator('#dp-voice-status').is_visible()
                assert 'Распознаю и редактирую' in page.locator('#dp-voice-status').inner_text()
                page.wait_for_timeout(200)
                assert page.locator('#dp-voice-overlay').evaluate("e=>parseFloat(getComputedStyle(e).opacity)")>.9
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

                # Physical touch on the contextual mic exits typing mode before
                # the hold gesture, including with an already focused editor.
                page.locator('#dp-input').evaluate("el=>el.focus()")
                page.wait_for_function("() => document.querySelector('#dp-form').classList.contains('voice-text-editing')")
                assert page.locator('#dp-mode-mic').is_visible()
                assert page.locator('#dp-voice-hitbox').evaluate(
                  "e=>getComputedStyle(e).pointerEvents")=='none', 'caret surface should be editable'
                mic_box=page.locator('#dp-mode-mic').bounding_box()
                assert mic_box is not None
                mx=mic_box['x']+mic_box['width']/2
                my=mic_box['y']+mic_box['height']/2
                cdp=context.new_cdp_session(page)
                cdp.send('Input.dispatchTouchEvent',{'type':'touchStart',
                         'touchPoints':[{'x':mx,'y':my,'id':40}]})
                cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
                page.wait_for_function("() => !document.querySelector('#dp-form').classList.contains('voice-text-editing')")
                assert page.locator('#dp-mode-mic').is_hidden()
                assert not page.locator('#dp-input').evaluate("e=>document.activeElement===e")
                assert page.locator('#dp-input').input_value()=='Мой незавершённый текст'
                # Now a real long touch on the hold surface must not select
                # text, open a context menu or cancel the microphone.
                guard_box=page.locator('#dp-voice-hitbox').bounding_box()
                assert guard_box is not None
                hit_x=guard_box['x']+guard_box['width']*.55
                hit_y=guard_box['y']+guard_box['height']*.5
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
                # Voice processing clears immediately before the async dispatch
                # request reaches the mocked HTTP handler. Wait for that
                # observable event instead of relying on a browser scheduler race.
                for _ in range(30):
                    if len(dispatch_calls) >= 3:
                        break
                    page.wait_for_timeout(100)
                assert len(dispatch_calls)==3,dispatch_calls
                # A pending resume rejection for the old session must not
                # close a newly started recording's AudioContext.
                race=page.evaluate("""async () => {
                  const saved=window.AudioContext, contexts=[];
                  class DeferredContext extends saved {
                    constructor(){
                      super();
                      this.state='suspended';
                      this.closed=false;
                      contexts.push(this);
                    }
                    resume(){
                      return new Promise((resolve,reject)=>{this.rejectResume=reject;});
                    }
                    close(){
                      this.closed=true;
                      this.state='closed';
                      return Promise.resolve();
                    }
                  }
                  try {
                    window.AudioContext=DeferredContext;
                    window.VoiceGlow.start({}); // first session
                    const first=contexts[0];
                    window.VoiceGlow.hide();
                    window.VoiceGlow.start({}); // newer session
                    const second=contexts[1];
                    first.rejectResume(new Error('stale resume failure'));
                    await Promise.resolve();
                    await Promise.resolve();
                    const preserved=!second.closed && document.getElementById('dp-voice-overlay-text').textContent==='Отпустите, чтобы отправить';
                    second.rejectResume(new Error('current resume failure'));
                    await Promise.resolve();
                    await Promise.resolve();
                    const currentRejected=second.closed &&
                      document.getElementById('dp-voice-overlay-text').textContent.includes('индикатор звука недоступен');
                    return {preserved,currentRejected,count:contexts.length};
                  } finally {
                    window.VoiceGlow.hide();
                    window.AudioContext=saved;
                  }
                }""")
                assert race['count']==2 and race['preserved'] and race['currentRejected'],race
                assert not [error for error in errors if error.startswith('js:')],errors
                result['checks'].append({'page':'voice-hold-mock','width':390,
                    'short_tap':True,'cancel':True,'automatic_send':True,'retry_idempotent':True,
                    'preserved_typed_draft':True,'nav_aligned':True,
                    'overlay_enter_exit':True,'processing_inside_composer':True,
                    'compact_composer':True,'real_touch_long_press':True,
                    'no_native_text_selection':True,'mic_visible_on_focus':True,
                    'mic_hides_keyboard':True,'text_editable_with_keyboard':True,
                    'typing_draft_preserved':True,'quiet_voice_moves_bars':True,
                    'context_armed_on_pointerdown':True,'short_tap_releases_context':True,
                    'old_resume_error_does_not_cancel_new_recording':True,
                    'current_resume_error_releases_analyser':True})
                page.screenshot(path=str(OUT/'voice-hold-390.png'),animations='disabled')
                # The conversation must not retain the centered empty-home
                # layout, or reserve a separate band for New Topic above chat.
                assert page.locator('body').evaluate("el=>!el.classList.contains('home-empty')")
                assert page.locator('#dp-new').is_visible()
                assert page.locator('#dp-new').evaluate(
                    "el=>el.parentElement.classList.contains('command-topicbar')")
                geometry=page.evaluate("""() => {
                  const main=document.querySelector('#main').getBoundingClientRect(),
                    feed=document.querySelector('#dp-feed').getBoundingClientRect(),
                    composer=document.querySelector('#dp-form').getBoundingClientRect(),
                    topic=document.querySelector('#dp-new').getBoundingClientRect(),
                    toolbar=document.querySelector('.command-tools').getBoundingClientRect();
                  return {gap:feed.top-main.top,topicBottom:topic.bottom,
                          composerTop:composer.top,composerBottom:composer.bottom,
                          toolbarTop:toolbar.top,topicParent:document.querySelector('#dp-new').parentElement.className};
                }""")
                assert geometry['gap']<=45, f"Separate header band still visible: {geometry}"
                assert geometry['topicParent']=='command-topicbar', geometry
                assert geometry['topicBottom']<=geometry['composerTop']+2, geometry
                assert geometry['toolbarTop']>=geometry['composerBottom']-2, geometry
                # The conversation extends behind the floating dock. Scroll padding
                # keeps the last message above the composer as its height changes.
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
                  return document.querySelector('#dp-feed').getBoundingClientRect().bottom
                    >=dock.getBoundingClientRect().bottom-1;
                }""")
                overlay=page.evaluate("""() => {
                  const feed=document.querySelector('#dp-feed'),
                        dock=document.querySelector('.command-dock'),
                        tools=document.querySelector('.command-tools'),
                        hints=document.querySelector('.command-hints'),
                        composer=document.querySelector('#dp-form'),
                        textarea=document.querySelector('#dp-input'),
                        bar=document.querySelector('.topbar');
                  feed.scrollTop=feed.scrollHeight;
                  const fr=feed.getBoundingClientRect(), dr=dock.getBoundingClientRect();
                  const cr=composer.getBoundingClientRect();
                  const transparent=el=>getComputedStyle(el).backgroundColor;
                  return {feedBottom:fr.bottom,dockTop:dr.top,dockBottom:dr.bottom,
                    feedScrollHeight:feed.scrollHeight,feedClientHeight:feed.clientHeight,
                    dockHeight:dr.height,reserved:parseFloat(getComputedStyle(feed).paddingBottom),
                    dockBg:transparent(dock),toolsBg:transparent(tools),
                    hintsBg:transparent(hints),composerBg:transparent(composer),
                    mainBg:transparent(document.querySelector('#main')),
                    workspaceBg:transparent(document.querySelector('.workspace')),
                    headerBorder:getComputedStyle(bar).borderBottomWidth,
                    lastBottom:feed.lastElementChild.getBoundingClientRect().bottom,
                    composerWidth:cr.width,homeWidth:document.querySelector('.dp-home').getBoundingClientRect().width,
                    placeholderFont:parseFloat(getComputedStyle(textarea,'::placeholder').fontSize),
                    placeholderOpacity:getComputedStyle(textarea,'::placeholder').opacity,
                    screenOverflow:document.documentElement.scrollWidth-innerWidth,
                    dockPosition:getComputedStyle(dock).position};
                }""")
                assert overlay['dockBg']=='rgba(0, 0, 0, 0)',overlay
                assert overlay['toolsBg']=='rgba(0, 0, 0, 0)',overlay
                assert overlay['hintsBg']=='rgba(0, 0, 0, 0)',overlay
                assert overlay['composerBg']==overlay['mainBg'],overlay
                assert overlay['dockPosition']=='absolute',overlay
                assert overlay['headerBorder']=='0px',overlay
                assert overlay['mainBg']==overlay['workspaceBg'],overlay
                assert overlay['feedBottom']>=overlay['dockBottom']-1,overlay
                assert overlay['dockBottom']>overlay['dockTop'],overlay
                assert overlay['reserved']>=overlay['dockHeight']+8,overlay
                assert overlay['feedScrollHeight']>overlay['feedClientHeight'],overlay
                assert overlay['lastBottom']<=overlay['dockTop']+3,overlay
                assert overlay['composerWidth']>=overlay['homeWidth']-4,overlay
                assert overlay['placeholderFont']>=12.5,overlay
                assert float(overlay['placeholderOpacity'])>=.8,overlay
                assert overlay['screenOverflow']<=2,overlay
                page.screenshot(path=str(OUT/'transparent-floating-composer-390.png'),animations='disabled')
                result['checks'].append({'page':'transparent-floating-composer-390',
                    'dock_transparent':True,'feed_scrolls_behind_dock':True,
                    'latest_message_readable':True,'no_header_strip':True,
                    'placeholder_integrated':True})
                page.route('**/studio/products/*/dispatch/new',
                           lambda route:route.fulfill(status=200,content_type='application/json',
                               body=json.dumps({'hints':[]})))
                page.locator('#dp-new').click()
                page.wait_for_function("() => document.body.classList.contains('home-empty') && !document.querySelector('.dp-home').classList.contains('has-feed')")
                assert page.locator('#dp-new').is_hidden()
                assert not page.locator('#dp-input').evaluate("e=>document.activeElement===e"), 'New Topic opened keyboard'
                assert page.locator('#dp-input').input_value()=='', 'New Topic retained old draft'
                result['checks'].append({'page':'topic-toolbar-390','no_blank_header_band':True,
                    'new_topic_above_composer':True,'no_keyboard_autofocus':True,'empty_state_restored':True})
                # SEO route must remain compact and readable on a real dark-mode
                # browser; calculate icon centers, not just class-name presence.
                page.goto(f'{origin}/studio/products/{product.id}/seo?design=v4', wait_until='load')
                for width in (360, 390, 780, 1280):
                    page.set_viewport_size({'width':width,'height':844})
                    page.evaluate("document.documentElement.dataset.theme='dark'")
                    box=page.locator('.sg-notifications>summary')
                    assert box.is_visible(), f'Notification control hidden at {width}px'
                    geo=box.evaluate("""el => {
                      const a=el.getBoundingClientRect(), b=el.querySelector('svg').getBoundingClientRect();
                      return {dx:Math.abs((a.left+a.right-b.left-b.right)/2),
                              dy:Math.abs((a.top+a.bottom-b.top-b.bottom)/2),
                              width:a.width,height:a.height};
                    }""")
                    assert geo['dx']<=1 and geo['dy']<=1, f'Off-center bell at {width}px: {geo}'
                    assert geo['width']>=43 and geo['height']>=43, geo
                    status=page.locator('.seo-activation').bounding_box()
                    steps=page.locator('.seo-process-overview').bounding_box()
                    assert status and steps and status['y']<steps['y'], (status,steps)
                    assert steps['height']<90, f'SEO stages should start collapsed: {steps}'
                    assert page.locator('.route-content').evaluate(
                        "el=>el.scrollWidth<=el.clientWidth+2"), 'SEO page horizontal overflow'
                    ratio=page.evaluate("""() => {
                      const el=document.querySelector('.seo-activation h2');
                      const title=getComputedStyle(el).color;
                      const bg=getComputedStyle(el.closest('.seo-activation')).backgroundColor;
                      const lum = value => {
                        const vals=(value.match(/[\d.]+/g)||[]).slice(0,3).map(x=>Number(x)/255);
                        return vals.map(v=>v<=.04045?v/12.92:Math.pow((v+.055)/1.055,2.4))
                                   .reduce((acc,v,i)=>acc+v*[.2126,.7152,.0722][i],0);
                      };
                      const a=lum(title),b=lum(bg);
                      return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                    }""")
                    assert ratio>=4.5, f'Low contrast SEO heading at {width}px: {ratio}'
                    if width==390:
                        page.screenshot(path=str(OUT/'seo-dark-390.png'),animations='disabled',full_page=True)
                result['checks'].append({'page':'seo-v4-dark',
                    'widths':[360,390,780,1280],'bell_centered':True,
                    'heading_contrast_at_least_4_5':True,'stages_collapsed':True})
                # Validate computed colours on REAL material routes, not only CSS
                # selector presence. Both light and dark must be readable.
                for theme in ("dark", "light"):
                    for material, checks in (
                        (script_material, [
                            (".material-detail-body .article-text", ".material-detail-body .article-text"),
                            (".story-shot b", ".story-shot"),
                            (".story-shot p", ".story-shot"),
                            (".story-prompt", ".story-prompt")]),
                        (post_material, [
                            (".material-detail-body .article-text", ".material-detail-body .article-text"),
                            (".sg-native-post-label", ".sg-post-preview")]),
                        (creative_material, [
                            (".sg-storyboard-card p", ".sg-storyboard-card"),
                            (".sg-storyboard-screen span", ".sg-storyboard-screen")]),
                    ):
                        page.goto(
                            f"{origin}/studio/products/{product.id}/materials/{material.id}?design=v4",
                            wait_until="load")
                        page.evaluate("(mode) => window.ServGoTheme.setPreference(mode)", theme)
                        for selector, background_selector in checks:
                            contrast = page.evaluate("""(input) => {
                              const fgEl=document.querySelector(input.selector);
                              const bgEl=document.querySelector(input.background);
                              if(!fgEl||!bgEl)throw Error('Fixture selector missing: '+input.selector);
                              const fg=getComputedStyle(fgEl).color;
                              const bg=getComputedStyle(bgEl).backgroundColor;
                              const luma=value=>{
                                const parts=(value.match(/[\\d.]+/g)||[]).slice(0,3).map(n=>Number(n)/255);
                                return parts.map(n=>n<=0.04045?n/12.92:Math.pow((n+0.055)/1.055,2.4))
                                  .reduce((sum,n,i)=>sum+n*[0.2126,0.7152,0.0722][i],0);
                              };
                              const a=luma(fg), b=luma(bg);
                              return {ratio:(Math.max(a,b)+0.05)/(Math.min(a,b)+0.05),
                                foreground:fg, background:bg};
                            }""", {"selector": selector, "background": background_selector})
                            assert contrast["ratio"] >= 4.5, (
                                f"{theme}: {selector} contrast {contrast}")
                result["checks"].append({"page":"material-detail-v4",
                    "themes":["dark","light"],"script_post_frames_contrast_at_least_4_5":True})
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
