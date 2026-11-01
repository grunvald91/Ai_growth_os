"""Focused regression coverage for ServGo business-intake persistence and privacy."""
import json
import unittest
from uuid import uuid4

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import create_async_engine

# The webapp package __init__ imports the whole production bot; load this
# isolated module directly so focused tests do not require live bot secrets.
import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location(
    "onboarding_under_test",
    Path(__file__).resolve().parents[1] / "webapp" / "onboarding_requests.py",
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_email_body = _module._email_body
_recent = _module._recent
register_onboarding_routes = _module.register_onboarding_routes
validate_application = _module.validate_application


def example_form():
    return {
        "version": 1,
        "fields": {
            "Название бизнеса": "Тестовая студия",
            "Сфера деятельности": "Студия",
            "Ответственный за запуск": "Мария",
            "Контакт для настройки": "@testowner",
            "ИНН": "1234567890",
            "Email для связи": "owner@example.org",
            "Касса и чеки": "Нужна помощь",
        },
        "services": [{"name": "Стрижка", "duration": "60", "price": "1000"}],
        "staff": [{"name": "Мастер", "role": "Парикмахер"}],
        "hours": {"Понедельник": {"start": "09:00", "end": "18:00", "off": False}},
        "done": {"t1": True, "t2": False},
        "resource_mode": "employee",
        "deposit_mode": "optional",
        "online_mode": "needed",
    }


def payload(form=None, key=None, consent=True):
    return {
        "consent": consent,
        "idempotency_key": key or str(uuid4()),
        "form": form or example_form(),
    }


class ValidationTests(unittest.TestCase):
    def test_accepts_complete_form_without_losing_fields(self):
        data = payload()
        parsed, name, key = validate_application(data)
        self.assertEqual(parsed["fields"]["Касса и чеки"], "Нужна помощь")
        self.assertEqual(parsed["services"][0]["name"], "Стрижка")
        self.assertEqual(name, "Тестовая студия")
        self.assertEqual(key, data["idempotency_key"])

    def test_rejects_missing_consent(self):
        with self.assertRaisesRegex(ValueError, "согласие"):
            validate_application(payload(consent=False))

    def test_rejects_invalid_inn(self):
        p = payload()
        p["form"]["fields"]["ИНН"] = "123"
        with self.assertRaisesRegex(ValueError, "ИНН"):
            validate_application(p)

    def test_rejects_nested_payload_and_bad_email(self):
        p = payload()
        p["form"]["fields"]["Email для связи"] = ["x"]
        with self.assertRaises(ValueError):
            validate_application(p)
        p = payload()
        p["form"]["fields"]["Email для связи"] = "invalid-email"
        with self.assertRaisesRegex(ValueError, "email"):
            validate_application(p)

    def test_link_has_trusted_origin_not_host(self):
        message = _email_body("a" * 32, example_form())
        self.assertIn("https://promo.servgo.ru/onboarding/request/" + "a" * 32, message)
        self.assertNotIn("localhost", message)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _recent.clear()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.mails = []
        self.smtp_works = True

        def mail_sender(data, *, subject, message_body):
            self.mails.append((data, subject, message_body))
            return self.smtp_works

        async def user(request):
            return {"id": 42} if request.headers.get("X-Test-Admin") == "yes" else None

        app = web.Application()
        register_onboarding_routes(
            app, engine=self.engine, get_user=user, admin_ids=[42], send_email=mail_sender
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.assertTrue(app["onboarding_table_ready"])

    async def asyncTearDown(self):
        await self.client.close()
        await self.engine.dispose()

    async def test_submission_and_private_review(self):
        body = payload()
        resp = await self.client.post("/api/onboarding/requests", json=body)
        self.assertEqual(resp.status, 201)
        result = await resp.json()
        self.assertTrue(result["saved"])
        self.assertTrue(result["email_sent"])
        self.assertEqual(len(self.mails), 1)
        message = self.mails[0][2]
        self.assertIn("/onboarding/request/", message)
        application_id = message.split("/onboarding/request/")[1].split("\n")[0]
        self.assertEqual(result["request_id"], application_id[:8].upper())

        public = await self.client.get("/api/onboarding/requests/" + application_id)
        self.assertEqual(public.status, 403)
        public_page = await self.client.get("/onboarding/request/" + application_id)
        self.assertEqual(public_page.status, 403)

        secure = await self.client.get(
            "/api/onboarding/requests/" + application_id,
            headers={"X-Test-Admin": "yes"},
        )
        self.assertEqual(secure.status, 200)
        self.assertEqual(secure.headers["Cache-Control"], "private, no-store, max-age=0")
        view = await secure.json()
        self.assertEqual(view["form"]["fields"]["ИНН"], "1234567890")
        self.assertEqual(view["form"]["services"], body["form"]["services"])

        html = await self.client.get(
            "/onboarding/request/" + application_id,
            headers={"X-Test-Admin": "yes"},
        )
        self.assertEqual(html.status, 200)
        self.assertIn("noindex", html.headers["X-Robots-Tag"])
        self.assertNotIn("Тестовая студия", await html.text())

    async def test_double_submit_does_not_double_email(self):
        body = payload()
        r1 = await self.client.post("/api/onboarding/requests", json=body)
        r2 = await self.client.post("/api/onboarding/requests", json=body)
        self.assertEqual((await r1.json())["request_id"], (await r2.json())["request_id"])
        self.assertEqual(len(self.mails), 1)

    async def test_email_failure_does_not_lose_saved_data(self):
        self.smtp_works = False
        body = payload()
        first = await self.client.post("/api/onboarding/requests", json=body)
        self.assertEqual(first.status, 202)
        result = await first.json()
        self.assertTrue(result["saved"])
        self.assertFalse(result["email_sent"])
        self.smtp_works = True
        second = await self.client.post("/api/onboarding/requests", json=body)
        self.assertEqual(second.status, 201)
        self.assertTrue((await second.json())["email_sent"])

    async def test_invalid_consent_and_origin_cannot_write(self):
        resp = await self.client.post("/api/onboarding/requests", json=payload(consent=False))
        self.assertEqual(resp.status, 400)
        resp = await self.client.post(
            "/api/onboarding/requests",
            json=payload(),
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(resp.status, 403)

    async def test_size_limit(self):
        resp = await self.client.post(
            "/api/onboarding/requests",
            data=json.dumps({"payload": "a" * 140000}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 413)


if __name__ == "__main__":
    unittest.main()
