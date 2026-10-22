"""Owner-only AI team dashboard, not a tenant-facing public API.

All state reads and writes require a separate bearer secret; the HTML shell is
static. Gateway keys stay in backend settings. No paid requests on page load.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.api.content_studio_auth import _basic, require_studio_auth, require_studio_csrf
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import AGENT_CONFIGS, AgentRole
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.llm.model_catalog import CatalogUnavailable, catalog_comparison, gateway_models, list_gateway_models
from app.llm.model_router import ModelRouter, RoutingError
from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.router_store import (
    RouterConfig, RouterConflict, RouterStoreError, RouterUpdate,
    read_state, save_state, validate_configuration,
)
from app.llm.usage_report import report_product_usage
from app.models.domain import Product

router = APIRouter()

# The base set matches app.agents.AgentRole. Extra production stages are
# included here because they appear in the actual content workflow.
_GROUPS = {
    "Руководство": ("cmo", "research_lead", "dispatcher"),
    "Изучение бизнеса": ("company_intake", "research_pmm", "segment_researcher", "channel_researcher",
                        "problem_researcher", "competitor_voc_researcher", "viral_video_researcher",
                        "viral_content_researcher", "competitive_intelligence"),
    "Контент и SEO": ("content_seo", "content_strategist", "seo_editor", "article_writer",
                     "human_editor", "marketing_editor", "conversion_editor",
                     "repurposing_editor", "platform_writer", "editorial_planner",
                     "post_strategist", "post_writer_editor", "post_rewriter"),
    "Видео и дизайн": ("scriptwriter", "creative_director", "video_producer", "video_director",
                      "story_director", "visual_designer", "youtube_shorts_director",
                      "instagram_reels_director", "creative_strategist", "whiteboard_storyboard"),
    "Продвижение и аналитика": ("distribution", "community_manager", "analytics", "cro", "comment_sales"),
}
_NAMES = {
    "dispatcher": "Диспетчер главной страницы",
    "comment_sales": "Продавец в комментариях",
    "cmo": "Маркетинговый директор",
    "research_lead": "Руководитель исследований",
    "company_intake": "Сбор данных о компании",
    "research_pmm": "Аналитик рынка и позиционирования",
    "segment_researcher": "Исследователь сегментов",
    "channel_researcher": "Исследователь каналов",
    "problem_researcher": "Исследователь потребностей",
    "competitor_voc_researcher": "Аналитик отзывов конкурентов",
    "viral_video_researcher": "Исследователь популярных видео",
    "viral_content_researcher": "Исследователь популярных материалов",
    "content_seo": "Контент и SEO",
    "content_strategist": "Контент-стратег",
    "seo_editor": "SEO-редактор",
    "article_writer": "Автор статей",
    "human_editor": "Редактор для читателя",
    "marketing_editor": "Продающий редактор",
    "conversion_editor": "Редактор конверсии",
    "repurposing_editor": "Адаптация материалов",
    "platform_writer": "Автор публикаций для площадок",
    "scriptwriter": "Сценарист",
    "creative_director": "Креативный директор",
    "video_producer": "Видеопродюсер",
    "video_director": "Видеорежиссёр",
    "story_director": "Режиссёр сторис",
    "visual_designer": "Визуальный дизайнер",
    "youtube_shorts_director": "YouTube Shorts",
    "instagram_reels_director": "Instagram Reels",
    "distribution": "Распространение контента",
    "community_manager": "Сообщества и комментарии",
    "analytics": "Аналитик",
    "cro": "Оптимизация конверсии",
    "competitive_intelligence": "Разведка конкурентов",
    "editorial_planner": "Редактор контент-плана",
    "post_strategist": "Стратег поста",
    "post_writer_editor": "Автор и редактор поста",
    "post_rewriter": "Редактор правок поста",
    "creative_strategist": "Креативный стратег",
    "whiteboard_storyboard": "Сценарий рисованного видео",
}


_DESCRIPTIONS = {
    "cmo": "Определяет стратегию, приоритеты и задачи всей команды.",
    "research_lead": "Объединяет исследования и проверяет обоснованность выводов.",
    "company_intake": "Собирает и сверяет подтверждённые сведения о компании.",
    "research_pmm": "Анализирует аудиторию, рынок и позиционирование.",
    "segment_researcher": "Готовит проверяемые гипотезы о сегментах клиентов.",
    "channel_researcher": "Исследует каналы привлечения и условия экспериментов.",
    "problem_researcher": "Сопоставляет проблемы клиентов с возможностями продукта.",
    "competitor_voc_researcher": "Анализирует предоставленные отзывы конкурентов и их источники.",
    "viral_video_researcher": "Изучает публичные данные и приёмы популярных видео.",
    "viral_content_researcher": "Находит темы, боли и форматы по подтверждённым источникам.",
    "content_seo": "Разрабатывает задания на SEO и контент.",
    "content_strategist": "Выбирает тему, задачу и структуру материала.",
    "seo_editor": "Формирует поисковые запросы, заголовки и структуру статей.",
    "article_writer": "Пишет статьи по подготовленному заданию.",
    "human_editor": "Делает тексты ясными, полезными и удобными для читателя.",
    "marketing_editor": "Усиливает продающую подачу и конкретику без выдуманных фактов.",
    "conversion_editor": "Планирует призывы к действию и путь до заявки.",
    "repurposing_editor": "Перерабатывает исходный материал в разные форматы.",
    "platform_writer": "Готовит готовые публикации под конкретную площадку.",
    "scriptwriter": "Разрабатывает сценарии и первые секунды роликов.",
    "creative_director": "Предлагает визуальные концепции и ход повествования.",
    "video_producer": "Готовит производственные задания для видеоматериалов.",
    "video_director": "Создаёт подробную покадровую режиссуру и монтажные указания.",
    "story_director": "Планирует сторис, кадры, подписи и переходы.",
    "visual_designer": "Готовит композиции, стили и задания для изображений.",
    "youtube_shorts_director": "Адаптирует сценарии и кадры для YouTube Shorts.",
    "instagram_reels_director": "Создаёт сценарии и кадры под Instagram Reels.",
    "distribution": "Планирует распространение одобренного контента.",
    "community_manager": "Подготавливает работу с сообществами и коммуникациями.",
    "analytics": "Структурирует фактические показатели маркетинговой воронки.",
    "cro": "Предлагает эксперименты по повышению конверсии.",
    "competitive_intelligence": "Собирает и сопоставляет сведения о конкурентах.",
    "editorial_planner": "Составляет контент-план на период.",
    "post_strategist": "Придумывает идею и структуру короткого поста.",
    "post_writer_editor": "Пишет и редактирует текст поста.",
    "post_rewriter": "Вносит правки в готовый пост по замечаниям.",
    "creative_strategist": "Изучает тему и аудиторию перед созданием материала.",
    "dispatcher": "Понимает просьбы владельца на главной странице и решает, куда их направить. Быстрая модель: ответ нужен сразу.",
    "whiteboard_storyboard": "Пишет сценарий рисованного видео. Работает при включённом Model Router. Модель берётся для НОВЫХ заданий; остальные из цепочки включаются, если выбранная не отвечает.",
}


def role_descriptors() -> list[dict]:
    roles = {role.value for role in AgentRole} | {
        "repurposing_editor", "platform_writer",
        "youtube_shorts_director", "instagram_reels_director",
        "competitive_intelligence", "editorial_planner", "post_strategist", "post_writer_editor",
        "post_rewriter", "creative_strategist", "whiteboard_storyboard", "dispatcher",
    }
    listed = []
    for department, identifiers in _GROUPS.items():
        for name in identifiers:
            if name not in roles:
                continue
            config = AGENT_CONFIGS.get(AgentRole(name)) if name in AgentRole._value2member_map_ else None
            listed.append({
                "id": name, "name": _NAMES.get(name, name.replace("_", " ").title()),
                "department": department,
                "description": _DESCRIPTIONS.get(name, "Подготавливает задания для конкретной площадки."),
                "registered": config is not None,
            })
    missing = roles - {item["id"] for item in listed}
    if missing:
        raise RuntimeError("Unmapped AI team roles: " + ",".join(sorted(missing)))
    return listed


def _decision(router_policy: ModelRouter, role: str, phase: str) -> dict:
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="Preview routing only; no inference")],
        max_tokens=1500, metadata={"team_role": role, "routing_phase": phase},
    )
    try:
        selected = router_policy.select(request)
        return {
            "models": list(selected.candidates),
            "tier": selected.required_tier,
            "status": "ready",
        }
    except RoutingError as exc:
        return {"models": [], "tier": router_policy._tier_for(role, phase),
                "status": "unavailable", "reason": str(exc)}


def preview_configuration(config: RouterConfig) -> dict:
    policy = validate_configuration(config)
    rows, issues = [], []
    for role in role_descriptors():
        main = _decision(policy, role["id"], "main")
        critic = _decision(policy, role["id"], "critic")
        row = {**role, "main": main, "critic": critic}
        rows.append(row)
        if main["status"] != "ready":
            issues.append(role["id"] + ": основной маршрут — " + main["reason"])
        if critic["status"] != "ready":
            issues.append(role["id"] + ": критик — " + critic["reason"])
    return {"ready": not issues, "issues": issues, "roles": rows,
            "critics_active": get_settings().agent_critic_enabled,  # Routing alone does not launch critic calls.
            "live_web_search_available": False}


# The dashboard page uses this prefix; /api/admin/team stays for bearer-token clients.
STUDIO_API = "/studio/team/api"


def require_owner(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    authorization: str = Header(default=""),
    settings: Settings = Depends(get_settings),
    db=Depends(get_db),
) -> None:
    from app.core.studio_sessions import COOKIE
    if credentials is not None or request.cookies.get(COOKIE):
        # Basic credentials or a Studio login session (the page's own fetches send the cookie).
        require_studio_auth(request, credentials, settings, db)
        if getattr(request.state, "studio_product_ids", None) is not None:
            raise HTTPException(403, "Administrator account required")
        require_studio_csrf(request, settings)
        return
    if request.url.path.startswith(STUDIO_API) and not authorization:
        # The page calls this prefix: browsers resend the Studio login only
        # under /studio/, so ask for it instead of failing on the token.
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})
    token = settings.model_router_admin_token or ""
    if len(token) < 32:
        raise HTTPException(503, "Owner team dashboard token is not configured")
    scheme, separator, supplied = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, token):
        raise HTTPException(401, "Owner authorization required", headers={"WWW-Authenticate": "Bearer"})


def _secure_response(content: str, media_type: str) -> Response:
    response = Response(content=content, media_type=media_type)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if media_type == "text/html":
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        )
    return response


@router.get("/studio/team", dependencies=[Depends(require_studio_auth)])
def team_page():
    page = Path(__file__).parent / "team_dashboard_assets" / "index.html"
    return _secure_response(page.read_text(encoding="utf-8"), "text/html")


@router.get("/studio/team/assets/team.css", dependencies=[Depends(require_studio_auth)])
def team_css():
    css = Path(__file__).parent / "team_dashboard_assets" / "team.css"
    return _secure_response(css.read_text(encoding="utf-8"), "text/css")


@router.get("/studio/team/assets/team.js", dependencies=[Depends(require_studio_auth)])
def team_js():
    script = Path(__file__).parent / "team_dashboard_assets" / "team.js"
    return _secure_response(script.read_text(encoding="utf-8"), "application/javascript")


@router.get("/api/admin/team", dependencies=[Depends(require_owner)])
@router.get("/studio/team/api", dependencies=[Depends(require_owner)])
def team_data(settings: Settings = Depends(get_settings), db: Session = Depends(get_db)):
    try:
        state = read_state(settings)
        preview = preview_configuration(state.config)
    except (ValueError, RouterStoreError, RoutingError) as exc:
        raise HTTPException(503, "Invalid router configuration") from exc
    products = list(db.scalars(select(Product).order_by(Product.name).limit(100)))
    return {
        "revision": state.revision, "config": state.config.model_dump(),
        "preview": preview,
        "products": [{"id": str(product.id), "name": product.name} for product in products],
        "gateway": settings.xai_base_url,
        "provider": settings.llm_provider,
    }


@router.post("/api/admin/team/preview", dependencies=[Depends(require_owner)])
@router.post("/studio/team/api/preview", dependencies=[Depends(require_owner)])
def team_preview(config: RouterConfig):
    try:
        return preview_configuration(config)
    except (ValueError, RoutingError) as exc:
        raise HTTPException(422, str(exc)) from None


@router.put("/api/admin/team", dependencies=[Depends(require_owner)])
@router.put("/studio/team/api", dependencies=[Depends(require_owner)])
def team_update(body: RouterUpdate, settings: Settings = Depends(get_settings)):
    try:
        preview = preview_configuration(body.config)
        if body.config.enabled and settings.llm_provider != "xai":
            raise HTTPException(422, "This router requires the configured OpenAI-compatible gateway")
        if body.config.enabled and not settings.xai_api_key:
            raise HTTPException(422, "The gateway API key must be configured before activation")
        if body.config.enabled and not preview["ready"]:
            raise HTTPException(422, {
                "message": "Configure safe main and critic routes before enabling.",
                "issues": preview["issues"][:32],
            })
        state = save_state(settings, body)
    except RouterConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except (RouterStoreError, RoutingError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from None
    return {"revision": state.revision, "config": state.config.model_dump(), "preview": preview}


@router.get("/api/admin/team/catalog", dependencies=[Depends(require_owner)])
@router.get("/studio/team/api/catalog", dependencies=[Depends(require_owner)])
async def team_catalog(settings: Settings = Depends(get_settings)):
    try:
        state = read_state(settings)
        policy = validate_configuration(state.config)
        ids = await list_gateway_models(
            base_url=settings.xai_base_url,
            api_key=settings.xai_api_key or "",
        )
    except (CatalogUnavailable, RouterStoreError, RoutingError, ValueError) as exc:
        raise HTTPException(503, "Model catalog unavailable; no paid inference attempted") from exc
    return {
        "models": catalog_comparison(ids, policy.profiles),
        "gateway_models": gateway_models(ids, policy.profiles),
        "listed_count": len(ids),
        "unconfigured_ids": [mid for mid in ids if mid not in {p.model for p in policy.profiles}],
        "web_search_verified": False,
        "paid_inference_requests": 0,
    }


@router.get("/api/admin/team/usage", dependencies=[Depends(require_owner)])
@router.get("/studio/team/api/usage", dependencies=[Depends(require_owner)])
def team_usage(product_id: UUID, db: Session = Depends(get_db)):
    try:
        return report_product_usage(db, product_id, limit=2000)
    except LookupError:
        raise HTTPException(404, "Product not found") from None
