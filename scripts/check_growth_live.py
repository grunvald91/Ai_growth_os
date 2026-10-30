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
                page.set_viewport_size({'width':390,'height':844})
                page.goto(base+'?design=v4')
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
                assert not errors, errors
                browser.close()
                print('PASS: 32 live page layouts + 3 global pages, queue creation, real dispatcher, theme persistence, both switches; no JS errors')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original)
    engine.dispose()


if __name__ == '__main__':
    main()
