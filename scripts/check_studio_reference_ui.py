"""Exercise the owner reference UI and touch drawer against isolated local data.

Run with Playwright installed: python scripts/check_studio_reference_ui.py --browser PATH
No model calls, publications or production data are used.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys
import threading
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.main import app
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.dispatcher.models import DispatcherConversation, DispatcherMessage
from app.models.domain import Product


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser')
    parser.add_argument('--screenshots', default='/tmp/studio-reference-ui')
    args = parser.parse_args()
    captures = Path(args.screenshots)
    captures.mkdir(parents=True, exist_ok=True)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{sock.getsockname()[1]}'
    overrides = dict(app.dependency_overrides)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name='Авто точка', slug='servgo')
        db.add(product)
        db.flush()
        conversation = DispatcherConversation(product_id=product.id, account='local-owner')
        db.add(conversation)
        db.flush()
        base = f'/studio/products/{product.id}'
        for seq in range(1, 9):
            text = f'Сообщение {seq}. Команда готовит материалы для бизнеса.'
            db.add(DispatcherMessage(conversation_id=conversation.id, product_id=product.id,
                seq=seq, role='assistant', text=text, blocks=[{'type':'text','text':text}]))
        subject = 'Что быстрее упадёт — тяжёлый молоток или лёгкое перо'
        video = base + '/whiteboard/' + str(uuid4())
        material = {'type':'material','artifact_id':str(uuid4()),'kind':'Рисованное видео',
            'title':'Рисованное видео: '+subject,'status':'Ждёт вашего решения',
            'excerpt':'','href':video}
        db.add(DispatcherMessage(conversation_id=conversation.id, product_id=product.id,
            seq=9, role='assistant', text='Готово: Видео: '+subject+'.', blocks=[
                {'type':'text','text':'Готово: Видео: '+subject+'.'}, material,
                {'type':'links','items':[{'label':'Открыть полностью','href':video}]}]))
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_settings] = lambda: Settings(app_env='development', _env_file=None)
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if time.monotonic() > deadline:
                    raise RuntimeError('Fixture server failed to start')
                time.sleep(.05)
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=args.browser)
                context = browser.new_context(viewport={'width':390,'height':844}, has_touch=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                # A genuine PNG response tests the preview presentation, without a render job.
                from PIL import Image
                import io
                cover = io.BytesIO()
                Image.new('RGB', (180, 320), '#273a53').save(cover, format='PNG')
                page.route('**/files/video.png', lambda route: route.fulfill(
                    status=200, content_type='image/png', body=cover.getvalue()))
                page.goto(origin + base + '?design=v4')
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','false')
                page.locator('#sgDailyClose').click()
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','true')
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('inert','')
                assert page.locator('#openSearch,#themeTop').count() == 0
                assert page.get_by_text('Старый дизайн',exact=True).count() == 0
                assert page.locator('#dp-mic').count() == 0
                assert page.locator('#vgKnob').count() == 1
                assert page.locator('.dp-material').count() == 1
                assert page.locator('.dp-material-top').inner_text() == 'Простое видео\nНа проверке'
                assert page.locator('.dp-material-title').inner_text() == subject
                assert page.get_by_text('Готово: Видео: '+subject+'.',exact=True).count() == 0
                assert page.get_by_text('Открыть полностью',exact=True).count() == 0
                expect(page.locator('.dp-video-preview img')).to_be_visible()
                for mode in ('dark','light'):
                    page.evaluate('(mode)=>window.ServGoTheme.setPreference(mode)', mode)
                    for width in (320,390,430,1440):
                        page.set_viewport_size({'width':width,'height':844})
                        page.locator('#dp-feed').evaluate('(el)=>el.scrollTop=el.scrollHeight')
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (mode,width,'overflow')
                        bar = page.locator('#dp-form')
                        assert bar.evaluate('(el)=>getComputedStyle(el).borderTopWidth') == '0px'
                        expect(page.locator('body')).to_have_css('background-color', 'rgb(17, 26, 41)' if mode == 'dark' else 'rgb(255, 255, 255)')
                        expect(bar).to_have_css('background-color', bar.evaluate(
                            '(el)=>{const n=document.createElement("i");n.style.color="var(--s-surface-2)";el.append(n);const c=getComputedStyle(n).color;n.remove();return c}'))
                        assert page.locator('#dp-send').evaluate('(el)=>getComputedStyle(el).borderRadius') == '12px'
                        assert page.locator('#dp-new').bounding_box()['y'] < bar.bounding_box()['y']
                        assert page.locator('#dp-chips').bounding_box()['y'] >= bar.bounding_box()['y'] + bar.bounding_box()['height']
                        if width == 390:
                            page.screenshot(path=str(captures / f'home-{mode}-390.png'))
                page.set_viewport_size({'width':390,'height':844})
                page.evaluate("window.ServGoTheme.setPreference('dark')")
                # Exercise real Chromium touch events, including native scroll arbitration.
                cdp = context.new_cdp_session(page)
                def touch(kind,x=None,y=None):
                    cdp.send('Input.dispatchTouchEvent', {'type':kind,'touchPoints':[] if x is None else [
                        {'x':x,'y':y,'id':1}], 'modifiers':0})
                page.locator('#dp-feed').evaluate('(el)=>el.scrollTop=0')
                touch('touchStart',100,400)
                touch('touchMove',160,400)
                expect(page.locator('body')).to_have_class(__import__('re').compile('sg-daily-dragging'))
                first = page.locator('#sgDailyDrawer').bounding_box()['x']
                touch('touchMove',230,400)
                second = page.locator('#sgDailyDrawer').bounding_box()['x']
                assert 65 < second-first < 75, ('finger synchronization', first, second)
                touch('touchMove',300,400)
                touch('touchEnd')
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','false')
                assert page.locator('.shell').evaluate('(el)=>el.inert')
                # Cancelled touch on an open drawer restores its existing state.
                touch('touchStart',180,300)
                touch('touchMove',140,300)
                touch('touchCancel')
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','false')
                page.screenshot(path=str(captures / 'daily-dark-390.png'))
                touch('touchStart',320,500); touch('touchMove',40,500); touch('touchEnd')
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','true')
                assert not page.locator('.shell').evaluate('(el)=>el.inert')
                # A short drag returns the drawer; edge and vertical gestures do not open it.
                touch('touchStart',100,400); touch('touchMove',125,400); touch('touchEnd')
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','true')
                touch('touchStart',5,250); touch('touchMove',200,250); touch('touchEnd')
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','true')
                touch('touchStart',180,350); touch('touchMove',182,250); touch('touchEnd')
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','true')
                page.locator('#sgDailyHandle').click()
                expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','false')
                page.locator('#sgDailyClose').click()
                # Theme remains available in More; navigation still opens real pages.
                page.locator('#mobileNav summary').click()
                page.locator('#mobileNav [data-sg-theme]').click()
                expect(page.locator('html')).to_have_attribute('data-theme','light')
                page.keyboard.press('Escape')
                for suffix in ('/create','/work','/materials','/calendar','/profile','/seo'):
                    response = page.goto(origin + base + suffix)
                    assert response.status == 200, suffix
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), suffix
                assert not errors, errors
                browser.close()
                print('PASS: reference surfaces, preserved controls, preview, 8 viewport/theme combinations, real touch drag, cancellation, edge/vertical scroll and 6 routes')
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            app.dependency_overrides.clear()
            app.dependency_overrides.update(overrides)
    engine.dispose()


if __name__ == '__main__':
    main()
