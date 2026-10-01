from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.schemas import (ProductCreate, ProductRead, GoalCreate, GoalRead, TaskRead,
    ExperimentCreate, ExperimentRead, ArtifactRead, DecisionRead, ApprovalRead, ArticleCreate, ArticleProductionRead,
    TeamCycleCreate, TeamCycleRead, ExperimentFromPlanCreate, ExperimentMaterializationRead)
from app.core.config import get_settings
from app.core.composition import build_review, build_article_production, build_team_cycle
from app.db.session import get_db
from app.models.domain import Product, Goal, Task, Experiment, Artifact, Decision, Approval
from app.services.experiments import ExperimentService
from app.workflows.contracts import ReviewResult
from app.workflows.experiment_materialization import ExperimentFromPlanInput, ExperimentMaterialization

router = APIRouter()

def require_product(db, product_id):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    return product

def get_review(db: Session = Depends(get_db)):
    try:
        return build_review(db, get_settings())
    except ValueError:
        raise HTTPException(503, "LLM runtime is not configured") from None

@router.post('/products', response_model=ProductRead, status_code=201)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    product = Product(**body.model_dump())
    db.add(product)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Product slug already exists") from None
    db.refresh(product)
    return product

@router.get('/products', response_model=list[ProductRead])
def products(db: Session = Depends(get_db), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return list(db.scalars(select(Product).order_by(Product.created_at.desc(), Product.id).limit(limit).offset(offset)))

@router.get('/products/{product_id}', response_model=ProductRead)
def product(product_id: UUID, db: Session = Depends(get_db)):
    return require_product(db, product_id)

@router.post('/products/{product_id}/goals', response_model=GoalRead, status_code=201)
def create_goal(product_id: UUID, body: GoalCreate, db: Session = Depends(get_db)):
    require_product(db, product_id)
    if body.starts_on and body.ends_on and body.starts_on > body.ends_on:
        raise HTTPException(422, "Goal dates are reversed")
    goal = Goal(product_id=product_id, **body.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal

@router.post('/products/{product_id}/experiments', response_model=ExperimentRead, status_code=201)
def create_experiment(product_id: UUID, body: ExperimentCreate, db: Session = Depends(get_db)):
    require_product(db, product_id)
    if body.goal_id is not None:
        goal = db.get(Goal, body.goal_id)
        if goal is None or goal.product_id != product_id:
            raise HTTPException(404, "Goal not found for product")
    experiment = ExperimentService(db).create(product_id=product_id, **body.model_dump())
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Experiment key already exists for product") from None
    db.refresh(experiment)
    return experiment


@router.post('/products/{product_id}/daily-growth-review', response_model=ReviewResult)
async def daily_growth_review(product_id: UUID, db: Session = Depends(get_db), review=Depends(get_review)):
    require_product(db, product_id)
    return await review.run(product_id)


@router.post('/products/{product_id}/experiments/from-content-plan',
             response_model=ExperimentMaterializationRead, status_code=201)
def experiment_from_content_plan(product_id: UUID, body: ExperimentFromPlanCreate,
                                 db: Session = Depends(get_db)):
    require_product(db, product_id)
    try:
        return ExperimentMaterialization(db).run(
            ExperimentFromPlanInput(product_id=product_id, **body.model_dump())
        )
    except LookupError:
        raise HTTPException(404, 'Plan artifact or decision not found for this product') from None
    except ValueError:
        raise HTTPException(409, 'Plan artifact cannot be materialized as an experiment') from None




def get_team_cycle(db: Session = Depends(get_db)):
    try:
        return build_team_cycle(db, get_settings())
    except ValueError:
        raise HTTPException(503, "LLM runtime is not configured") from None


@router.post('/products/{product_id}/team-cycle', response_model=TeamCycleRead)
async def team_cycle(product_id: UUID, body: TeamCycleCreate,
                     db: Session = Depends(get_db), cycle=Depends(get_team_cycle)):
    require_product(db, product_id)
    return await cycle.run(product_id, max_tasks=body.max_tasks, followup_cmo=body.followup_cmo)


def get_article_production(db: Session = Depends(get_db)):
    try:
        return build_article_production(db, get_settings())
    except ValueError:
        raise HTTPException(503, 'LLM runtime is not configured') from None


@router.post('/products/{product_id}/articles', response_model=ArticleProductionRead)
async def produce_article(product_id: UUID, body: ArticleCreate,
                          db: Session = Depends(get_db), workflow=Depends(get_article_production)):
    require_product(db, product_id)
    try:
        result = await workflow.run(body.workflow_input(product_id))
    except LookupError:
        raise HTTPException(404, 'Product, task or decision not found for this product') from None
    except ValueError:
        raise HTTPException(409, 'Task is not available for article production') from None
    return ArticleProductionRead(**result.model_dump(),
        main_artifact_id=result.artifact_ids[0] if result.artifact_ids else None,
        derivative_artifact_ids=result.artifact_ids[1:])


@router.get('/products/{product_id}/artifacts/{artifact_id}', response_model=ArtifactRead)
def artifact(product_id: UUID, artifact_id: UUID, db: Session = Depends(get_db)):
    require_product(db, product_id)
    record = db.get(Artifact, artifact_id)
    if record is None or record.product_id != product_id:
        raise HTTPException(404, 'Artifact not found')
    return record


def register_list(path, model, schema):
    def endpoint(product_id: UUID, db: Session = Depends(get_db),
                 limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        require_product(db, product_id)
        return list(db.scalars(select(model).where(model.product_id == product_id)
            .order_by(model.created_at.desc(), model.id).limit(limit).offset(offset)))
    endpoint.__name__ = 'list_' + path
    router.add_api_route('/products/{product_id}/' + path, endpoint, response_model=list[schema])

for path, model, schema in [('goals', Goal, GoalRead), ('tasks', Task, TaskRead),
    ('experiments', Experiment, ExperimentRead), ('artifacts', Artifact, ArtifactRead),
    ('decisions', Decision, DecisionRead), ('approvals', Approval, ApprovalRead)]:
    register_list(path, model, schema)
