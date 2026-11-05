"""Never bypass the full backend fingerprint check except for nonsemantic Studio SSO changes."""
from scripts.deploy_servgo_frontend_atomic import STUDIO_SSO, backend_equivalent


def test_identical_sources_are_accepted():
    assert backend_equivalent("web/api/routes/owner_mail.py", b"print(1)\n", b"print(1)\n")


def test_non_sso_files_must_remain_byte_identical():
    assert not backend_equivalent("web/api/routes/owner_mail.py", b"print(1)\n", b"print(1) # comment\n")
    assert not backend_equivalent("web/api/routes/mail_pwa.py", None, b"print(1)\n")


def test_sso_comments_and_whitespace_are_nonsemantic():
    a=b"def studio_role(x):\n    return x + 1\n"
    b=b"# reformat only\ndef studio_role(x):\n  return (x + 1)\n"
    assert backend_equivalent(STUDIO_SSO, a, b)


def test_any_sso_code_change_fails_closed():
    a=b"def studio_role(x):\n    return x + 1\n"
    assert not backend_equivalent(STUDIO_SSO, a, b"def studio_role(x):\n    return x + 2\n")
    assert not backend_equivalent(STUDIO_SSO, a, b"def studio_role(x):\n    return True\n")
    assert not backend_equivalent(STUDIO_SSO, a, b"def studio_role(x):\n    return x\n\ndef new_route(): pass\n")
    assert not backend_equivalent(STUDIO_SSO, None, a)


def test_runtime_ignored_sso_type_comments_are_allowed():
    a=b"def f(x): # type: (int) -> int\n    return x\n"
    b=b"def f(x): # type: (str) -> int\n    return x\n"
    assert backend_equivalent(STUDIO_SSO, a, b)


def test_unparseable_sso_cannot_pass_preflight():
    assert not backend_equivalent(STUDIO_SSO, b"def role(:\n", b"def role(): pass\n")
    assert not backend_equivalent(STUDIO_SSO, b"\xff", b"def role(): pass\n")


def test_exact_production_sso_two_declarations_relocated_only():
    main=b"""def _businesses_for_studio():
    return 5
def studio_businesses():
    return _businesses_for_studio()
@router.get('/internal/studio/businesses')
async def studio_accessible_businesses():
    return _businesses_for_studio()
def unrelated(x):
    return x + 1
"""
    live=b"""def studio_businesses():
    return _businesses_for_studio()
def unrelated(x):
    return x + 1
def _businesses_for_studio():
    return 5
@router.get('/internal/studio/businesses')
async def studio_accessible_businesses():
    return _businesses_for_studio()
"""
    assert backend_equivalent(STUDIO_SSO, live, main)
    assert not backend_equivalent(STUDIO_SSO, live.replace(b"return 5", b"return 6"), main)
    assert not backend_equivalent(STUDIO_SSO, live.replace(b"x + 1", b"x + 2"), main)
    assert not backend_equivalent(STUDIO_SSO, live.replace(b"@router.get('/internal/studio/businesses')", b"@router.get('/internal/studio/users')"), main)
    assert not backend_equivalent(STUDIO_SSO, live.replace(b"def unrelated(x):", b"def legacy_extra(x):"), main)
    assert not backend_equivalent(STUDIO_SSO, live.replace(
        b"def _businesses_for_studio():", b"def removed_helper():"
    ), main)


def test_studio_auth_smoke_goes_to_actual_api_not_spa_fallback():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "scripts/deploy_servgo_frontend_atomic.py").read_text()
    assert 'http://127.0.0.1:8000/internal/studio/businesses?user_id=0' in script
    assert '("servgo.ru", "/internal/studio/businesses' not in script
    assert 'studio_code != "403"' in script
