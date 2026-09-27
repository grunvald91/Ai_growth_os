from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Product
from app.models.domain import ArtifactStatus, DecisionType, TaskStatus
from app.services import ApprovalService, ArtifactService, DecisionService, ExperimentService, TaskService


def product(db: Session) -> Product:
    item = Product(name="ServGo", slug=f"servgo-{uuid4()}")
    db.add(item)
    db.flush()
    return item


def test_task_service_create_list_and_update(db: Session) -> None:
    owner = product(db)
    service = TaskService(db)

    task = service.create(product_id=owner.id, title="Analyze funnel", priority=3)
    updated = service.update(task.id, status=TaskStatus.RUNNING, assigned_agent="analytics")

    assert updated.status is TaskStatus.RUNNING
    assert service.get(task.id) is task
    assert service.list(product_id=owner.id, status=TaskStatus.RUNNING) == [task]
    with pytest.raises(ValueError, match="Invalid task status"):
        service.update(task.id, status="unknown")


def test_artifact_service_versions_updates(db: Session) -> None:
    owner = product(db)
    service = ArtifactService(db)

    artifact = service.create(product_id=owner.id, kind="report", title="Funnel", content="Draft")
    service.update(artifact.id, content="Revised", status=ArtifactStatus.AWAITING_APPROVAL)

    assert artifact.version == 2
    assert artifact.status is ArtifactStatus.AWAITING_APPROVAL
    assert service.list(product_id=owner.id) == [artifact]


def test_experiment_service_crud(db: Session) -> None:
    owner = product(db)
    service = ExperimentService(db)

    experiment = service.create(product_id=owner.id, name="Segment test", hypothesis="Studios convert")
    service.update(experiment.id, status="running", channel="search")

    assert service.get(experiment.id) is experiment
    assert experiment.status == "running"
    assert experiment.channel == "search"


def test_decision_service_preserves_types_and_empty_evidence(db: Session) -> None:
    owner = product(db)
    service = DecisionService(db)

    decision = service.create(product_id=owner.id, decision_type="TEST", rationale="Validate hypothesis")

    assert decision.decision_type is DecisionType.TEST
    assert decision.evidence_references == []
    with pytest.raises(ValueError, match="Invalid decision type"):
        service.create(product_id=owner.id, decision_type="GUESS", rationale="Invalid")


def test_approval_service_validates_status(db: Session) -> None:
    owner = product(db)
    service = ApprovalService(db)

    approval = service.create(product_id=owner.id, action_type="publish", risk_level="YELLOW")
    service.update(approval.id, status="approved", decided_by="owner")

    assert approval.status == "approved"
    with pytest.raises(ValueError, match="Invalid approval status"):
        service.update(approval.id, status="unknown")
