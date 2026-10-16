import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Product, Task


@pytest.fixture
def studio():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add(Product(name="Цветочная мастерская", slug="florist-company-test"))
        db.commit()

        def override():
            yield db

        app.dependency_overrides[get_db] = override
        with TestClient(app) as client:
            yield client, db
        app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


ABOUT = "Студия маникюра в Сочи: маникюр, педикюр, наращивание. Для женщин 25–45, запись онлайн."


def test_selector_offers_to_add_a_company(studio):
    client, _ = studio
    page = client.get("/studio?choose=1")
    assert page.status_code == 200
    assert 'href="/studio/new"' in page.text
    assert "Добавить компанию" in client.get("/studio/new").text


def test_new_company_starts_intake_with_owner_description(studio):
    client, db = studio
    response = client.post("/studio/new", data={"name": "Ноготок", "about": ABOUT}, follow_redirects=False)
    assert response.status_code == 303
    product = db.scalar(select(Product).where(Product.name == "Ноготок"))
    assert product is not None and product.slug.startswith("company-")
    assert response.headers["location"] == f"/studio/products/{product.id}/profile"
    intake = db.scalar(select(Task).where(Task.product_id == product.id, Task.assigned_agent == "company_intake"))
    assert intake is not None
    profile = client.get(response.headers["location"])
    assert profile.status_code == 200
    assert "growth studio: описание владельца" in profile.text
    assert "Собрать профиль" in profile.text


def test_new_company_requires_name_and_description(studio):
    client, db = studio
    response = client.post("/studio/new", data={"name": "Н", "about": "коротко"})
    assert response.status_code == 422
    assert db.scalar(select(Product).where(Product.name == "Н")) is None
