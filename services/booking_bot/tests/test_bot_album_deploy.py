"""Album-only production patch must leave unrelated ServGo code unchanged."""
import ast
import hashlib
from pathlib import Path

import pytest

from scripts.deploy_bot_album_fix import patch_album_source
from scripts.deploy_bot_room_photo_fix import nodes

BEFORE = '''"""Synthetic production module."""
import os
from bot.utils.message_utils import safe_edit_or_send

SUPER_ADMIN_ID = 1

async def unrelated():
    return "do not change"

async def emp_add_photo_receive(message, state):
    # Берём фото максимального размера
    return message.photo

async def emp_photos_done(callback, state):
    return callback

async def emp_photos_back(callback, state):
    return state
'''


def baseline_and_patch():
    previous = BEFORE
    current = Path("bot/handlers/admin.py").read_text(encoding="utf-8")
    baseline = {name: hashlib.sha256(ast.get_source_segment(previous, nodes(previous)[name]).encode()).hexdigest()
                for name in ("emp_add_photo_receive", "emp_photos_done", "emp_photos_back")}
    return previous, current, baseline, patch_album_source(previous, current, baseline)


def test_album_patch_preserves_all_unrelated_functions():
    previous, current, baseline, patched = baseline_and_patch()
    before = nodes(previous)
    after = nodes(patched)
    assert set(before) == set(after) - {"_send_room_photo_album_summary"}
    for name, node in before.items():
        if name not in baseline:
            assert ast.get_source_segment(previous, node) == ast.get_source_segment(patched, after[name]), name
    for name in baseline:
        assert ast.get_source_segment(patched, after[name]) == ast.get_source_segment(current, nodes(current)[name])
    assert "FOR UPDATE" not in patched  # SQLAlchemy constructs the query; no raw SQL injection.
    assert patched.count("import asyncio\n") == 1
    assert patched.count("def _cancel_room_photo_album_summaries") == 1
    assert patched.count("async def _send_room_photo_album_summary") == 1
    ast.parse(patched)


def test_album_patch_rejects_changed_production_handler():
    previous, current, baseline, _ = baseline_and_patch()
    with pytest.raises(RuntimeError, match="baseline mismatch"):
        patch_album_source(previous.replace("# Берём фото максимального размера", "# changed live handler", 1), current, baseline)


def test_album_patch_rejects_missing_import_anchor():
    previous, current, baseline, _ = baseline_and_patch()
    with pytest.raises(RuntimeError, match="insertion baseline mismatch"):
        patch_album_source(previous.replace("from bot.utils.message_utils import safe_edit_or_send", "# different live imports", 1), current, baseline)
