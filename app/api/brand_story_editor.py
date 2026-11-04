"""Mobile first, tenant-verified editing of finished branded Stories.

Local edits never call a paid model. Each edit is an immutable composition and
artifact. Paid background regeneration has an explicit second confirmation and
a durable intent checkpoint to prevent double submissions/billing.
"""
from __future__ import annotations

import hashlib
import html
import json
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.content_studio import _owner_product, studio_page_shell
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.brand_kit.contracts import BrandStyle
from app.brand_kit.design_models import BrandComposition, BrandRender
from app.brand_kit.design_service import create_composition, store_background
from app.brand_kit.renderer import StoryFrame
from app.brand_kit.models import BrandVersion
from app.content.brand_story_pipeline import (
    assert_task_brand, composed_images, verified_gallery_image,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Artifact, Product, Task
from app.services.artifacts import ArtifactService

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
MAX_FORM_BYTES = 12_000


def esc(value) -> str:
    return html.escape(str(value), quote=True)


async def _fields(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Ожидаются поля формы")
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "Некорректная длина запроса") from None
    if length > MAX_FORM_BYTES:
        raise HTTPException(413, "Слишком большой запрос")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_FORM_BYTES:
            raise HTTPException(413, "Слишком большой запрос")
    try:
        values = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True,
                          max_num_fields=35)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "Некорректные поля формы") from None
    if any(len(v) != 1 for v in values.values()):
        raise HTTPException(422, "Повторяющиеся поля формы")
    return {k: v[0] for k, v in values.items()}


def _source(db: Session, root, company_id: UUID, task_id: UUID, artifact_id: UUID):
    company = _owner_product(db, company_id)
    task = db.scalar(select(Task).where(
        Task.id == task_id, Task.product_id == company_id,
        Task.assigned_agent == "content_creative"))
    record = db.scalar(select(Artifact).where(
        Artifact.id == artifact_id, Artifact.product_id == company_id,
        Artifact.task_id == task_id, Artifact.kind == "creative_story_render"))
    if task is None or record is None:
        raise HTTPException(404, "Фирменный кадр не найден")
    try:
        metadata = json.loads(record.content)
        rid = UUID(metadata["render_id"])
        render = db.scalar(select(BrandRender).where(
            BrandRender.id == rid, BrandRender.product_id == company_id))
        if render is None:
            raise LookupError("Кадр не найден")
        composition = db.scalar(select(BrandComposition).where(
            BrandComposition.id == render.composition_id,
            BrandComposition.product_id == company_id))
        if composition is None:
            raise LookupError("История композиции недоступна")
        version = db.scalar(select(BrandVersion).where(
            BrandVersion.id == composition.version_id,
            BrandVersion.product_id == company_id,
            BrandVersion.status == "approved"))
        if version is None:
            raise LookupError("Утверждённая версия не найдена")
        assert_task_brand(db, company_id, task_id, version)
        verified_gallery_image(db, root, company_id, task_id, record)
    except (LookupError, ValueError, TypeError, KeyError):
        raise HTTPException(404, "Файл кадра или его версия недоступны") from None
    return company, record, metadata, composition


def _new_revision(db, root, company_id, task_id, source, metadata, composition,
                  frame: StoryFrame, style: BrandStyle, token: UUID,
                  background_id: UUID | None = None, paid_model: str | None = None):
    # Exact source artifact remains immutable. Repeated identical POST token
    # reuses its original render and never introduces another history entry.
    db.scalar(select(Product.id).where(Product.id == company_id).with_for_update())
    number = int(metadata["frame_number"])
    key = f"edit-{str(task_id)[:24]}-{number:02d}-{token.hex[:18]}"
    render = create_composition(
        db, root, company_id, composition.version_id, frame, frame_key=key,
        background_id=background_id if background_id is not None else composition.background_id,
        pinned_task_id=task_id, style_override=style)
    # create_composition commits. Re-lock before the artifact idempotency check
    # so simultaneous submits with the same token cannot append two revisions.
    db.scalar(select(Product.id).where(Product.id == company_id).with_for_update())
    for item in composed_images(db, company_id, task_id):
        try:
            if json.loads(item.content).get("render_id") == str(render.id):
                return item
        except (ValueError, TypeError):
            continue
    revisions = [
        int(json.loads(item.content).get("revision", 0))
        for item in composed_images(db, company_id, task_id)
        if int(json.loads(item.content).get("frame_number", -1)) == number
    ]
    latest = max(revisions, default=0) + 1
    artifact = ArtifactService(db).create(
        product_id=company_id, task_id=task_id,
        kind="creative_story_render",
        title=f"Stories · кадр {number} · версия {latest + 1}",
        content=json.dumps({
            "frame_number": number, "render_id": str(render.id),
            "sha256": render.sha256, "filename": f"frame-{number:02d}.png",
            "ratio": "9:16",
            "model": "brand:" + (paid_model if paid_model is not None else
                                  str(metadata.get("model", "own")).removeprefix("brand:")),
            "prompt": "Local edit" if paid_model is None else "Confirmed background regeneration",
            "brand_version_id": str(composition.version_id),
            "revision": latest,
            "source_artifact_id": str(source.id),
            "background_id": str(background_id or composition.background_id)
                             if (background_id or composition.background_id) else None,
        }, ensure_ascii=False))
    db.commit()
    return artifact


def _history(db, root, company_id: UUID, task_id: UUID, frame_no: int) -> str:
    """Every immutable revision is linked to its owner-verified preview and PNG."""
    history = []
    for artifact in composed_images(db, company_id, task_id):
        try:
            meta = json.loads(artifact.content)
            if int(meta.get("frame_number", -1)) != frame_no:
                continue
            verified_gallery_image(db, root, company_id, task_id, artifact)
            revision = int(meta.get("revision", 0))
        except (ValueError, KeyError, TypeError, LookupError, OSError):
            continue
        history.append((revision, artifact.created_at, str(artifact.id), artifact.id))
    history.sort(reverse=True)
    if not history:
        return '<p class="sg-muted">Сохранённых версий пока нет.</p>'
    base = f"/studio/products/{company_id}/tasks/{task_id}"
    items = "".join(
        '<li><span>Версия ' + str(number+1) + '</span> '
        f'<a href="{base}/stories/{artifact_id}/edit">Открыть</a> · '
        f'<a href="{base}/images/{artifact_id}?download=true">PNG</a></li>'
        for number, _, _, artifact_id in history)
    return ('<details class="sg-surface" open><summary>История версий · '
            + str(len(history)) + '</summary><ol>' + items + '</ol></details>')


def _page(product, task_id, artifact_id, *, style, frame, metadata,
          history_html: str = "", error: str = ""):
    base = f"/studio/products/{product.id}/tasks/{task_id}"
    here = f"{base}/stories/{artifact_id}/edit"
    layout = getattr(style, frame.template)
    def options(items, selected):
        return "".join(
            f'<option value="{esc(value)}"{" selected" if value == selected else ""}>'
            f'{esc(label)}</option>'
            for value, label in items)
    def color(name, label):
        return (f'<label class="sg-field"><span>{esc(label)}</span>'
                f'<input name="{name}" type="color" value="{esc(getattr(style.palette, name))}" '
                'required></label>')
    body = f'''
    <style>
    .brand-story-editor{{display:grid;grid-template-columns:minmax(0,310px) minmax(0,1fr);gap:16px;align-items:start}}
    .brand-story-editor img{{width:100%;max-height:74dvh;aspect-ratio:9/16;object-fit:contain;border-radius:12px}}
    .brand-story-editor fieldset{{border:1px solid var(--sg-line,#aaa);border-radius:12px;padding:12px}}
    .brand-story-editor .editor-fields{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}
    .brand-story-editor .editor-fields .wide{{grid-column:1/-1}}
    .brand-story-editor input,.brand-story-editor select,.brand-story-editor textarea{{max-width:100%;width:100%}}
    @media(max-width:720px){{.brand-story-editor{{grid-template-columns:minmax(0,1fr)}}.brand-story-editor img{{max-height:55dvh}}}}
    </style>
    <p><a class="btn secondary" href="{base}">← К результатам задания</a></p>
    {'<p role="alert" class="sg-warning">' + esc(error) + '</p>' if error else ''}
    <div class="brand-story-editor">
      <section class="sg-surface"><img src="{base}/images/{artifact_id}"
       alt="Готовый кадр {esc(metadata['frame_number'])}" />
       <p>Кадр {esc(metadata['frame_number'])} · версия {int(metadata.get("revision",0))+1}.
       Редактирование ниже не вызывает платных моделей.</p>
       <a class="btn secondary" href="{base}/images/{artifact_id}?download=true">Скачать PNG</a>
      </section>
      <section class="sg-surface"><h2>Редактировать кадр</h2>
       <form method="post" action="{here}" class="sg-form">
        <input type="hidden" name="token" value="{uuid4()}">
        <div class="editor-fields">
         <label class="sg-field wide"><span>Заголовок</span>
          <textarea name="heading" maxlength="240" required rows="3">{esc(frame.heading)}</textarea></label>
         <label class="sg-field wide"><span>Основной текст</span>
          <textarea name="body" maxlength="360" rows="3">{esc(frame.body)}</textarea></label>
         <label class="sg-field wide"><span>Призыв к действию</span>
          <input name="cta" maxlength="90" value="{esc(frame.cta)}"></label>
         <label class="sg-field"><span>Шрифт заголовка</span>
          <select name="heading_font">{options([('Manrope','Manrope'),('DejaVu Sans','DejaVu Sans')],style.typography.heading)}</select></label>
         <label class="sg-field"><span>Шрифт текста</span>
          <select name="body_font">{options([('Manrope','Manrope'),('DejaVu Sans','DejaVu Sans')],style.typography.body)}</select></label>
         <label class="sg-field"><span>Положение логотипа</span>
          <select name="logo_position">{options([(v,v) for v in ('top-left','top-right','bottom-left','bottom-right')],layout.logo_position)}</select></label>
         <label class="sg-field"><span>Вариант логотипа</span>
          <select name="logo_variant">{options([(v,v) for v in ('original','light','dark')],frame.logo_variant)}</select></label>
         <label class="sg-field"><span>Положение фона</span>
          <select name="image_position">{options([(v,v) for v in ('full','top','bottom')],layout.image_position)}</select></label>
         <label class="sg-field"><span>Выравнивание текста</span>
          <select name="text_align">{options([(v,v) for v in ('left','center','right')],layout.text_align)}</select></label>
         <label class="sg-field"><span>Фокус кадрирования по горизонтали (0–100)</span>
          <input name="crop_x" type="number" min="0" max="100" step="1" value="{round(frame.crop_x*100)}"></label>
         <label class="sg-field"><span>Фокус кадрирования по вертикали (0–100)</span>
          <input name="crop_y" type="number" min="0" max="100" step="1" value="{round(frame.crop_y*100)}"></label>
         {color('primary','Основной цвет')}{color('secondary','Дополнительный цвет')}
         {color('accent','Акцентный цвет')}{color('background','Цвет фона')}
         {color('text','Цвет текста')}
        </div>
        <p class="sg-muted">Новая версия сохраняется отдельно. Предыдущая остаётся в истории и в базе данных.</p>
        <button class="btn primary" type="submit">Сохранить изменения · бесплатно</button>
       </form>
       <p><a class="btn secondary" href="{base}/stories/{artifact_id}/regenerate-background">
        Перегенерировать только фон · платный вызов</a></p>
      </section>
    </div>
    {history_html}'''
    return HTMLResponse(studio_page_shell(
        product, active="work", title="Редактор Stories",
        subtitle="Результат в фирменном стиле компании. Сохранение бесплатно.", body=body),
        headers={"Cache-Control": "private, no-store"})


@router.get("/studio/products/{product_id}/tasks/{task_id}/stories/{artifact_id}/edit")
def edit_story(product_id: UUID, task_id: UUID, artifact_id: UUID,
               db: Session = Depends(get_db), settings=Depends(get_settings)):
    product, _, metadata, composition = _source(
        db, settings.products_root, product_id, task_id, artifact_id)
    return _page(product, task_id, artifact_id, style=BrandStyle.model_validate(
        composition.brand_snapshot), frame=StoryFrame.model_validate(
        composition.spec), metadata=metadata,
        history_html=_history(db, settings.products_root, product_id, task_id,
                              int(metadata["frame_number"])))


@router.post("/studio/products/{product_id}/tasks/{task_id}/stories/{artifact_id}/edit")
async def save_story_edit(product_id: UUID, task_id: UUID, artifact_id: UUID,
                          request: Request, db: Session = Depends(get_db),
                          settings=Depends(get_settings)):
    product, source, metadata, composition = _source(
        db, settings.products_root, product_id, task_id, artifact_id)
    fields = await _fields(request)
    try:
        token = UUID(fields["token"])
        old_style = BrandStyle.model_validate(composition.brand_snapshot)
        old_frame = StoryFrame.model_validate(composition.spec)
        changes = old_style.model_dump(mode="json")
        for name in ("primary", "secondary", "accent", "background", "text"):
            changes["palette"][name] = fields[name]
        changes["typography"]["heading"] = fields["heading_font"]
        changes["typography"]["body"] = fields["body_font"]
        layout = changes[old_frame.template]
        for name in ("logo_position", "image_position", "text_align"):
            layout[name] = fields[name]
        style = BrandStyle.model_validate(changes)
        new_frame = StoryFrame.model_validate({
            **old_frame.model_dump(mode="json"),
            **{key:fields[key] for key in ("heading", "body", "cta", "logo_variant")},
            "crop_x": int(fields["crop_x"]) / 100,
            "crop_y": int(fields["crop_y"]) / 100,
        })
        result = _new_revision(
            db, settings.products_root, product_id, task_id, source,
            metadata, composition, new_frame, style, token)
    except (KeyError, ValueError, TypeError) as exc:
        db.rollback()
        return _page(product, task_id, artifact_id, style=BrandStyle.model_validate(
            composition.brand_snapshot), frame=StoryFrame.model_validate(
            composition.spec), metadata=metadata,
            history_html=_history(db, settings.products_root, product_id, task_id,
                                  int(metadata["frame_number"])),
            error=f"Не удалось сохранить: {str(exc)[:220]}")
    return RedirectResponse(
        f"/studio/products/{product_id}/tasks/{task_id}/stories/{result.id}/edit",
        status_code=303)


@router.get("/studio/products/{product_id}/tasks/{task_id}/stories/{artifact_id}/regenerate-background")
def confirm_background(product_id: UUID, task_id: UUID, artifact_id: UUID,
                       db: Session = Depends(get_db), settings=Depends(get_settings)):
    product, _, metadata, _ = _source(
        db, settings.products_root, product_id, task_id, artifact_id)
    uri = f"/studio/products/{product_id}/tasks/{task_id}/stories/{artifact_id}/regenerate-background"
    body = (f'<p><a class="btn secondary" href="{uri[:-len("/regenerate-background")]}/edit">← Редактор</a></p>'
            '<section class="sg-surface"><h2>Подтвердить платный запрос</h2>'
            '<p>Фон будет создан заново выбранной ИИ-моделью. '
            'Логотип и текст останутся оригинальными и будут наложены локально. '
            'Цена зависит от провайдера. Если ответ модели потерян, '
            'автоматического повторного списания не будет.</p>'
            f'<form method="post" action="{uri}" class="sg-form">'
            f'<input type="hidden" name="token" value="{uuid4()}">'
            '<label class="sg-field"><span>Модель</span><select name="model">'
            '<option value="gemini">Gemini</option>'
            '<option value="openai">ChatGPT Image</option></select></label>'
            '<label><input type="checkbox" name="confirm" value="yes" required>'
            ' Подтверждаю один платный вызов генерации фона</label>'
            '<button class="btn primary" type="submit">Перегенерировать фон · платно</button>'
            '</form></section>')
    return HTMLResponse(studio_page_shell(
        product, active="work", title="Новый фон Stories",
        subtitle=f"Кадр {metadata['frame_number']}: требуется явное подтверждение", body=body),
        headers={"Cache-Control": "private, no-store"})


@router.post("/studio/products/{product_id}/tasks/{task_id}/stories/{artifact_id}/regenerate-background")
async def regenerate_background(product_id: UUID, task_id: UUID, artifact_id: UUID,
                                request: Request, db: Session = Depends(get_db),
                                settings=Depends(get_settings)):
    from app.content.creative import CreativeDraft
    from app.content.creative_images import image_prompt, png_output, request_image, resolve_model
    product, source, metadata, composition = _source(
        db, settings.products_root, product_id, task_id, artifact_id)
    fields = await _fields(request)
    if fields.get("confirm") != "yes" or fields.get("model") not in {"gemini", "openai"}:
        raise HTTPException(422, "Необходимо подтвердить платную генерацию")
    try:
        token = UUID(fields["token"])
    except (ValueError, KeyError):
        raise HTTPException(422, "Некорректный идентификатор подтверждения") from None
    model = resolve_model(settings, fields["model"])
    brief = db.scalar(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task_id,
        Artifact.kind == "creative_brief").order_by(
        Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
    if brief is None:
        raise HTTPException(409, "Исходная раскадровка недоступна")
    try:
        draft = CreativeDraft.model_validate({
            k:v for k,v in json.loads(brief.content).items()
            if k in CreativeDraft.model_fields})
        number = int(metadata["frame_number"])
        scene = draft.frames[number-1]
        if scene.number != number:
            raise ValueError("Номер кадра не совпадает")
    except (ValueError, TypeError, KeyError, IndexError):
        raise HTTPException(409, "Исходная раскадровка повреждена") from None

    db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    # Search the entire history for this nonce; slicing at 200 permitted
    # already charged requests to be repeated as the history grew.
    prior = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task_id,
        Artifact.kind == "creative_background_regen",
        Artifact.content.contains('"token": "' + str(token) + '"'))))
    for row in prior:
        try:
            if json.loads(row.content).get("token") == str(token):
                raise HTTPException(409, "Этот платный запрос уже был отправлен. Повторное списание заблокировано.")
        except (ValueError, TypeError):
            continue
    intent = ArtifactService(db).create(
        product_id=product_id, task_id=task_id, kind="creative_background_regen",
        title=f"Подтверждение платного фона · кадр {number}",
        content=json.dumps({"token":str(token), "state":"pending",
                            "frame_number":number, "model":model,
                            "source_artifact_id":str(source.id)}))
    db.commit()
    try:
        # Paid call exactly once for this confirmed nonce. No implicit retry.
        prompt = image_prompt(draft, scene, number, len(draft.frames), "9:16", branded=True)
        raw = await request_image(settings, model, prompt, "9:16")
        png = png_output(raw, ratio="9:16", branded=True)
        background = store_background(db, settings.products_root, product_id, png)
        frame = StoryFrame.model_validate(composition.spec)
        style = BrandStyle.model_validate(composition.brand_snapshot)
        final = _new_revision(
            db, settings.products_root, product_id, task_id, source, metadata,
            composition, frame, style, token, background_id=background.id,
            paid_model=fields["model"])
        intent.content = json.dumps({
            "token":str(token), "state":"completed",
            "frame_number":number, "model":model, "render_artifact_id":str(final.id)})
        db.commit()
    except Exception as exc:
        db.rollback()
        state = db.get(Artifact, intent.id)
        if state is not None:
            state.content = json.dumps({
                "token":str(token), "state":"unknown",
                "frame_number":number, "model":model,
                "error_type":type(exc).__name__})
            db.commit()
        raise HTTPException(502,
            "Не удалось подтвердить результат модели. Автоматический повтор не выполняется.") from None
    return RedirectResponse(
        f"/studio/products/{product_id}/tasks/{task_id}/stories/{final.id}/edit",
        status_code=303)
