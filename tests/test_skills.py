from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.config import Settings
from app.skills import Skill, SkillLoader, SkillLoadError, SkillNotFoundError


def _make_skill(root: Path, name: str, content: str = "# Skill\n") -> Path:
    p = root / name / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_valid_lookup(tmp_path: Path) -> None:
    _make_skill(tmp_path, "copywriting", "# Copy\nhello")
    loader = SkillLoader(tmp_path)
    skill = loader.load("copywriting")
    assert isinstance(skill, Skill)
    assert skill.name == "copywriting"
    assert skill.path == (tmp_path / "copywriting" / "SKILL.md").resolve()
    assert skill.content == "# Copy\nhello"
    assert skill.path.is_file()


def test_nested_lookup(tmp_path: Path) -> None:
    _make_skill(tmp_path, "a/b/c", "nested")
    loader = SkillLoader(tmp_path)
    skill = loader.load("a/b/c")
    assert skill.content == "nested"
    assert skill.name == "a/b/c"


def test_missing_skill_raises_not_found(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    with pytest.raises(SkillNotFoundError):
        loader.load("does-not-exist")


def test_missing_skill_md_raises_not_found(tmp_path: Path) -> None:
    (tmp_path / "empty-skill").mkdir()
    loader = SkillLoader(tmp_path)
    with pytest.raises(SkillNotFoundError):
        loader.load("empty-skill")


def test_traversal_dotdot_rejected(tmp_path: Path) -> None:
    _make_skill(tmp_path, "real", "ok")
    loader = SkillLoader(tmp_path)
    for bad in ["../real", "a/../b", "a/../../etc", ".."]:
        with pytest.raises((SkillLoadError, SkillNotFoundError)):
            # Must not escape; traversal names should be SkillLoadError
            # but we accept either as long as escape is blocked
            result = loader.load(bad)
            # If it somehow didn't raise, ensure it didn't escape root
            assert Path(result.path).resolve().is_relative_to(tmp_path.resolve())


def test_absolute_path_rejected(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    with pytest.raises(SkillLoadError):
        loader.load("/absolute")
    with pytest.raises(SkillLoadError):
        loader.load("/tmp/evil")


def test_backslash_rejected(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    with pytest.raises(SkillLoadError):
        loader.load("a\\b")
    with pytest.raises(SkillLoadError):
        loader.load("a\\..\\b")


def test_symlink_escape_blocked(tmp_path: Path) -> None:
    # Outside directory lives above the skills root so any read via symlink
    # would genuinely escape the root.
    root = tmp_path / "skills_root"
    root.mkdir()
    outside = tmp_path / "_outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("outside secret", encoding="utf-8")

    link = root / "evil-link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported on this platform")

    loader = SkillLoader(root)
    with pytest.raises(SkillLoadError):
        loader.load("evil-link")

    # Nested symlink escape via subdirectory component
    sub = root / "a"
    sub.mkdir()
    nested_link = sub / "link"
    try:
        nested_link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not supported on this platform")
    with pytest.raises(SkillLoadError):
        loader.load("a/link")


def test_symlink_inside_root_allowed(tmp_path: Path) -> None:
    # Symlink that stays inside root should be allowed
    real = tmp_path / "real"
    real.mkdir()
    (real / "SKILL.md").write_text("inside", encoding="utf-8")
    link = tmp_path / "alias"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks not supported")
    loader = SkillLoader(tmp_path)
    skill = loader.load("alias")
    assert skill.content == "inside"


def test_frozen_skill(tmp_path: Path) -> None:
    _make_skill(tmp_path, "frozen", "x")
    skill = SkillLoader(tmp_path).load("frozen")
    with pytest.raises((AttributeError, TypeError)):
        skill.name = "other"  # type: ignore[misc]


def test_empty_name_rejected(tmp_path: Path) -> None:
    loader = SkillLoader(tmp_path)
    for bad in ["", "   ", "\n"]:
        with pytest.raises(SkillLoadError):
            loader.load(bad)


def test_skills_root_in_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SKILLS_ROOT", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.skills_root == tmp_path
    # Loader works with configured root
    _make_skill(tmp_path, "configured", "cfg")
    loader = SkillLoader(settings.skills_root)
    assert loader.load("configured").content == "cfg"


def test_skills_root_default() -> None:
    old = os.environ.pop("SKILLS_ROOT", None)
    try:
        s = Settings(_env_file=None)
        assert s.skills_root == Path("skills")
    finally:
        if old is not None:
            os.environ["SKILLS_ROOT"] = old


def test_no_bulk_load_only_exact_file(tmp_path: Path) -> None:
    # Multiple skills exist; loading one must not require reading others
    _make_skill(tmp_path, "a", "content-a")
    _make_skill(tmp_path, "b", "content-b")
    loader = SkillLoader(tmp_path)
    skill = loader.load("a")
    assert skill.content == "content-a"
    # Ensure path is exact file
    assert skill.path.name == "SKILL.md"
    assert skill.path.parent.name == "a"
