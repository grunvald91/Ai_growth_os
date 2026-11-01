"""Isolated Mail PWA contract: origin, SSO, role, cookie scope and URL routing.

No SMTP, IMAP, network, production DB, external AI, or campaign launches.
"""
import asyncio
from http.cookies import SimpleCookie
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi import HTTPException
from starlette.requests import Request

from web.api.routes import owner_mail, mail_pwa


OWNER = SimpleNamespace(id=7, telegram_id=777)
SECRET = "z" * 48


def request(host="servgo.ru", *, origin=None, cookie=None, method="GET", scheme="https", fetch_site=None):
    headers = [(b"host", host.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if fetch_site:
        headers.append((b"sec-fetch-site", fetch_site.encode()))
    return Request({
        "type": "http", "method": method, "scheme": scheme,
        "server": (host, 443), "path": "/owner-mail/workspace",
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 49152),
    })


class FakeQueryResult:
    def scalar_one_or_none(self):
        return OWNER


class FakeDB:
    async def execute(self, _query):
        return FakeQueryResult()


def ticket(url):
    return parse_qs(urlsplit(url).query)["ticket"][0]


def check_exception(func, code):
    try:
        func()
    except HTTPException as error:
        assert error.status_code == code, error
    else:
        raise AssertionError("expected HTTPException " + str(code))


class MailPwaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = {
            "SERVGO_MAIL_SESSION_SECRET": SECRET,
            "SERVGO_MAIL_OWNER_USER_ID": "7",
            "SERVGO_MAIL_OWNER_TELEGRAM_ID": "",
            "SERVGO_MAIL_DATA_DIR": str(Path(self.temp.name) / "owner"),
        }
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_default_entry_still_uses_old_domain_and_pwa_route_is_not_exposed(self):
        os.environ.pop("SERVGO_MAIL_GATEWAY_ENABLED", None)
        link = owner_mail.entry(OWNER)["url"]
        self.assertTrue(link.startswith("https://mail.servgo.ru/claim?ticket="))
        self.assertEqual(owner_mail.verify(ticket(link), "mail-entry")["destination"], "legacy")
        check_exception(lambda: mail_pwa._require_pwa(request()), 404)

    def test_canonical_entry_is_opt_in_and_not_an_open_redirect(self):
        with patch.dict(os.environ, {"SERVGO_MAIL_GATEWAY_ENABLED": "true",
                                     "SERVGO_MAIL_URL": "https://attacker.example"}):
            link = owner_mail.entry(OWNER)["url"]
        self.assertTrue(link.startswith("https://servgo.ru/api/owner-mail/claim?ticket="))
        self.assertNotIn("attacker.example", link)
        self.assertEqual(owner_mail.verify(ticket(link), "mail-entry")["destination"], "pwa")

    def test_writes_are_bound_to_the_actual_host_and_origin(self):
        with patch.dict(os.environ, {"SERVGO_MAIL_GATEWAY_ENABLED": "true"}):
            owner_mail.verify_mail_write_origin(request(origin="https://servgo.ru", method="POST"))
            owner_mail.verify_mail_write_origin(request(
                "mail.servgo.ru", origin="https://mail.servgo.ru", method="POST"))
            for host, origin in [
                ("servgo.ru", "https://mail.servgo.ru"),
                ("mail.servgo.ru", "https://servgo.ru"),
                ("evil.example", "https://servgo.ru"),
                ("servgo.ru", "https://evil.example"),
                ("servgo.ru", "http://servgo.ru"),
            ]:
                check_exception(lambda h=host, o=origin: owner_mail.verify_mail_write_origin(
                    request(h, origin=o, method="POST")), 403)
            check_exception(lambda: owner_mail.verify_mail_write_origin(
                request(origin="https://servgo.ru", method="POST", scheme="http")), 403)
            check_exception(lambda: owner_mail.verify_mail_write_origin(
                request(origin=None, method="POST")), 403)
            check_exception(lambda: owner_mail.verify_mail_write_origin(
                request(origin="https://servgo.ru", method="POST", fetch_site="cross-site")), 403)

    def test_one_time_ticket_scope_cookie_and_replay_denial(self):
        with patch.dict(os.environ, {"SERVGO_MAIL_GATEWAY_ENABLED": "true"}):
            link = owner_mail.entry(OWNER)["url"]
            token = ticket(link)
            response = asyncio.run(owner_mail.claim(token, request(), FakeDB()))
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/api/owner-mail/workspace")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")
            cookie = SimpleCookie()
            cookie.load(response.headers["set-cookie"])
            morsel = cookie[owner_mail.COOKIE]
            self.assertEqual(morsel["path"], "/api/owner-mail")
            self.assertTrue(morsel["secure"])
            self.assertTrue(morsel["httponly"])
            self.assertEqual(morsel["samesite"].casefold(), "lax")
            check_exception(lambda: asyncio.run(owner_mail.claim(
                token, request(), FakeDB())), 401)
            check_exception(lambda: asyncio.run(owner_mail.claim(
                ticket(owner_mail.entry(OWNER)["url"]),
                request("mail.servgo.ru"), FakeDB())), 401)

    def test_legacy_ticket_never_works_at_canonical_host_without_flag(self):
        with patch.dict(os.environ, {"SERVGO_MAIL_GATEWAY_ENABLED": ""}):
            legacy = ticket(owner_mail.entry(OWNER)["url"])
            check_exception(lambda: asyncio.run(owner_mail.claim(
                legacy, request(), FakeDB())), 403)
            old = asyncio.run(owner_mail.claim(
                legacy, request("mail.servgo.ru"), FakeDB()))
            self.assertEqual(old.headers["location"], "/")
            self.assertIn("Path=/", old.headers["set-cookie"])

    def test_mail_html_rewrites_only_trusted_source_to_protected_same_origin_urls(self):
        folder = Path(mail_pwa.__file__).parent
        campaigns = mail_pwa.adapt_mail_html((folder / "mail_campaign_dashboard.html").read_text())
        mailbox = mail_pwa.adapt_mail_html((folder / "owner_mail_ui.html").read_text(), mailbox=True)
        self.assertIn('fetch("/api/owner-mail/api/campaigns"+path', campaigns)
        self.assertIn('fetch("/api/owner-mail/api/company-search"+path', campaigns)
        self.assertIn('href="/api/owner-mail/workspace/mailbox#compose"', campaigns)
        self.assertIn('fetch("/api/owner-mail/api"+path', mailbox)
        self.assertIn('href="/api/owner-mail/workspace"', mailbox)
        self.assertIn('location.replace("/mail")', mailbox)
        self.assertNotIn('fetch("/api"+path', mailbox)
        self.assertNotIn('href="/api/mailbox-dashboard', campaigns)
        for page in (campaigns, mailbox):
            self.assertIn("Выйти из почты", page)
            self.assertNotIn("https://mail.servgo.ru/claim", page)

    def test_workspace_protected_by_mail_owner_and_excludes_other_businesses(self):
        with patch.dict(os.environ, {"SERVGO_MAIL_GATEWAY_ENABLED": "true"}):
            valid = owner_mail.sign(OWNER.id, "mail-session", 300)
            client = request(cookie=owner_mail.COOKIE + "=" + valid)
            response = asyncio.run(mail_pwa._workspace(
                client, FakeDB(), "owner_mail_ui.html", mailbox=True))
            self.assertEqual(response.status_code, 200)
            self.assertIn("private, no-store", response.headers["cache-control"])
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
            self.assertIn(b"/api/owner-mail/api", response.body)
            with patch.dict(os.environ, {"SERVGO_MAIL_OWNER_USER_ID": "9"}):
                check_exception(lambda: asyncio.run(mail_pwa._workspace(
                    client, FakeDB(), "owner_mail_ui.html", mailbox=True)), 403)

    def test_canonical_logout_only_revokes_mail_api_cookie(self):
        with patch.dict(os.environ, {"SERVGO_MAIL_GATEWAY_ENABLED": "true"}):
            response = mail_pwa.mail_workspace_logout(request())
        self.assertEqual(response.headers["location"], "/admin")
        self.assertIn("Path=/api/owner-mail", response.headers["set-cookie"])
        self.assertIn("Max-Age=0", response.headers["set-cookie"])


    def test_both_mail_logout_endpoints_clear_the_correct_cookie_scope(self):
        with patch.dict(os.environ, {"SERVGO_MAIL_GATEWAY_ENABLED": "true"}):
            pwa = owner_mail.logout(request())
            old = owner_mail.logout(request("mail.servgo.ru"))
        self.assertEqual(pwa.headers["location"], "/admin")
        self.assertEqual(old.headers["location"], "/")
        self.assertIn("Path=/api/owner-mail", pwa.headers["set-cookie"])
        self.assertIn("Path=/", old.headers["set-cookie"])
        self.assertIn("Max-Age=0", pwa.headers["set-cookie"])
        self.assertIn("Max-Age=0", old.headers["set-cookie"])


if __name__ == "__main__":
    unittest.main()
