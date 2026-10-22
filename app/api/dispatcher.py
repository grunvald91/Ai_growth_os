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
from app.core.composition import SAFE_LLM_METADATA_FIELDS, build_llm_provider
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.dispatcher import service
from app.dispatcher.blocks import Chip
from app.dispatcher.executors import Context
from app.dispatcher.hints import compute_hints
from app.llm.service import LLMService
from app.models.domain import Product
from app.privacy import PrivacyGateway

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
ASSETS = Path(__file__).parent / "dispatcher_assets"
ASSET_VERSION = "3"
MAX_BODY_BYTES = 1_000_000  # same guard as the Studio forms


def enabled_for(settings: Settings, product: Product) -> bool:
    slugs = {item.strip() for item in settings.dispatcher_enabled_slugs.split(",") if item.strip()}
    return product.slug in slugs


def get_dispatcher_llm(settings: Settings = Depends(get_settings)) -> LLMService | None:
    """The model behind the dispatcher; tests replace this dependency. None means no model is configured."""
    try:
        provider = build_llm_provider(settings)
    except ValueError:
        return None
    return LLMService(privacy_gateway=PrivacyGateway(allowed_context_fields=SAFE_LLM_METADATA_FIELDS),
                      provider=provider, critic_enabled=False)


def _account(request: Request) -> str:
    return getattr(request.state, "studio_login", None) or "local-owner"


def _context(db: Session, product: Product, settings: Settings, request: Request) -> Context:
    return Context(db=db, product=product, settings=settings, account=_account(request))


class ChipIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    capability: str = Field(min_length=1, max_length=60)
    params: dict[str, str] = Field(default_factory=dict)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = ""
    chip: ChipIn | None = None
    client_key: str | None = Field(default=None, max_length=100)
    mode: str = Field(default="text", pattern="^(text|chip|voice)$")


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation_id: str = Field(min_length=1, max_length=64)
    accept: bool
    via: str = Field(default="button", pattern="^(button|voice)$")


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
    return [chip.model_dump() for chip in compute_hints(db, product, settings.products_root)]


def _state(db: Session, product: Product, settings: Settings, request: Request) -> dict:
    conversation = service.open_conversation(db, product.id, _account(request))
    return {"messages": [service.to_dict(m) for m in service.messages(db, conversation)],
            "hints": _hints(db, product, settings)}


def home_html(db: Session, product: Product, settings: Settings, request: Request) -> str:
    state = json.dumps(_state(db, product, settings, request), ensure_ascii=False).replace("<", "\\u003c")
    base = f"/studio/products/{product.id}"
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#ffffff">
  <title>{esc(product.name)} · Главная · Growth Studio</title>
  <link rel="icon" type="image/svg+xml" href="/studio/assets/servgo.svg">
  <link rel="stylesheet" href="/studio/assets/studio.css?v=14">
  <link rel="stylesheet" href="/studio/assets/dispatcher.css?v={ASSET_VERSION}">
</head>
<body>
<div class="studio-page"><div class="studio-layout">
  {sidebar_html(product.id, "overview")}
  {mobile_header_html(product)}
  {mobile_nav_html(product.id, "overview")}
  <main class="main dp-main">
    <section class="dp-home" data-api="{base}/dispatch" aria-label="Диспетчер">
      <header class="dp-hello">
        <h1>Чем помочь?</h1>
        <p>Напишите, что нужно, — я передам команде или покажу, где это.</p>
      </header>
      <div class="dp-feed" id="dp-feed" role="log" aria-live="polite" aria-label="Разговор"></div>
      <div class="dp-chips" id="dp-chips" aria-label="Подсказки"></div>
      <form class="dp-form" id="dp-form" autocomplete="off">
        <label class="dp-sr" for="dp-input">Ваш запрос</label>
        <textarea id="dp-input" class="dp-input" rows="1" placeholder="Что нужно сделать?" enterkeyhint="send"></textarea>
        <button class="dp-send" id="dp-send" type="submit" aria-label="Отправить">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
        </button>
      </form>
      <button class="dp-new" id="dp-new" type="button">Новая тема</button>
    </section>
  </main>
</div></div>
<script type="application/json" id="dp-state">{state}</script>
<script src="/studio/assets/dispatcher.js?v={ASSET_VERSION}"></script>
</body></html>"""


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
        mode="chip" if body.chip else body.mode, chip=body.chip.model_dump() if body.chip else None)
    result["hints"] = _hints(db, product, settings)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/studio/products/{product_id}/dispatch/confirm")
async def dispatch_confirm(product_id: UUID, request: Request, db: Session = Depends(get_db),
                           settings: Settings = Depends(get_settings)):
    product = _owner_product(db, product_id)
    body = await _json(request, ConfirmIn)
    try:
        result = await service.confirm(db, _context(db, product, settings, request), body.confirmation_id,
                                       accept=body.accept, via=body.via)
    except LookupError:
        raise HTTPException(404, "Этот вопрос уже недоступен") from None
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from None
    result["hints"] = _hints(db, product, settings)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/studio/products/{product_id}/dispatch/new")
def dispatch_new(product_id: UUID, request: Request, db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings)):
    product = _owner_product(db, product_id)
    service.open_conversation(db, product.id, _account(request), fresh=True)
    return JSONResponse({"messages": [], "hints": _hints(db, product, settings)}, headers={"Cache-Control": "no-store"})
