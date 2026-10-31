"""Exercise the live UI locally against an isolated SQLite fixture, no external APIs.

Requires playwright and Chromium. Run: python scripts/check_growth_live.py --browser /usr/bin/chromium
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.models.domain import Product, Task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser')
    args = parser.parse_args()
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    original = dict(app.dependency_overrides)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name='Проверка интерфейса', slug='servgo')
        db.add(product)
        db.commit()
        def session():
            yield db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_settings] = lambda: Settings(app_env='development', _env_file=None)
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic()+15
            while not server.started:
                if time.monotonic()>deadline:
                    raise RuntimeError('Local fixture did not start')
                time.sleep(.05)
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=args.browser)
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(page.url.split('?')[0]+': '+str(e)))
                base = origin+f'/studio/products/{product.id}'
                page.goto(base)
                page.get_by_text('Новый дизайн →', exact=True).click()
                expect(page.locator('.topbar')).to_be_visible()
                for width in (390, 1440):
                    page.set_viewport_size({'width':width,'height':844})
                    for suffix in ('','/create','/work','/materials','/review','/channels',
                                   '/calendar','/analytics','/whiteboard','/seo','/campaigns','/profile','/accounts','/plan','/comments','/speed'):
                        result = page.goto(base+suffix)
                        assert result.status == 200, (suffix, result.status)
                        expect(page.locator('.topbar')).to_be_visible()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width,suffix,'overflow')
                        if width == 390:
                            expect(page.locator('#mobileNav')).to_be_visible()
                            page.locator('#mobileNav summary').click()
                            expect(page.locator('.v4-more')).to_be_visible()
                            page.keyboard.press('Escape')
                for destination in ('/studio/team','/studio/tune','/intelligence?product_id='+str(product.id)):
                    response=page.goto(origin+destination)
                    assert response.status==200, destination
                    expect(page.locator('.topbar')).to_be_visible()
                from app.models.domain import Artifact
                db.add_all([Artifact(product_id=product.id,kind='article',title='ARTICLE_FIXTURE',content='unique-query'),Artifact(product_id=product.id,kind='channel_text',title='POST_FIXTURE',content='other')])
                db.commit()
                page.goto(base+'/materials?kind=article&q=unique-query')
                expect(page.locator('.materials-grid .material-card')).to_have_count(1)
                expect(page.locator('.materials-grid')).to_contain_text('ARTICLE_FIXTURE')
                page.goto(origin+'/studio/tune/work')
                expect(page.get_by_role('link',name='Промпты агентов',exact=True)).to_be_visible()
                page.set_viewport_size({'width':390,'height':844})
                page.goto(base+'?design=v4')
                page.locator('[data-sg-notifications] summary').click()
                expect(page.locator('.sg-notify-popover')).to_be_visible()
                page.locator('[data-sg-notifications] summary').click()
                page.screenshot(path='/tmp/growth-v4-mobile.png',full_page=True)
                page.set_viewport_size({'width':1440,'height':900})
                page.screenshot(path='/tmp/growth-v4-desktop.png',full_page=True)
                page.emulate_media(reduced_motion='reduce')
                assert page.locator('.animate-in').first.evaluate("e=>getComputedStyle(e).animationName") == 'none'
                page.emulate_media(reduced_motion='no-preference')
                page.goto(base+'/create')
                page.screenshot(path='/tmp/growth-v4-create.png',full_page=True)
                page.locator('[name="topic"]').fill('Тест реальной очереди')
                page.locator('form button[type="submit"]').click()
                page.wait_for_url('**/tasks/*')
                assert len(list(db.scalars(select(Task)))) == 1
                expect(page.locator('.topbar')).to_be_visible()
                page.goto(base+'/work')
                expect(page.locator('.v4-records .board-card')).to_have_count(1)
                expect(page.locator('.v4-records .board-card')).to_contain_text('Тест реальной очереди')
                page.goto(base)
                # Deterministic dispatcher chip uses the real authenticated endpoint.
                chip = page.get_by_role('button', name='Что в работе', exact=True)
                with page.expect_response(lambda r: '/dispatch/messages' in r.url) as response:
                    chip.click()
                assert response.value.status == 200
                expect(page.locator('.dp-msg.bot').first).to_be_visible()
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path='/tmp/growth-v4-conversation.png',full_page=True)
                page.locator('#themeTop').click()
                assert page.locator('body').evaluate("e=>e.classList.contains('dark')")
                page.reload()
                assert page.locator('body').evaluate("e=>e.classList.contains('dark')")
                page.get_by_text('Старый дизайн', exact=True).click()
                expect(page.locator('[data-growth-design]')).to_have_count(0)
                expect(page.get_by_text('Новый дизайн →', exact=True)).to_be_visible()
                # A populated paid-video approval, without running any provider.
                from app.content.whiteboard.contracts import WhiteboardRequest
                from app.content.whiteboard.runtime import configured_models
                from app.content.whiteboard.jobs import queue_job
                video=queue_job(db,WhiteboardRequest(product_id=product.id,
                    topic='Почему сенсорный экран телефона перестаёт слушаться пальцев, когда руки мокрые',
                    duration_seconds=60,scenes=6,call_to_action='Подпишитесь на канал'),
                    configured_models(Settings(_env_file=None,whiteboard_storyboard_model="fixture/story",whiteboard_image_model="gemini-3.1-flash-image",whiteboard_speech_model="fixture/speech",whiteboard_regions_model="fixture/regions")),max_api_calls=34,visual_review=True,image_provider='claudehub')
                page.evaluate("localStorage.setItem('servgo-desktop-theme','light')")
                for width in (360,390,1440):
                    page.set_viewport_size({'width':width,'height':844})
                    page.goto(base+'/whiteboard/'+str(video.id)+'?design=v4')
                    expect(page.locator('.wb-approval')).to_be_visible()
                    consent=page.locator('input[name="confirmed"]')
                    expect(consent).not_to_be_checked()
                    assert consent.evaluate('e=>e.required')
                    box=consent.bounding_box()
                    assert box['width']<=24 and box['height']<=24,box
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    expect(page.locator('.wb-cost')).to_contain_text('Точная стоимость')
                    page.locator('.wb-actions .btn.primary').scroll_into_view_if_needed()
                    bounds=page.locator('.wb-actions .btn.primary').bounding_box()
                    nav=page.locator('#mobileNav').bounding_box()
                    if nav: assert bounds['y']+bounds['height']<=nav['y'],(bounds,nav)
                    consent.check()
                    expect(consent).to_be_checked()
                    page.evaluate('scrollTo(0,0)')
                    page.screenshot(path=f'/tmp/v4-video-approval-{width}.png',full_page=True,animations='disabled')
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#themeTop').click()
                assert page.locator('.wb-heading h2').evaluate("e=>getComputedStyle(e).color")=='rgb(237, 243, 251)'
                page.screenshot(path='/tmp/v4-video-approval-dark.png',full_page=True,animations='disabled')
                from app.models.domain import TaskStatus
                page.set_viewport_size({'width':390,'height':844})
                for status in (TaskStatus.QUEUED,TaskStatus.RUNNING,TaskStatus.FAILED,TaskStatus.CANCELLED,TaskStatus.COMPLETED):
                    video.status=status;db.commit()
                    page.goto(base+'/whiteboard/'+str(video.id))
                    expect(page.locator('.wb-job')).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),status
                    page.screenshot(path=f'/tmp/v4-video-{status.value}.png',full_page=True,animations='disabled')
                # Persist a long history, matching the populated production screen.
                from app.dispatcher import service
                conversation=service.open_conversation(db,product.id,'local-owner')
                for i in range(30):
                    service._add(db,conversation,role='assistant',text='Проверка длинной истории',blocks=[
                        {'type':'text','text':f'Результат {i+1}. Проверка длинного диалога.'},
                        {'type':'links','items':[{'label':'Не удалось подготовить материал — проверьте задание','href':f'/studio/products/{product.id}/work'} for _ in range(5)]}])
                db.commit()
                page.evaluate("localStorage.setItem('servgo-desktop-theme','light')")
                for width,height in [(360,740),(390,780),(412,820),(390,400),(1440,900)]:
                    page.set_viewport_size({'width':width,'height':height})
                    page.goto(base+'?design=v4')
                    expect(page.locator('.dp-msg').last).to_be_visible()
                    page.locator('#dp-input').fill('Черновик сохраняется при изменении окна')
                    page.wait_for_function("()=>{const e=document.querySelector('#dp-feed');return e.scrollHeight-e.clientHeight-e.scrollTop<3}")
                    page.screenshot(path=f'/tmp/v4-history-{width}-{height}.png',animations='disabled')
                    metrics=page.evaluate("""()=>{
                      const rect=s=>document.querySelector(s).getBoundingClientRect();
                      const feed=rect('#dp-feed'),form=rect('#dp-form'),dock=rect('.command-dock'),nav=rect('#mobileNav'),top=rect('.topbar');
                      const el=document.querySelector('#dp-feed');
                      return {feedTop:feed.top,feedBottom:feed.bottom,formTop:form.top,dockBottom:dock.bottom,navTop:nav.height?nav.top:innerHeight,
                        mainStyle:getComputedStyle(document.querySelector('#main')).padding,rootStyle:getComputedStyle(document.querySelector('.dp-home')).padding,rootMargin:getComputedStyle(document.querySelector('.dp-home')).margin,headerBottom:top.bottom,overflow:document.documentElement.scrollWidth>innerWidth,scrollable:el.scrollHeight>el.clientHeight};
                    }""")
                    assert metrics['scrollable'] and not metrics['overflow'],metrics
                    assert metrics['feedTop']-metrics['headerBottom']<75,metrics
                    assert metrics['feedBottom']<=metrics['formTop'],metrics
                    assert metrics['dockBottom']<=metrics['navTop']+1,metrics
                    page.locator('#dp-feed').evaluate('e=>e.scrollTop=0')
                    assert page.locator('#dp-feed').evaluate('e=>e.scrollTop')==0
                    expect(page.locator('#dp-input')).to_have_value('Черновик сохраняется при изменении окна')
                assert not errors, errors
                browser.close()
                print('PASS: 32 live page layouts + 3 global pages, queue creation, real dispatcher, theme persistence, both switches, 30-message history at 5 viewport sizes; no JS errors')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original)
    engine.dispose()


if __name__ == '__main__':
    main()
