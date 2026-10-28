from fastapi import FastAPI, Depends
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf

from app.api.health import router as health_router

app = FastAPI(title="AI Growth OS", version="0.1.0")
from app.api.content_studio_auth import StudioOriginError
from app.api.content_studio_errors import studio_origin_error
app.add_exception_handler(StudioOriginError, studio_origin_error)
app.include_router(health_router)

from app.api.products import router as products_router
app.include_router(products_router, dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

from app.api.research_sources import router as research_sources_router
app.include_router(research_sources_router, dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

from app.api.competitive_intelligence import router as intelligence_router
app.include_router(intelligence_router, dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])


from app.api.control import router as control_router
app.include_router(control_router, dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

from app.api.company_profiles import router as company_profiles_router
app.include_router(company_profiles_router, dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

from app.api.company_intake import router as company_intake_router
app.include_router(company_intake_router, dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

# Public sign-in page; registered before the protected Studio routes.
from app.api.studio_login import router as studio_login_router
app.include_router(studio_login_router)
from app.api.studio_sso import router as studio_sso_router
app.include_router(studio_sso_router)
from app.api.content_studio import router as content_studio_router
app.include_router(content_studio_router)
from app.api.content_studio_company import router as content_studio_company_router
app.include_router(content_studio_company_router)
from app.api.tune import router as tune_router
app.include_router(tune_router)

from app.api.comments import router as comments_router
app.include_router(comments_router, dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
from app.api.team_dashboard import router as team_dashboard_router
app.include_router(team_dashboard_router)

from app.api.whiteboard import router as whiteboard_router
app.include_router(whiteboard_router)

# Public read-only feed of published articles for the blog on servgo.ru.
from app.api.public_blog import router as public_blog_router
app.include_router(public_blog_router)

from app.api.dispatcher import router as dispatcher_router
app.include_router(dispatcher_router)

from app.api.studio_accounts import router as studio_accounts_router
app.include_router(studio_accounts_router)
from app.api.internal_models import router as internal_models_router
app.include_router(internal_models_router)

from app.api.voice import service_router as voice_service_router, studio_router as voice_studio_router
app.include_router(voice_studio_router)
app.include_router(voice_service_router)
