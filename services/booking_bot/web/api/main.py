"""
BookingBot API — FastAPI backend for the booking service.
Works with the same PostgreSQL database as the Telegram bot.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import client, admin, auth, ai, subscriptions, notifications, email_auth, payments, telegram_link, studio_sso, studio_schedule, yandex_auth, master, crm, seo_pages, seo_reporting
from . import rental

app = FastAPI(
    title="BookingBot API",
    description="API for SochiService booking platform",
    version="1.0.0",
    root_path="/api",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(email_auth.router, prefix="/auth", tags=["auth"])
app.include_router(telegram_link.router, prefix="/auth", tags=["auth"])
app.include_router(yandex_auth.router, prefix="/auth", tags=["auth"])
app.include_router(payments.router, prefix="/admin", tags=["admin"])
app.include_router(client.router, tags=["client"])
app.include_router(seo_pages.router)
app.include_router(seo_reporting.router)
app.include_router(rental.router, tags=["rental"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(crm.router, prefix="/admin", tags=["crm"])
app.include_router(master.router, prefix="/master", tags=["master"])
app.include_router(studio_sso.router, tags=["studio"])
app.include_router(studio_schedule.router, tags=["studio"])
app.include_router(ai.router, tags=["ai"])
app.include_router(subscriptions.router, tags=["subscriptions"])
app.include_router(notifications.router, prefix="/notifications", tags=["notifications"])


@app.get("/health")
async def health():
    return {"status": "ok"}

# Platform-owner mail console (preserve existing auth and payment routers).
from .routes import owner_mail, mail_campaign_api, mail_campaign_review, mail_campaign_analytics, mail_campaign_unsubscribe, mail_campaign_ui, mail_company_search, mail_pwa
app.include_router(owner_mail.router)
app.include_router(mail_campaign_api.router)
app.include_router(mail_campaign_review.router)
app.include_router(mail_campaign_analytics.router)
app.include_router(mail_campaign_unsubscribe.router)
app.include_router(mail_campaign_ui.router)
app.include_router(mail_company_search.router)
app.include_router(mail_pwa.router)
