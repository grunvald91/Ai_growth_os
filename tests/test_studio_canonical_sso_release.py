"""Safety tests for the production Studio canonical-entry hot patch."""
import ast

import pytest

from scripts.deploy_studio_canonical_sso import patch, _canonical_constant
from pathlib import Path


BASE = '''CANONICAL_STUDIO_URL = "https://servgo.ru"
STUDIO_URL = "https://growth.servgo.ru"

def studio_public_url() -> str:
    """Existing signed ticket route."""
    enabled = os.getenv("SERVGO_GROWTH_GATEWAY_ENABLED", "").casefold() in {"1", "true", "yes"}
    return CANONICAL_STUDIO_URL if enabled else STUDIO_URL

def unrelated():
    return 42
'''

TARGET = '''CANONICAL_STUDIO_URL = "https://servgo.ru"

def studio_public_url() -> str:
    """Only the canonical ServGo PWA."""
    return CANONICAL_STUDIO_URL

def unrelated():
    return 42
'''


def test_target_source_always_uses_same_origin():
    source = (Path(__file__).resolve().parents[1] / "web/api/routes/studio_sso.py").read_text()
    assert _canonical_constant(source)
    tree = ast.parse(source)
    method = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "studio_public_url")
    assert len(method.body) == 2
    assert isinstance(method.body[-1], ast.Return)
    assert ast.unparse(method.body[-1].value) == "CANONICAL_STUDIO_URL"
    assert "SERVGO_GROWTH_GATEWAY_ENABLED" not in ast.get_source_segment(source, method)
    assert "https://servgo.ru/studio/sso" not in source  # constructed from constant, not hard-coded ticket


def test_reviewed_live_function_is_patched_without_touching_unrelated_code():
    revised, changed = patch(BASE, TARGET)
    assert changed
    assert 'def unrelated():\n    return 42' in revised
    assert ast.unparse(ast.parse(revised)).find("return CANONICAL_STUDIO_URL") >= 0
    assert patch(revised, TARGET) == (revised, False)


def test_refuse_unknown_live_routing_or_host():
    with pytest.raises(ValueError):
        patch(BASE.replace("return CANONICAL_STUDIO_URL if enabled else STUDIO_URL",
                           "return request.headers['Host']"), TARGET)
    with pytest.raises(ValueError):
        patch(BASE.replace('CANONICAL_STUDIO_URL = "https://servgo.ru"',
                           'CANONICAL_STUDIO_URL = "https://untrusted.invalid"'), TARGET)
    with pytest.raises(ValueError):
        patch(BASE, TARGET.replace('return CANONICAL_STUDIO_URL', 'return STUDIO_URL'))


def test_refuse_missing_or_multiple_origin_functions():
    with pytest.raises(ValueError):
        patch(BASE.replace('def studio_public_url()', 'def another_name()'), TARGET)
    with pytest.raises(ValueError):
        patch(BASE + '\ndef studio_public_url():\n    return STUDIO_URL\n', TARGET)
