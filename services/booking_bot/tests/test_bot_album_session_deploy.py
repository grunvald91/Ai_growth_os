"""Scoped, fail-closed bot album session deployment tests."""
import ast
import hashlib
from pathlib import Path

import pytest

from scripts.deploy_bot_album_session_fix import patch_session_source
from scripts.deploy_bot_room_photo_fix import nodes

OLD = '''"""Production-like bot module."""
import asyncio
_room_photo_album_pending = {}


async def _send_room_photo_album_summary(key):
    return key

async def untouched():
    return "leave intact"

async def edit_emp_photos_menu(callback, state):
    return callback

async def emp_add_photo_start(callback, state):
    return state

async def emp_add_photo_receive(message, state):
    return message

async def emp_photos_done(callback, state):
    return callback

async def emp_photos_back(callback, state):
    return state
'''
NAMES = ("edit_emp_photos_menu", "emp_add_photo_start", "emp_add_photo_receive",
         "emp_photos_done", "emp_photos_back")


def setup_patch(source=OLD):
    baseline = {name: hashlib.sha256(ast.get_source_segment(OLD, nodes(OLD)[name]).encode()).hexdigest()
                for name in NAMES}
    latest = Path("bot/handlers/admin.py").read_text(encoding="utf-8")
    return latest, baseline, patch_session_source(source, latest,
                                                  expected_sha=hashlib.sha256(source.encode()).hexdigest(),
                                                  baseline=baseline)


def test_session_patch_only_changes_photo_functions_and_capture_helper():
    latest, baseline, patched = setup_patch()
    before, after, desired = nodes(OLD), nodes(patched), nodes(latest)
    for name in NAMES:
        assert ast.get_source_segment(patched, after[name]) == ast.get_source_segment(latest, desired[name])
    assert ast.get_source_segment(OLD, before["untouched"]) == ast.get_source_segment(patched, after["untouched"])
    assert patched.count("def _advance_room_photo_capture") == 1
    assert patched.count("_room_photo_capture_sessions = {}") == 1
    assert "async def _send_room_photo_album_summary" in patched
    ast.parse(patched)


def test_session_patch_rejects_unexpected_live_file_or_handler():
    latest, baseline, _ = setup_patch()
    with pytest.raises(RuntimeError, match="SHA mismatch"):
        patch_session_source(OLD + "# other production change", latest, baseline=baseline)
    changed = OLD.replace("return message", "return None")
    with pytest.raises(RuntimeError, match="function mismatch"):
        patch_session_source(changed, latest, expected_sha=hashlib.sha256(changed.encode()).hexdigest(),
                             baseline=baseline)
