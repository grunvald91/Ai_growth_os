"""Actual ServGo login handler coverage for the Growth same-origin browser identity.

Running only the Studio ticket test misses failures in the upstream login routes.
No outgoing Telegram requests, database writes or real logins are performed.
"""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

from starlette.responses import Response

from web.api.routes import auth
from web.api.schemas import TelegramAuthIn, VerifyCodeIn
from web.api.studio_identity import COOKIE, issue, read


USER = SimpleNamespace(id=222, telegram_id=456789, full_name="Test B",
                       username=None, phone="+79991112233", email=None)


def cookie(response):
    header = response.headers.get("set-cookie", "")
    assert header.startswith(COOKIE + "="), header
    return header.split("=", 1)[1].split(";", 1)[0]


def patch_telegram_user():
    """Keep real token-building logic; replace only DB/account-link dependencies."""
    stack = ExitStack()
    stack.enter_context(mock.patch.object(auth, "_find_or_create_user_from_telegram",
                                           new=mock.AsyncMock(return_value=USER)))
    stack.enter_context(mock.patch.object(auth, "_join_yandex_account",
                                           new=mock.AsyncMock(return_value=USER)))
    stack.enter_context(mock.patch.object(auth, "_role_for_user",
                                           new=mock.AsyncMock(return_value="admin")))
    stack.enter_context(mock.patch.object(auth, "create_token", return_value="test-jwt"))
    return stack


class LoginIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.keys = mock.patch.dict(os.environ, {"STUDIO_SSO_SECRET": "s" * 40})
        self.keys.start()

    def tearDown(self):
        self.keys.stop()
        auth._pending_codes.clear()

    async def test_telegram_token_helper_is_independent_of_response(self):
        with patch_telegram_user():
            result = await auth._token_for_telegram_user(db=object(), telegram_id=456789)
        self.assertEqual((result.user.id, result.role), (USER.id, "admin"))

    async def test_telegram_widget_login_sets_current_account_cookie(self):
        from web.api.routes.auth import TelegramLoginWidgetIn
        response = Response()
        body = TelegramLoginWidgetIn(id=456789, auth_date=123456,
                                     first_name="Test", hash="a" * 64)
        with patch_telegram_user(), \
             mock.patch.object(auth, "verify_telegram_login", return_value=True), \
             mock.patch.object(auth, "auth_dict_from_payload", return_value={"id": 456789}), \
             mock.patch.object(auth, "_set_shared_auth_cookie", new=mock.AsyncMock()), \
             mock.patch.object(auth, "get_current_user_optional",
                               new=mock.AsyncMock(return_value=None)):
            result = await auth.telegram_login_widget(body, response, db=object(), authorization=None)
        self.assertEqual(result.user.id, USER.id)
        self.assertEqual(read(cookie(response)), USER.id)

    async def test_telegram_webapp_login_sets_current_account_cookie(self):
        response = Response()
        with patch_telegram_user(), \
             mock.patch.object(auth, "_validate_init_data", return_value={"id": 456789}), \
             mock.patch.object(auth, "_shared_auth_from_init_data", return_value=None), \
             mock.patch.object(auth, "get_current_user_optional",
                               new=mock.AsyncMock(return_value=None)):
            result = await auth.telegram_auth(TelegramAuthIn(init_data="signed"),
                                              response, db=object(), authorization=None)
        self.assertEqual(result.user.id, USER.id)
        self.assertEqual(read(cookie(response)), USER.id)

    async def test_telegram_cookie_session_repairs_missing_identity(self):
        from servgo_auth import auth_cookie_name
        from starlette.requests import Request
        request = Request({"type": "http", "method": "GET", "scheme": "https",
                           "path": "/auth/session", "query_string": b"",
                           "headers": [(b"cookie", f"{auth_cookie_name()}=test-shared".encode())]})
        response = Response()
        async def execute(callback):
            return {"telegram_id": 456789}  # Signed shared auth itself is mocked.
        with patch_telegram_user(), mock.patch.object(auth, "run_in_threadpool",
                                                       new=mock.AsyncMock(side_effect=execute)):
            result = await auth.session_from_shared_cookie(request, response, db=object())
        self.assertEqual(result.user.id, USER.id)
        self.assertEqual(read(cookie(response)), USER.id)

    async def test_phone_code_rotates_existing_account_cookie_immediately(self):
        phone = USER.phone
        auth._pending_codes[phone] = ("123456", time.time())
        old = issue(111)
        response = Response()
        with mock.patch.object(auth, "_user_by_phone", new=mock.AsyncMock(return_value=USER)), \
             mock.patch.object(auth, "_join_yandex_account", new=mock.AsyncMock(return_value=USER)), \
             mock.patch.object(auth, "get_current_user_optional",
                               new=mock.AsyncMock(return_value=None)), \
             mock.patch.object(auth, "_role_for_user", new=mock.AsyncMock(return_value="admin")), \
             mock.patch.object(auth, "create_token", return_value="test-jwt"):
            result = await auth.verify_code(VerifyCodeIn(phone=phone, code="123456"),
                                            response, db=object(), authorization=None)
        self.assertEqual(result.user.id, USER.id)
        self.assertEqual(read(old), 111)
        self.assertEqual(read(cookie(response)), USER.id)
        self.assertNotEqual(cookie(response), old)

    async def test_bad_phone_code_does_not_rotate_browser_identity(self):
        phone = USER.phone
        auth._pending_codes[phone] = ("123456", time.time())
        from fastapi import HTTPException
        response = Response()
        with self.assertRaises(HTTPException):
            await auth.verify_code(VerifyCodeIn(phone=phone, code="000000"),
                                   response, db=object(), authorization=None)
        self.assertNotIn("set-cookie", response.headers)


if __name__ == "__main__":
    unittest.main()
