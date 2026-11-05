"""Exercise the owner reference UI and touch drawer against isolated local data.

Run with Playwright installed: python scripts/check_studio_reference_ui.py --browser PATH
No model calls, publications or production data are used.
"""
from __future__ import annotations

import argparse
import json
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
from app.models.domain import Product, Artifact, ArtifactStatus


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
            blocks = [{'type':'text','text':text}]
            if seq == 8:
                text = 'Запустил простое видео. Оно появится в ленте, когда будет готово.'
                blocks = [{'type':'text','text':text}, {'type':'links','items':[
                    {'label':'Ход создания','href':base+'/work'}]}]
            db.add(DispatcherMessage(conversation_id=conversation.id, product_id=product.id,
                seq=seq, role='assistant', text=text, blocks=blocks))
        subject = 'Что быстрее упадёт — тяжёлый молоток или лёгкое перо'
        video = base + '/whiteboard/' + str(uuid4())
        material = {'type':'material','artifact_id':str(uuid4()),'kind':'Рисованное видео',
            'title':'Рисованное видео: '+subject,'status':'Ждёт вашего решения',
            'excerpt':'','href':video}
        db.add(DispatcherMessage(conversation_id=conversation.id, product_id=product.id,
            seq=9, role='assistant', text='Готово: Видео: '+subject+'.', blocks=[
                {'type':'text','text':'Готово: Видео: '+subject+'.'}, material,
                {'type':'links','items':[{'label':'Открыть полностью','href':video}]}]))
        title = 'ИИ для продвижения бизнеса: задачи, ограничения и критерии выбора'
        for kind, channel in [('article','own_site'), ('channel_text','telegram')]:
            db.add(Artifact(product_id=product.id, kind=kind, title=title,
                status=ArtifactStatus.AWAITING_APPROVAL,
                content=json.dumps({'platform':channel,'body':'# '+title+'\n\n'+
                    'Владелец небольшого сервисного бизнеса тратит часы на переписку и планирование записей. '*8})))
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
                page.goto(origin + '/studio')
                assert page.url == origin + base
                assert page.get_by_text('Ваши компании',exact=True).count() == 0
                page.goto(origin + '/studio?choose=1')
                expect(page.locator('#sgOrgMenu')).to_be_visible()
                expect(page.locator('#sgOrgMenu .sg-org-option')).to_have_count(1)
                page.locator('#bizBtn').click()
                assert page.locator('#openSearch,#themeTop').count() == 0
                assert page.get_by_text('Старый дизайн',exact=True).count() == 0
                assert page.locator('#dp-mic').count() == 0
                assert page.locator('#vgKnob').count() == 1
                assert page.locator('.dp-material').count() == 1
                assert page.locator('.dp-material-kind').inner_text() == 'Простое видео'
                assert page.locator('.dp-material-status').inner_text() == 'На проверке'
                assert page.locator('.dp-material-title').inner_text() == subject
                assert page.get_by_text('Готово: Видео: '+subject+'.',exact=True).count() == 0
                assert page.get_by_text('Открыть полностью',exact=True).count() == 0
                expect(page.locator('.dp-msg--progress .dp-text')).to_have_text('Видео создаётся')
                assert page.get_by_role('link',name='Ход создания',exact=True).get_attribute('href') == base+'/work'
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
                        if width <= 780:
                            expect(page.locator('#mobileNav')).to_have_css('background-color', 'rgb(17, 26, 41)' if mode == 'dark' else 'rgb(255, 255, 255)')
                        assert bar.evaluate('(el)=>getComputedStyle(el).backgroundColor') != 'rgba(0, 0, 0, 0)'
                        if width <= 780:
                            expect(page.locator('.topbar .mobile-brand')).to_be_visible()
                            expect(page.locator('#crumbTitle')).to_be_hidden()
                        bounds = page.evaluate("""() => ({
                          feed:document.querySelector('#dp-feed').getBoundingClientRect().bottom,
                          dock:document.querySelector('.command-dock').getBoundingClientRect().top,
                          dockBottom:document.querySelector('.command-dock').getBoundingClientRect().bottom,
                          tab:document.querySelector('.sg-daily-tab').getBoundingClientRect().right,
                          card:document.querySelector('.dp-material').getBoundingClientRect().left
                        })""")
                        assert bounds['feed'] >= bounds['dockBottom'] - 1, (mode,width,bounds)
                        assert bounds['tab'] < bounds['card'], (mode,width,bounds)
                        assert page.locator('.dp-material--video').get_attribute('href') == video
                        assert page.locator('.dp-material--video a').count() == 0
                        thumb=page.locator('.dp-video-preview').bounding_box()
                        assert abs(thumb['width']/thumb['height']-9/16)<.01, thumb
                        assert page.locator('#dp-send').evaluate('(el)=>getComputedStyle(el).borderRadius') == '12px'
                        assert page.locator('#dp-new').bounding_box()['y'] < bar.bounding_box()['y']
                        assert page.locator('#dp-chips').bounding_box()['y'] >= bar.bounding_box()['y'] + bar.bounding_box()['height']
                        if width == 390:
                            page.screenshot(path=str(captures / f'home-{mode}-390.png'))
                page.set_viewport_size({'width':390,'height':844})
                page.evaluate("window.ServGoTheme.setPreference('dark')")
                # Android resize-visual: layout remains 844px while the visible
                # viewport shrinks. A normal Playwright viewport resize misses this.
                page.locator('#dp-input').focus()
                page.locator('#dp-input').fill('Первая строка\nВторая строка')
                page.evaluate("""() => {
                  window.testVisibleHeight=410;window.testVisibleOffset=0;
                  Object.defineProperty(visualViewport,'height',{configurable:true,get:()=>window.testVisibleHeight});
                  Object.defineProperty(visualViewport,'offsetTop',{configurable:true,get:()=>window.testVisibleOffset});
                  visualViewport.dispatchEvent(new Event('resize'));
                }""")
                expect(page.locator('body')).to_have_class(__import__('re').compile('sg-keyboard-open'))
                expect(page.locator('#mobileNav')).to_be_hidden()
                expect(page.locator('#sgDailyHandle')).to_be_hidden()
                for offset in (0,35):
                    page.evaluate('(offset)=>{window.testVisibleOffset=offset;visualViewport.dispatchEvent(new Event("scroll"))}', offset)
                    bounds=page.evaluate("""() => {
                      const box=s=>document.querySelector(s).getBoundingClientRect().toJSON();
                      return {dock:box('.command-dock'),form:box('#dp-form'),input:box('#dp-input'),
                        chips:box('#dp-chips'),feed:box('#dp-feed'),header:box('.topbar'),height:innerHeight};
                    }""")
                    assert bounds['height']==844, bounds
                    assert bounds['header']['width']==390, bounds
                    assert 410+offset-14 <= bounds['dock']['bottom'] <= 410+offset, bounds
                    assert bounds['feed']['bottom'] >= bounds['dock']['bottom']-1, bounds
                    assert bounds['form']['x']>=12 and bounds['form']['right']<=378, bounds
                    assert bounds['input']['x']-bounds['form']['x']>=10, bounds
                    assert bounds['input']['top']-bounds['form']['top']>=5, bounds
                    assert bounds['chips']['bottom']<=410+offset-4, bounds
                page.screenshot(path=str(captures / 'keyboard-dark-390.png'))
                page.evaluate("""() => {
                  delete visualViewport.height;delete visualViewport.offsetTop;
                  visualViewport.dispatchEvent(new Event('resize'));
                  document.activeElement.blur();
                }""")
                expect(page.locator('#mobileNav')).to_be_visible()
                assert page.locator('#dp-input').input_value()=='Первая строка\nВторая строка'
                page.locator('#dp-input').fill('')
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
                # Closing must start on the actual clickable stats/buttons too,
                # not only on an empty part of the drawer.
                for selector in ('.sg-daily-stat:nth-child(2)', '#sgDailyClose'):
                    page.locator('#sgDailyHandle').click()
                    page.wait_for_function('Math.abs(document.querySelector("#sgDailyDrawer").getBoundingClientRect().x-16)<1')
                    rect=page.locator(selector).bounding_box()
                    x=rect['x']+rect['width']/2;y=rect['y']+rect['height']/2
                    touch('touchStart',x,y);touch('touchMove',x-100,y);touch('touchEnd')
                    expect(page.locator('#sgDailyDrawer')).to_have_attribute('aria-hidden','true')
                    assert page.url == origin + base + '?companies=1'
                page.locator('#sgDailyHandle').click()
                page.locator('.sg-daily-stat').nth(1).click()
                page.wait_for_url('**/review')
                for mode in ('dark','light'):
                    page.evaluate('(mode)=>window.ServGoTheme.setPreference(mode)',mode)
                    for width in (320,390,430):
                        page.set_viewport_size({'width':width,'height':844})
                        assert page.locator('.sg-review-item').count()==2
                        item=page.locator('.sg-review-item').first
                        actions=item.locator('.sg-inline-actions .btn')
                        a,b=actions.nth(0).bounding_box(),actions.nth(1).bounding_box()
                        assert abs(a['y']-b['y'])<=1 and b['x']>=a['x']+a['width']+8,(width,a,b)
                        assert 44<=a['height']<=48 and 44<=b['height']<=48,(width,a,b)
                        assert item.locator('p').bounding_box()['height']<90
                        assert not item.locator('p').inner_text().startswith('#')
                        assert page.locator('.sg-filters').bounding_box()['height']<=44
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        if width==390: page.screenshot(path=str(captures / f'review-{mode}-390.png'))
                page.set_viewport_size({'width':390,'height':844})
                page.evaluate("window.ServGoTheme.setPreference('dark')")
                page.goto(origin + base)
                # Theme remains available in More; navigation still opens real pages.
                page.locator('#mobileNav summary').click()
                page.locator('#mobileNav [data-sg-theme]').click()
                expect(page.locator('html')).to_have_attribute('data-theme','light')
                page.keyboard.press('Escape')
                # Failed thumbnails leave the material readable and actionable.
                page.unroute('**/files/video.png')
                page.route('**/files/video.png', lambda route: route.fulfill(status=404))
                page.reload()
                expect(page.locator('.dp-material--no-preview')).to_be_visible()
                assert page.locator('.dp-video-preview').count() == 0
                assert page.locator('.dp-material--video').get_attribute('href') == video
                expect(page.locator('.dp-material-title')).to_have_text(subject)
                # Canonical dropdown expands the list through the existing
                # JWT/ticket contract, including a company not yet connected.
                page.route('**/organizations.json', lambda route: route.fulfill(json={
                    'items':[{'id':str(product.id),'name':product.name}], 'can_add':False,
                    'unified_user_id':7,'linked_business_ids':[12], 'current_business_ids':[12],
                    'current_product_id':str(product.id), 'platform_product':None}))
                page.route('**/api/studio/businesses', lambda route: route.fulfill(json=[
                    {'id':12,'name':'Авто точка'},{'id':13,'name':'Новая организация'}]))
                page.evaluate("localStorage.setItem('token','fixture.'+btoa(JSON.stringify({sub:7}))+'.fixture')")
                page.locator('#bizBtn').click()
                expect(page.locator('#sgOrgMenu button')).to_have_count(2)
                expect(page.locator('#sgOrgMenu [aria-current="true"]')).to_have_text('Авто точка')
                assert page.locator('#sgOrgMenu .sg-company-choice').count()==0
                assert page.locator('#sgOrgMenu').bounding_box()['height']<230
                page.screenshot(path=str(captures / 'company-dropdown-dark-390.png'))
                issued=[]
                def ticket(route):
                    issued.append(route.request.post_data_json)
                    route.fulfill(json={'url':origin+'/studio/sso?t=fixture'})
                page.route('**/api/studio/ticket', ticket)
                page.route('**/studio/sso?t=fixture', lambda route: route.fulfill(
                    content_type='text/html',body='<main>Fixture SSO entry</main>'))
                page.locator('#sgOrgMenu button').filter(has_text='Новая организация').click()
                page.wait_for_url('**/studio/sso?t=fixture')
                assert issued==[{'business_id':13}]
                for suffix in ('/create','/work','/materials','/calendar','/profile','/seo'):
                    response = page.goto(origin + base + suffix)
                    assert response.status == 200, suffix
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), suffix
                response=page.goto(origin + '/studio/new')
                assert response.status==200
                page.locator('#bizBtn').click()
                expect(page.locator('#sgOrgMenu button')).to_have_count(2)
                assert not errors, errors
                browser.close()
                print('PASS: reference surfaces, root resume/dropdown, resize-visual keyboard with offset, compact review actions in both themes, drawer swipe closing on links/buttons, previews, touch cancellation, edge/vertical scroll and 6 routes')
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            app.dependency_overrides.clear()
            app.dependency_overrides.update(overrides)
    engine.dispose()


if __name__ == '__main__':
    main()
