from fastapi import FastAPI, Depends
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf

from app.api.health import router as health_router

app = FastAPI(title="AI Growth OS", version="0.1.0")
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

from app.api.content_studio import router as content_studio_router
app.include_router(content_studio_router)

from app.api.whiteboard import router as whiteboard_router
app.include_router(whiteboard_router)

from app.api.comments import router as comments_router
app.include_router(comments_router, dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
