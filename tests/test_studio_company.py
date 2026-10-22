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


@pytest.fixture
def owned(studio, tmp_path, monkeypatch):
    """A Studio company with its own products folder, so tests never touch real files."""
    from app.core.config import get_settings
    monkeypatch.setenv("PRODUCTS_ROOT", str(tmp_path))
    get_settings.cache_clear()
    client, db = studio
    response = client.post("/studio/new", data={"name": "Ноготок", "about": ABOUT}, follow_redirects=False)
    assert response.status_code == 303
    product = db.scalar(select(Product).where(Product.name == "Ноготок"))
    yield client, db, product, tmp_path
    get_settings.cache_clear()


def _cancel_collection(client, product):
    assert client.post(f"/studio/products/{product.id}/profile/cancel", data={"go": "1"}, follow_redirects=False).status_code == 303


def test_profile_page_offers_rename_cancel_and_delete(owned):
    client, db, product, _ = owned
    page = client.get(f"/studio/products/{product.id}/profile")
    assert "Отменить сбор данных" in page.text and "Сохранить название" in page.text
    assert f"/studio/products/{product.id}/profile/delete" in page.text
    assert "Убрать" in page.text  # every material can be removed


def test_company_can_be_renamed(owned):
    client, db, product, _ = owned
    response = client.post(f"/studio/products/{product.id}/profile/rename",
                           data={"name": "Ноготок Плюс", "description": "Новое описание"}, follow_redirects=False)
    assert response.status_code == 303
    db.refresh(product)
    assert (product.name, product.description) == ("Ноготок Плюс", "Новое описание")
    bad = client.post(f"/studio/products/{product.id}/profile/rename", data={"name": "Н", "description": ""})
    assert bad.status_code == 409


def test_a_material_can_be_removed_and_the_collection_cancelled(owned):
    from app.intake.service import CompanyIntakeService
    client, db, product, root = owned
    service = CompanyIntakeService(db, products_root=root)
    session = service.active(product.id, "local_owner")
    state = service.get(product.id, session.id, actor="local_owner")
    source_id = state["sources"][0]["id"]
    response = client.post(f"/studio/products/{product.id}/profile/remove", data={"source_id": source_id},
                           follow_redirects=False)
    assert response.status_code == 303
    assert service.get(product.id, session.id, actor="local_owner")["sources"] == []
    again = client.post(f"/studio/products/{product.id}/profile/remove", data={"source_id": source_id})
    assert again.status_code == 409
    _cancel_collection(client, product)
    assert service.active(product.id, "local_owner") is None
    page = client.get(f"/studio/products/{product.id}/profile")
    assert "Заполнить вручную" in page.text and "Добавить материалы" in page.text


def test_profile_fields_can_be_edited_directly_and_history_is_kept(owned):
    import json
    from app.models.domain import Artifact
    client, db, product, root = owned
    _cancel_collection(client, product)
    form = client.get(f"/studio/products/{product.id}/profile/edit")
    assert form.status_code == 200 and 'name="f_identity.name"' in form.text
    response = client.post(f"/studio/products/{product.id}/profile/edit", data={
        "f_identity.name": "Ноготок", "f_identity.service_geography": "Сочи\nАдлер",
        "f_brand.prohibited_claims": "Гарантия результата"}, follow_redirects=False)
    assert response.status_code == 303
    saved = json.loads((root / product.slug / "company-profile.json").read_text())
    assert saved["identity"]["name"] == "Ноготок"
    assert saved["identity"]["service_geography"] == ["Сочи", "Адлер"]
    shown = client.get(f"/studio/products/{product.id}/profile").text
    assert "Ноготок" in shown and "Сочи; Адлер" in shown
    # A second edit changes one field, keeps the rest and records the previous file.
    client.post(f"/studio/products/{product.id}/profile/edit", data={
        "f_identity.name": "Ноготок", "f_identity.service_geography": "Сочи\nАдлер",
        "f_brand.prohibited_claims": "", "f_identity.website": "nogotok.ru"})
    saved = json.loads((root / product.slug / "company-profile.json").read_text())
    assert saved["identity"]["website"] == "nogotok.ru" and saved["brand"]["prohibited_claims"] == []
    history = db.scalars(select(Artifact).where(Artifact.product_id == product.id,
                                                Artifact.kind == "company_profile_previous")).all()
    assert len(history) == 2


def test_direct_edit_is_refused_while_a_collection_is_open(owned):
    client, db, product, root = owned
    response = client.post(f"/studio/products/{product.id}/profile/edit", data={"f_identity.name": "Другое"})
    assert response.status_code == 409
    assert not (root / product.slug / "company-profile.json").exists()


def test_company_deletion_needs_the_exact_name_and_removes_everything(owned):
    from app.db.models import Base
    client, db, product, root = owned
    (root / product.slug).mkdir(exist_ok=True)
    (root / product.slug / "company-profile.json").write_text("{}")
    (root / "_content" / str(product.id)).mkdir(parents=True)
    other = Product(name="Другая", slug="company-other")
    db.add(other)
    db.commit()
    page = client.get(f"/studio/products/{product.id}/profile/delete")
    assert page.status_code == 200 and "Удалить компанию навсегда" in page.text
    wrong = client.post(f"/studio/products/{product.id}/profile/delete", data={"confirm_name": "Не то"})
    assert wrong.status_code == 422 and db.get(Product, product.id) is not None
    done = client.post(f"/studio/products/{product.id}/profile/delete", data={"confirm_name": " ноготок "},
                       follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/studio?choose=1"
    db.expire_all()
    assert db.get(Product, product.id) is None and db.get(Product, other.id) is not None
    for table in Base.metadata.sorted_tables:
        if "product_id" in table.c:
            assert db.execute(select(table).where(table.c.product_id == product.id)).first() is None
    assert not (root / product.slug).exists() and not (root / "_content" / str(product.id)).exists()


def test_deleting_a_repository_company_keeps_its_profile_folder(studio, tmp_path, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("PRODUCTS_ROOT", str(tmp_path))
    get_settings.cache_clear()
    try:
        client, db = studio
        product = db.scalar(select(Product))
        (tmp_path / product.slug).mkdir()
        (tmp_path / product.slug / "company-profile.json").write_text("{}")
        client.post(f"/studio/products/{product.id}/profile/delete",
                    data={"confirm_name": product.name}, follow_redirects=False)
        assert db.get(Product, product.id) is None
        assert (tmp_path / product.slug / "company-profile.json").exists()
    finally:
        get_settings.cache_clear()


def test_direct_edit_does_not_hide_an_old_text_description(owned):
    client, db, product, root = owned
    _cancel_collection(client, product)
    legacy = root / product.slug / ".claude"
    legacy.mkdir(parents=True)
    (legacy / "product-marketing-context.md").write_text("Старое описание компании")
    response = client.post(f"/studio/products/{product.id}/profile/edit", data={"f_identity.name": "Ноготок"})
    assert response.status_code == 409 and "старое текстовое описание" in response.text
    assert not (root / product.slug / "company-profile.json").exists()


def test_deletion_respects_foreign_keys_and_spares_other_companies(tmp_path):
    """SQLite ignores foreign keys by default; switch them on so the delete order is really checked."""
    import datetime
    import uuid
    from sqlalchemy import event, func, insert
    from app.intake.company_admin import delete_company

    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    def fill(connection, table, ids, product_id):
        row = {}
        for column in table.columns:
            if column.foreign_keys:
                target = next(iter(column.foreign_keys)).column.table.name
                row[column.name] = product_id if target == "products" else ids.get(target)
            elif column.primary_key:
                row[column.name] = uuid.uuid4()
            elif not (column.nullable or column.default is not None or column.server_default is not None):
                if getattr(column.type, "enums", None):
                    row[column.name] = column.type.enums[0]
                elif type(column.type).__name__ in {"Numeric", "Float"}:
                    row[column.name] = 1.0
                else:
                    try:
                        kind = column.type.python_type
                    except NotImplementedError:
                        kind = str
                    row[column.name] = {int: 1, bool: False, dict: {}, list: [], uuid.UUID: uuid.uuid4(),
                                        datetime.datetime: datetime.datetime.now(datetime.timezone.utc)}.get(kind, "x")
        connection.execute(insert(table).values(**row))
        ids[table.name] = row.get("id")

    with Session(engine) as db:
        products = [Product(name=name, slug=f"company-{name}") for name in ("a", "b")]
        db.add_all(products)
        db.commit()
        for product in products:
            ids = {}
            for table in Base.metadata.sorted_tables:
                if table.name != "products":
                    fill(db.connection(), table, ids, product.id)
            db.commit()
        delete_company(db, tmp_path, products[0].id)
        for table in Base.metadata.sorted_tables:
            assert db.execute(select(func.count()).select_from(table)).scalar() == 1, table.name
    engine.dispose()
