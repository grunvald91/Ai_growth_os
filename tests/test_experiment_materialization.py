import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, Experiment, Product
from app.workflows.experiment_materialization import (
    ExperimentFromPlanInput,
    ExperimentMaterialization,
)
from app.workflows.team_contracts import ContentBrief


def plan_content():
    return ContentBrief(
        summary="One unpublished own-site SEO test only.",
        format="content_plan",
        audience_hypothesis="Unranked hypothesis: small massage-studio owners evaluating booking tools.",
        problem_hypothesis="They may need a clear service-to-confirmation booking path.",
        angle="Explain the verified booking path without competitor superiority claims.",
        title_options=[
            "Own-site SEO test — massage-studio hypothesis",
            "Booking-flow test for a massage-studio hypothesis",
        ],
        outline=["Scope", "Verified path", "Measurement", "Stopping rule"],
        cta="Invite a business owner to provide business name, contact method, and intent to evaluate ServGo.",
        verified_facts_to_use=["Verified booking path only."],
        hypotheses_to_label=["Massage studios are an unranked hypothesis."],
        proof_needed=[
            "Persist a qualified lead tied to the page.",
            "Define an observation window before STOP can be judged.",
        ],
    ).model_dump_json()


@pytest.fixture
def product(db):
    row=Product(name="ServGo", slug=f"servgo-{uuid4()}")
    db.add(row); db.commit()
    return row


def test_materialize_content_plan_is_idempotent_and_traceable(db, product):
    plan=Artifact(
        product_id=product.id,
        kind="content_plan",
        title="Test plan",
        content=plan_content(),
    )
    db.add(plan); db.commit()

    workflow=ExperimentMaterialization(db)
    first=workflow.run(ExperimentFromPlanInput(product_id=product.id, plan_artifact_id=plan.id))
    second=workflow.run(ExperimentFromPlanInput(product_id=product.id, plan_artifact_id=plan.id))

    assert first.experiment_id == second.experiment_id
    assert first.manifest_artifact_id == second.manifest_artifact_id
    experiment=db.get(Experiment, first.experiment_id)
    assert experiment.status == "draft"
    assert experiment.channel == "own_site"
    assert experiment.target_metric == "qualified_leads"
    assert "massage-studio" in experiment.audience
    manifest=db.get(Artifact, first.manifest_artifact_id)
    payload=json.loads(manifest.content)
    assert payload["plan_artifact_id"] == str(plan.id)
    assert payload["experiment_id"] == str(experiment.id)
    assert payload["external_action"] is False
    assert len(list(db.scalars(select(Experiment).where(Experiment.product_id==product.id)))) == 1


def test_materialization_rejects_wrong_artifact_kind_and_foreign_product(db, product):
    wrong=Artifact(product_id=product.id, kind="article_brief", title="Wrong", content=plan_content())
    db.add(wrong)
    other=Product(name="Other", slug=f"other-{uuid4()}")
    db.add(other); db.commit()
    foreign=Artifact(product_id=other.id, kind="content_plan", title="Foreign", content=plan_content())
    db.add(foreign); db.commit()
    workflow=ExperimentMaterialization(db)

    with pytest.raises(LookupError):
        workflow.run(ExperimentFromPlanInput(product_id=product.id, plan_artifact_id=wrong.id))
    with pytest.raises(LookupError):
        workflow.run(ExperimentFromPlanInput(product_id=product.id, plan_artifact_id=foreign.id))


def test_materialization_api(tmp_path):
    engine=create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product=Product(name="ServGo", slug="servgo")
        db.add(product); db.flush()
        plan=Artifact(product_id=product.id, kind="content_plan", title="Plan", content=plan_content())
        db.add(plan); db.commit()
        app.dependency_overrides[get_db]=lambda: db
        try:
            with TestClient(app) as client:
                response=client.post(
                    f"/products/{product.id}/experiments/from-content-plan",
                    json={"plan_artifact_id": str(plan.id)},
                )
                assert response.status_code == 201, response.text
                body=response.json()
                assert body["status"] == "draft"
                assert client.get(f"/products/{product.id}/experiments").status_code == 200
                assert client.post(
                    f"/products/{product.id}/experiments/from-content-plan",
                    json={"plan_artifact_id": str(uuid4())},
                ).status_code == 404
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
