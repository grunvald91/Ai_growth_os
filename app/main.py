from fastapi import FastAPI

from app.api.health import router as health_router

app = FastAPI(title="AI Growth OS", version="0.1.0")
app.include_router(health_router)

from app.api.products import router as products_router
app.include_router(products_router)
