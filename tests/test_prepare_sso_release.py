import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare", ROOT / "deploy/ops/20261023/prepare_sso_release.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)

BASE = ["0001_a.py", "0006_publication_due.py"]
NEW = BASE + ["0007_dispatcher_conversations.py", "0008_studio_sso.py"]


def test_goes_when_the_database_is_where_the_base_image_expects():
    assert prepare.plan(BASE, NEW, "0006", []) == []


def test_stops_when_the_database_is_somewhere_else():
    assert "database is at 0005" in prepare.plan(BASE, NEW, "0005", [])[0]
    assert "database is at None" in prepare.plan(BASE, NEW, None, [])[0]


def test_stops_on_missing_dependencies_or_dropped_migrations():
    assert "lacks dependencies" in prepare.plan(BASE, NEW, "0006", ["httpx (not installed)"])[0]
    assert "lacks migrations" in prepare.plan(BASE, ["0001_a.py"], "0006", [])[0]


def test_reads_the_revision_out_of_alembic_output():
    assert prepare.revision_of("INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n0006 (head)\n") == "0006"
    assert prepare.revision_of("0008\n") == "0008"
    assert prepare.revision_of("INFO  [alembic.runtime.migration] Will assume transactional DDL.\n") is None


def test_each_release_gets_its_own_image_tag():
    assert prepare.release_tag(NEW) == "servgo-growth-release:0008"
    assert prepare.release_tag(NEW + ["0009_insight_settings.py"]) == "servgo-growth-release:0009"
    assert prepare.release_tag(BASE) == "servgo-growth-release:0006"


def test_real_migrations_follow_the_base():
    names = sorted(p.name for p in (ROOT / "alembic/versions").glob("*.py"))
    # Later migrations may follow; the release these two belong to must stay in this order.
    first = names.index("0007_dispatcher_conversations.py")
    assert names[first:first + 2] == ["0007_dispatcher_conversations.py", "0008_studio_sso.py"]
