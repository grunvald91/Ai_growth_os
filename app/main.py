from fastapi import FastAPI

from app.api.health import router as health_router

app = FastAPI(title="AI Growth OS", version="0.1.0")
app.include_router(health_router)

from app.api.products import router as products_router
app.include_router(products_router)

from app.api.research_sources import router as research_sources_router
app.include_router(research_sources_router)

from app.api.competitive_intelligence import router as intelligence_router
app.include_router(intelligence_router)


from app.api.control import router as control_router
app.include_router(control_router)

from app.api.company_profiles import router as company_profiles_router
app.include_router(company_profiles_router)

from app.api.company_intake import router as company_intake_router
app.include_router(company_intake_router)

from app.api.content_studio import router as content_studio_router
app.include_router(content_studio_router)
