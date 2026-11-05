"""Guarantee the live room-photo patch cannot replace unrelated bot code."""
import ast
import hashlib

import pytest

from scripts.deploy_bot_room_photo_fix import OLD_IMPORT, VIEW_MARKER, patch_source


OLD = OLD_IMPORT + "\n" + """
PRODUCTION_ONLY = 'keep this setting'
async def edit_emp_photos_menu():
    pass
async def emp_add_photo_start():
    pass
async def emp_add_photo_receive():
    pass
async def emp_photos_done():
    pass
""" + VIEW_MARKER + "\nasync def emp_view_photos():\n    pass\n"
NEW = OLD.replace("    pass\n", "    return 42\n", 4).replace(
    VIEW_MARKER,
    '@router.callback_query(F.data.startswith("emp_photos_back:"))\n'
    'async def emp_photos_back():\n    return None\n\n' + VIEW_MARKER,
)


def _baseline(source):
    names = ("edit_emp_photos_menu", "emp_add_photo_start", "emp_add_photo_receive", "emp_photos_done")
    tree = ast.parse(source)
    return {
        name: hashlib.sha256(ast.get_source_segment(source, next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)).encode()).hexdigest()
        for name in names
    }


def test_scoped_room_patch_preserves_unrelated_production_content():
    result = patch_source(OLD, NEW, baseline=_baseline(OLD))
    assert "PRODUCTION_ONLY = 'keep this setting'" in result
    assert "async def emp_photos_back():" in result
    assert "    return 42" in result
    assert "InlineKeyboardMarkup, InlineKeyboardButton" in result
    ast.parse(result)


def test_scoped_room_patch_rejects_changed_production_handler():
    changed = OLD.replace("async def emp_add_photo_receive():\n    pass", "async def emp_add_photo_receive():\n    return 7")
    with pytest.raises(RuntimeError, match="baseline mismatch"):
        patch_source(changed, NEW, baseline=_baseline(OLD))
