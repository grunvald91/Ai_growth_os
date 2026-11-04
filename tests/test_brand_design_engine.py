"""Deterministic Stories composition and tenant-isolation regressions."""
from __future__ import annotations

import hashlib
import io
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.content_studio_auth import _apply_scope, require_studio_auth
from app.brand_kit.contracts import BrandStyle
from app.brand_kit.design_models import BrandBackground, BrandComposition, BrandRender
from app.brand_kit.design_service import (
    create_composition, store_background, verified_background, verified_render,
)
from app.brand_kit.renderer import (
    HEIGHT, SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT, SAFE_TOP, WIDTH,
    StoryFrame, load_font, render_story,
)
from app.brand_kit.service import approve_version, save_draft, store_asset
from app.core.config import get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Product


def png_logo():
    stream = io.BytesIO()
    Image.new("RGBA", (340, 132), (235, 30, 160, 255)).save(stream, format="PNG")
    return stream.getvalue()


def picture(fmt="JPEG"):
    stream = io.BytesIO()
    Image.new("RGB", (1600, 900), (12, 71, 85)).save(stream, format=fmt)
    return stream.getvalue()


@pytest.fixture
def company(tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCTS_ROOT", str(tmp_path))
    get_settings.cache_clear()
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        a = Product(name="Салон", slug="design-a")
        b = Product(name="Кафе", slug="design-b")
        db.add_all([a, b])
        db.commit()
        logo_a = store_asset(db, tmp_path, a.id, "original", png_logo())
        logo_b = store_asset(db, tmp_path, b.id, "original", png_logo())
        style_a = BrandStyle.model_validate({
            "logos": {"original": str(logo_a.id)},
            "palette": {"primary": "#2F5BEB", "secondary": "#0D1B2A",
                        "accent": "#EE20A0", "background": "#0D1B2A",
                        "text": "#FAFAFA"},
        })
        style_b = BrandStyle.model_validate({"logos": {"original": str(logo_b.id)}})
        va = save_draft(db, tmp_path, a.id, style_a.model_dump(mode="json"))
        vb = save_draft(db, tmp_path, b.id, style_b.model_dump(mode="json"))
        approve_version(db, tmp_path, a.id, va.id)
        approve_version(db, tmp_path, b.id, vb.id)

        def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        with TestClient(app) as client:
            yield client, db, a, b, va, vb, tmp_path, style_a
        app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()
    get_settings.cache_clear()


def frame(template="hook", **overrides):
    body = dict(
        template=template, heading="Записи теряются каждый день",
        body="Три простых шага помогают клиентам записаться.",
        cta="Записаться онлайн" if template == "cta" else "",
    )
    body.update(overrides)
    return StoryFrame.model_validate(body)


def test_renderer_outputs_three_true_stories_with_original_logo_and_safe_boxes(company):
    _, _, _, _, _, _, _, style = company
    logo = png_logo()
    img = picture()
    results = [render_story(style, frame(template), logo=logo, background=img)
               for template in ("hook", "explanation", "cta")]
    assert len({hashlib.sha256(item.png).hexdigest() for item in results}) == 3
    for result in results:
        with Image.open(io.BytesIO(result.png)) as rendered:
            assert rendered.size == (WIDTH, HEIGHT)
            assert rendered.format == "PNG"
            lx1, ly1, lx2, ly2 = result.logo_box
            assert rendered.getpixel(((lx1 + lx2) // 2, (ly1 + ly2) // 2)) == (235, 30, 160)
        for box in (result.logo_box, result.logo_backdrop_box, result.panel_box, result.heading_box, result.body_box, result.cta_box):
            if box is not None:
                assert SAFE_LEFT <= box[0] < box[2] <= SAFE_RIGHT
                assert SAFE_TOP <= box[1] < box[3] <= SAFE_BOTTOM
    assert results[-1].cta_box is not None


def test_long_text_fails_rather_than_clipping(company):
    _, _, _, _, _, _, _, style = company
    with pytest.raises(ValueError, match="помещается|Слишком длинная"):
        render_story(style, frame(body="Очень " * 55), logo=png_logo())
    with pytest.raises(ValueError, match="требует призыва"):
        render_story(style, frame("cta", cta=""), logo=png_logo())


def test_cyrillic_font_is_real_and_no_silent_font_fallback(company):
    _, _, _, _, _, _, _, style = company
    font = load_font("Manrope", 60, True)
    assert font.getmask("Привет, мир! Ёж").getbbox() is not None
    with pytest.raises(ValueError, match="не установлен"):
        load_font("Придуманный шрифт", 42, False)
    result = render_story(style, frame(heading="Услуги и запись: ёлка, счастье"),
                          logo=png_logo())
    assert result.png.startswith(b"\x89PNG\r\n\x1a\n")


def test_uploaded_background_and_composition_retries_are_idempotent(company, monkeypatch):
    client, db, a, _, va, _, root, _ = company
    # A local change must never enter the paid image provider path.
    from app.content import creative_images
    def forbidden(*args, **kwargs):
        raise AssertionError("Image generation must not be called")
    monkeypatch.setattr(creative_images, "request_image", forbidden)
    uploaded = client.post(
        f"/studio/products/{a.id}/brand-kit/backgrounds",
        files={"file": ("background.jpg", picture(), "image/jpeg")})
    assert uploaded.status_code == 201, uploaded.text
    background_id = uploaded.json()["id"]
    repeated_upload = client.post(
        f"/studio/products/{a.id}/brand-kit/backgrounds",
        files={"file": ("background.jpg", picture(), "image/jpeg")})
    assert repeated_upload.status_code == 201
    assert repeated_upload.json()["id"] == background_id
    payload = {
        "version_id": str(va.id), "frame_key": "story:demo:frame1",
        "background_id": background_id,
        "frame": frame().model_dump(mode="json"),
    }
    a1 = client.post(f"/studio/products/{a.id}/brand-kit/compose", json=payload)
    assert a1.status_code == 201, a1.text
    a2 = client.post(f"/studio/products/{a.id}/brand-kit/compose", json=payload)
    assert a2.status_code == 201
    assert a1.json()["id"] == a2.json()["id"]
    assert db.scalar(select(func.count()).select_from(BrandComposition)) == 1
    assert db.scalar(select(func.count()).select_from(BrandRender)) == 1
    image = client.get(a1.json()["url"])
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert image.headers["cache-control"] == "private, no-store"
    assert hashlib.sha256(image.content).hexdigest() == a1.json()["sha256"]
    conflicting = {**payload, "frame": frame(heading="Другая тема").model_dump(mode="json")}
    assert client.post(f"/studio/products/{a.id}/brand-kit/compose",
                       json=conflicting).status_code == 409


def test_previous_images_do_not_change_when_approved_brand_changes(company):
    client, db, a, _, va, _, root, style = company
    original = create_composition(db, root, a.id, va.id, frame(),
                                  frame_key="pinned:frame1")
    baseline = verified_render(db, root, a.id, original.id)[1].read_bytes()
    new_style = style.model_copy(deep=True)
    new_style.palette.primary = "#9900CC"
    new_style.palette.background = "#FFFFFF"
    new_style.palette.text = "#111111"
    next_version = save_draft(db, root, a.id, new_style.model_dump(mode="json"))
    approve_version(db, root, a.id, next_version.id)
    assert next_version.id != va.id
    record = db.scalar(select(BrandComposition).where(
        BrandComposition.product_id == a.id,
        BrandComposition.frame_key == "pinned:frame1"))
    assert record.version_id == va.id
    assert record.brand_snapshot["palette"]["background"] == "#0D1B2A"
    assert verified_render(db, root, a.id, original.id)[1].read_bytes() == baseline


def test_asset_endpoints_and_compose_reject_other_company_ids(company):
    client, db, a, b, va, vb, root, _ = company
    background = store_background(db, root, a.id, picture())
    assert client.get(f"/studio/products/{b.id}/brand-kit/backgrounds/{background.id}").status_code == 404
    payload = {
        "version_id": str(va.id), "frame_key": "isolation:test",
        "frame": frame().model_dump(mode="json"),
        "background_id": str(background.id),
    }
    assert client.post(f"/studio/products/{b.id}/brand-kit/compose",
                       json=payload).status_code == 404
    result = client.post(f"/studio/products/{a.id}/brand-kit/compose", json=payload)
    assert result.status_code == 201, result.text
    assert client.get(f"/studio/products/{b.id}/brand-kit/renders/{result.json()['id']}").status_code == 404
    payload["version_id"] = str(vb.id)
    payload["frame_key"] = "isolation:version"
    assert client.post(f"/studio/products/{a.id}/brand-kit/compose",
                       json=payload).status_code == 404


def test_corrupted_background_and_render_are_rejected(company):
    client, db, a, _, va, _, root, _ = company
    background = store_background(db, root, a.id, picture())
    _, background_file = verified_background(db, root, a.id, background.id)
    background_file.write_bytes(b"tampered")
    assert client.get(f"/studio/products/{a.id}/brand-kit/backgrounds/{background.id}").status_code == 404
    assert client.post(f"/studio/products/{a.id}/brand-kit/compose", json={
        "version_id": str(va.id), "frame_key": "invalid:background",
        "frame": frame().model_dump(mode="json"), "background_id": str(background.id),
    }).status_code == 404
    render = create_composition(db, root, a.id, va.id, frame(),
                                frame_key="good:no-background")
    _, file = verified_render(db, root, a.id, render.id)
    file.write_bytes(b"tampered")
    assert client.get(f"/studio/products/{a.id}/brand-kit/renders/{render.id}").status_code == 404
    repaired = client.post(f"/studio/products/{a.id}/brand-kit/compose", json={
        "version_id": str(va.id), "frame_key": "good:no-background",
        "frame": frame().model_dump(mode="json"),
    })
    assert repaired.status_code == 201, repaired.text
    assert repaired.json()["id"] == str(render.id)
    assert hashlib.sha256(file.read_bytes()).hexdigest() == render.sha256


def test_company_scoped_session_denies_other_company(company):
    client, db, a, b, va, _, _, _ = company
    def limited(request: Request):
        return _apply_scope(request, "tenant", [str(a.id)])
    app.dependency_overrides[require_studio_auth] = limited
    try:
        assert client.post(f"/studio/products/{b.id}/brand-kit/compose", json={
            "version_id": str(va.id), "frame_key": "forbidden:test",
            "frame": frame().model_dump(mode="json")}).status_code == 404
        assert client.get(f"/studio/products/{b.id}/brand-kit/renders/{uuid4()}").status_code == 404
    finally:
        app.dependency_overrides.pop(require_studio_auth, None)


def test_missing_png_restored_with_same_id_and_sha(company):
    client, db, a, _, va, _, root, _ = company
    record = create_composition(db, root, a.id, va.id, frame(), frame_key="recover:file")
    _, path = verified_render(db, root, a.id, record.id)
    original = path.read_bytes()
    path.unlink()
    assert client.get(f"/studio/products/{a.id}/brand-kit/renders/{record.id}").status_code == 404
    answer = client.post(f"/studio/products/{a.id}/brand-kit/compose", json={
        "version_id": str(va.id), "frame_key": "recover:file",
        "frame": frame().model_dump(mode="json"),
    })
    assert answer.status_code == 201, answer.text
    assert answer.json()["id"] == str(record.id)
    assert path.read_bytes() == original
    assert db.scalar(select(func.count()).select_from(BrandRender)) == 1


def test_png_recovery_refuses_mismatch_after_renderer_upgrade(company, monkeypatch):
    client, db, a, _, va, _, root, _ = company
    record = create_composition(db, root, a.id, va.id, frame(), frame_key="recover:mismatch")
    _, path = verified_render(db, root, a.id, record.id)
    path.unlink()
    from app.brand_kit import design_service
    previous = design_service.render_story

    def modified(*args, **kwargs):
        result = previous(*args, **kwargs)
        return result.__class__(**{**vars(result), "png": result.png + b"extra"})
    monkeypatch.setattr(design_service, "render_story", modified)
    answer = client.post(f"/studio/products/{a.id}/brand-kit/compose", json={
        "version_id": str(va.id), "frame_key": "recover:mismatch",
        "frame": frame().model_dump(mode="json"),
    })
    assert answer.status_code == 404
    assert not path.exists()


def test_cmap_detects_unsupported_character_and_uses_real_weights(company):
    _, _, _, _, _, _, _, style = company
    assert load_font("Manrope", 60, True)
    assert load_font("Manrope", 60, False)
    with pytest.raises(ValueError, match="U\\+"):
        render_story(style, frame(heading="Креатив 🦄"), logo=png_logo())


def test_background_chunked_body_limit(company):
    client, db, a, _, _, _, _, _ = company
    boundary = "photo-limit"
    def chunks():
        yield (f'--{boundary}\\r\\nContent-Disposition: form-data; '
               'name="file"; filename="big.png"\\r\\n'
               'Content-Type: image/png\\r\\n\\r\\n').encode()
        yield b"x" * (12_000_000 + 20_000)
        yield f'\\r\\n--{boundary}--\\r\\n'.encode()
    r = client.post(f"/studio/products/{a.id}/brand-kit/backgrounds",
        content=chunks(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}",
                 "transfer-encoding": "chunked"})
    assert r.status_code == 413
    assert db.scalar(select(func.count()).select_from(BrandBackground)) == 0


def test_background_sections_and_text_alignment(company):
    _, _, _, _, _, _, _, brand = company
    configured = brand.model_copy(deep=True)
    configured.hook.image_position = "bottom"
    configured.hook.text_align = "right"
    result = render_story(configured, frame(), logo=png_logo(), background=picture())
    assert result.background_mode == "bottom"
    with Image.open(io.BytesIO(result.png)) as image:
        assert image.getpixel((30, 30)) == (13, 27, 42)
        assert image.getpixel((30, 1400)) == (12, 71, 85)


def test_new_frames_require_active_brand_but_old_frames_remain_immutable(company):
    client, db, a, _, va, _, root, style = company
    initial = create_composition(
        db, root, a.id, va.id, frame(), frame_key="active:original")
    baseline = verified_render(db, root, a.id, initial.id)[1].read_bytes()
    changed = style.model_copy(deep=True)
    changed.palette.primary = "#BB0022"
    changed.palette.secondary = "#00BB22"
    newer = save_draft(db, root, a.id, changed.model_dump(mode="json"))
    approve_version(db, root, a.id, newer.id)
    old_new = client.post(f"/studio/products/{a.id}/brand-kit/compose", json={
        "version_id": str(va.id), "frame_key": "active:forbidden",
        "frame": frame().model_dump(mode="json")})
    assert old_new.status_code == 409
    old_retry = client.post(f"/studio/products/{a.id}/brand-kit/compose", json={
        "version_id": str(va.id), "frame_key": "active:original",
        "frame": frame().model_dump(mode="json")})
    assert old_retry.status_code == 201
    assert old_retry.json()["id"] == str(initial.id)
    assert verified_render(db, root, a.id, initial.id)[1].read_bytes() == baseline
    current = client.post(f"/studio/products/{a.id}/brand-kit/compose", json={
        "version_id": str(newer.id), "frame_key": "active:current",
        "frame": frame().model_dump(mode="json")})
    assert current.status_code == 201, current.text


def test_explicit_background_cleanup_respects_render_references_and_tenant(company):
    client, db, a, b, va, _, root, _ = company
    unused = store_background(db, root, a.id, picture())
    _, unused_path = verified_background(db, root, a.id, unused.id)
    assert client.delete(
        f"/studio/products/{b.id}/brand-kit/backgrounds/{unused.id}").status_code == 404
    assert client.delete(
        f"/studio/products/{a.id}/brand-kit/backgrounds/{unused.id}").status_code == 200
    assert not unused_path.exists()

    used = store_background(db, root, a.id, picture("PNG"))
    render = create_composition(db, root, a.id, va.id, frame(),
                                frame_key="clean:used", background_id=used.id)
    assert client.delete(
        f"/studio/products/{a.id}/brand-kit/backgrounds/{used.id}").status_code == 409
    _, render_path = verified_render(db, root, a.id, render.id)
    assert client.delete(
        f"/studio/products/{b.id}/brand-kit/renders/{render.id}").status_code == 404
    assert client.delete(
        f"/studio/products/{a.id}/brand-kit/renders/{render.id}").status_code == 200
    assert not render_path.exists()
    assert client.delete(
        f"/studio/products/{a.id}/brand-kit/backgrounds/{used.id}").status_code == 200


def test_brand_storage_quota_limits_counts_and_bytes(company):
    from app.brand_kit.quotas import enforce_quota
    from app.brand_kit.models import BrandAsset
    client, db, a, _, va, _, root, style = company
    with pytest.raises(ValueError, match="лимит файлов"):
        enforce_quota(db, a.id, BrandAsset, additional_bytes=48_000_001,
                      max_files=32, max_bytes=48_000_000)
    enforce_quota(db, a.id, BrandBackground, additional_bytes=5,
                  max_files=80, max_bytes=128_000_000)
    store_background(db, root, a.id, picture())
    with pytest.raises(ValueError, match="лимит файлов"):
        enforce_quota(db, a.id, BrandBackground, additional_bytes=1,
                      max_files=1, max_bytes=128_000_000)


def test_transparent_background_uses_company_background_color(company):
    _, _, _, _, _, _, _, style = company
    picture_rgba = Image.new("RGBA", (600, 1000), (245, 0, 0, 0))
    stream = io.BytesIO()
    picture_rgba.save(stream, "PNG")
    result = render_story(style, frame(), logo=png_logo(), background=stream.getvalue())
    with Image.open(io.BytesIO(result.png)) as img:
        assert img.convert("RGB").getpixel((25, 25)) == (13, 27, 42)


def test_white_logo_is_visible_on_white_brand_backdrop(company):
    from app.brand_kit.contracts import contrast_ratio
    _, _, _, _, _, _, _, style = company
    white_brand = style.model_copy(deep=True)
    white_brand.palette.background = "#FFFFFF"
    white_brand.palette.text = "#000000"
    white_logo = io.BytesIO()
    Image.new("RGB", (320, 140), (255, 255, 255)).save(white_logo, "PNG")
    result = render_story(white_brand, frame(), logo=white_logo.getvalue())
    with Image.open(io.BytesIO(result.png)) as img:
        lx1, ly1, _, _ = result.logo_box
        backdrop = img.convert("RGB").getpixel((lx1 - 7, ly1 - 5))
        backing_hex = "#" + "".join(f"{c:02X}" for c in backdrop)
        assert contrast_ratio("#FFFFFF", backing_hex) >= 3


def test_original_jpeg_logo_obeys_exif_orientation(company):
    from app.brand_kit.renderer import _logo
    logo = Image.new("RGB", (300, 120), (255, 255, 255))
    exif = Image.Exif()
    exif[274] = 6
    stream = io.BytesIO()
    logo.save(stream, "JPEG", exif=exif)
    assert _logo(stream.getvalue()).size == (120, 300)


def test_primary_and_secondary_palette_both_affect_story(company):
    _, _, _, _, _, _, _, style = company
    baseline = render_story(style, frame(), logo=png_logo()).png
    modified = style.model_copy(deep=True)
    modified.palette.primary = "#663399"
    modified.palette.secondary = "#556677"
    updated = render_story(modified, frame(), logo=png_logo()).png
    assert hashlib.sha256(updated).digest() != hashlib.sha256(baseline).digest()
