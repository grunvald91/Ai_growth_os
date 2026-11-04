"""Mobile-first company Brand Kit screen and company-isolated read/write endpoints."""
from __future__ import annotations

import html
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.content_studio import _owner_product, studio_page_shell
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.api.multipart_limits import IMAGE_UPLOAD_SLOTS, limited_multipart_form
from app.brand_kit.contracts import ACCENTS, FONTS, GENERATORS, BrandStyle
from app.brand_kit.models import BrandAsset, BrandVersion
from app.brand_kit.service import (
    ASSET_KINDS, MAX_FILE_BYTES, approve_version, delete_unused_logo, read_state,
    save_draft, store_asset, verified_asset,
)
from app.core.config import get_settings
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

LABELS = {
    "original": "Оригинальный логотип",
    "light": "Для светлого фона",
    "dark": "Для тёмного фона",
    "minimal": "Минимализм",
    "photo": "Фотографии",
    "illustration": "Иллюстрации",
    "decorative": "Декоративные элементы",
    "gemini": "Gemini",
    "openai": "ChatGPT Image",
    "own": "Только свои изображения",
}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _bad_request(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(404, str(error))
    return HTTPException(422, str(error)[:400])


def _style_from_form(fields) -> BrandStyle:
    source = {key: fields.get(key, "") for key in (
        "primary", "secondary", "accent", "background", "text",
        "heading", "body", "visual_character", "preferred_generator")}
    return BrandStyle.model_validate({
        "palette": {name: source[name] for name in ("primary", "secondary", "accent", "background", "text")},
        "typography": {"heading": source["heading"], "body": source["body"]},
        "visual_character": source["visual_character"],
        "preferred_generator": source["preferred_generator"],
        "logos": {
            name: fields.get("logo_" + name) or None for name in ASSET_KINDS
        },
        **{name: {
            "logo_position": fields.get(name + "_logo_position", "top-left"),
            "image_position": fields.get(name + "_image_position", "full"),
            "text_align": fields.get(name + "_text_align", "left"),
        } for name in ("hook", "explanation", "cta")},
    })


def _select(name: str, values, value, *, labels=None) -> str:
    options = "".join(
        '<option value="' + esc(item) + '"' + (' selected' if str(item) == str(value) else '') +
        '>' + esc((labels or {}).get(item, item)) + '</option>'
        for item in values
    )
    return '<select name="' + esc(name) + '" id="bk-' + esc(name) + '">' + options + '</select>'


def _brand_body(product_id, db: Session, error: str = "", *, can_approve: bool = True) -> str:
    active, versions, latest = read_state(db, product_id)
    previewing_draft = bool(latest and latest.status == "draft")
    preview_version = latest if previewing_draft else (active or latest)
    style = BrandStyle.model_validate(preview_version.config) if preview_version else BrandStyle()
    base = f"/studio/products/{product_id}/brand-kit"
    assets = list(db.scalars(select(BrandAsset).where(
        BrandAsset.product_id == product_id
    ).order_by(BrandAsset.created_at.desc(), BrandAsset.id.desc()).limit(100)))
    referenced = {
        str(value)
        for configuration in db.scalars(select(BrandVersion.config).where(
            BrandVersion.product_id == product_id))
        for value in (configuration.get("logos") or {}).values() if value
    }
    upload_form = (
        f'<form method="post" enctype="multipart/form-data" action="{base}/assets">'
        '<label>Тип ресурса' + _select("kind", ASSET_KINDS, "original", labels=LABELS) + '</label>'
        '<label>Файл PNG, JPEG или WebP, до 6 МБ<input type="file" name="file" '
        'accept="image/png,image/jpeg,image/webp" required></label>'
        '<button class="btn secondary" type="submit">Загрузить оригинальный файл</button>'
        '</form>'
    )
    cleanup = "".join(
        f'<form method="post" action="{base}/assets/{asset.id}/delete" '
        'onsubmit="return confirm(\'Удалить неиспользуемый логотип?\')">'
        f'<small>{esc(asset.kind)} · {asset.width}×{asset.height} · '
        f'{asset.byte_size / 1_000_000:.2f} МБ</small>'
        '<button type="submit" class="btn danger">Удалить</button></form>'
        for asset in assets if str(asset.id) not in referenced
    )
    colors = "".join(
        f'<label>{esc(label)}<input type="color" name="{name}" '
        f'value="{esc(getattr(style.palette, name))}" required></label>'
        for name, label in (
            ("primary", "Основной"), ("secondary", "Дополнительный"),
            ("accent", "Акцентный"), ("background", "Фон"), ("text", "Текст")))
    logo_selects = []
    for kind in ASSET_KINDS:
        options = [('', "Не выбран")] + [
            (str(asset.id), f"{asset.width}×{asset.height} · {asset.media_type.split('/')[-1].upper()} · {str(asset.id)[:8]}")
            for asset in assets if asset.kind == kind]
        selected = getattr(style.logos, kind)
        logo_selects.append(
            '<label>' + LABELS[kind] +
            _select("logo_" + kind, [v for v, _ in options], str(selected) if selected else '',
                    labels=dict(options)) + '</label>')
    position_names = (
        ("top-left", "Сверху слева"), ("top-right", "Сверху справа"),
        ("bottom-left", "Снизу слева"), ("bottom-right", "Снизу справа"))
    positions = "".join(
        '<fieldset class="bk-frame-layout"><legend>' + esc(label) + '</legend>' +
        '<label>Логотип' + _select(name + "_logo_position",
            [v for v, _ in position_names], getattr(style, name).logo_position,
            labels=dict(position_names)) + '</label>' +
        '<label>Фотография' + _select(name + "_image_position",
            ("full", "top", "bottom"), getattr(style, name).image_position,
            labels={"full": "На весь кадр", "top": "Верхняя половина",
                    "bottom": "Нижняя половина"}) + '</label>' +
        '<label>Выравнивание текста' + _select(name + "_text_align",
            ("left", "center", "right"), getattr(style, name).text_align,
            labels={"left": "Слева", "center": "По центру", "right": "Справа"}) +
        '</label></fieldset>'
        for name, label in (("hook", "Кадр 1 · внимание"),
                            ("explanation", "Кадр 2 · раскрытие"),
                            ("cta", "Кадр 3 · призыв")))
    settings_form = (
        f'<form id="brand-kit-form" method="post" action="{base}/versions">'
        '<h3>Логотипы</h3><div class="bk-grid">' + "".join(logo_selects) + '</div>'
        '<h3>Цвета</h3><div class="bk-colors">' + colors + '</div>'
        '<h3>Шрифты</h3><div class="bk-grid"><label>Заголовки' +
        _select("heading", FONTS, style.typography.heading) + '</label><label>Основной текст' +
        _select("body", FONTS, style.typography.body) + '</label></div>'
        '<h3>Характер и генератор</h3><div class="bk-grid"><label>Визуальный стиль' +
        _select("visual_character", ACCENTS, style.visual_character, labels=LABELS) +
        '</label><label>Фоны' +
        _select("preferred_generator", GENERATORS, style.preferred_generator, labels=LABELS) +
        '</label></div><h3>Композиции кадров</h3><div class="bk-grid">' + positions + '</div>'
        '<button class="btn primary" type="submit">Сохранить новую версию</button>'
        '<p class="sg-muted">Сохранение — черновик. В генерации используется только утверждённая версия.</p>'
        '</form>'
    )
    selected_logo = style.logos.original
    logo_html = (f'<img class="bk-preview-logo" alt="Логотип компании" src="{base}/assets/{selected_logo}">'
                 if selected_logo else '<span class="bk-no-logo">Логотип не выбран</span>')
    preview = (
        '<div class="bk-preview-row" id="bk-preview">'
        + "".join(
            f'<div class="bk-phone" data-role="{role}" data-position="{getattr(style, role).logo_position}"><div class="bk-safe"><div class="bk-preview-logo-wrap">' +
            logo_html + '</div><div class="bk-preview-content"><small>Пример оформления</small>'
            f'<strong>{esc(title)}</strong><span>{esc(subtitle)}</span>'
            + ('<b class="bk-preview-cta">Записаться</b>' if role == "cta" else '') +
            '</div></div></div>'
            for role, title, subtitle in (
                ("hook", "Что мешает клиентам записаться?", "Первый кадр привлекает внимание"),
                ("explanation", "Покажите простое решение", "Понятная мысль крупным текстом"),
                ("cta", "Готовы попробовать?", "Завершите серию призывом к действию"))
        ) + '</div>'
    )
    history = "".join(
        '<div class="bk-history-row"><span>Версия ' + str(v.version) + ' · ' +
        ("Утверждена" if v.status == "approved" else "Черновик") +
        (' · действует' if active and v.id == active.id else '') + '</span>' +
        (f'<form method="post" action="{base}/versions/{v.id}/approve">'
         '<button class="btn secondary" type="submit">' +
         ("Вернуть версию" if v.status == "approved" else "Утвердить") +
         '</button></form>' if can_approve and (not active or v.id != active.id) else '') + '</div>'
        for v in versions[:20]
    )
    css = """<style>
@font-face{font-family:"Manrope Brand";src:url("/studio/assets/brand-manrope.woff2") format("woff2");font-weight:100 900;font-style:normal;font-display:swap}
@font-face{font-family:"DejaVu Brand";src:url("/studio/assets/brand-dejavu.ttf") format("truetype");font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:"DejaVu Brand";src:url("/studio/assets/brand-dejavu-bold.ttf") format("truetype");font-weight:700;font-style:normal;font-display:swap}
.bk-wrap{display:grid;grid-template-columns:minmax(0,1fr) minmax(270px,.75fr);gap:14px}
.bk-wrap section{min-width:0}.bk-wrap h3{font-size:15px;margin:15px 0 10px}
.bk-wrap form label{display:grid;gap:6px;font-size:12px;font-weight:650;margin:0 0 12px}
.bk-frame-layout{min-width:0;border:1px solid var(--sg-line,#dde0e5);border-radius:10px;padding:10px}
.bk-frame-layout legend{font-size:12px;font-weight:700;padding:0 5px}
.bk-wrap select,.bk-wrap input[type=color],.bk-wrap input[type=file]{width:100%;min-height:42px;border:1px solid var(--sg-line,#d7dce5);border-radius:9px;background:var(--sg-card,#fff);color:var(--sg-text,#17212f);padding:8px}
.bk-wrap input[type=color]{padding:3px}.bk-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 12px}
.bk-colors{display:grid;grid-template-columns:repeat(auto-fit,minmax(85px,1fr));gap:8px}
.bk-preview-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.bk-phone{aspect-ratio:9/16;border-radius:11px;overflow:hidden;min-width:0;
background:var(--bk-background);color:var(--bk-text);font-family:var(--bk-font)}
.bk-safe{height:100%;padding:16% 8% 20%;display:flex;flex-direction:column;justify-content:space-between;gap:12px}
.bk-preview-logo{max-width:64%;max-height:30px;object-fit:contain;object-position:left center}
.bk-no-logo{font-size:9px;opacity:.7}
.bk-phone[data-position^="bottom"] .bk-preview-logo-wrap{order:2}
.bk-phone[data-position$="right"] .bk-preview-logo-wrap{text-align:right}
.bk-phone[data-position$="right"] .bk-preview-logo{object-position:right center}.bk-preview-content{display:grid;gap:8px;min-width:0}
.bk-preview-content small{font-size:9px;opacity:.75}.bk-preview-content strong{font-size:clamp(10px,1.6vw,21px);line-height:1.2;overflow-wrap:anywhere}
.bk-preview-content span{font-size:clamp(9px,1.2vw,15px);line-height:1.3;overflow-wrap:anywhere}
.bk-preview-cta{padding:7px;background:var(--bk-accent);color:#fff;border-radius:6px;font-size:10px;text-align:center}
.bk-history-row{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 0;border-bottom:1px solid var(--sg-line,#dde0e5);font-size:12px}
.bk-history-row form{margin:0}.bk-history-row button{min-height:34px}
@media(max-width:820px){.bk-wrap{grid-template-columns:minmax(0,1fr)}.bk-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.bk-preview-content strong{font-size:clamp(10px,3vw,18px)}.bk-preview-content span{font-size:clamp(9px,2.2vw,14px)}}
@media(max-width:370px){.bk-grid{grid-template-columns:minmax(0,1fr)}}
</style>"""
    active_label = f"Действует версия {active.version}" if active else "Утверждённого стиля пока нет"
    content = (
        css + (f'<p role="alert" class="sg-warning">{esc(error)}</p>' if error else '') +
        f'<p role="status"><strong>{esc(active_label)}</strong>. '
        f'Черновик: {latest.version if latest else "нет"}.</p>'
        '<div class="bk-wrap"><div>'
        '<section class="sg-surface"><h2>1. Исходные ресурсы</h2>' + upload_form +
        ('<details><summary>Неиспользуемые файлы · освободить место</summary>'
         + cleanup + '</details>' if cleanup else '') +
        f'<a class="btn secondary" href="{base}/library">Хранилище · материалы и лимиты</a></section>'
        '<section class="sg-surface"><h2>2. Настройки фирменного стиля</h2>' + settings_form +
        '</section></div><div>'
        '<section class="sg-surface"><h2>Предварительный просмотр 9:16</h2>' +
        ('<p class="sg-warning">Предпросмотр черновика · ещё не действует в Stories.</p>'
         if previewing_draft else
         '<p class="sg-muted">Предпросмотр действующего фирменного стиля.</p>') +
        '<p class="sg-muted">Три согласованных типа кадров. Это макет, а не результат ИИ.</p>' +
        preview + '</section><section class="sg-surface"><h2>История версий</h2>' +
        (history or '<p class="sg-muted">Версий пока нет.</p>') +
        '</section></div></div>'
    )
    content += """<script>
(function(){
  const form=document.getElementById('brand-kit-form');
  const preview=document.getElementById('bk-preview');
  if(!form||!preview)return;
  const update=()=>{
    for(const key of ['background','text','accent']){
      const field=form.elements.namedItem(key);
      if(field) preview.style.setProperty('--bk-'+key,field.value);
    }
    for (const role of ['hook','explanation','cta']) {
      const frame=preview.querySelector('[data-role="'+role+'"]');
      const position=form.elements.namedItem(role+'_logo_position');
      if(frame&&position) frame.dataset.position=position.value;
      const align=form.elements.namedItem(role+'_text_align');
      const content=frame&&frame.querySelector('.bk-preview-content');
      if(content&&align) content.style.textAlign=align.value;
    }
    const font=form.elements.namedItem('heading');
    if(font)preview.style.setProperty('--bk-font',font.value==='Manrope' ? '"Manrope Brand",sans-serif' : '"DejaVu Brand",sans-serif');
    const logo=form.elements.namedItem('logo_original');
    const src=logo&&logo.value ? '""" + base + """/assets/'+logo.value : '';
    preview.querySelectorAll('.bk-preview-logo-wrap').forEach(el=>{
      const img=el.querySelector('img');
      if(src){ if(img)img.src=src; else {el.textContent='';const next=document.createElement('img');next.className='bk-preview-logo';next.alt='Логотип компании';next.src=src;el.appendChild(next);} }
      else el.textContent='Логотип не выбран';
    });
  };
  form.addEventListener('input',update);form.addEventListener('change',update);update();
})();
</script>"""
    return content


@router.get("/studio/products/{product_id}/brand-kit", response_class=HTMLResponse)
def brand_kit_page(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    can_approve = (
        getattr(request.state, "studio_product_ids", None) is None
        or getattr(request.state, "studio_sso_role", None) == "owner"
        or getattr(request.state, "studio_brand_approver", False))
    body = _brand_body(product_id, db, can_approve=can_approve)
    return HTMLResponse(studio_page_shell(
        product, active="profile", title="Фирменный стиль",
        subtitle="Логотип, цвета, шрифты и три шаблона для Stories.", body=body),
        headers={"Cache-Control": "private, no-store"})


@router.get("/studio/products/{product_id}/brand-kit/state")
def brand_kit_state(product_id: UUID, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    active, versions, latest = read_state(db, product_id)
    payload = {
        "active_version_id": str(active.id) if active else None,
        "latest_draft_id": str(latest.id) if latest and latest.status == "draft" else None,
        "versions": [
            {"id": str(v.id), "version": v.version, "status": v.status,
             "config": v.config, "approved_at": v.approved_at.isoformat() if v.approved_at else None}
            for v in versions
        ],
    }
    return JSONResponse(payload, headers={"Cache-Control": "private, no-store"})


def _require_brand_owner(request: Request) -> None:
    """Only a platform operator or the actual SSO business owner may approve."""
    if getattr(request.state, "studio_product_ids", None) is None:
        return
    if (getattr(request.state, "studio_sso_role", None) != "owner"
            and not getattr(request.state, "studio_brand_approver", False)):
        raise HTTPException(403, "Утвердить стиль может владелец или уполномоченный сотрудник")


@router.post("/studio/products/{product_id}/brand-kit/assets")
async def upload_brand_asset(product_id: UUID, request: Request,
                             db: Session = Depends(get_db),
                             settings = Depends(get_settings)):
    _owner_product(db, product_id)
    async with IMAGE_UPLOAD_SLOTS:
        form = await limited_multipart_form(
            request, max_body=MAX_FILE_BYTES + 12_000, max_files=1, max_fields=3)
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            await form.close()
            raise HTTPException(422, "Выберите изображение")
        try:
            raw = await upload.read(MAX_FILE_BYTES + 1)
            await run_in_threadpool(store_asset, db, settings.products_root,
                                    product_id, str(form.get("kind")), raw)
        except (ValueError, LookupError) as exc:
            raise _bad_request(exc) from None
        finally:
            await form.close()
    return RedirectResponse(f"/studio/products/{product_id}/brand-kit", status_code=303)


@router.get("/studio/products/{product_id}/brand-kit/assets/{asset_id}")
def read_brand_asset(product_id: UUID, asset_id: UUID,
                     db: Session = Depends(get_db),
                     settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        record, path = verified_asset(db, settings.products_root, product_id, asset_id)
    except (LookupError, ValueError):
        raise HTTPException(404, "Файл не найден") from None
    return FileResponse(path, media_type=record.media_type,
        headers={"Cache-Control": "private, no-store",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.post("/studio/products/{product_id}/brand-kit/versions")
async def new_brand_version(product_id: UUID, request: Request,
                            db: Session = Depends(get_db),
                            settings = Depends(get_settings)):
    _owner_product(db, product_id)
    form = await request.form(max_fields=35, max_files=0)
    try:
        style = _style_from_form(form)
        save_draft(db, settings.products_root, product_id,
                   style.model_dump(mode="json"))
    except (ValueError, LookupError, ValidationError) as exc:
        raise _bad_request(exc) from None
    return RedirectResponse(f"/studio/products/{product_id}/brand-kit", status_code=303)


@router.post("/studio/products/{product_id}/brand-kit/versions/{version_id}/approve")
def approve_brand(product_id: UUID, version_id: UUID, request: Request,
                  db: Session = Depends(get_db),
                  settings = Depends(get_settings)):
    _owner_product(db, product_id)
    _require_brand_owner(request)
    try:
        approve_version(db, settings.products_root, product_id, version_id)
    except (ValueError, LookupError, ValidationError) as exc:
        raise _bad_request(exc) from None
    return RedirectResponse(f"/studio/products/{product_id}/brand-kit", status_code=303)


@router.get("/studio/assets/brand-manrope.woff2")
def brand_preview_manrope():
    from pathlib import Path
    return FileResponse(Path(__file__).resolve().parent / "assets" / "manrope.woff2",
                        media_type="font/woff2", headers={"Cache-Control": "private, max-age=86400"})

@router.get("/studio/assets/brand-dejavu.ttf")
def brand_preview_dejavu():
    from pathlib import Path
    path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not path.is_file():
        raise HTTPException(503, "DejaVu недоступен")
    return FileResponse(path, media_type="font/ttf", headers={"Cache-Control": "private, max-age=86400"})

@router.get("/studio/assets/brand-dejavu-bold.ttf")
def brand_preview_dejavu_bold():
    from pathlib import Path
    path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not path.is_file():
        raise HTTPException(503, "DejaVu недоступен")
    return FileResponse(path, media_type="font/ttf", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/studio/products/{product_id}/brand-kit/assets/{asset_id}/delete")
def remove_unused_brand_logo(product_id: UUID, asset_id: UUID,
                             db: Session = Depends(get_db),
                             settings = Depends(get_settings)):
    _owner_product(db, product_id)
    try:
        delete_unused_logo(db, settings.products_root, product_id, asset_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/brand-kit", status_code=303)
