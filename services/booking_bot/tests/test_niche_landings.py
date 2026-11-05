"""Каждый лендинг ниши рендерится: YAML-данные согласованы с шаблоном niche.html."""
import os
os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from aiohttp.test_utils import TestClient, TestServer

from webapp.server import create_webapp
from webapp.niches import list_niches


class NicheLandingsRenderTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_niche_renders(self):
        async with TestClient(TestServer(create_webapp())) as client:
            for slug in list_niches():
                res = await client.get(f'/{slug}')
                self.assertEqual(res.status, 200, f'/{slug}')

    async def test_offer_is_single_source_for_prices(self):
        """Цены берутся из _offer.yaml: старые 9 900 ₽ нигде не остаются."""
        async with TestClient(TestServer(create_webapp())) as client:
            for slug in list_niches():
                html = await (await client.get(f'/{slug}')).text()
                self.assertNotIn('9 900', html, slug)
                self.assertNotIn('[[', html, slug)
                self.assertIn('990', html, slug)
                self.assertIn('Свободно 3 из 3', html, slug)

    async def test_no_overclaimed_promises(self):
        async with TestClient(TestServer(create_webapp())) as client:
            for slug in list_niches():
                html = await (await client.get(f'/{slug}')).text()
                self.assertNotIn('нескольких соцсет', html, slug)
                self.assertNotIn('обхода блокировок', html, slug)

    async def test_forbidden_words_absent(self):
        banned = ('no-show', 'b17', 'yclients', 'dikidi', 'remonline')
        async with TestClient(TestServer(create_webapp())) as client:
            for path in ['/'] + [f'/{s}' for s in list_niches()] + ['/roadmap']:
                html = (await (await client.get(path)).text()).lower()
                for word in banned:
                    self.assertNotIn(word, html, f'{word} on {path}')

    async def test_root_landing_has_new_offer_and_calculator(self):
        async with TestClient(TestServer(create_webapp())) as client:
            res = await client.get('/')
            self.assertEqual(res.status, 200)
            html = await res.text()
            self.assertIn('Ваша клиентская база стоит', html)
            self.assertIn('data-base-calc', html)
            self.assertNotIn('9 900', html)
            self.assertNotIn('1 490', html)
            self.assertIn('2\u00a0990', html)

    async def test_roadmap_page_renders_with_counts(self):
        async with TestClient(TestServer(create_webapp())) as client:
            res = await client.get('/roadmap')
            self.assertEqual(res.status, 200)
            html = await res.text()
            self.assertIn('Куда движется ServGo', html)
            self.assertEqual(html.count('class="card"'), 24)
            self.assertIn('Работает', html)

    async def test_roadmap_in_sitemap(self):
        async with TestClient(TestServer(create_webapp())) as client:
            xml = await (await client.get('/sitemap.xml')).text()
            self.assertIn('https://promo.servgo.ru/roadmap', xml)


class LandingLeadTagTest(unittest.TestCase):
    def test_free_seat_and_niche_reach_the_message(self):
        from webapp.server import (
            format_landing_request_message, landing_request_tag, validate_landing_request_payload,
        )
        payload = validate_landing_request_payload({
            'name': 'Анна', 'phone': '+7 900 000-00-00', 'niche': 'beauty', 'intent': 'free_seat',
        })
        self.assertEqual(payload['niche'], 'Салоны красоты')
        self.assertEqual(landing_request_tag(payload), '[Бесплатное место · Салоны красоты]')
        message = format_landing_request_message(payload)
        self.assertIn('занять бесплатное место', message)
        self.assertIn('Ниша: Салоны красоты', message)

    def test_unknown_niche_and_intent_are_dropped(self):
        from webapp.server import landing_request_tag, validate_landing_request_payload
        payload = validate_landing_request_payload({
            'name': 'Анна', 'email': 'a@b.ru', 'niche': '../etc', 'intent': 'hack',
        })
        self.assertEqual(payload['niche'], '')
        self.assertEqual(payload['intent'], '')
        self.assertEqual(landing_request_tag(payload), '')

    def test_other_niche(self):
        from webapp.server import validate_landing_request_payload
        payload = validate_landing_request_payload({'name': 'Анна', 'email': 'a@b.ru', 'niche': 'other'})
        self.assertEqual(payload['niche'], 'Другое')


class OfferLoaderTest(unittest.TestCase):
    def test_money_formatting_and_counters(self):
        from webapp.niches import get_offer
        offer = get_offer('beauty')
        self.assertEqual(offer['entry_price_text'], '990\u00a0₽')
        self.assertEqual(offer['studio_price_text'], '2\u00a0990\u00a0₽')
        self.assertEqual(offer['seats_left'], 3)
        self.assertEqual(offer['studio_free_left'], 1)


if __name__ == '__main__':
    unittest.main()
