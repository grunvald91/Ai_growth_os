import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.comments import service as service_dependency
from app.comments.contracts import AgentSettings, CommentInput, ConnectInput, Fact
from app.comments.gateway import CommentGateway, PlatformError
from app.comments.models import BusinessComment, CommentChannel
from app.comments.service import CommentService
from app.core.config import Settings
from app.db.models import Base
from app.db.session import get_db
from app.llm.fake import FakeLLMProvider, LLMResponse, LLMUsage
from app.llm.service import LLMService
from app.main import app
from app.models.domain import AgentRun, Approval, Artifact, CostRecord, Product, Publication, Task, ToolCall
from app.privacy import PrivacyGateway


class Gateway:
    def __init__(self):
        self.sent = []
        self.fail = False

    async def check(self, platform, account_id, token):
        if self.fail:
            raise PlatformError("Нет доступа")
        return "Канал бизнеса"

    async def fetch(self, channel, token):
        return [{"external_id": "c-1", "text": "Сколько стоит?", "post_text": "О сервисе",
                 "target": {"video_id": "video", "published_at": datetime.now(timezone.utc).isoformat()}}], {"page": None}

    async def send(self, channel, token, comment, text):
        self.sent.append(text)
        if self.fail:
            raise TimeoutError("secret must not escape")
        return "reply-1"


@pytest.fixture
def ctx(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    product = Product(name="ServGo", slug="servgo")
    db.add(product); db.commit()
    root = tmp_path / "servgo"
    root.mkdir()
    (root / "company-profile.json").write_text(json.dumps({"schema_version": "1.0", "identity": {"name": "ServGo"},
        "proof": {"approved_facts": [{"statement": "Тариф стоит 1000 рублей"}]},
        "hypotheses": [{"statement": "SPECULATIVE", "evidence_refs": []}]}))
    # Minimal strict profile without speculative data when a contract changes.
    data = json.loads((root / "company-profile.json").read_text())
    data.pop("hypotheses")
    (root / "company-profile.json").write_text(json.dumps(data))
    settings = Settings(products_root=tmp_path, comment_platform_proxy_url="http://foreign-proxy:8891", _env_file=None)
    provider = FakeLLMProvider(LLMResponse(model="fake", usage=LLMUsage(input_tokens=120, output_tokens=70), content=json.dumps({"intent": "question", "action": "draft",
        "reply": "Тариф стоит 1000 рублей. Для какой команды выбираете?", "reason": "Цена из профиля",
        "used_fact_ids": ["profile.proof"], "missing_information": []})))
    gateway = Gateway()
    svc = CommentService(db, settings, llm=LLMService(privacy_gateway=PrivacyGateway(), provider=provider), gateway=gateway)
    yield db, product, svc, provider, gateway
    app.dependency_overrides.clear()
    db.close(); engine.dispose()


def client_for(ctx):
    db, _, svc, _, _ = ctx
    def override_db():
        yield db
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[service_dependency] = lambda: svc
    return TestClient(app)


async def connected(ctx):
    db, product, svc, _, _ = ctx
    channel = await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    await svc.sync(product.id, "youtube")
    comment = db.scalar(select(BusinessComment))
    await svc.draft(product.id, comment.id)
    return channel, comment


@pytest.mark.asyncio
async def test_grounded_agent_persists_audited_draft_and_never_sees_credentials(ctx):
    db, product, svc, provider, gateway = ctx
    channel, comment = await connected(ctx)
    request = provider.last_request
    assert "private-token" not in request.model_dump_json()
    assert "profile.proof" in request.messages[0].content
    assert "Тариф стоит" in request.messages[0].content
    assert comment.status == "draft"
    assert not gateway.sent
    assert db.scalar(select(AgentRun)).status == "completed"
    assert db.scalar(select(CostRecord)).input_tokens == 120
    assert db.get(Artifact, comment.reply_id).kind == "comment_reply"
    encrypted = svc.secrets.path(channel.id).read_bytes()
    assert b"private-token" not in encrypted
    assert svc.secrets.path(channel.id).stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_sync_deduplicates_native_ids_and_scopes_products(ctx):
    db, product, svc, _, _ = ctx
    await connected(ctx)
    assert await svc.sync(product.id, "youtube") == 0
    second = Product(name="Other", slug="other"); db.add(second); db.commit()
    with pytest.raises(LookupError):
        svc.comment(second.id, db.scalar(select(BusinessComment)).id)
    assert len(list(db.scalars(select(BusinessComment)))) == 1


@pytest.mark.asyncio
async def test_explicit_final_text_approval_and_idempotent_delivery(ctx):
    db, product, svc, _, gateway = ctx
    _, comment = await connected(ctx)
    assert await svc.send(product.id, comment.id, "Проверенный владельцем ответ") == "reply-1"
    assert gateway.sent == ["Проверенный владельцем ответ"]
    approval = db.scalar(select(Approval))
    assert approval.status == "approved" and approval.risk_level == "YELLOW"
    assert db.get(Artifact, approval.artifact_id).content == gateway.sent[0]
    assert db.scalar(select(ToolCall).where(ToolCall.operation == "publish")).status == "executed"
    assert db.scalar(select(Publication)).external_reference == "reply-1"
    from app.services.artifacts import ArtifactService
    with pytest.raises(ValueError, match="immutable"):
        ArtifactService(db).update(approval.artifact_id, content="Changed after approval")
    with pytest.raises(ValueError):
        await svc.send(product.id, comment.id, "Повтор")
    assert len(gateway.sent) == 1


@pytest.mark.asyncio
async def test_timeout_is_durable_unknown_and_never_retried(ctx):
    db, product, svc, _, gateway = ctx
    _, comment = await connected(ctx)
    gateway.fail = True
    with pytest.raises(ValueError, match="повторная отправка заблокирована"):
        await svc.send(product.id, comment.id, "Ответ")
    assert comment.status == "delivery_unknown"
    with pytest.raises(ValueError):
        await svc.send(product.id, comment.id, "Ответ")
    with pytest.raises(ValueError):
        await svc.draft(product.id, comment.id)
    assert len(gateway.sent) == 1


@pytest.mark.asyncio
async def test_changed_business_settings_and_profile_reject_stale_reply(ctx):
    db, product, svc, _, gateway = ctx
    _, comment = await connected(ctx)
    svc.configure(product.id, AgentSettings(tone="professional"))
    with pytest.raises(ValueError, match="изменились"):
        await svc.send(product.id, comment.id, "Ответ")
    await svc.draft(product.id, comment.id)
    path = svc.settings.products_root / "servgo" / "company-profile.json"
    data = json.loads(path.read_text()); data["identity"]["name"] = "New business"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="изменились"):
        await svc.send(product.id, comment.id, "Ответ")
    assert not gateway.sent


@pytest.mark.asyncio
async def test_disconnect_deletes_secret_and_prevents_send(ctx):
    _, product, svc, _, gateway = ctx
    channel, comment = await connected(ctx)
    svc.disconnect(product.id, "youtube")
    assert not svc.secrets.path(channel.id).exists()
    with pytest.raises(ValueError, match="Подключите"):
        await svc.send(product.id, comment.id, "Ответ")
    assert not gateway.sent


@pytest.mark.asyncio
async def test_complaints_require_human_and_manual_comments_never_publish(ctx):
    db, product, svc, _, gateway = ctx
    comment = svc.manual(product.id, CommentInput(platform="tiktok", text="Верните деньги"))
    artifact = await svc.draft(product.id, comment.id)
    assert json.loads(artifact.content)["action"] == "human_review"
    assert comment.status == "review"
    with pytest.raises(ValueError, match="копированием"):
        await svc.send(product.id, comment.id, "Ответ")
    assert not gateway.sent


@pytest.mark.asyncio
async def test_unknown_fact_ids_fail_closed_and_run_is_recorded(ctx):
    db, product, svc, provider, _ = ctx
    provider.response.content = json.dumps({"intent": "question", "action": "draft", "reply": "Бесплатно!",
        "reason": "", "used_fact_ids": ["nonexistent"], "missing_information": []})
    comment = svc.manual(product.id, CommentInput(platform="youtube", text="Цена?"))
    with pytest.raises(ValueError):
        await svc.draft(product.id, comment.id)
    assert not comment.reply_id
    assert db.scalar(select(AgentRun)).status == "failed"


def test_all_tabs_reuse_growth_studio_and_escape_untrusted_input(ctx):
    db, product, svc, _, _ = ctx
    svc.manual(product.id, CommentInput(platform="youtube", text='<script>alert("x")</script>'))
    client = client_for(ctx)
    base = f"/studio/products/{product.id}/comments"
    for tab in ["inbox", "agent", "platforms", "test"]:
        response = client.get(base + "?tab=" + tab)
        assert response.status_code == 200
        assert "Growth Studio" in response.text
        assert 'class="sidebar"' in response.text and 'class="mobile-nav"' in response.text
        assert response.headers["cache-control"] == "no-store"
        assert "/studio/assets/studio.css" in response.text
    inbox = client.get(base).text
    assert '<script>alert("x")</script>' not in inbox
    assert "&lt;script&gt;" in inbox
    assert "зарубежный сервер" in inbox
    assert base in client.get(f"/studio/products/{product.id}").text
    assert "instagram_business_manage_comments" in client.get(base + "?tab=platforms").text


def test_api_product_isolation_csrf_and_secret_validation(ctx):
    client = client_for(ctx); _, product, _, _, _ = ctx
    base = f"/products/{product.id}/comments"
    assert client.post(base + "/manual", json={"platform": "youtube", "text": "Вопрос"}, headers={"origin": "https://evil.invalid"}).status_code == 403
    invalid = client.post(base + "/connect", json={"platform": "wrong", "token": "secret-value", "account_id": "x"})
    assert invalid.status_code == 422 and "secret-value" not in invalid.text
    assert client.post(f"/products/{uuid4()}/comments/manual", json={"platform": "youtube", "text": "Вопрос"}).status_code == 404
    response = client.post(base + "/settings", json=AgentSettings().model_dump())
    assert response.status_code == 200
    assert client.post(base + "/manual", json={"platform": "youtube", "text": "Вопрос"}).status_code == 200


@pytest.mark.asyncio
async def test_gateway_requires_proxy_and_sanitizes_http_errors():
    with pytest.raises(PlatformError, match="зарубежный сервер"):
        await CommentGateway().check("youtube", "UCtest", "token")
    def failed(request):
        raise httpx.ConnectError("secret-token", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        gateway = CommentGateway(client=client)
        with pytest.raises(PlatformError) as caught:
            await gateway.check("telegram", "-100123", "123:secret_token")
        assert "secret_token" not in str(caught.value)


@pytest.mark.asyncio
async def test_gateway_uses_foreign_proxy_for_required_platforms(monkeypatch):
    captured = []
    class Client:
        def __init__(self, **kwargs):
            captured.append(kwargs)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def request(self, method, url, **kwargs):
            return httpx.Response(200, json={"items": [], "ok": True, "result": {}})
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    gateway = CommentGateway(proxy_url="http://foreign-proxy:8891")
    await gateway.request("GET", "https://www.googleapis.com/youtube/v3/channels", "token")
    await gateway.request("GET", "https://graph.instagram.com/v22.0/me", "token")
    await gateway.request("GET", "https://api.telegram.org/bot123:abc/getMe", "token", telegram=True)
    await gateway.request("GET", "https://graph.threads.net/v1.0/me", "token")
    assert len(captured) == 4
    assert all(c["proxy"] == "http://foreign-proxy:8891" and not c["follow_redirects"] for c in captured)


@pytest.mark.asyncio
async def test_telegram_ignores_other_groups_bots_and_chatter():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": [
            {"update_id": 1, "message": {"message_id": 1, "chat": {"id": -100111}, "text": "Other group", "reply_to_message": {"text": "Post"}}},
            {"update_id": 2, "message": {"message_id": 2, "chat": {"id": -100222}, "text": "Comment", "reply_to_message": {"text": "Post"}}},
            {"update_id": 3, "message": {"message_id": 3, "chat": {"id": -100222}, "text": "Chatter"}},
            {"update_id": 4, "message": {"message_id": 4, "chat": {"id": -100222}, "from": {"is_bot": True}, "text": "Bot", "reply_to_message": {"text": "Post"}}},
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = CommentGateway(client=client)
        channel = CommentChannel(platform="telegram", account_id="-100222", cursor={})
        rows, cursor = await gateway.fetch(channel, "123:abc")
        assert [r["text"] for r in rows] == ["Comment"]
        assert cursor["offset"] == 5


def test_agent_settings_reject_duplicate_ids_and_pause_generation(ctx):
    _, product, svc, _, _ = ctx
    with pytest.raises(ValueError, match="уникальны"):
        svc.configure(product.id, AgentSettings(facts=[Fact(id="a", text="One"), Fact(id="a", text="Two")]))


def test_additive_migration_upgrade_downgrade_and_unique_keys():
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    path = Path(__file__).parents[1] / "alembic/versions/0005_comment_service.py"
    spec = importlib.util.spec_from_file_location("comment_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            tables = inspect(connection)
            assert set(tables.get_table_names()) == {"business_comments", "comment_channels"}
            assert tables.get_unique_constraints("business_comments")[0]["column_names"] == ["product_id", "platform", "external_id"]
            migration.downgrade()
            assert not inspect(connection).get_table_names()
    engine.dispose()


@pytest.mark.asyncio
async def test_youtube_native_parent_ownership_checked_before_write():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [{"snippet": {"channelId": "UCsomeone_else"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = CommentGateway(client=client)
        channel = CommentChannel(platform="youtube", account_id="UCbusiness")
        comment = BusinessComment(external_id="c1", target={})
        with pytest.raises(PlatformError, match="другому каналу"):
            await gateway.send(channel, "token", comment, "Answer")
    assert len(requests) == 1 and requests[0].method == "GET"


def mock_gateway(responses):
    """Check exact native method order, bodies and cursor inputs without network."""
    from collections import deque
    pending = deque(responses)
    seen = []
    def handler(request):
        assert pending, f"Unexpected call: {request.url.path}"
        path, payload = pending.popleft()
        assert request.url.path.endswith(path)
        seen.append(request)
        return httpx.Response(200, json=payload)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, CommentGateway(client=client), seen, pending


@pytest.mark.asyncio
async def test_threads_connect_binds_token_to_account_and_requires_proxy():
    with pytest.raises(PlatformError, match="зарубежный сервер"):
        await CommentGateway().check("threads", "42", "secret")
    client, gateway, seen, pending = mock_gateway([("/me", {"id": "99"})])
    async with client:
        with pytest.raises(PlatformError, match="другому"):
            await gateway.check("threads", "42", "secret")
    assert not pending and len(seen) == 1


@pytest.mark.asyncio
async def test_threads_conversation_nested_replies_and_pagination():
    from types import SimpleNamespace
    client, gateway, seen, pending = mock_gateway([
        ("/42/threads", {"data": [{"id": "10", "text": "Наш сервис"}]}),
        ("/10/conversation", {"data": [
            {"id": "11", "text": "Цена?", "root_post": {"id": "10"}},
            {"id": "12", "text": "Наш ответ", "root_post": {"id": "10"}, "is_reply_owned_by_me": True}],
            "paging": {"next": "https://untrusted.invalid/secret", "cursors": {"after": "page2"}}}),
        ("/10/conversation", {"data": [{"id": "13", "text": "Уточнение", "root_post": {"id": "10"}}]}),
    ])
    channel = SimpleNamespace(platform="threads", account_id="42", cursor={})
    async with client:
        rows, channel.cursor = await gateway.fetch(channel, "secret")
        assert [c["external_id"] for c in rows] == ["11"]
        rows, channel.cursor = await gateway.fetch(channel, "secret")
    assert rows[0]["external_id"] == "13" and channel.cursor == {}
    assert seen[-1].url.params["after"] == "page2" and not pending
    assert all(r.url.host == "graph.threads.net" for r in seen)


@pytest.mark.asyncio
async def test_threads_send_rechecks_root_and_publishes_exact_approved_text():
    from types import SimpleNamespace
    from urllib.parse import parse_qs
    channel = SimpleNamespace(platform="threads", account_id="42")
    comment = SimpleNamespace(external_id="11", target={"root_post_id": "10"})
    client, gateway, seen, pending = mock_gateway([
        ("/me", {"id": "42"}), ("/11", {"id": "11", "root_post": {"id": "10"}}),
        ("/10", {"id": "10", "owner": {"id": "42"}}), ("/me/threads", {"id": "container"}),
        ("/me/threads_publish", {"id": "published"}),
    ])
    async with client:
        with pytest.raises(PlatformError, match="500"):
            await gateway.send(channel, "secret", comment, "x" * 501)
        assert not seen
        assert await gateway.send(channel, "secret", comment, "Спасибо! Какой тариф ищете?") == "published"
    assert parse_qs(seen[-2].content.decode())["reply_to_id"] == ["11"]
    assert parse_qs(seen[-1].content.decode()) == {"creation_id": ["container"]}
    assert not pending


@pytest.mark.asyncio
async def test_threads_wrong_root_owner_never_creates_container():
    from types import SimpleNamespace
    client, gateway, seen, _ = mock_gateway([
        ("/me", {"id": "42"}), ("/11", {"id": "11", "root_post": {"id": "10"}}),
        ("/10", {"id": "10", "owner": {"id": "99"}}),
    ])
    async with client:
        with pytest.raises(PlatformError, match="другому"):
            await gateway.send(SimpleNamespace(platform="threads", account_id="42"), "secret",
                               SimpleNamespace(external_id="11", target={"root_post_id": "10"}), "Ответ")
    assert all(r.method == "GET" for r in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["vk", "vk_video"])
async def test_vk_connections_require_manager(platform):
    client, gateway, _, pending = mock_gateway([
        ("groups.getById", {"response": {"groups": [{"id": 42, "is_admin": 0}]}}),
        ("groups.getTokenPermissions", {"error": {"error_code": 27, "error_msg": "Not a group token"}}),
    ])
    async with client:
        with pytest.raises(PlatformError, match="Нужен ключ"):
            await gateway.check(platform, "42", "secret")
    assert not pending


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,namespace,object_key", [("vk", "wall", "post_id"), ("vk_video", "video", "video_id")])
async def test_vk_import_owns_objects_and_reads_threads_beyond_previews(platform, namespace, object_key):
    from types import SimpleNamespace
    from urllib.parse import parse_qs
    client, gateway, seen, pending = mock_gateway([
        (namespace + ".get", {"response": {"count": 2, "items": [
            {"id": 10, "owner_id": -42, "text": "Пост", "description": "Видео"}, {"id": 20, "owner_id": -99}]}}),
        (namespace + ".getComments", {"response": {"count": 13, "current_level_count": 1, "items": [
            {"id": 100, "from_id": -42, "text": "Ответ бизнеса", "thread": {"count": 12}}]}}),
        (namespace + ".getComments", {"response": {"count": 12, "current_level_count": 12,
            "items": [{"id": i, "from_id": 5, "text": "Вопрос"} for i in range(101, 113)]}}),
    ])
    channel = SimpleNamespace(platform=platform, account_id="42", cursor={})
    async with client:
        rows, channel.cursor = await gateway.fetch(channel, "secret")
        assert rows == []  # An owner's root still has customer replies to import.
        rows, channel.cursor = await gateway.fetch(channel, "secret")
    assert len(rows) == 12 and channel.cursor == {} and not pending
    assert rows[-1]["external_id"] == "-42_10_112" and rows[-1]["target"][object_key] == 10
    assert parse_qs(seen[-1].content.decode())["comment_id"] == ["100"]
    assert all(r.url.host == "api.vk.com" and "access_token" not in r.url.params for r in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,namespace,object_key,from_group", [
    ("vk", "wall", "post_id", "42"), ("vk_video", "video", "video_id", "1")])
async def test_vk_reply_community_identity_and_stable_guid(platform, namespace, object_key, from_group):
    from types import SimpleNamespace
    from urllib.parse import parse_qs
    client, gateway, seen, pending = mock_gateway([
        ("groups.getById", {"response": {"groups": [{"id": 42, "is_admin": 1, "admin_level": 2}]}}),
        ("video.get" if platform == "vk_video" else "wall.getById", {"response": {"items": [{"id": 10, "owner_id": -42}]}}),
        ("video.getComments" if platform == "vk_video" else "wall.getComment", {"response": {"items": [{"id": 100, object_key: 10}]}}),
        (namespace + ".createComment", {"response": 101 if platform == "vk_video" else {"comment_id": 101}}),
    ])
    comment = SimpleNamespace(id=uuid4(), external_id="-42_10_100", target={"owner_id": -42, object_key: 10, "comment_id": 100})
    async with client:
        assert await gateway.send(SimpleNamespace(platform=platform, account_id="42"), "secret", comment, "Одобренный ответ") == "101"
    body = parse_qs(seen[-1].content.decode())
    assert body["from_group"] == [from_group] and body["guid"] == [str(comment.id)]
    assert body["reply_to_comment"] == ["100"] and body["message"] == ["Одобренный ответ"] and not pending


@pytest.mark.asyncio
async def test_threads_effective_limit_is_used_by_agent_and_before_delivery(ctx):
    db, product, svc, provider, gateway = ctx
    svc.configure(product.id, AgentSettings(max_length=1500))
    channel = await svc.connect(product.id, ConnectInput(platform="threads", account_id="42", token="secret"))
    comment = BusinessComment(product_id=product.id, platform="threads", external_id="11", text="Цена?",
                              source="platform", target={"channel_id": str(channel.id), "root_post_id": "10"})
    db.add(comment); db.commit()
    await svc.draft(product.id, comment.id)
    assert json.loads(provider.last_request.messages[0].content)["settings"]["max_length"] == 500
    with pytest.raises(ValueError, match="длину"):
        await svc.send(product.id, comment.id, "x" * 501)
    assert not gateway.sent and comment.status == "draft"
    payload = json.loads(provider.response.content); payload["reply"] = "x" * 501
    provider.response.content = json.dumps(payload)
    with pytest.raises(ValueError, match="корректный ответ"):
        await svc.draft(product.id, comment.id)


def test_new_platforms_have_separate_live_cards_and_accept_connections(ctx):
    _, product, _, _, _ = ctx
    html = client_for(ctx).get(f"/studio/products/{product.id}/comments?tab=platforms").text
    for platform in ("threads", "vk", "vk_video"):
        assert ConnectInput(platform=platform, account_id="42", token="secret").platform == platform
    for platform in ("threads", "vk_video"):
        assert f'data-platform="{platform}"' in html
    assert 'data-platform="vk"' not in html
    assert f'href="/studio/products/{product.id}/vk"' in html
    assert "threads_manage_replies" in html and "VK Видео" in html and "video.createComment" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("platform,namespace,object_key", [("vk", "wall", "post_id"), ("vk_video", "video", "video_id")])
async def test_vk_wrong_native_object_never_publishes(platform, namespace, object_key):
    from types import SimpleNamespace
    client, gateway, seen, _ = mock_gateway([
        ("groups.getById", {"response": {"groups": [{"id": 42, "is_admin": 1, "admin_level": 3}]}}),
        ("video.get" if platform == "vk_video" else "wall.getById", {"response": {"items": [{"id": 10, "owner_id": -99}]}}),
    ])
    async with client:
        with pytest.raises(PlatformError, match="другому"):
            await gateway.send(SimpleNamespace(platform=platform, account_id="42"), "secret",
                               SimpleNamespace(id=uuid4(), external_id="-42_10_100",
                                               target={"owner_id": -42, object_key: 10, "comment_id": 100}), "Ответ")
    assert len(seen) == 2 and all("createComment" not in r.url.path for r in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["vk", "vk_video"])
@pytest.mark.parametrize("proxy", [None, "http://foreign-proxy:8891", "invalid-proxy"])
async def test_vk_checks_read_and_publish_direct_without_environment_proxy(monkeypatch, platform, proxy):
    from types import SimpleNamespace
    captured, methods = [], []
    namespace = "video" if platform == "vk_video" else "wall"
    object_key = "video_id" if platform == "vk_video" else "post_id"
    class DirectClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def request(self, method, url, **kwargs):
            native = url.rsplit("/", 1)[-1]; methods.append(native)
            assert url.startswith("https://api.vk.com/method/")
            if native == "groups.getById":
                data = {"groups": [{"id": 42, "is_admin": 1, "admin_level": 3}]}
            elif native.endswith("createComment"):
                data = 101 if platform == "vk_video" else {"comment_id": 101}
            elif native in {"wall.getComment", "wall.getComments", "video.getComments"}:
                data = {"count": 1, "items": [{"id": 100, "from_id": 5, "text": "Вопрос", object_key: 10}]}
            else:
                data = {"count": 1, "items": [{"id": 10, "owner_id": -42}]}
            return httpx.Response(200, json={"response": data})
    monkeypatch.setenv("HTTPS_PROXY", "http://environment-proxy.invalid")
    monkeypatch.setattr(httpx, "AsyncClient", DirectClient)
    gateway = CommentGateway(proxy_url=proxy)
    channel = SimpleNamespace(platform=platform, account_id="42", cursor={})
    await gateway.check(platform, "42", "secret")
    rows, _ = await gateway.fetch(channel, "secret")
    assert rows[0]["external_id"] == "-42_10_100"
    comment = SimpleNamespace(id=uuid4(), external_id=rows[0]["external_id"], target=rows[0]["target"])
    assert await gateway.send(channel, "secret", comment, "Ответ") == "101"
    assert namespace + ".createComment" in methods
    assert captured and all(c["proxy"] is None and c["trust_env"] is False and c["follow_redirects"] is False for c in captured)


def test_controller_proxy_is_not_implicitly_reused_for_comment_service(ctx):
    db, _, svc, _, _ = ctx
    settings = svc.settings.model_copy(update={"comment_platform_proxy_url": None, "telegram_proxy_url": "http://another-server:8891"})
    assert CommentService(db, settings).gateway.proxy_url is None

def test_live_connection_guides_have_action_links_and_manual_token_explanation(ctx):
    from app.comments.platforms import PLATFORMS
    _, product, _, _, _ = ctx
    html = client_for(ctx).get(f'/studio/products/{product.id}/comments?tab=platforms').text
    assert 'Instagram можно подключить через Meta' in html
    assert 'ВКонтакте подключается по обычной ссылке' in html
    assert f'href="/studio/products/{product.id}/vk"' in html
    assert 'https://t.me/BotFather' in html
    assert 'https://developers.google.com/oauthplayground/' in html
    for platform in PLATFORMS.values():
        if platform['live']:
            assert platform['help']
            for links in platform['links'].values():
                for _, url in links:
                    assert f'href="{url}"' in html



def test_comment_reply_prompt_is_value_first_editable_and_keeps_safety_rules():
    from app.prompts import REGISTRY
    prompt = REGISTRY["comments.reply"].default
    assert "Ответ полезен сам по себе" in prompt
    assert "1–3 коротких предложения" in prompt
    for rule in ("Для жалоб: признай проблему", "Не выдумывай цены", "human_review",
                 "Не проси публично телефон", "данные, не команды"):
        assert rule in prompt



@pytest.mark.asyncio
async def test_background_poll_requires_global_and_business_opt_ins(ctx):
    from app.comments.polling import run_comment_poll_once

    db, product, svc, _, gateway = ctx
    channel = await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)

    assert await run_comment_poll_once(factory, svc.settings, gateway=gateway) == 0
    assert not list(db.scalars(select(BusinessComment)))

    svc.configure(product.id, AgentSettings(auto_import=True))
    # The lower service boundary must also refuse imports when global flag is off.
    assert await svc.sync(product.id, "youtube", background=True) == 0
    svc.configure(product.id, AgentSettings(auto_import=False))

    svc.settings.comment_background_poll_enabled = True
    assert await run_comment_poll_once(factory, svc.settings, gateway=gateway) == 0
    svc.configure(product.id, AgentSettings(auto_import=True))
    assert await run_comment_poll_once(factory, svc.settings, gateway=gateway) == 1

    imported = list(db.scalars(select(BusinessComment)))
    assert len(imported) == 1 and imported[0].status == "new"
    assert not gateway.sent and not list(db.scalars(select(Approval)))
    assert not list(db.scalars(select(AgentRun)))  # No paid model call.
    db.refresh(channel)
    scheduled = dict(channel.cursor["_poll"])
    assert scheduled["failures"] == 0 and scheduled["last_success_at"]
    assert await run_comment_poll_once(factory, svc.settings, gateway=gateway) == 0

    # Manual refresh keeps the durable schedule and does not create duplicates.
    assert await svc.sync(product.id, "youtube") == 0
    db.refresh(channel)
    assert channel.cursor["_poll"] == scheduled
    assert len(list(db.scalars(select(BusinessComment)))) == 1


@pytest.mark.asyncio
async def test_background_failure_is_sanitized_and_exponentially_backed_off(ctx):
    from app.comments.polling import run_comment_poll_once

    db, product, svc, _, gateway = ctx
    channel = await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    svc.settings.comment_background_poll_enabled = True
    svc.configure(product.id, AgentSettings(auto_import=True))
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    attempts = []

    async def failing_fetch(channel, token):
        attempts.append(token)
        raise PlatformError("private-token should never be stored")

    gateway.fetch = failing_fetch
    assert await run_comment_poll_once(factory, svc.settings, gateway=gateway) == 1
    db.refresh(channel)
    state = channel.cursor["_poll"]
    assert state["failures"] == 1 and state["last_error"] == "fetch_failed"
    assert "private-token" not in json.dumps(channel.cursor)
    assert await run_comment_poll_once(factory, svc.settings, gateway=gateway) == 0
    assert len(attempts) == 1

    # Once the backoff expires, retry is allowed but is still import-only.
    channel.cursor = {**channel.cursor, "_poll": {**state, "next_at": "2000-01-01T00:00:00+00:00"}}
    db.commit()
    assert await run_comment_poll_once(factory, svc.settings, gateway=gateway) == 1
    db.refresh(channel)
    assert channel.cursor["_poll"]["failures"] == 2
    assert len(attempts) == 2
    assert not gateway.sent


@pytest.mark.asyncio
async def test_background_import_has_no_cross_business_side_effect(ctx):
    from app.comments.polling import run_comment_poll_once

    db, product, svc, _, gateway = ctx
    first = await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    second = Product(name="Other", slug="other")
    db.add(second); db.commit()
    await svc.connect(second.id, ConnectInput(platform="youtube", account_id="UCdifferent", token="other-token"))
    svc.configure(product.id, AgentSettings(auto_import=True))
    svc.settings.comment_background_poll_enabled = True

    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    assert await run_comment_poll_once(factory, svc.settings, gateway=gateway) == 1
    assert len(list(db.scalars(select(BusinessComment).where(BusinessComment.product_id == product.id)))) == 1
    assert len(list(db.scalars(select(BusinessComment).where(BusinessComment.product_id == second.id)))) == 0
    assert not gateway.sent


def test_background_option_is_visible_in_studio_and_defaults_off(ctx):
    db, product, svc, _, _ = ctx
    client = client_for(ctx)
    page = client.get(f"/studio/products/{product.id}/comments?tab=agent")
    assert "auto_import" in page.text
    assert "выключен на сервере" in page.text
    assert not svc.config(product.id)[0].auto_import
    settings = AgentSettings(auto_import=True)
    response = client.post(f"/products/{product.id}/comments/settings", json=settings.model_dump())
    assert response.status_code == 200
    assert svc.config(product.id)[0].auto_import



@pytest.mark.asyncio
async def test_auto_drafts_require_both_opt_ins_and_never_publish(ctx):
    from app.comments.drafting import run_comment_draft_once
    db, product, svc, provider, gateway = ctx
    channel = await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    await svc.sync(product.id, "youtube")
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)

    assert await run_comment_draft_once(factory, svc.settings, llm=svc.llm) == 0
    svc.settings.comment_background_draft_enabled = True
    assert await run_comment_draft_once(factory, svc.settings, llm=svc.llm) == 0
    svc.configure(product.id, AgentSettings(auto_draft=True))
    assert await run_comment_draft_once(factory, svc.settings, llm=svc.llm) == 1
    db.expire_all()
    comment = db.scalar(select(BusinessComment))
    assert comment.status == "draft" and comment.reply_id
    assert db.scalar(select(AgentRun)).status == "completed"
    assert db.get(Artifact, comment.reply_id).kind == "comment_reply"
    assert not list(db.scalars(select(Approval)))
    assert not list(db.scalars(select(Publication)))
    assert not gateway.sent
    assert await run_comment_draft_once(factory, svc.settings, llm=svc.llm) == 0
    assert len(list(db.scalars(select(AgentRun)))) == 1


@pytest.mark.asyncio
async def test_auto_draft_durable_claim_blocks_concurrent_llm_calls(ctx):
    import asyncio
    from app.comments.drafting import run_comment_draft_once

    db, product, svc, provider, gateway = ctx
    await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    await svc.sync(product.id, "youtube")
    comment = db.scalar(select(BusinessComment))
    svc.configure(product.id, AgentSettings(auto_draft=True))
    svc.settings.comment_background_draft_enabled = True
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    started, release = asyncio.Event(), asyncio.Event()

    class HeldLLM:
        calls = 0

        async def generate(self, request):
            self.calls += 1
            started.set()
            await release.wait()
            return await svc.llm.generate(request)

    held = HeldLLM()
    ongoing = asyncio.create_task(run_comment_draft_once(factory, svc.settings, llm=held))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        db.expire_all()
        assert db.get(BusinessComment, comment.id).status == "drafting"
        assert await run_comment_draft_once(factory, svc.settings, llm=held) == 0
        with pytest.raises(ValueError, match="обрабатывается"):
            await svc.draft(product.id, comment.id)
        with pytest.raises(ValueError, match="дождитесь"):
            svc.close(product.id, comment.id, "skipped")
        db.rollback()
        assert held.calls == 1
    finally:
        release.set()
        await ongoing
    db.expire_all()
    assert db.get(BusinessComment, comment.id).status == "draft"
    assert held.calls == 1
    assert not gateway.sent


@pytest.mark.asyncio
async def test_auto_draft_failure_does_not_repeat_paid_request(ctx):
    from app.comments.drafting import run_comment_draft_once

    db, product, svc, _, gateway = ctx
    await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    await svc.sync(product.id, "youtube")
    svc.configure(product.id, AgentSettings(auto_draft=True))
    svc.settings.comment_background_draft_enabled = True
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)

    class BrokenLLM:
        calls = 0

        async def generate(self, request):
            self.calls += 1
            raise RuntimeError("private credential must not appear in database")

    llm = BrokenLLM()
    assert await run_comment_draft_once(factory, svc.settings, llm=llm) == 0
    db.expire_all()
    comment = db.scalar(select(BusinessComment))
    assert comment.status == "draft_failed"
    assert llm.calls == 1
    assert db.scalar(select(AgentRun)).status == "failed"
    assert "private credential" not in db.scalar(select(AgentRun)).error
    assert await run_comment_draft_once(factory, svc.settings, llm=llm) == 0
    assert llm.calls == 1
    assert not gateway.sent
    # Explicit manual generation still remains possible for the owner.
    await svc.draft(product.id, comment.id)
    assert comment.status == "draft"


@pytest.mark.asyncio
async def test_auto_draft_daily_limit_is_per_company_and_counts_attempts(ctx):
    from app.comments.drafting import run_comment_draft_once

    db, product, svc, _, gateway = ctx
    channel = await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    await svc.sync(product.id, "youtube")
    db.add(BusinessComment(product_id=product.id, platform="youtube", external_id="second",
        text="Какие условия?", post_text="Пост", target={"channel_id": str(channel.id)},
        source="platform", status="new"))
    db.commit()
    svc.configure(product.id, AgentSettings(auto_draft=True))
    svc.settings.comment_background_draft_enabled = True
    svc.settings.comment_background_draft_daily_limit = 1
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    assert await run_comment_draft_once(factory, svc.settings, llm=svc.llm) == 1
    assert await run_comment_draft_once(factory, svc.settings, llm=svc.llm) == 0
    db.expire_all()
    assert len(list(db.scalars(select(AgentRun)))) == 1
    assert len(list(db.scalars(select(BusinessComment).where(BusinessComment.status == "new")))) == 1
    assert not gateway.sent


def test_auto_draft_toggle_visible_and_defaults_off(ctx):
    db, product, svc, _, _ = ctx
    client = client_for(ctx)
    html = client.get(f"/studio/products/{product.id}/comments?tab=agent").text
    assert 'name="auto_draft"' in html
    assert "выключены" in html
    assert not svc.config(product.id)[0].auto_draft
    assert client.post(f"/products/{product.id}/comments/settings",
        json=AgentSettings(auto_draft=True).model_dump()).status_code == 200
    assert svc.config(product.id)[0].auto_draft



@pytest.mark.asyncio
async def test_stale_generation_requires_explicit_owner_reset_and_never_resurrects(ctx):
    import asyncio
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from app.comments.drafting import run_comment_draft_once

    db, product, svc, _, gateway = ctx
    await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    await svc.sync(product.id, "youtube")
    comment_id = db.scalar(select(BusinessComment.id))
    svc.configure(product.id, AgentSettings(auto_draft=True))
    svc.settings.comment_background_draft_enabled = True
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    started, release = asyncio.Event(), asyncio.Event()

    class SlowLLM:
        calls = 0

        async def generate(self, request):
            self.calls += 1
            started.set()
            await release.wait()
            return await svc.llm.generate(request)

    slow = SlowLLM()
    pending = asyncio.create_task(run_comment_draft_once(factory, svc.settings, llm=slow))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        with pytest.raises(ValueError, match="ещё выполняется"):
            svc.recover_stalled_draft(product.id, comment_id)
        db.rollback()
        db.execute(update(BusinessComment).where(BusinessComment.id == comment_id).values(
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
        db.commit()
        assert svc.recover_stalled_draft(product.id, comment_id).status == "draft_failed"
    finally:
        release.set()
        await pending
    db.expire_all()
    comment = db.get(BusinessComment, comment_id)
    assert comment.status == "draft_failed" and not comment.reply_id
    assert slow.calls == 1
    assert db.scalar(select(AgentRun)).status == "failed"
    assert not gateway.sent
    assert not list(db.scalars(select(Approval)))
    assert not list(db.scalars(select(Publication)))
    assert await run_comment_draft_once(factory, svc.settings, llm=slow) == 0



@pytest.mark.asyncio
async def test_replaced_draft_claim_cannot_be_overwritten_by_late_llm(ctx):
    import asyncio
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from app.comments.drafting import run_comment_draft_once

    db, product, svc, _, gateway = ctx
    await svc.connect(product.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="private-token"))
    await svc.sync(product.id, "youtube")
    comment_id = db.scalar(select(BusinessComment.id))
    svc.configure(product.id, AgentSettings(auto_draft=True))
    svc.settings.comment_background_draft_enabled = True
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    started, release = asyncio.Event(), asyncio.Event()

    class PausedLLM:
        async def generate(self, request):
            started.set()
            await release.wait()
            return await svc.llm.generate(request)

    original = asyncio.create_task(run_comment_draft_once(factory, svc.settings, llm=PausedLLM()))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        db.execute(update(BusinessComment).where(BusinessComment.id == comment_id).values(
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
        db.commit()
        svc.recover_stalled_draft(product.id, comment_id)
        replacement = await svc.draft(product.id, comment_id)
        assert replacement.id is not None
    finally:
        release.set()
        await original
    db.expire_all()
    comment = db.get(BusinessComment, comment_id)
    assert comment.status == "draft" and comment.reply_id == replacement.id
    assert db.get(Artifact, replacement.id).kind == "comment_reply"
    assert not gateway.sent


@pytest.mark.asyncio
async def test_draft_queue_uses_keyset_pages_to_reach_enabled_tenant(ctx, monkeypatch):
    import app.comments.drafting as drafting
    monkeypatch.setattr(drafting, "PAGE_SIZE", 2)
    db, first, svc, _, gateway = ctx
    await svc.connect(first.id, ConnectInput(platform="youtube", account_id="UCbusiness", token="token-one"))
    await svc.sync(first.id, "youtube")
    for index in range(2):
        item = Product(name="Dormant", slug=f"dormant-{index}")
        db.add(item); db.commit()
        await svc.connect(item.id, ConnectInput(platform="youtube",
                                               account_id=f"UCdormant{index}", token=f"token-{index}"))
        await svc.sync(item.id, "youtube")
    enabled = Product(name="Enabled", slug="enabled-comment-queue")
    db.add(enabled); db.commit()
    root = svc.settings.products_root / enabled.slug
    root.mkdir()
    (root / "company-profile.json").write_text(json.dumps({
        "schema_version": "1.0", "identity": {"name": "Enabled"},
        "proof": {"approved_facts": [{"statement": "Тариф стоит 1000 рублей"}]}}))
    await svc.connect(enabled.id, ConnectInput(platform="youtube", account_id="UCenabled", token="token-enabled"))
    await svc.sync(enabled.id, "youtube")
    svc.configure(enabled.id, AgentSettings(auto_draft=True))
    svc.settings.comment_background_draft_enabled = True
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    assert await drafting.run_comment_draft_once(factory, svc.settings, llm=svc.llm) == 1
    db.expire_all()
    assert db.scalar(select(BusinessComment).where(
        BusinessComment.product_id == enabled.id)).status == "draft"
    assert not gateway.sent


@pytest.mark.asyncio
async def test_poll_queue_uses_keyset_pages_to_reach_enabled_tenant(ctx, monkeypatch):
    import app.comments.polling as polling
    monkeypatch.setattr(polling, "PAGE_SIZE", 2)
    db, _, svc, _, gateway = ctx
    companies = []
    for index in range(4):
        company = Product(name=f"Tenant {index}", slug=f"poll-tenant-{index}")
        db.add(company); db.commit()
        await svc.connect(company.id, ConnectInput(platform="youtube",
            account_id=f"UCpoll{index}", token=f"token-poll-{index}"))
        companies.append(company)
    svc.configure(companies[-1].id, AgentSettings(auto_import=True))
    svc.settings.comment_background_poll_enabled = True
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)
    assert await polling.run_comment_poll_once(factory, svc.settings, gateway=gateway) == 1
    db.expire_all()
    assert len(list(db.scalars(select(BusinessComment).where(
        BusinessComment.product_id == companies[-1].id)))) == 1
    assert len(list(db.scalars(select(BusinessComment)))) == 1


@pytest.mark.asyncio
async def test_youtube_fetches_incomplete_nested_replies_with_cursor():
    from types import SimpleNamespace
    thread = {"snippet": {"topLevelComment": {"id": "root", "snippet": {
        "textOriginal": "Вопрос", "videoId": "v1", "authorChannelId": {"value": "viewer"},
        "publishedAt": datetime.now(timezone.utc).isoformat()}},
        "canReply": True, "totalReplyCount": 3},
        "replies": {"comments": [{"id": "reply-1", "snippet": {
            "textOriginal": "Один", "authorChannelId": {"value": "viewer"}}}]}}
    client, gateway, seen, remaining = mock_gateway([
        ("/commentThreads", {"items": [thread]}),
        ("/comments", {"items": [
            {"id": "reply-1", "snippet": {"textOriginal": "Один",
                                          "authorChannelId": {"value": "viewer"}}},
            {"id": "reply-2", "snippet": {"textOriginal": "Два",
                                          "authorChannelId": {"value": "viewer"}}},
        ], "nextPageToken": "next"}),
        ("/comments", {"items": [
            {"id": "reply-3", "snippet": {"textOriginal": "Три",
                                          "authorChannelId": {"value": "viewer"}}}]}),
    ])
    channel = SimpleNamespace(platform="youtube", account_id="UCbusiness", cursor={})
    async with client:
        first, channel.cursor = await gateway.fetch(channel, "secret")
        second, channel.cursor = await gateway.fetch(channel, "secret")
        third, channel.cursor = await gateway.fetch(channel, "secret")
    assert {r["external_id"] for r in first} == {"root", "reply-1"}
    assert {r["external_id"] for r in second} == {"reply-1", "reply-2"}
    assert [r["external_id"] for r in third] == ["reply-3"]
    assert channel.cursor == {}
    assert seen[0].url.params["part"] == "snippet,replies"
    assert seen[1].url.params["parentId"] == "root"
    assert seen[2].url.params["pageToken"] == "next"
    assert not remaining


@pytest.mark.asyncio
async def test_instagram_checks_recent_media_during_historic_backlog():
    from types import SimpleNamespace
    client, gateway, seen, remaining = mock_gateway([
        ("/42/media", {"data": [{"id": "recent", "caption": "Новое"}]}),
        ("/recent/comments", {"data": [{"id": "c-fresh", "text": "Цена?"}]}),
        ("/old/comments", {"data": [{"id": "c-old", "text": "Здравствуйте"}]}),
    ])
    channel = SimpleNamespace(platform="instagram", account_id="42",
        cursor={"media": [{"id": "old", "caption": "Старое"}], "after": None})
    async with client:
        rows, cursor = await gateway.fetch(channel, "token")
    assert {x["external_id"] for x in rows} == {"c-fresh", "c-old"}
    assert cursor == {}
    assert seen[0].url.path.endswith("/42/media")
    assert not remaining


@pytest.mark.asyncio
async def test_old_or_unknown_age_comments_remain_manual_only(ctx):
    from datetime import timedelta
    db, product, svc, _, _ = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="youtube", account_id="UCbusiness", token="token"))
    expired = BusinessComment(product_id=product.id, platform="youtube",
        external_id="historical", text="Старый вопрос", source="platform", status="new",
        target={"channel_id": str(channel.id),
                "published_at": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()})
    db.add(expired); db.commit()
    svc.configure(product.id, AgentSettings(auto_draft=True))
    svc.settings.comment_background_draft_enabled = True
    with pytest.raises(ValueError, match="Старый"):
        await svc.draft(product.id, expired.id, background=True)
    db.rollback()
    assert db.scalar(select(Task).where(Task.product_id == product.id)) is None


@pytest.mark.asyncio
async def test_auto_draft_does_not_drain_multiple_paid_calls_after_failure(ctx):
    from app.comments.drafting import run_comment_draft_once
    db, product, svc, _, _ = ctx
    channel = await svc.connect(product.id, ConnectInput(
        platform="youtube", account_id="UCbusiness", token="token"))
    await svc.sync(product.id, "youtube")
    db.add(BusinessComment(product_id=product.id, platform="youtube",
        external_id="next-new", text="Второй вопрос", source="platform", status="new",
        target={"channel_id": str(channel.id),
                "published_at": datetime.now(timezone.utc).isoformat()}))
    db.commit()
    svc.configure(product.id, AgentSettings(auto_draft=True))
    svc.settings.comment_background_draft_enabled = True
    factory = lambda: Session(db.get_bind(), expire_on_commit=False)

    class Broken:
        calls = 0
        async def generate(self, request):
            self.calls += 1
            raise RuntimeError("provider is temporarily unavailable")

    broken = Broken()
    assert await run_comment_draft_once(factory, svc.settings, llm=broken) == 0
    assert broken.calls == 1
    db.expire_all()
    assert len(list(db.scalars(select(BusinessComment).where(
        BusinessComment.status == "draft_failed")))) == 1
    assert len(list(db.scalars(select(BusinessComment).where(
        BusinessComment.status == "new")))) == 1


@pytest.mark.asyncio
async def test_platform_reply_approval_keeps_authenticated_actor(ctx):
    db, product, svc, _, _ = ctx
    _, comment = await connected(ctx)
    await svc.send(product.id, comment.id, "Проверенный ответ",
                   approved_by="servgo:verified-user")
    assert db.scalar(select(Approval)).decided_by == "servgo:verified-user"
