"""One explicit, bounded live image+vision canary; isolated from production.

This script runs only under the one-shot GitHub workflow. It calls the real
ClaudeHub image endpoint once and the real vision endpoint once; no retry,
no publishing, no real company or production database. The API credential is
read only from the runner environment and never included in output.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
from uuid import UUID

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.content.creative_images import verified_file
from app.content.review import ReviewError, approve_material
from app.content.vk_image_critic import (
    REVIEW_POLICY, critic_profile, review_vk_post_image,
    verified_vk_image_approval,
)
from app.content.vk_post_image import prepare_vk_post_image
from app.core.config import Settings
from app.db.models import Base
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task


async def main() -> int:
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        print("REFUSED: a rerun might repeat charged requests")
        return 3
    target = Path(os.environ.get("VK_LIVE_CANARY_DIR", "/tmp/servgo-vk-live-canary"))
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path = target / "result.json"
    if report_path.exists():
        print("REFUSED: existing canary result")
        return 3
    key = os.environ.get("XAI_API_KEY", "")
    if not key.strip():
        report_path.write_text(json.dumps({"status": "not_run", "reason": "provider_secret_missing"}))
        print("NO_PROVIDER_SECRET: live API calls not attempted")
        return 2
    # Explicitly never rely on an untrusted .env, user context, global secrets,
    # or production paths. The provider key is not logged or persisted.
    settings = Settings(
        _env_file=None, products_root=target / "products",
        xai_api_key=key.strip(), openai_api_key=None,
        llm_provider="xai", agent_critic_enabled=False,
    )
    profile = critic_profile(settings)
    if profile[0] != "claudehub" or profile[1] != "gemini-flash-3.8":
        print("INVALID_VISION_PROFILE")
        return 3

    report: dict = {
        "policy": REVIEW_POLICY, "public_test_only": True,
        "image_requests_planned": 1, "vision_requests_planned": 1,
        "provider": "claudehub", "image_model": "gemini-3.1-flash-image",
        "vision_model": profile[1],
        "status": "started",
    }
    # Immutable, fictional local test company and post. No production DB,
    # customer text, contacts, or publication connection are used.
    engine = create_engine("sqlite+pysqlite:///" + str(target / "canary.sqlite3"))
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            company = Product(name="Тестовая мастерская", slug="vk-live-canary-no-publication")
            db.add(company)
            db.flush()
            task = Task(
                product_id=company.id, assigned_agent="content_factory",
                title="CONTENT: post:vk:live-canary", status="running",
            )
            db.add(task)
            db.flush()
            value = {
                "channel": "vk", "title": "Почему клиенты пропускают запись",
                "body": (
                    "В небольшую мастерскую звонит клиент, пока мастер занят "
                    "заказом. На столе лежит телефон с пропущенным звонком. "
                    "Потерянное обращение может означать потерянную запись. "
                    "Удобно вести список обращений и вовремя отвечать клиентам."
                ),
                "image_model": "gemini", "image_ratio": "4:5",
                "image_state": "pending",
            }
            post = Artifact(
                product_id=company.id, task_id=task.id,
                kind="channel_text", title=value["title"],
                content=json.dumps(value, ensure_ascii=False),
                status="awaiting_approval",
            )
            db.add(post)
            db.commit()
            try:
                report["stage"] = "image"
                asset = await prepare_vk_post_image(
                    db, settings, product=company, task=task, post=post,
                    model_choice="gemini", ratio="4:5",
                )
                png = verified_file(
                    settings.products_root, company.id, task.id, asset,
                )
                with Image.open(png) as image:
                    image.load()
                    if image.format != "PNG" or image.size != (1080, 1350):
                        raise AssertionError("Invalid real VK image output")
                    report["image"] = {
                        "format": image.format,
                        "size": list(image.size),
                        "sha256_prefix": hashlib.sha256(png.read_bytes()).hexdigest()[:16],
                    }
                (target / "vk-image-canary.png").write_bytes(png.read_bytes())
                report["stage"] = "vision"
                outcome = await review_vk_post_image(
                    db, settings, product=company, task=task, post=post,
                )
                data = json.loads(post.content)
                report["verdict"] = outcome
                report["image_state"] = data.get("image_state")
                report["vision_error_type"] = data.get("image_review_error_code")
                if outcome not in ("approved", "blocked"):
                    raise RuntimeError("Vision service did not produce a valid verdict")
                expected = outcome == "approved"
                valid = verified_vk_image_approval(db, post, asset)
                if valid != expected:
                    raise AssertionError("Visual approval was not bound to image")
                report["verified_approval"] = valid
                if expected:
                    approved = approve_material(db, settings.products_root,
                                                company.id, post.id,
                                                facts_confirmed=True)
                    if approved.status != ArtifactStatus.APPROVED:
                        raise AssertionError("Approved verdict did not unlock approval")
                    report["owner_approval_gate"] = "allowed"
                else:
                    try:
                        approve_material(db, settings.products_root,
                                         company.id, post.id, facts_confirmed=True)
                    except ReviewError as exc:
                        if exc.status != 409:
                            raise
                        report["owner_approval_gate"] = "rejected"
                    else:
                        raise AssertionError("Blocked image was approved")
                if db.scalars(select(Publication).where(
                    Publication.product_id == company.id,
                )).first() is not None:
                    raise AssertionError("Canary must never publish to VK")
                report["publication_count"] = 0
                report["status"] = "ok"
                print("LIVE_CANARY_OK image=1080x1350 vision=" + outcome
                      + " publication_count=0")
                return 0
            except Exception as exc:
                report["status"] = "failed"
                report["error_type"] = type(exc).__name__
                # Never print exception strings: provider errors can contain
                # sensitive request details. One failed paid call is not retried.
                print("LIVE_CANARY_FAILED stage=" + str(report.get("stage"))
                      + " type=" + type(exc).__name__)
                return 1
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
