from app.models.domain import AgentRun, Product, Task, TaskStatus
from app.team_worker import _recover_transient_failed_experiment_tasks


def _failed_parent_with_runs(db, run_errors, *, model=None):
    product = Product(name="ServGo", slug=f"servgo-{len(run_errors)}")
    db.add(product)
    db.flush()
    parent = Task(
        product_id=product.id,
        title="EXP-SERVGO-001: prepare outreach cohort",
        assigned_agent="research_pmm",
        status=TaskStatus.FAILED,
    )
    db.add(parent)
    db.flush()
    for index, error in enumerate(run_errors):
        child = Task(
            product_id=product.id,
            parent_id=parent.id,
            title=f"EXP-SERVGO-001: child {index}",
            assigned_agent="segment_researcher",
            status=TaskStatus.FAILED,
        )
        db.add(child)
        db.flush()
        db.add(
            AgentRun(
                task_id=child.id,
                agent_name="segment_researcher",
                status="failed",
                error=error,
                model=model,
            )
        )
    db.commit()
    return parent


def test_worker_requeues_first_transient_http_failure_cycle(db):
    parent = _failed_parent_with_runs(
        db,
        ["research_worker_failed:HTTPStatusError"] * 4,
    )

    recovered = _recover_transient_failed_experiment_tasks(db)

    db.refresh(parent)
    assert recovered == 1
    assert parent.status == TaskStatus.QUEUED


def test_worker_does_not_retry_nontransient_failure(db):
    parent = _failed_parent_with_runs(
        db,
        [
            "research_worker_failed:HTTPStatusError",
            "research_worker_failed:ValueError",
        ],
    )

    recovered = _recover_transient_failed_experiment_tasks(db)

    db.refresh(parent)
    assert recovered == 0
    assert parent.status == TaskStatus.FAILED


def test_worker_does_not_retry_after_one_full_retry_generation(db):
    parent = _failed_parent_with_runs(
        db,
        ["research_worker_failed:HTTPStatusError"] * 5,
    )

    recovered = _recover_transient_failed_experiment_tasks(db)

    db.refresh(parent)
    assert recovered == 0
    assert parent.status == TaskStatus.FAILED



def test_worker_allows_one_retry_after_model_failover(db):
    parent = _failed_parent_with_runs(
        db,
        ["research_worker_failed:HTTPStatusError"] * 8,
        model="grok-4.7",
    )

    recovered = _recover_transient_failed_experiment_tasks(
        db,
        current_model="gpt-6-sol",
    )

    db.refresh(parent)
    assert recovered == 1
    assert parent.status == TaskStatus.QUEUED


def test_worker_does_not_retry_same_model_after_two_generations(db):
    parent = _failed_parent_with_runs(
        db,
        ["research_worker_failed:HTTPStatusError"] * 8,
        model="gpt-6-sol",
    )

    recovered = _recover_transient_failed_experiment_tasks(
        db,
        current_model="gpt-6-sol",
    )

    db.refresh(parent)
    assert recovered == 0
    assert parent.status == TaskStatus.FAILED



def test_worker_allows_one_retry_after_fallback_chain_activation(db):
    parent = _failed_parent_with_runs(
        db,
        ["research_worker_failed:HTTPStatusError"] * 8,
        model="gpt-6-sol",
    )

    recovered = _recover_transient_failed_experiment_tasks(
        db,
        current_model="gpt-6-sol",
        fallback_enabled=True,
    )

    db.refresh(parent)
    assert recovered == 1
    assert parent.status == TaskStatus.QUEUED


def test_worker_stops_after_fallback_recovery_generation(db):
    parent = _failed_parent_with_runs(
        db,
        ["research_worker_failed:HTTPStatusError"] * 12,
        model="gpt-6-sol",
    )

    recovered = _recover_transient_failed_experiment_tasks(
        db,
        current_model="gpt-6-sol",
        fallback_enabled=True,
    )

    db.refresh(parent)
    assert recovered == 0
    assert parent.status == TaskStatus.FAILED
