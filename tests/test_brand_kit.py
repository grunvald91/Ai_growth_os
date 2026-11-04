"""Brand Kit contract tests: tenant scoping, approval, integrity and mobile UI."""
from __future__ import annotations

import io
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_auth import _apply_scope, require_studio_auth
from app.brand_kit.models import BrandActive, BrandAsset, BrandVersion
from app.brand_kit.service import verified_asset
from app.core.config import get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Product


def picture() -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (280, 180), (28, 83, 177, 255)).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCTS_ROOT", str(tmp_path))
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        a = Product(name="Компания А", slug="brand-tenant-a")
        b = Product(name="Компания Б", slug="brand-tenant-b")
        db.add_all([a, b])
        db.commit()

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        with TestClient(app) as client:
            yield client, db, a, b, tmp_path
        app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()
    get_settings.cache_clear()


def upload(client, company, *, kind="original", data=None):
    return client.post(
        f"/studio/products/{company.id}/brand-kit/assets",
        data={"kind": kind},
        files={"file": ("brand.png", data if data is not None else picture(), "image/png")},
        follow_redirects=False)


def fields(asset_id=None, **overrides):
    values = {
        "logo_original": str(asset_id) if asset_id else "",
        "logo_light": "", "logo_dark": "",
        "primary": "#2F5BEB", "secondary": "#0D1B2A",
        "accent": "#2F5BEB", "background": "#FFFFFF", "text": "#0D1B2A",
        "heading": "Manrope", "body": "Manrope",
        "visual_character": "photo", "preferred_generator": "gemini",
        "hook_logo_position": "top-left",
        "explanation_logo_position": "top-left",
        "cta_logo_position": "top-left",
    }
    values.update(overrides)
    return values


def test_upload_draft_approval_and_preview_are_company_scoped(studio):
    client, db, a, b, root = studio
    page = client.get(f"/studio/products/{a.id}/brand-kit")
    assert page.status_code == 200
    assert "Предварительный просмотр 9:16" in page.text
    assert "Сохранить новую версию" in page.text
    assert "Фирменный стиль" in client.get(f"/studio/products/{a.id}/profile").text
    saved = upload(client, a)
    assert saved.status_code == 303
    asset = db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id))
    assert asset is not None and asset.kind == "original"
    assert client.get(f"/studio/products/{a.id}/brand-kit/assets/{asset.id}").content == picture()
    assert client.get(f"/studio/products/{b.id}/brand-kit/assets/{asset.id}").status_code == 404
    new = client.post(f"/studio/products/{a.id}/brand-kit/versions",
                      data=fields(asset.id), follow_redirects=False)
    assert new.status_code == 303
    draft = db.scalar(select(BrandVersion).where(BrandVersion.product_id == a.id))
    assert draft is not None and draft.status == "draft"
    assert db.get(BrandActive, a.id) is None
    assert client.post(f"/studio/products/{a.id}/brand-kit/versions/{draft.id}/approve",
                       follow_redirects=False).status_code == 303
    assert db.get(BrandActive, a.id).version_id == draft.id
    assert draft.status == "approved"
    state = client.get(f"/studio/products/{a.id}/brand-kit/state").json()
    assert state["active_version_id"] == str(draft.id)
    assert state["versions"][0]["config"]["logos"]["original"] == str(asset.id)
    assert client.get(f"/studio/products/{b.id}/brand-kit/state").json()["versions"] == []
    assert client.post(f"/studio/products/{b.id}/brand-kit/versions/{draft.id}/approve").status_code == 404
    second = client.post(f"/studio/products/{a.id}/brand-kit/versions",
                         data=fields(asset.id, primary="#8020AA"), follow_redirects=False)
    assert second.status_code == 303
    assert db.get(BrandActive, a.id).version_id == draft.id
    assert db.scalar(select(BrandVersion).where(
        BrandVersion.product_id == a.id, BrandVersion.version == 2)).status == "draft"


def test_approval_requires_logo_and_readable_text(studio):
    client, db, a, _, _ = studio
    assert client.post(f"/studio/products/{a.id}/brand-kit/versions",
                       data=fields(), follow_redirects=False).status_code == 303
    v = db.scalar(select(BrandVersion).where(BrandVersion.product_id == a.id))
    assert client.post(f"/studio/products/{a.id}/brand-kit/versions/{v.id}/approve").status_code == 422
    asset = None
    assert upload(client, a).status_code == 303
    asset = db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id))
    assert client.post(f"/studio/products/{a.id}/brand-kit/versions",
                       data=fields(asset.id, text="#FFFFFF"), follow_redirects=False).status_code == 303
    v2 = db.scalar(select(BrandVersion).where(
        BrandVersion.product_id == a.id, BrandVersion.version == 2))
    assert client.post(f"/studio/products/{a.id}/brand-kit/versions/{v2.id}/approve").status_code == 422
    assert db.get(BrandActive, a.id) is None


def test_repeated_upload_and_form_save_reuse_existing_checkpoints(studio):
    client, db, a, _, _ = studio
    assert upload(client, a).status_code == 303
    assert upload(client, a).status_code == 303
    assets = list(db.scalars(select(BrandAsset).where(BrandAsset.product_id == a.id)))
    assert len(assets) == 1
    for _ in range(2):
        assert client.post(f"/studio/products/{a.id}/brand-kit/versions",
                           data=fields(assets[0].id), follow_redirects=False).status_code == 303
    versions = list(db.scalars(select(BrandVersion).where(BrandVersion.product_id == a.id)))
    assert len(versions) == 1 and versions[0].version == 1


def test_upload_rejects_fake_images_and_alien_logo(studio):
    client, db, a, b, _ = studio
    assert upload(client, a, data=b"not an image").status_code == 422
    assert upload(client, a, data=b"x" * 6_000_001).status_code == 422
    assert upload(client, a).status_code == 303
    asset = db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id))
    assert client.post(f"/studio/products/{b.id}/brand-kit/versions",
                       data=fields(asset.id), follow_redirects=False).status_code == 404
    assert db.scalar(select(BrandVersion).where(BrandVersion.product_id == b.id)) is None


def test_corrupted_logo_is_not_served_or_approved(studio):
    client, db, a, _, root = studio
    assert upload(client, a).status_code == 303
    asset = db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id))
    _, path = verified_asset(db, root, a.id, asset.id)
    path.write_bytes(b"tampered")
    assert client.get(f"/studio/products/{a.id}/brand-kit/assets/{asset.id}").status_code == 404
    assert client.post(f"/studio/products/{a.id}/brand-kit/versions",
                       data=fields(asset.id)).status_code == 404


def test_company_deletion_removes_brand_files_without_touching_other_tenant(studio):
    from app.intake.company_admin import delete_company
    client, db, a, b, root = studio
    assert upload(client, a).status_code == 303
    assert upload(client, b).status_code == 303
    assert delete_company(db, root, a.id) == a.name
    assert not (root / "_brand_assets" / str(a.id)).exists()
    assert (root / "_brand_assets" / str(b.id)).exists()


def test_company_account_cannot_open_another_brand(studio):
    client, db, a, b, _ = studio

    def restricted(request: Request):
        return _apply_scope(request, "company", [str(a.id)])

    app.dependency_overrides[require_studio_auth] = restricted
    try:
        assert client.get(f"/studio/products/{a.id}/brand-kit").status_code == 200
        assert client.get(f"/studio/products/{b.id}/brand-kit").status_code == 404
        assert client.post(f"/studio/products/{b.id}/brand-kit/versions",
                           data=fields()).status_code == 404
    finally:
        app.dependency_overrides.pop(require_studio_auth, None)


def test_brand_approval_requires_owner_role(studio):
    client, db, a, _, root = studio
    assert upload(client, a).status_code == 303
    asset = db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id))
    assert client.post(f"/studio/products/{a.id}/brand-kit/versions",
                       data=fields(asset.id), follow_redirects=False).status_code == 303
    version = db.scalar(select(BrandVersion).where(BrandVersion.product_id == a.id))

    def company_login(role):
        def login(request: Request):
            identity = _apply_scope(request, "company", [str(a.id)])
            if role:
                request.state.studio_sso_role = role
            return identity
        return login

    for role in (None, "administrator", "co_owner"):
        app.dependency_overrides[require_studio_auth] = company_login(role)
        assert client.post(
            f"/studio/products/{a.id}/brand-kit/versions/{version.id}/approve",
            follow_redirects=False).status_code == 403
        assert db.get(BrandActive, a.id) is None
    app.dependency_overrides[require_studio_auth] = company_login("owner")
    try:
        assert client.post(
            f"/studio/products/{a.id}/brand-kit/versions/{version.id}/approve",
            follow_redirects=False).status_code == 303
        assert db.get(BrandActive, a.id).version_id == version.id
    finally:
        app.dependency_overrides.pop(require_studio_auth, None)


def test_brand_rejects_tiny_and_invisible_originals(studio):
    client, db, a, _, _ = studio
    for size, alpha in (((1, 1), 255), ((280, 180), 0)):
        data = io.BytesIO()
        Image.new("RGBA", size, (255, 255, 255, alpha)).save(data, "PNG")
        assert upload(client, a, data=data.getvalue()).status_code == 422
    assert db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id)) is None


def test_brand_layout_fields_are_saved(studio):
    client, db, a, _, _ = studio
    data = fields()
    data.update(hook_image_position="bottom", hook_text_align="right",
                explanation_image_position="top", explanation_text_align="center")
    r = client.post(f"/studio/products/{a.id}/brand-kit/versions",
                    data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    version = db.scalar(select(BrandVersion).where(BrandVersion.product_id == a.id))
    assert version.config["hook"]["image_position"] == "bottom"
    assert version.config["hook"]["text_align"] == "right"
    assert version.config["explanation"]["image_position"] == "top"
    assert version.config["explanation"]["text_align"] == "center"
    page = client.get(f"/studio/products/{a.id}/brand-kit")
    assert 'name="hook_image_position"' in page.text
    assert 'name="hook_text_align"' in page.text


def test_brand_multipart_stream_limit_without_length(studio):
    client, db, a, _, _ = studio
    boundary = "brand-limit"
    def stream():
        yield (f'--{boundary}\\r\\nContent-Disposition: form-data; '
               'name="file"; filename="big.png"\\r\\n'
               'Content-Type: image/png\\r\\n\\r\\n').encode().replace(b"\\\\r",b"\\r").replace(b"\\\\n",b"\\n")
        yield b"x" * (6_000_000 + 20_000)
        yield f'\\r\\n--{boundary}--\\r\\n'.encode().replace(b"\\\\r",b"\\r").replace(b"\\\\n",b"\\n")
    result = client.post(f"/studio/products/{a.id}/brand-kit/assets",
        content=stream(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}",
                 "transfer-encoding": "chunked"})
    assert result.status_code == 413
    assert db.scalar(select(BrandAsset)) is None


def test_unused_original_logo_can_be_deleted_but_saved_version_logo_cannot(studio):
    client, db, a, b, root = studio
    assert upload(client, a).status_code == 303
    first = db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id))
    # Initially the file is not pinned to any version and may be released.
    assert client.post(
        f"/studio/products/{b.id}/brand-kit/assets/{first.id}/delete",
        follow_redirects=False).status_code == 404
    assert client.post(
        f"/studio/products/{a.id}/brand-kit/assets/{first.id}/delete",
        follow_redirects=False).status_code == 303
    assert db.get(BrandAsset, first.id) is None
    assert upload(client, a).status_code == 303
    second = db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id))
    assert client.post(f"/studio/products/{a.id}/brand-kit/versions",
                       data=fields(second.id), follow_redirects=False).status_code == 303
    assert client.post(
        f"/studio/products/{a.id}/brand-kit/assets/{second.id}/delete",
        follow_redirects=False).status_code == 409
    assert db.get(BrandAsset, second.id) is not None
    assert "Неиспользуемые файлы" not in client.get(
        f"/studio/products/{a.id}/brand-kit").text


def test_corrupted_unused_logo_can_be_purged_to_release_company_quota(studio):
    client, db, a, _, root = studio
    assert upload(client, a).status_code == 303
    asset = db.scalar(select(BrandAsset).where(BrandAsset.product_id == a.id))
    _, location = verified_asset(db, root, a.id, asset.id)
    location.write_bytes(b"corrupted")
    result = client.post(
        f"/studio/products/{a.id}/brand-kit/assets/{asset.id}/delete",
        follow_redirects=False)
    assert result.status_code == 303
    assert db.get(BrandAsset, asset.id) is None
    assert not location.exists()
