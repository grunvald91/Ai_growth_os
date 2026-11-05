"""Every bot callsite must match the real mode-switch keyboard signature."""
import ast
import inspect
from pathlib import Path

from bot.keyboards import client_kb


def test_mode_switch_callsite_keywords_bind_to_keyboard_signature():
    source = Path('bot/handlers/client.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    signature = inspect.signature(client_kb.get_mode_switch_keyboard)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'get_mode_switch_keyboard'
    ]
    assert calls, 'The booking mode-switch callsites must exist'
    for call in calls:
        signature.bind(**{kw.arg: False for kw in call.keywords})
