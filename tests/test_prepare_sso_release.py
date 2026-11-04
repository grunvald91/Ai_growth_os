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


def test_only_reviewed_woff_dependency_is_installable(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\ndependencies = ["fonttools[woff]>=4.60,<5", "unrelated>=1"]\n')
    extras = prepare.installable_requirements(
        tmp_path, ["fonttools[woff]>=4.60,<5 (not installed)",
                   "unrelated>=1 (not installed)"])
    assert extras == ["fonttools[woff]>=4.60,<5"]
    invalid = ["unrelated>=1 (not installed)"]
    unsupported = [item for item in invalid
                   if not any(item.startswith(req + " (") for req in extras)]
    assert "lacks dependencies" in prepare.plan(BASE, NEW, "0006", unsupported)[0]


def test_pg_backup_verified_before_migration(tmp_path, monkeypatch):
    import io
    import os
    import subprocess

    monkeypatch.setattr(prepare, "BACKUPS", tmp_path)
    def fake_run(command, *, stdout, stderr, check):
        assert command[1:3] == ["exec", prepare.POSTGRES]
        stdout.write(b"PGDMP" + b"safe-test-data" * 16)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(prepare.subprocess, "run", fake_run)
    result = prepare.backup_database("0012", "0013")
    assert result.read_bytes().startswith(b"PGDMP")
    assert os.stat(result).st_mode & 0o777 == 0o600
    assert result.name.startswith("before-brand-kit-0012-to-0013")


def test_pg_backup_refuses_invalid_pg_dump(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(prepare, "BACKUPS", tmp_path)
    def fake_run(command, *, stdout, stderr, check):
        stdout.write(b"invalid" * 60)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(prepare.subprocess, "run", fake_run)
    import pytest
    with pytest.raises(RuntimeError, match="not a custom-format"):
        prepare.backup_database("0012", "0013")
    assert list(tmp_path.iterdir()) == []
