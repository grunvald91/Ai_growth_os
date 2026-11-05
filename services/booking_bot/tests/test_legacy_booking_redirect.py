"""Старая страница записи (aiohttp webapp) ведёт на новую запись servgo.ru."""
import os
os.environ.setdefault('BOT_TOKEN', '123:test')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import json
import unittest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from webapp import server


class LegacyBookingRedirectTest(unittest.IsolatedAsyncioTestCase):
    async def test_book_page_redirects_with_query(self):
        req = make_mocked_request('GET', '/book?business_id=7&service=3')
        with self.assertRaises(web.HTTPFound) as ctx:
            await server.booking_handler(req)
        self.assertEqual(ctx.exception.location, 'https://servgo.ru/book?business_id=7&service=3')

    async def test_legacy_create_booking_is_closed(self):
        req = make_mocked_request('POST', '/api/client/booking')
        res = await server.api_client_create_booking(req)
        self.assertEqual(res.status, 410)
        self.assertEqual(json.loads(res.text)['booking_url'], 'https://servgo.ru/book')


if __name__ == '__main__':
    unittest.main()
