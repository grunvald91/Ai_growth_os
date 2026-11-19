"""Tests for ServGo AI Chat (/studio/chat and /studio/api/ai-chat/*)."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
from uuid import UUID

import httpx
from fastapi import Request
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai_chat.client import (
    AiChatOutcomeUnknownError,
    AiChatProviderError,
    ChatStreamChunk,
    GeneratedImageResult,
)
from app.ai_chat.models import AiChatAttachment, AiChatConversation, AiChatMessage, AiChatRequest
from app.api.ai_chat import get_ai_chat_client, get_catalog_transport
from app.api.content_studio_auth import require_studio_auth
from app.core.studio_accounts import create_account
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import CostRecord, Product


class FakeAiChatClient:
    """Deterministic test double for ClaudeHub chat & image generation."""

    def __init__(self) -> None:
        self.chat_calls: list[dict] = []
        self.image_calls: list[dict] = []
        self.chunks: list[ChatStreamChunk] = [
            ChatStreamChunk(delta="Привет, "),
            ChatStreamChunk(delta="это ответ ИИ."),
            ChatStreamChunk(done=True, model="fake-text", input_tokens=20, output_tokens=10),
        ]
        self.raise_on_chat: Exception | None = None
        self.raise_on_image: Exception | None = None
        self.slow_delay_s: float = 0.0

    async def stream_chat(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[dict],
        capabilities,
        conversation_id: str,
        cancel_event: asyncio.Event | None = None,
    ):
        self.chat_calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "messages": messages,
                "conversation_id": conversation_id,
            }
        )
        if self.raise_on_chat is not None:
            raise self.raise_on_chat
        for idx, chunk in enumerate(self.chunks):
            if self.slow_delay_s > 0:
                await asyncio.sleep(self.slow_delay_s)
            if cancel_event is not None and cancel_event.is_set():
                return
            yield ChatStreamChunk(
                delta=chunk.delta,
                done=chunk.done,
                model=model if chunk.done else chunk.model,
                input_tokens=chunk.input_tokens,
                output_tokens=chunk.output_tokens,
            )

    async def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        capabilities,
        size: str | None = None,
    ) -> GeneratedImageResult:
        self.image_calls.append({"model": model, "prompt": prompt, "size": size})
        if self.raise_on_image is not None:
            raise self.raise_on_image
        img = Image.new("RGB", (64, 64), color=(37, 99, 235))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        return GeneratedImageResult(
            png_bytes=raw,
            width=64,
            height=64,
            sha256=hashlib.sha256(raw).hexdigest(),
            model=model,
        )


def _make_png_bytes() -> bytes:
    img = Image.new("RGB", (32, 24), color=(16, 185, 129))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_xlsx_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Продажи"
    ws.append(["Услуга", "Выручка"])
    ws.append(["ТО-1", 45000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        ev_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                ev_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if data_lines:
            events.append((ev_name, json.loads("\n".join(data_lines))))
    return events


@pytest.fixture
def chat_env(tmp_path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)

    company_a = Product(name="Company A", slug="company-a")
    company_b = Product(name="Company B", slug="company-b")
    db.add_all([company_a, company_b])
    db.commit()

    settings = Settings(
        _env_file=None,
        products_root=tmp_path,
        xai_api_key="hub-secret-should-never-leak",
        xai_base_url="https://hub.example/v1",
        ai_chat_max_requests_per_hour=60,
        ai_chat_max_requests_per_day=500,
        ai_chat_daily_spend_limit_usd="10.0",
    )
    fake_client = FakeAiChatClient()
    catalog_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-opus-4-6", "owned_by": "anthropic"},
                    {"id": "gemini-3.1-flash", "owned_by": "google"},
                    {"id": "deepseek-chat", "owned_by": "deepseek"},
                    {"id": "gemini-3.1-flash-image", "owned_by": "google"},
                    {"id": "custom/text-with-image-word", "owned_by": "custom"},
                ]
            },
        )
    )

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_ai_chat_client] = lambda: fake_client
    app.dependency_overrides[get_catalog_transport] = lambda: catalog_transport

    yield db, settings, company_a, company_b, fake_client

    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_chat_page_renders_without_growth_design_wrapper_and_serves_assets(chat_env):
    _, _, company_a, _, _ = chat_env
    with TestClient(app) as client:
        page = client.get(f"/studio/chat?product_id={company_a.id}")
        assert page.status_code == 200
        assert "ServGo AI Chat" in page.text
        assert "servgo-air-tokens.css" in page.text
        assert "ai-chat.css" in page.text
        assert "voice-glow.js" in page.text
        assert "ai-chat.js" in page.text
        # VoiceGlow + hold-to-talk + corrector boot config
        assert 'id="dp-voice-overlay"' in page.text
        assert 'id="vgBars"' in page.text
        assert 'id="vgKnob"' in page.text
        assert 'id="dp-voice-hitbox"' in page.text
        assert 'id="dp-mode-mic"' in page.text
        assert '"/studio/voice/dictate"' in page.text
        assert '"/studio/voice/preview"' in page.text
        assert '"voiceProject": "company-a"' in page.text
        # Must NOT be wrapped by GrowthDesignMiddleware preview shell
        assert "growth-servgo-air.css" not in page.text
        assert "hub-secret-should-never-leak" not in page.text

        css = client.get("/studio/assets/ai-chat.css")
        assert css.status_code == 200
        assert ".ai-sidebar" in css.text
        assert ".voice-hold-overlay.vg" in css.text
        assert "@keyframes vg-breathe" in css.text
        assert "@keyframes sg-composer-wait" in css.text

        js = client.get("/studio/assets/ai-chat.js")
        assert js.status_code == 200
        assert "renderMarkdownInto" in js.text
        assert "setupVoiceInput" in js.text
        assert "Вернуть как сказано" in js.text


def test_chat_requires_auth_and_csrf_when_configured(chat_env):
    db, settings, company_a, _, _ = chat_env
    sec = settings.model_copy(
        update={
            "app_env": "production",
            "studio_admin_username": "owner",
            "studio_admin_password": "secret-pass",
            "studio_public_origin": "https://growth.servgo.ru",
        }
    )
    app.dependency_overrides[get_settings] = lambda: sec

    with TestClient(app) as client:
        unauth_page = client.get("/studio/chat")
        assert unauth_page.status_code == 401

        unauth_api = client.get("/studio/api/ai-chat/models")
        assert unauth_api.status_code == 401

        csrf_fail = client.post(
            "/studio/api/ai-chat/conversations",
            auth=("owner", "secret-pass"),
            headers={"Origin": "https://evil.example"},
            json={"title": "Test", "product_id": str(company_a.id)},
        )
        assert csrf_fail.status_code == 403

        ok = client.post(
            "/studio/api/ai-chat/conversations",
            auth=("owner", "secret-pass"),
            headers={"Origin": "https://growth.servgo.ru"},
            json={"title": "Test", "product_id": str(company_a.id)},
        )
        assert ok.status_code == 201


def test_model_catalog_uses_confirmed_capabilities_and_stale_fallback(chat_env):
    db, settings, _, _, _ = chat_env
    with TestClient(app) as client:
        res = client.get("/studio/api/ai-chat/models")
        assert res.status_code == 200
        payload = res.json()
        assert payload["stale"] is False
        assert payload["split_categories"] is True
        by_id = {m["id"]: m for m in payload["models"]}
        assert by_id["claude-opus-4-6"]["supports_text"] is True
        assert by_id["claude-opus-4-6"]["supports_image_generation"] is False
        assert by_id["gemini-3.1-flash-image"]["supports_image_generation"] is True
        # Having "image" in the name alone MUST NOT grant image generation without confirmed registry entry
        assert "custom/text-with-image-word" not in by_id or (
            by_id["custom/text-with-image-word"]["supports_image_generation"] is False
        )

        # Now simulate ClaudeHub /models outage -> must fall back to saved snapshot with warning
        app.dependency_overrides[get_catalog_transport] = lambda: httpx.MockTransport(
            lambda request: httpx.Response(503, json={"error": "down"})
        )
        stale_res = client.get("/studio/api/ai-chat/models")
        assert stale_res.status_code == 200
        stale_payload = stale_res.json()
        assert stale_payload["stale"] is True
        assert stale_payload["warning"]
        assert len(stale_payload["models"]) >= 3


def test_multi_account_and_tenant_isolation_including_files(chat_env):
    db, settings, company_a, company_b, _ = chat_env
    sec = settings.model_copy(
        update={
            "studio_admin_username": "owner",
            "studio_admin_password": "owner-secret-password",
        }
    )
    app.dependency_overrides[get_settings] = lambda: sec

    pass_a = "pass_a_very_secure"
    pass_b = "pass_b_very_secure"
    create_account(
        sec,
        username="manager_a",
        password=pass_a,
        role="company",
        product_ids=[str(company_a.id)],
        created_by="owner",
    )
    create_account(
        sec,
        username="manager_b",
        password=pass_b,
        role="company",
        product_ids=[str(company_b.id)],
        created_by="owner",
    )

    with TestClient(app) as client:
        # Account A creates conversation & uploads file for company A
        conv_a = client.post(
            "/studio/api/ai-chat/conversations",
            auth=("manager_a", pass_a),
            json={"title": "Секрет компании A", "product_id": str(company_a.id)},
        )
        assert conv_a.status_code == 201
        conv_a_id = conv_a.json()["id"]

        # Account A cannot create a conversation inside company B
        forbidden_company = client.post(
            "/studio/api/ai-chat/conversations",
            auth=("manager_a", pass_a),
            json={"title": "Чужая компания", "product_id": str(company_b.id)},
        )
        assert forbidden_company.status_code == 404

        upload_a = client.post(
            "/studio/api/ai-chat/files",
            auth=("manager_a", pass_a),
            data={"product_id": str(company_a.id)},
            files={"file": ("secret.txt", b"confidential A notes", "text/plain")},
        )
        assert upload_a.status_code == 201
        att_a_id = upload_a.json()["id"]

        # Account B cannot list, read, patch, delete A's conversation or download A's file
        assert all(c["id"] != conv_a_id for c in client.get("/studio/api/ai-chat/conversations", auth=("manager_b", pass_b)).json())
        assert client.get(f"/studio/api/ai-chat/conversations/{conv_a_id}", auth=("manager_b", pass_b)).status_code == 404
        assert client.patch(f"/studio/api/ai-chat/conversations/{conv_a_id}", auth=("manager_b", pass_b), json={"title": "x"}).status_code == 404
        assert client.delete(f"/studio/api/ai-chat/conversations/{conv_a_id}", auth=("manager_b", pass_b)).status_code == 404
        assert client.get(f"/studio/api/ai-chat/files/{att_a_id}", auth=("manager_b", pass_b)).status_code == 404


def test_text_chat_streaming_dedup_quote_switch_model_and_history(chat_env):
    db, _, company_a, _, fake_client = chat_env

    with TestClient(app) as client:
        conv = client.post(
            "/studio/api/ai-chat/conversations",
            json={"product_id": str(company_a.id)},
        ).json()
        conv_id = conv["id"]

        # First turn (auto-title + streaming + client_key dedup)
        turn_body = {
            "content": "Расскажи про три этапа запуска сервиса",
            "mode": "text",
            "model_id": "gemini-3.1-flash",
            "client_key": "dedup-key-1",
        }
        r1 = client.post(f"/studio/api/ai-chat/conversations/{conv_id}/messages", json=turn_body)
        assert r1.status_code == 201
        d1 = r1.json()
        assert d1["deduplicated"] is False
        assert d1["conversation"]["title"].startswith("Расскажи про три этапа")

        # Duplicate submit returns identical IDs without creating duplicate request
        r1_dup = client.post(f"/studio/api/ai-chat/conversations/{conv_id}/messages", json=turn_body)
        assert r1_dup.status_code == 201
        assert r1_dup.json()["deduplicated"] is True
        assert r1_dup.json()["request"]["id"] == d1["request"]["id"]

        # Stream response
        sse_resp = client.get(f"/studio/api/ai-chat/requests/{d1['request']['id']}/events")
        assert sse_resp.status_code == 200
        events = _parse_sse(sse_resp.text)
        ev_names = [e[0] for e in events]
        assert "delta" in ev_names
        assert ev_names[-1] == "done"

        assistant_msg_id = d1["assistant_message"]["id"]

        # Second turn: switch model + attach selected quote from first assistant answer
        r2 = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={
                "content": "Объясни второй этап подробнее",
                "mode": "text",
                "model_id": "gemini-3.1-flash",
                "quote_message_id": assistant_msg_id,
                "quote_text": "это ответ ИИ.",
                "client_key": "dedup-key-2",
            },
        )
        assert r2.status_code == 201
        req2_id = r2.json()["request"]["id"]
        client.get(f"/studio/api/ai-chat/requests/{req2_id}/events")

        # Verify model call received conversation history + quote
        last_call = fake_client.chat_calls[-1]
        assert last_call["model"] == "gemini-3.1-flash"
        joined = json.dumps(last_call["messages"], ensure_ascii=False)
        assert "Расскажи про три этапа запуска сервиса" in joined
        assert "Цитата из ответа:\\n> это ответ ИИ." in joined or "Цитата из ответа:\n> это ответ ИИ." in joined

        # Verify DB persistence of quote linkage and search/rename
        detail = client.get(f"/studio/api/ai-chat/conversations/{conv_id}").json()
        assert len(detail["messages"]) == 4
        user_q_msg = detail["messages"][2]
        assert user_q_msg["quote_message_id"] == assistant_msg_id
        assert user_q_msg["quote_text"] == "это ответ ИИ."

        client.patch(f"/studio/api/ai-chat/conversations/{conv_id}", json={"title": "Этапы запуска"})
        found = client.get("/studio/api/ai-chat/conversations", params={"q": "Этапы"}).json()
        assert any(c["id"] == conv_id for c in found)


def test_capability_validation_no_auto_retry_and_manual_retry(chat_env):
    db, _, company_a, _, fake_client = chat_env

    with TestClient(app) as client:
        conv_id = client.post("/studio/api/ai-chat/conversations", json={"product_id": str(company_a.id)}).json()["id"]

        # Text-only model rejects image mode before calling provider
        bad_mode = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={
                "content": "Нарисуй логотип",
                "mode": "image",
                "model_id": "gemini-3.1-flash",
                "client_key": "bad-mode-1",
            },
        )
        assert bad_mode.status_code == 422
        assert len(fake_client.image_calls) == 0

        # Outcome unknown error triggers ZERO automatic retries and surfaces clear status
        fake_client.raise_on_chat = AiChatOutcomeUnknownError("Таймаут ожидания ответа")
        r = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={
                "content": "Проверь таймаут",
                "mode": "text",
                "model_id": "gemini-3.1-flash",
                "client_key": "timeout-1",
            },
        ).json()
        sse = client.get(f"/studio/api/ai-chat/requests/{r['request']['id']}/events")
        events = _parse_sse(sse.text)
        assert events[-1][0] == "error"
        assert len(fake_client.chat_calls) == 1

        # Manual retry runs once after explicit user click
        fake_client.raise_on_chat = None
        retry_res = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages/{r['assistant_message']['id']}/retry",
            json={"model_id": "gemini-3.1-flash"},
        )
        assert retry_res.status_code == 201
        retry_sse = client.get(f"/studio/api/ai-chat/requests/{retry_res.json()['request']['id']}/events")
        assert _parse_sse(retry_sse.text)[-1][0] == "done"
        assert len(fake_client.chat_calls) == 2


def test_stop_generation_stops_stream_cleanly(chat_env):
    db, _, company_a, _, fake_client = chat_env
    fake_client.slow_delay_s = 0.05

    with TestClient(app) as client:
        conv_id = client.post("/studio/api/ai-chat/conversations", json={"product_id": str(company_a.id)}).json()["id"]
        turn = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={"content": "Длинный ответ", "mode": "text", "model_id": "gemini-3.1-flash", "client_key": "stop-1"},
        ).json()
        req_id = UUID(turn["request"]["id"])

        stop_res = client.post(f"/studio/api/ai-chat/requests/{req_id}/stop")
        assert stop_res.status_code == 200
        assert stop_res.json()["status"] == "stopped"

        sse = client.get(f"/studio/api/ai-chat/requests/{req_id}/events")
        events = _parse_sse(sse.text)
        assert events[-1][0] == "done"
        assert events[-1][1]["status"] == "stopped"


def test_file_uploads_validation_extraction_vision_check_and_cleanup(chat_env):
    db, settings, company_a, _, fake_client = chat_env
    png_bytes = _make_png_bytes()
    xlsx_bytes = _make_xlsx_bytes()

    with TestClient(app) as client:
        # Reject spoofed PNG magic bytes
        fake_png = client.post(
            "/studio/api/ai-chat/files",
            data={"product_id": str(company_a.id)},
            files={"file": ("malware.png", b"NOT A REAL PNG FILE", "image/png")},
        )
        assert fake_png.status_code == 422

        # Reject oversized payload (> 20 MB)
        oversized = client.post(
            "/studio/api/ai-chat/files",
            data={"product_id": str(company_a.id)},
            files={"file": ("huge.txt", b"a" * (20 * 1024 * 1024 + 50), "text/plain")},
        )
        assert oversized.status_code in (413, 422)

        # Valid XLSX + PNG uploads succeed and serve with security headers
        up_xlsx = client.post(
            "/studio/api/ai-chat/files",
            data={"product_id": str(company_a.id)},
            files={"file": ("report.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert up_xlsx.status_code == 201
        xlsx_att = up_xlsx.json()
        assert xlsx_att["extraction_status"] == "ready"

        up_png = client.post(
            "/studio/api/ai-chat/files",
            data={"product_id": str(company_a.id)},
            files={"file": ("photo.png", png_bytes, "image/png")},
        )
        assert up_png.status_code == 201
        png_att = up_png.json()
        assert png_att["is_image"] is True

        dl = client.get(f"/studio/api/ai-chat/files/{png_att['id']}")
        assert dl.status_code == 200
        assert dl.headers.get("X-Content-Type-Options") == "nosniff"
        assert "sandbox" in (dl.headers.get("Content-Security-Policy") or "")

        conv_id = client.post("/studio/api/ai-chat/conversations", json={"product_id": str(company_a.id)}).json()["id"]

        # Model without confirmed vision rejects image attachment pre-send
        no_vis = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={
                "content": "Опиши фото",
                "mode": "text",
                "model_id": "custom/text-no-vision-placeholder",
                "attachment_ids": [png_att["id"]],
                "client_key": "att-bad-model",
            },
        )
        assert no_vis.status_code == 422

        # Vision-capable model receives extracted spreadsheet + data:image part
        ok_turn = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={
                "content": "Проанализируй таблицу и скриншот",
                "mode": "text",
                "model_id": "gemini-3.1-flash",
                "attachment_ids": [xlsx_att["id"], png_att["id"]],
                "client_key": "att-good-1",
            },
        )
        assert ok_turn.status_code == 201
        client.get(f"/studio/api/ai-chat/requests/{ok_turn.json()['request']['id']}/events")
        sent_parts = fake_client.chat_calls[-1]["messages"][-1]["content"]
        assert isinstance(sent_parts, list)
        assert any(p["type"] == "text" and "45000" in p["text"] for p in sent_parts)
        assert any(p["type"] == "image_url" for p in sent_parts)

        # Verify file exists on disk, then deleting conversation removes file from disk too
        db_att = db.get(AiChatAttachment, UUID(xlsx_att["id"]))
        assert db_att is not None
        disk_path = settings.products_root / "_ai_chat" / "files" / db_att.storage_path
        assert disk_path.exists()

        del_res = client.delete(f"/studio/api/ai-chat/conversations/{conv_id}")
        assert del_res.status_code == 204
        assert not disk_path.exists()


def test_image_generation_cost_logging_and_rate_limits(chat_env):
    db, settings, company_a, _, fake_client = chat_env

    with TestClient(app) as client:
        conv_id = client.post("/studio/api/ai-chat/conversations", json={"product_id": str(company_a.id)}).json()["id"]
        turn = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={
                "content": "Минималистичный постер для автосервиса",
                "mode": "image",
                "model_id": "gemini-3.1-flash-image",
                "client_key": "img-gen-1",
            },
        )
        assert turn.status_code == 201
        req_id = turn.json()["request"]["id"]
        sse = client.get(f"/studio/api/ai-chat/requests/{req_id}/events")
        events = _parse_sse(sse.text)
        assert any(e[0] == "image" for e in events)
        assert events[-1][0] == "done"

        detail = client.get(f"/studio/api/ai-chat/conversations/{conv_id}").json()
        assistant_msg = detail["messages"][1]
        assert len(assistant_msg["generated_images"]) == 1
        img_url = assistant_msg["generated_images"][0]["url"]
        img_resp = client.get(img_url)
        assert img_resp.status_code == 200
        assert img_resp.content.startswith(b"\x89PNG\r\n\x1a\n")

        # CostRecord and AiChatRequest recorded usage
        cost_rows = db.scalars(select(CostRecord).where(CostRecord.product_id == company_a.id)).all()
        assert len(cost_rows) == 1
        assert cost_rows[0].category == "ai_chat"
        assert cost_rows[0].provider == "claudehub"

        # Exceeding per-hour limit returns 429 before provider call
        limited_settings = settings.model_copy(update={"ai_chat_max_requests_per_hour": 1})
        app.dependency_overrides[get_settings] = lambda: limited_settings
        blocked = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={
                "content": "Ещё одно изображение",
                "mode": "image",
                "model_id": "gemini-3.1-flash-image",
                "client_key": "img-gen-limit",
            },
        )
        assert blocked.status_code == 429


def test_context_window_truncation_preserves_full_db_history(chat_env):
    db, _, company_a, _, fake_client = chat_env
    with TestClient(app) as client:
        conv_id = client.post("/studio/api/ai-chat/conversations", json={"product_id": str(company_a.id)}).json()["id"]
        # Seed 12 earlier user/assistant turns totaling > 120k chars in DB
        conv_uuid = UUID(conv_id)
        for idx in range(12):
            db.add(
                AiChatMessage(
                    conversation_id=conv_uuid,
                    seq=idx + 1,
                    role="user" if idx % 2 == 0 else "assistant",
                    mode="text",
                    status="completed",
                    content=("Длинный контекст " * 1200)[:20000],
                    model_id="deepseek-chat",
                )
            )
        db.commit()

        turn = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={
                "content": "Короткий финальный вопрос",
                "mode": "text",
                "model_id": "deepseek-chat",
                "client_key": "ctx-trunc-1",
            },
        ).json()
        sse = client.get(f"/studio/api/ai-chat/requests/{turn['request']['id']}/events")
        events = _parse_sse(sse.text)
        meta = next(e[1] for e in events if e[0] == "meta")
        assert meta["context_truncated"] is True
        assert meta["context_omitted_count"] > 0

        # Full history stays intact in DB
        detail = client.get(f"/studio/api/ai-chat/conversations/{conv_id}").json()
        assert len(detail["messages"]) == 14


def test_chatgpt_image_models_and_payloads(chat_env):
    _, settings, _, _, _ = chat_env
    # When ClaudeHub lists gpt-image-1 / dall-e-3 alongside gemini-3.1-flash-image, all confirmed image models appear
    app.dependency_overrides[get_catalog_transport] = lambda: httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-opus-4-6"},
                    {"id": "gemini-3.1-flash-image"},
                    {"id": "gpt-image-1"},
                    {"id": "gpt-image-1.5"},
                    {"id": "dall-e-3"},
                ]
            },
        )
    )
    with TestClient(app) as client:
        res = client.get("/studio/api/ai-chat/models")
        assert res.status_code == 200
        by_id = {m["id"]: m for m in res.json()["models"]}
        for mid in ("gemini-3.1-flash-image", "gpt-image-1", "gpt-image-1.5", "dall-e-3"):
            assert by_id[mid]["supports_image_generation"] is True
            assert by_id[mid]["provider"] in {"Google Gemini", "OpenAI ChatGPT"}

    # When only openai_api_key exposes ChatGPT image models while ClaudeHub lacks them, catalog includes them too
    app.dependency_overrides[get_catalog_transport] = lambda: httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "claude-opus-4-6"}, {"id": "gemini-3.1-flash-image"}]})
    )
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"openai_api_key": "sk-openai-test"})
    with TestClient(app) as client:
        res2 = client.get("/studio/api/ai-chat/models")
        assert res2.status_code == 200
        ids2 = {m["id"] for m in res2.json()["models"]}
        assert "gpt-image-1" in ids2
        assert "gpt-image-2.5-sunburst" in ids2

    # Direct client payload check for gpt-image-1 (no response_format) vs dall-e-3 (response_format=b64_json)
    import asyncio
    import base64
    from io import BytesIO
    from PIL import Image
    from app.ai_chat.client import ClaudeHubAiChatClient
    from app.llm.model_catalog import resolve_model_capabilities

    buf = BytesIO()
    Image.new("RGB", (64, 64), (20, 90, 200)).save(buf, format="PNG")
    b64_png = base64.b64encode(buf.getvalue()).decode("ascii")
    seen_calls: list[tuple[str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_calls.append((str(req.url), json.loads(req.content.decode("utf-8"))))
        return httpx.Response(200, json={"data": [{"b64_json": b64_png}]})

    client_obj = ClaudeHubAiChatClient(
        api_key="hub-key",
        openai_api_key="openai-key",
        claudehub_model_ids={"dall-e-3"},
        transport=httpx.MockTransport(handler),
    )

    async def _run():
        await client_obj.generate_image(
            model="gpt-image-1",
            prompt="Автосервис",
            capabilities=resolve_model_capabilities("gpt-image-1"),
        )
        await client_obj.generate_image(
            model="dall-e-3",
            prompt="Автосервис",
            capabilities=resolve_model_capabilities("dall-e-3"),
        )

    asyncio.run(_run())
    assert seen_calls[0][0].startswith("https://api.openai.com/v1/")
    assert "response_format" not in seen_calls[0][1]
    assert seen_calls[1][0].startswith("https://api.claudehub.fun/v1/")
    assert seen_calls[1][1]["response_format"] == "b64_json"


def test_chat_navigation_links_company_context_and_ui_layout(chat_env):
    db, _, company_a, company_b, _ = chat_env

    def scoped(request: Request):
        request.state.studio_login = "owner-a"
        request.state.studio_product_ids = [str(company_a.id)]

    app.dependency_overrides[require_studio_auth] = scoped
    with TestClient(app) as client:
        page = client.get(f"/studio/chat?product_id={company_a.id}")
        assert page.status_code == 200
        assert 'id="context-chips"' in page.text
        # Contextual company chips sit in the composer, never duplicate inside empty-state
        assert 'id="quick-suggestions"' not in page.text

        boot_raw = page.text.split('<script id="ai-chat-boot" type="application/json">', 1)[1].split("</script>", 1)[0]
        boot = json.loads(boot_raw)
        assert boot["companyContext"]["name"] == company_a.name
        assert [c["id"] for c in boot["companies"]] == [str(company_a.id)]
        assert str(company_b.id) not in boot_raw

        css = client.get("/studio/assets/ai-chat.css").text
        assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css
        assert ".ai-context-chips" in css

        # Check Studio entry points expose link to /studio/chat
        studio_home = client.get(f"/studio/products/{company_a.id}")
        assert studio_home.status_code == 200
        assert f"/studio/chat?product_id={company_a.id}" in studio_home.text
        assert "ИИ-чат" in studio_home.text

        team_page = client.get("/studio/team")
        assert team_page.status_code == 200
        assert "/studio/chat" in team_page.text


def test_specialist_catalog_validation_and_role_persists_in_chat(chat_env):
    """Role is server-owned, scoped to a conversation and independent of the selected model."""
    _, _, company_a, _, fake_client = chat_env
    with TestClient(app) as client:
        catalog_response = client.get("/studio/api/ai-chat/specialists")
        assert catalog_response.status_code == 200
        catalog = catalog_response.json()["specialists"]
        assert {item["id"] for item in catalog} == {"business_consultant", "marketer", "smm_specialist", "sales_manager", "copywriter", "ads_specialist"}
        assert all("filename" not in item and "system_prompt" not in item for item in catalog)
        invalid = client.post(
            "/studio/api/ai-chat/conversations",
            json={"product_id": str(company_a.id), "specialist_id": "user-supplied-prompt"},
        )
        assert invalid.status_code == 422

        expected = {
            "business_consultant": "Бизнес-консультант",
            "marketer": "Маркетолог",
            "smm_specialist": "SMM-специалист",
            "sales_manager": "Менеджер по продажам",
            "copywriter": "Копирайтер",
            "ads_specialist": "Специалист по рекламе",
        }
        for specialist_id, label in expected.items():
            created = client.post(
                "/studio/api/ai-chat/conversations",
                json={"product_id": str(company_a.id), "specialist_id": specialist_id},
            )
            assert created.status_code == 201
            conv = created.json()
            assert conv["specialist_id"] == specialist_id
            conv_id = conv["id"]
            msg = client.post(
                f"/studio/api/ai-chat/conversations/{conv_id}/messages",
                json={"content": "Дай один практический совет", "mode": "text", "model_id": "gemini-3.1-flash"},
            )
            assert msg.status_code == 201
            response = client.get(f"/studio/api/ai-chat/requests/{msg.json()['request']['id']}/events")
            assert response.status_code == 200
            system_prompt = fake_client.chat_calls[-1]["system_prompt"]
            assert label in system_prompt
            assert "ServGo" in system_prompt
            assert "Company A" in system_prompt
            stored = client.get(f"/studio/api/ai-chat/conversations/{conv_id}").json()["conversation"]
            assert stored["specialist_id"] == specialist_id
            changed = client.patch(
                f"/studio/api/ai-chat/conversations/{conv_id}",
                json={"selected_model": "gemini-3.1-flash"},
            )
            assert changed.status_code == 200
            assert changed.json()["specialist_id"] == specialist_id
            no_role_change = client.patch(
                f"/studio/api/ai-chat/conversations/{conv_id}", json={"specialist_id": "marketer"},
            )
            assert no_role_change.status_code == 422

        ordinary = client.post("/studio/api/ai-chat/conversations", json={}).json()
        assert ordinary["specialist_id"] is None
        message = client.post(
            f"/studio/api/ai-chat/conversations/{ordinary['id']}/messages",
            json={"content": "Привет", "mode": "text", "model_id": "gemini-3.1-flash"},
        )
        client.get(f"/studio/api/ai-chat/requests/{message.json()['request']['id']}/events")
        assert "ПРОФИЛЬ ВЫБРАННОГО СПЕЦИАЛИСТА" not in fake_client.chat_calls[-1]["system_prompt"]

def test_retired_specialist_does_not_strand_streaming_request(chat_env):
    """A removed role fails before calling the provider and persists an error."""
    db, _, company_a, _, fake_client = chat_env
    with TestClient(app) as client:
        created = client.post(
            "/studio/api/ai-chat/conversations",
            json={"product_id": str(company_a.id), "specialist_id": "marketer"},
        )
        assert created.status_code == 201
        conv_id = created.json()["id"]
        conv = db.get(AiChatConversation, UUID(conv_id))
        conv.specialist_id = "retired_specialist"
        db.commit()

        turn = client.post(
            f"/studio/api/ai-chat/conversations/{conv_id}/messages",
            json={"content": "Составь план", "mode": "text", "model_id": "gemini-3.1-flash"},
        )
        assert turn.status_code == 201
        request_id = turn.json()["request"]["id"]
        response = client.get(f"/studio/api/ai-chat/requests/{request_id}/events")
        assert response.status_code == 200
        assert any(name == "error" and "специалиста" in payload.get("error", "")
                   for name, payload in _parse_sse(response.text))
        assert fake_client.chat_calls == []
        db.expire_all()
        detail = client.get(f"/studio/api/ai-chat/conversations/{conv_id}").json()
        assert detail["messages"][-1]["status"] == "error"
        assert detail["messages"][-1]["error_message"]


def test_specialist_frontend_has_text_mode_notice_and_isolated_drafts(chat_env):
    with TestClient(app) as client:
        res = client.get("/studio/assets/ai-chat.js")
        assert res.status_code == 200
        assert "specialistId ? " in res.text
        assert "pendingSpecialistKey()" in res.text
        assert "Инструкции специалиста действуют только для текста." in res.text


def test_chat_paid_model_access_is_server_enforced(chat_env):
    from app.ai_chat.model_access import model_access
    db, _, company, _, fake = chat_env
    for expensive in ("claude-sonnet-5-5", "anthropic/claude-opus-4-6",
                      "openai/gpt-5", "gpt-4.1", "grok-4.7", "gemini-2.5-pro"):
        assert model_access(expensive).status == "paid"
        assert model_access(expensive).enabled is False
    for cheap in ("claude-haiku-5-5", "gemini-3.1-flash", "deepseek-chat"):
        assert model_access(cheap).enabled is True
    assert model_access("kimi-k3").status == "review"
    assert model_access("gemini-3.8-flash").status == "review"
    assert model_access("claude-haiku-6").status == "review"
    assert model_access("strange/gpt-4o-mini").status == "review"
    assert model_access("unknown/another/gpt-4o-mini").enabled is False
    with TestClient(app) as client:
        catalog = client.get("/studio/api/ai-chat/models").json()
        entries = {item["id"]: item for item in catalog["models"]}
        assert entries["claude-opus-4-6"]["access_status"] == "paid"
        assert not entries["claude-opus-4-6"]["access_enabled"]
        assert entries["gemini-3.1-flash"]["access_enabled"]
        assert entries[catalog["default_text_model"]]["access_enabled"] is True
        conv_id = client.post("/studio/api/ai-chat/conversations",
                              json={"product_id": str(company.id)}).json()["id"]
        denied = client.post(f"/studio/api/ai-chat/conversations/{conv_id}/messages",
                             json={"mode": "text", "content": "Привет", "model_id": "claude-opus-4-6"})
        assert denied.status_code == 402
        assert not fake.chat_calls
        assert not client.get(f"/studio/api/ai-chat/conversations/{conv_id}").json()["messages"]
        allowed = client.post(f"/studio/api/ai-chat/conversations/{conv_id}/messages",
                              json={"mode": "text", "content": "Привет", "model_id": "gemini-3.1-flash"})
        assert allowed.status_code == 201
        client.get(f"/studio/api/ai-chat/requests/{allowed.json()['request']['id']}/events")
        answer = allowed.json()["assistant_message"]["id"]
        retry = client.post(f"/studio/api/ai-chat/conversations/{conv_id}/messages/{answer}/retry",
                            json={"model_id": "claude-opus-4-6"})
        assert retry.status_code == 402

def test_paid_model_ui_has_disabled_choices(chat_env):
    with TestClient(app) as client:
        js = client.get("/studio/assets/ai-chat.js")
        assert js.status_code == 200
        assert "m.access_enabled !== true" in js.text
        assert "m.access_label" in js.text


def test_new_specialists_are_allowlisted_and_cannot_leak_internal_instructions(chat_env):
    from app.ai_chat.specialists import SPECIALISTS, specialist_catalog, get_specialist_prompt
    from app.ai_chat.schemas import ConversationCreateIn

    expected = {"business_consultant", "marketer", "smm_specialist",
                "sales_manager", "copywriter", "ads_specialist"}
    assert set(SPECIALISTS) == expected
    public = specialist_catalog()
    assert len(public) == len(expected)
    for item in public:
        assert item["id"] in expected
        assert item["starter_prompts"]
        assert not any(key in item for key in ("filename", "prompt", "system_prompt", "filepath"))
        assert item["description"]
        assert ConversationCreateIn(specialist_id=item["id"]).specialist_id == item["id"]
        prompt = get_specialist_prompt(item["id"])
        assert prompt and len(prompt) > 1200 and "ServGo" in prompt
        if item["id"] in {"sales_manager", "copywriter", "ads_specialist"}:
            assert "без разрешения" in prompt or "без реального" in prompt
    assert get_specialist_prompt(None) is None
    with pytest.raises(ValueError):
        get_specialist_prompt("../../../private.txt")
    with TestClient(app) as client:
        second_route = client.get("/api/ai-chat/specialists")
        assert second_route.status_code == 200
        assert len(second_route.json()["specialists"]) == len(expected)


def test_sales_copy_ads_are_different_specialists_with_clear_boundaries():
    from app.ai_chat.specialists import get_specialist_prompt

    sales = get_specialist_prompt("sales_manager")
    copy = get_specialist_prompt("copywriter")
    ads = get_specialist_prompt("ads_specialist")
    assert "возражен" in sales and "запис" in sales
    assert "маркетинговая статья" in copy.lower() and "готовый текст" in copy
    assert "ROAS" in ads and "бюджет" in ads
    assert len({sales, copy, ads}) == 3
