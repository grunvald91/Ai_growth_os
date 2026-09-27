from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SkillNotFoundError(FileNotFoundError):
    """Raised when the requested skill directory or SKILL.md does not exist."""


class SkillLoadError(ValueError):
    """Raised when the skill name is invalid or escapes the skills root."""


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    path: Path
    content: str


class SkillLoader:
    """Loads exactly one SKILL.md for a given skill name.

    Mapping:  name (relative directory) -> <root>/<name>/SKILL.md

    No directory scanning, no bulk loading, no network access.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def load(self, name: str) -> Skill:
        if not isinstance(name, str) or not name.strip():
            raise SkillLoadError("skill name must be a non-empty string")
        if "\\" in name:
            raise SkillLoadError("skill name must not contain backslashes")
        if name.startswith("/") or Path(name).is_absolute():
            raise SkillLoadError("skill name must be a relative path, not absolute")

        # Reject path traversal components before touching the filesystem.
        parts = Path(name).parts
        if ".." in parts:
            raise SkillLoadError("skill name must not contain '..' traversal")
        if any(p in (".", "") for p in parts):
            # "." would resolve to root itself; disallow ambiguous names
            if name in (".", "./"):
                raise SkillLoadError("skill name must not be '.'")
        # Ensure name does not try to escape via leading/trailing slashes tricks
        # Normalise: strip leading/trailing slashes already handled by absolute check,
        # but reject empty segments like "a//b"
        if "//" in name or name.startswith("./") or "/./" in name:
            raise SkillLoadError("skill name contains invalid path segment")

        # Resolve root and target to prevent symlink escape.
        try:
            resolved_root = self._root.resolve()
        except (OSError, RuntimeError) as exc:
            raise SkillLoadError(f"cannot resolve skills root: {exc}") from exc

        # Single exact file — no scanning.
        candidate = self._root / name / "SKILL.md"

        try:
            resolved_candidate = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise SkillLoadError(f"cannot resolve skill path: {exc}") from exc

        # Ensure the resolved candidate is inside the resolved root.
        # resolved_candidate is .../SKILL.md, its parent is the skill dir.
        # We check that the skill dir (and file) lives under resolved_root.
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise SkillLoadError("skill path escapes skills root") from exc

        # Also ensure the parent dir is inside root (extra guard)
        try:
            resolved_candidate.parent.relative_to(resolved_root)
        except ValueError as exc:
            raise SkillLoadError("skill path escapes skills root") from exc

        if not resolved_candidate.is_file():
            raise SkillNotFoundError(f"skill '{name}' not found at {candidate}")

        try:
            content = resolved_candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillNotFoundError(f"cannot read skill '{name}': {exc}") from exc

        return Skill(name=name, path=resolved_candidate, content=content)
