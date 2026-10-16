import importlib.util
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deploy_growth", ROOT / "deploy" / "server" / "deploy_growth.py")
deploy_growth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy_growth)


def tarball(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_unpack_accepts_git_archive_of_this_repository(tmp_path):
    payload = subprocess.check_output(
        ["git", "archive", "--format=tar.gz", "HEAD", "app", "alembic/versions", "pyproject.toml", "products"],
        cwd=ROOT,
    )
    deploy_growth.unpack(payload, tmp_path)
    assert (tmp_path / "app" / "main.py").is_file()
    assert (tmp_path / "pyproject.toml").is_file()


def test_unpack_rejects_unexpected_paths(tmp_path):
    with pytest.raises(RuntimeError, match="unexpected path"):
        deploy_growth.unpack(tarball({"app/main.py": "", "deploy/x.sh": ""}), tmp_path)


def test_unpack_rejects_path_traversal(tmp_path):
    with pytest.raises(Exception):
        deploy_growth.unpack(tarball({"app/../../escape.py": ""}), tmp_path / "release")
    assert not (tmp_path / "escape.py").exists()


def test_unpack_requires_app(tmp_path):
    with pytest.raises(RuntimeError, match="no app/"):
        deploy_growth.unpack(tarball({"pyproject.toml": ""}), tmp_path)


def test_incompatibilities_only_for_dependency_or_schema_changes(tmp_path):
    deploy_growth.unpack(tarball({
        "app/main.py": "",
        "pyproject.toml": "deps",
        "alembic/versions/0001_a.py": "",
    }), tmp_path)
    base = {"pyproject": "deps", "migrations": ["0001_a.py"]}
    assert deploy_growth.incompatibilities(tmp_path, base) == []

    problems = deploy_growth.incompatibilities(tmp_path, {"pyproject": "old", "migrations": []})
    assert any("pyproject.toml" in p for p in problems)
    assert any("added ['0001_a.py']" in p for p in problems)


def test_deploy_rejects_bad_input_before_touching_the_server():
    with pytest.raises(SystemExit, match="40 lowercase hex"):
        deploy_growth.deploy("HEAD", b"x")
    with pytest.raises(SystemExit, match="no source archive"):
        deploy_growth.deploy("a" * 40, b"")
