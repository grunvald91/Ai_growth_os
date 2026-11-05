"""The production patch adds only the studio lines and survives production-only differences."""
import ast
import subprocess
from pathlib import Path

import pytest

from scripts.deploy_studio_backend import patch_main, patch_models, patch_schemas, sql_statements

FEATURE = 'debaecf'


def _before(path, commit=FEATURE + '~1'):
    return subprocess.run(['git', 'show', f'{commit}:{path}'], capture_output=True, text=True, check=True).stdout


def _now(path):
    return Path(path).read_text(encoding='utf-8')


def test_models_patch_gives_the_reviewed_file():
    patched, changed = patch_models(_before('database/models.py'))
    # Compare with the file as of the feature commit; later features (e.g. Yandex login) edit models.py again.
    assert changed and patched == _before('database/models.py', FEATURE)


def test_schemas_patch_gives_the_reviewed_file():
    patched, changed = patch_schemas(_before('web/api/schemas.py'))
    assert changed and patched == _now('web/api/schemas.py')


def test_main_patch_gives_the_reviewed_file():
    # Today's main.py also carries the schedule router and the mail console; start from the file just before both.
    patched, changed = patch_main(_before('web/api/main.py'))
    assert changed
    assert 'studio_sso, studio_schedule' in patched
    assert patched.count('include_router(studio_') == 2
    assert 'studio_sso.router' in patched and 'studio_schedule.router' in patched


@pytest.mark.parametrize('func,path', [(patch_models, 'database/models.py'), (patch_schemas, 'web/api/schemas.py'),
                                       (patch_main, 'web/api/main.py')])
def test_patch_is_idempotent_and_keeps_production_only_code(func, path):
    source = _before(path) + '\n\nPRODUCTION_ONLY = "keep"\n'
    patched, _ = func(source)
    assert patched.endswith('PRODUCTION_ONLY = "keep"\n')
    again, changed = func(patched)
    assert not changed and again == patched
    ast.parse(patched)


def test_models_patch_survives_a_production_only_column_next_to_the_anchor():
    source = _before('database/models.py').replace(
        '    legal_documents_enabled: Mapped[bool] = mapped_column(Boolean, default=False)\n',
        '    legal_documents_enabled: Mapped[bool] = mapped_column(Boolean, default=False)\n'
        '    hotfix_only: Mapped[int] = mapped_column(Integer, default=0)\n')
    patched, changed = patch_models(source)
    assert changed and 'hotfix_only' in patched and 'studio_admin_access' in patched


def test_models_patch_without_the_usual_anchor_uses_the_last_field():
    source = _before('database/models.py').replace('legal_documents_enabled', 'legal_docs_enabled')
    patched, changed = patch_models(source)
    assert changed and 'studio_admin_access' in patched


def test_refuses_unexpected_structure():
    with pytest.raises(RuntimeError):
        patch_models('class Other:\n    pass\n')
    with pytest.raises(RuntimeError):
        patch_schemas('class BusinessUpdateIn:\n    pass\n')
    with pytest.raises(RuntimeError):
        patch_main('from .routes import auth\napp = None\n')


def test_main_refuses_a_half_patched_file():
    half = _before('web/api/main.py').replace('telegram_link\n', 'telegram_link, studio_sso\n')
    with pytest.raises(RuntimeError):
        patch_main(half)


def test_migration_has_no_comment_lines_and_is_additive():
    sql = sql_statements('migrations/add_studio_admin_access.sql')
    assert '--' not in sql
    assert 'ADD COLUMN IF NOT EXISTS studio_admin_access' in sql
    assert 'DROP' not in sql.upper()
