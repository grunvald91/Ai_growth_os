"""Local-owner API, following the existing studio/intake deployment boundary."""
import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.competitive_intelligence.contracts import ExportInput, FollowUpInput, RunInput
from app.competitive_intelligence.service import IntelligenceService
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Product

router = APIRouter()
BASE = "/competitive-intelligence/runs"


def get_intelligence(db: Session = Depends(get_db)):
    return IntelligenceService(db, products_root=get_settings().products_root)


def find_run(service, run_id):
    try:
        return service.get(run_id)
    except LookupError:
        raise HTTPException(404, "Intelligence run not found") from None


@router.post(BASE, status_code=202)
def start_run(body: RunInput, service=Depends(get_intelligence)):
    try:
        return service.start(body)
    except LookupError:
        raise HTTPException(404, "Product not found") from None


@router.get(BASE)
def list_runs(product_id: UUID, service=Depends(get_intelligence)):
    return service.list_runs(product_id)


@router.get(BASE + "/{run_id}")
def get_run(run_id: UUID, service=Depends(get_intelligence)):
    return find_run(service, run_id)


@router.get(BASE + "/{run_id}/evidence")
def get_evidence(run_id: UUID, disposition: str | None = Query(default=None, pattern="^(retained|excluded)$"),
                 service=Depends(get_intelligence)):
    find_run(service, run_id)
    rows = service.evidence(run_id)
    return [row for row in rows if row["disposition"] == disposition] if disposition else rows


@router.get(BASE + "/{run_id}/report")
def get_report(run_id: UUID, service=Depends(get_intelligence)):
    find_run(service, run_id)
    try:
        return service.report(run_id)
    except ValueError:
        raise HTTPException(409, "Report is not ready") from None


@router.post(BASE + "/{run_id}/follow-up", status_code=202)
def follow_up(run_id: UUID, body: FollowUpInput, service=Depends(get_intelligence)):
    find_run(service, run_id)
    return service.follow_up(run_id, body.brief)


@router.post(BASE + "/{run_id}/export", status_code=201)
def export(run_id: UUID, body: ExportInput, service=Depends(get_intelligence)):
    find_run(service, run_id)
    try:
        return service.export(run_id, body.insight_id, body.target)
    except LookupError:
        raise HTTPException(404, "Insight not found") from None
    except ValueError:
        raise HTTPException(409, "Report is not ready") from None


@router.get(BASE + "/{run_id}/events")
async def events(run_id: UUID, request: Request, after: int = Query(default=0, ge=0),
                 service=Depends(get_intelligence)):
    find_run(service, run_id)
    async def stream():
        cursor = after
        header = request.headers.get("last-event-id", "")
        if header.isdigit():
            cursor = max(cursor, int(header))
        while not await request.is_disconnected():
            # End the read transaction on each poll, so PostgreSQL and SQLite see new commits.
            service.db.expire_all()
            state = service.get(run_id)
            service.db.rollback()
            for event in state["events"]:
                if event["id"] > cursor:
                    cursor = event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if state["status"] not in {"queued", "running"}:
                yield f"event: done\ndata: {json.dumps({'status': state['status']})}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/intelligence", response_class=HTMLResponse)
def intelligence_page(product_id: UUID | None = None, db: Session = Depends(get_db)):
    from app.competitive_intelligence.page import render_page
    products = list(db.scalars(select(Product).order_by(Product.name)))
    if product_id is not None and not any(product.id == product_id for product in products):
        raise HTTPException(404, "Product not found")
    return render_page(products, product_id or (products[0].id if products else None))


@router.get("/intelligence/assets/page.js")
def page_script():
    from app.competitive_intelligence.page import SCRIPT
    return Response(SCRIPT, media_type="application/javascript")
