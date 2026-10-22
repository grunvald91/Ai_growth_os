import importlib.util
import io
import json
import subprocess
import sys
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
        "pyproject.toml": "",
        "alembic/versions/0001_a.py": "",
    }), tmp_path)
    assert deploy_growth.incompatibilities(tmp_path, {"unmet": [], "migrations": ["0001_a.py"]}) == []

    problems = deploy_growth.incompatibilities(tmp_path, {"unmet": ["x>=2 (installed 1)"], "migrations": []})
    assert any("lacks dependencies: x>=2" in p for p in problems)
    assert any("added ['0001_a.py']" in p for p in problems)


def test_base_facts_script_checks_installed_dependencies(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["pytest>=1", "pytest<1", "no-such-package-zz>=1"]\n')
    facts = json.loads(subprocess.run(
        [sys.executable, "-c", deploy_growth.BASE_FACTS], input=(tmp_path / "pyproject.toml").read_text(),
        capture_output=True, text=True, check=True).stdout)
    assert [u.split(" ")[0] for u in facts["unmet"]] == ["pytest<1", "no-such-package-zz>=1"]

    project = (ROOT / "pyproject.toml").read_text()
    facts = json.loads(subprocess.run(
        [sys.executable, "-c", deploy_growth.BASE_FACTS], input=project,
        capture_output=True, text=True, check=True).stdout)
    assert facts["unmet"] == []


def test_deploy_rejects_bad_input_before_touching_the_server():
    with pytest.raises(SystemExit, match="40 lowercase hex"):
        deploy_growth.deploy("HEAD", b"x")
    with pytest.raises(SystemExit, match="no source archive"):
        deploy_growth.deploy("a" * 40, b"")


def test_sso_secrets_are_created_once_and_never_changed(tmp_path):
    env_file, handoff = tmp_path / ".env", tmp_path / "deploy" / "servgo_sso.env"
    env_file.write_text("ANTHROPIC_API_KEY=keep-me\nSERVGO_INTERNAL_KEY=\n")
    env_file.chmod(0o640)
    created = deploy_growth.provision_sso_secrets(env_file, handoff)
    assert created == ["SERVGO_SSO_SECRET", "SERVGO_INTERNAL_KEY"]
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    assert values["ANTHROPIC_API_KEY"] == "keep-me"
    assert len(values["SERVGO_SSO_SECRET"]) == len(values["SERVGO_INTERNAL_KEY"]) == 64
    assert values["SERVGO_SSO_SECRET"] != values["SERVGO_INTERNAL_KEY"]
    assert env_file.read_text().count("SERVGO_INTERNAL_KEY") == 1          # the empty placeholder was replaced
    assert oct(env_file.stat().st_mode & 0o777) == "0o640"                 # the file's mode is kept
    # ServGo gets the same values under its own names, in a root-only file.
    given = dict(line.split("=", 1) for line in handoff.read_text().splitlines())
    assert given == {"STUDIO_SSO_SECRET": values["SERVGO_SSO_SECRET"], "STUDIO_INTERNAL_KEY": values["SERVGO_INTERNAL_KEY"]}
    assert oct(handoff.stat().st_mode & 0o777) == "0o600"
    # A second deploy changes nothing.
    before = env_file.read_text()
    assert deploy_growth.provision_sso_secrets(env_file, handoff) == []
    assert env_file.read_text() == before


def test_sso_provisioning_works_without_an_env_file_and_does_not_print(tmp_path, capsys):
    env_file, handoff = tmp_path / ".env", tmp_path / "servgo_sso.env"
    deploy_growth.provision_sso_secrets(env_file, handoff)
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    secret = dict(line.split("=", 1) for line in env_file.read_text().splitlines())["SERVGO_SSO_SECRET"]
    assert secret not in capsys.readouterr().out
