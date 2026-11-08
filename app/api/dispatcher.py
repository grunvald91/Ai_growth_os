"""Owner home page: one input line, hint chips and the dispatcher's answers.

Everything runs through the Studio's own sign-in and origin checks. The page never starts paid
work by itself; confirmations are separate, explicit requests.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.api.content_studio import (_owner_product, esc, mobile_header_html, mobile_nav_html, sidebar_html)
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.core.composition import SAFE_LLM_METADATA_FIELDS, build_dispatcher_provider
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.dispatcher import service
from app.dispatcher.blocks import Chip
from app.dispatcher.executors import Context
from app.dispatcher.hints import compute_hints
from app.llm.service import LLMService
from app.models.domain import Product
from app.privacy import PrivacyGateway
from app.voice.dictionary import SLUG

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
ASSETS = Path(__file__).parent / "dispatcher_assets"
ASSET_VERSION = "10"
MAX_BODY_BYTES = 1_000_000  # same guard as the Studio forms


def enabled_for(settings: Settings, product: Product) -> bool:
    slugs = {item.strip() for item in settings.dispatcher_enabled_slugs.split(",") if item.strip()}
    return "*" in slugs or product.slug in slugs


def get_dispatcher_llm(settings: Settings = Depends(get_settings)) -> LLMService | None:
    """The model behind the dispatcher; tests replace this dependency. None means no model is configured."""
    try:
        provider = build_dispatcher_provider(settings)
    except ValueError:
        return None
    return LLMService(privacy_gateway=PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS),
                      provider=provider, critic_enabled=False)


def _account(request: Request) -> str:
    return getattr(request.state, "studio_login", None) or "local-owner"


def _context(db: Session, product: Product, settings: Settings, request: Request) -> Context:
    return Context(db=db, product=product, settings=settings, account=_account(request),
                   studio_role=getattr(request.state, "studio_sso_role", None))


class ChipIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    capability: str = Field(min_length=1, max_length=60)
    params: dict[str, str] = Field(default_factory=dict)


from app.dispatcher.editable import CreateHints, EditCreation


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = ""
    chip: ChipIn | None = None
    client_key: str | None = Field(default=None, max_length=100)
    mode: str = Field(default="text", pattern="^(text|chip|voice)$")
    creation: CreateHints | None = None


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_id: str = Field(min_length=1, max_length=64)
    accept: bool
    via: str = Field(default="button", pattern="^(button|voice)$")
    revision: int | None = Field(default=None, ge=0)


async def _json(request: Request, model):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise HTTPException(413, "Слишком большой запрос")
        chunks.append(chunk)
    try:
        return model.model_validate_json(b"".join(chunks))
    except ValidationError:
        raise HTTPException(422, "Не удалось разобрать запрос") from None


def _hints(db: Session, product: Product, settings: Settings) -> list[dict]:
    # Schedule chips only when the booking platform can really be asked, like the «Маркетинг» entry in ServGo.
    ready = len(settings.servgo_internal_key or "") >= 32
    return [chip.model_dump() for chip in compute_hints(db, product, settings.products_root, schedule_ready=ready)]


def _state(db: Session, product: Product, settings: Settings, request: Request) -> dict:
    conversation = service.open_conversation(db, product.id, _account(request))
    return {"messages": [service.to_dict(m) for m in service.messages(db, conversation)],
            "hints": _hints(db, product, settings)}


def home_html(db: Session, product: Product, settings: Settings, request: Request) -> str:
    state = json.dumps(_state(db, product, settings, request), ensure_ascii=False).replace("<", "\\u003c")
    base = f"/studio/products/{product.id}"
    # The product's own dictionary is used only when its slug is a valid dictionary name.
    voice_project = product.slug if SLUG.match(product.slug or "") else ""
    voice_max = int(settings.voice_max_audio_seconds)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#ffffff">
  <title>{esc(product.name)} · Главная · Growth Studio</title>
  <link rel="icon" type="image/svg+xml" href="/studio/assets/servgo.svg">
  <link rel="manifest" href="/manifest.webmanifest">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <link rel="stylesheet" href="/studio/assets/studio.css?v=14">
  <link rel="stylesheet" href="/studio/assets/dispatcher.css?v={ASSET_VERSION}">
</head>
<body>
<div class="studio-page"><div class="studio-layout">
  {sidebar_html(product.id, "overview")}
  {mobile_header_html(product)}
  {mobile_nav_html(product.id, "overview")}
  <main class="main dp-main">
    <section class="dp-home" data-api="{base}/dispatch" data-voice-url="/studio/voice/dictate" data-voice-preview-url="/studio/voice/preview"
             data-voice-project="{esc(voice_project)}" data-voice-max="{voice_max}" aria-label="Диспетчер">
      <header class="dp-hello">
        <h1>Чем помочь?</h1>
        <p>Напишите, что нужно, — я передам команде или покажу, где это.</p>
      </header>
      <div class="dp-feed" id="dp-feed" role="log" aria-live="polite" aria-label="Разговор"></div>
      <div class="dp-chips" id="dp-chips" aria-label="Подсказки"></div>
      <div class="dp-compose">
        <div class="dp-live" id="dp-live" hidden>
          <span class="dp-live-label" id="dp-live-label">Слушаю…</span>
          <p class="dp-live-text" id="dp-live-text"></p>
        </div>
        <p class="dp-voice-note" id="dp-voice-note" role="status" aria-live="polite" hidden></p>
        <form class="dp-form" id="dp-form" autocomplete="off">
          <label class="dp-sr" for="dp-input">Ваш запрос</label>
          <textarea id="dp-input" class="dp-input" rows="1" placeholder="Что нужно сделать?" enterkeyhint="send"></textarea>
          <button class="dp-mic" id="dp-mic" type="button" aria-label="Надиктовать голосом" aria-pressed="false" hidden>
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>
          </button>
          <button class="dp-send" id="dp-send" type="submit" aria-label="Отправить">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
          </button>
        </form>
      </div>
      <button class="dp-new" id="dp-new" type="button">Новая тема</button>
    </section>
  </main>
</div></div>
<script type="application/json" id="dp-state">{state}</script>
<script src="/studio/assets/dispatcher.js?v={ASSET_VERSION}"></script>
</body></html>"""


@router.get("/studio/products/{product_id}/home-summary.json")
def daily_home_summary(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    """No LLM invocation, no paid activity; one authorised tenant at a time."""
    permitted = getattr(request.state, "studio_product_ids", None)
    if permitted is not None and str(product_id) not in {str(p) for p in permitted}:
        raise HTTPException(403, "Нет доступа к компании")
    product = _owner_product(db, product_id)
    from app.dispatcher.home_summary import home_summary
    return JSONResponse(home_summary(db, product), headers={"Cache-Control": "private, no-store"})


@router.get("/studio/products/{product_id}/organizations.json")
def home_organizations(product_id: UUID, request: Request, db: Session = Depends(get_db),
                       settings: Settings = Depends(get_settings)):
    """Company dropdown uses the same scope as Studio's company selector."""
    permitted = getattr(request.state, "studio_product_ids", None)
    if permitted is not None and str(product_id) not in {str(p) for p in permitted}:
        raise HTTPException(403, "Нет доступа к компании")
    _owner_product(db, product_id)
    return organization_choices(request, db, settings, product_id)


@router.get("/studio/organizations.json")
def studio_organizations(request: Request, db: Session = Depends(get_db),
                         settings: Settings = Depends(get_settings)):
    return organization_choices(request, db, settings)


def organization_choices(request: Request, db: Session, settings: Settings, product_id: UUID | None = None):
    permitted = getattr(request.state, "studio_product_ids", None)
    from sqlalchemy import select
    q = select(Product)
    if permitted is not None:
        q = q.where(Product.id.in_([UUID(str(p)) for p in permitted]))
    products = list(db.scalars(q.order_by(Product.name, Product.id).limit(100)))
    if product_id is not None and not any(item.id == product_id for item in products):
        current = db.get(Product, product_id)
        if current is not None:
            products.append(current)
    from app.api.content_studio import _canonical_servgo_user
    user_id = _canonical_servgo_user(request, settings)
    metadata = {}
    if user_id is not None:
        from app.sso.models import StudioBusinessLink
        linked = list(db.scalars(select(StudioBusinessLink).where(
            StudioBusinessLink.product_id.in_([item.id for item in products]))))
        platform = next((item for item in products if item.slug == 'servgo'), None) if permitted is None else None
        metadata = {"unified_user_id": user_id,
                    "linked_business_ids": [item.servgo_business_id for item in linked],
                    "current_business_ids": [item.servgo_business_id for item in linked if item.product_id == product_id],
                    "current_product_id": str(product_id) if product_id else None,
                    "platform_product": {"id": str(platform.id), "name": platform.name} if platform else None}
    return JSONResponse({
        "items": [{"id": str(item.id), "name": item.name} for item in products],
        "can_add": permitted is None and user_id is None,
        **metadata,
    }, headers={"Cache-Control": "private, no-store"})


@router.get("/studio/assets/dispatcher.css")
def dispatcher_css():
    return Response((ASSETS / "dispatcher.css").read_text(encoding="utf-8"), media_type="text/css",
                    headers={"Cache-Control": "no-cache"})


@router.get("/studio/assets/dispatcher.js")
def dispatcher_js():
    return Response((ASSETS / "dispatcher.js").read_text(encoding="utf-8"), media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@router.get("/studio/products/{product_id}/dispatch/state")
def dispatch_state(product_id: UUID, request: Request, db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings)):
    product = _owner_product(db, product_id)
    return JSONResponse(_state(db, product, settings, request), headers={"Cache-Control": "no-store"})


@router.post("/studio/products/{product_id}/dispatch/messages")
async def dispatch_message(product_id: UUID, request: Request, db: Session = Depends(get_db),
                           settings: Settings = Depends(get_settings),
                           llm: LLMService | None = Depends(get_dispatcher_llm)):
    product = _owner_product(db, product_id)
    body = await _json(request, MessageIn)
    text = body.text.strip()
    if body.chip is None and not text:
        raise HTTPException(422, "Напишите, что нужно сделать")
    result = await service.handle_message(
        db, _context(db, product, settings, request), llm, text=text, client_key=body.client_key,
        mode="chip" if body.chip else body.mode, chip=body.chip.model_dump() if body.chip else None,
        creation=body.creation)
    result["hints"] = _hints(db, product, settings)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/studio/products/{product_id}/dispatch/edit")
async def dispatch_edit(product_id: UUID, request: Request, db: Session = Depends(get_db),
                        settings: Settings = Depends(get_settings)):
    product = _owner_product(db, product_id)
    body = await _json(request, EditCreation)
    try:
        result = await service.edit_confirmation(db, _context(db, product, settings, request), body)
    except LookupError:
        raise HTTPException(404, "Карточка не найдена") from None
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/studio/products/{product_id}/dispatch/confirm")
async def dispatch_confirm(product_id: UUID, request: Request, db: Session = Depends(get_db),
                           settings: Settings = Depends(get_settings)):
    product = _owner_product(db, product_id)
    body = await _json(request, ConfirmIn)
    try:
        result = await service.confirm(db, _context(db, product, settings, request), body.confirmation_id,
                                       accept=body.accept, via=body.via, revision=body.revision)
    except LookupError:
        raise HTTPException(404, "Этот вопрос уже недоступен") from None
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from None
    result["hints"] = _hints(db, product, settings)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/studio/products/{product_id}/dispatch/updates")
def dispatch_updates(product_id: UUID, request: Request, db: Session = Depends(get_db),
                     settings: Settings = Depends(get_settings)):
    """Results of tasks started from this conversation that have finished since the page was last updated."""
    from app.dispatcher.updates import collect_updates
    product = _owner_product(db, product_id)
    ctx = _context(db, product, settings, request)
    conversation = service.open_conversation(db, product.id, ctx.account)
    added = collect_updates(db, ctx, conversation)
    body: dict = {"messages": [service.to_dict(m) for m in added]}
    if added:
        body["hints"] = _hints(db, product, settings)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.post("/studio/products/{product_id}/dispatch/new")
def dispatch_new(product_id: UUID, request: Request, db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    product = _owner_product(db, product_id)
    service.open_conversation(db, product.id, _account(request), fresh=True)
    return JSONResponse({"messages": [], "hints": _hints(db, product, settings)}, headers={"Cache-Control": "no-store"})
