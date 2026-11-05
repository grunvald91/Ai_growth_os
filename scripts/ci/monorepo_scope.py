#!/usr/bin/env python3
"""Classify ServGo monorepo changes without assuming every commit deploys Growth.

Only the self-contained BookingBot subtree and explicitly named monorepo
bookkeeping files are ignored for Growth runtime releases. Unknown paths
are treated conservatively as Growth-affecting.
"""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import PurePosixPath


BOOKING_PREFIX = "services/booking_bot/"
BOOKING_ONLY_FILES = {
    ".github/workflows/bookingbot-module-ci.yml",
    "docs/architecture/bookingbot-source-import.md",
}
RELEASE_GATE_FILES = {
    ".github/workflows/ci.yml",
    ".github/workflows/deploy-growth.yml",
    "scripts/ci/monorepo_scope.py",
}


def affects_growth(path: str) -> bool:
    path = path.replace("\\", "/")
    if path.startswith(BOOKING_PREFIX):
        return False
    return path not in BOOKING_ONLY_FILES | RELEASE_GATE_FILES


def changed_paths(base: str, head: str) -> list[str]:
    """Use two explicit commits: merge commits are compared to first parent."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACDMRTUXB", "-z", base, head],
        check=True,
        capture_output=True,
    )
    return [p.decode("utf-8") for p in result.stdout.split(b"\0") if p]


def scope(paths: list[str]) -> tuple[bool, bool]:
    return any(affects_growth(p) for p in paths), any(
        p.startswith(BOOKING_PREFIX) or p in BOOKING_ONLY_FILES for p in paths
    )


def self_test() -> None:
    cases = [
        (["services/booking_bot/webapp/server.py"], (False, True)),
        (["services/booking_bot/.github/workflows/deploy.yml"], (False, True)),
        ([".github/workflows/bookingbot-module-ci.yml"], (False, True)),
        (["docs/architecture/bookingbot-source-import.md"], (False, True)),
        ([".github/workflows/ci.yml", ".github/workflows/deploy-growth.yml"], (False, False)),
        (["app/main.py"], (True, False)),
        (["products/servgo/README.md"], (True, False)),
        (["tests/test_growth.py"], (True, False)),
        (["alembic/versions/9999_upgrade.py"], (True, False)),
        (["pyproject.toml"], (True, False)),
        (["services/booking_bot/webapp/server.py", "app/main.py"], (True, True)),
        (["services/booking_bot/webapp/server.py", "docs/unrelated.md"], (True, True)),
        ([], (False, False)),
    ]
    for paths, expected in cases:
        result = scope(paths)
        if result != expected:
            raise AssertionError(f"Wrong scope for {paths}: {result} != {expected}")
    print(f"MONOREPO_SCOPE_SELF_TEST_OK cases={len(cases)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="HEAD^")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--github-output", default=os.getenv("GITHUB_OUTPUT"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    paths = changed_paths(args.base, args.head)
    growth, booking = scope(paths)
    outputs = {
        "growth_changed": str(growth).lower(),
        "booking_changed": str(booking).lower(),
        "changed_files": str(len(paths)),
    }
    print("MONOREPO_SCOPE", f"growth={growth}", f"booking={booking}", f"files={len(paths)}")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            for key, value in outputs.items():
                f.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
