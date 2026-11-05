"""The production patch adds only the Yandex lines and survives production-only differences."""
import ast
import subprocess
from pathlib import Path

import pytest

from scripts.deploy_yandex_login import patch_main, patch_models, sql_statements

BASE_COMMIT = '1d0db2f51d3342c99fe587100de12f7c5d1232d8'  # main just before this feature (stays valid after merge)


def _origin(path):
    return subprocess.run(['git', 'show', f'{BASE_COMMIT}:{path}'], capture_output=True, text=True, check=True).stdout


def _now(path):
    return Path(path).read_text(encoding='utf-8')


def test_models_patch_adds_birthday_and_both_tables():
    repository = _now('database/models.py')
    patched, changed = patch_models(_origin('database/models.py'), repository)
    assert changed
    assert 'birthday: Mapped[Optional[str]]' in patched
    assert 'class YandexIdentity(Base):' in patched and 'class YandexTicket(Base):' in patched
    ast.parse(patched)


def test_main_patch_imports_and_includes_the_router():
    patched, changed = patch_main(_origin('web/api/main.py'))
    assert changed
    assert 'studio_schedule, yandex_auth' in patched
    assert patched.count('yandex_auth.router') == 1
    ast.parse(patched)


def test_patches_are_idempotent_and_keep_production_only_code():
    repository = _now('database/models.py')
    models = _origin('database/models.py') + '\n\nPRODUCTION_ONLY = "keep"\n'
    patched, _ = patch_models(models, repository)
    assert patched.endswith('PRODUCTION_ONLY = "keep"\n')
    again, changed = patch_models(patched, repository)
    assert not changed and again == patched
    main = _origin('web/api/main.py') + '\n\nPRODUCTION_ONLY = "keep"\n'
    patched, _ = patch_main(main)
    assert patched.endswith('PRODUCTION_ONLY = "keep"\n')
    again, changed = patch_main(patched)
    assert not changed and again == patched


def test_unexpected_production_structure_is_refused():
    with pytest.raises(RuntimeError):
        patch_models('class Other:\n    pass\n', _now('database/models.py'))
    with pytest.raises(RuntimeError):
        patch_main('app = None\n')


def test_migration_sql_is_additive_and_has_no_comments_or_destructive_statements():
    sql = sql_statements('migrations/add_yandex_auth.sql')
    assert '--' not in sql
    upper = sql.upper()
    for forbidden in ('DROP ', 'DELETE FROM', 'TRUNCATE', 'UPDATE '):
        assert forbidden not in upper
    assert 'IF NOT EXISTS' in upper


def test_workflow_runs_only_on_manual_dispatch_and_never_echoes_the_secret():
    text = Path('.github/workflows/servgo-yandex-login-deploy.yml').read_text(encoding='utf-8')
    assert 'workflow_dispatch' in text and 'push:' not in text and 'pull_request' not in text
    assert 'secrets.YANDEX_CLIENT_SECRET' in text
    assert 'echo' not in text


def test_deploy_script_never_prints_the_secret():
    text = Path('scripts/deploy_yandex_login.py').read_text(encoding='utf-8')
    for line in text.splitlines():
        if 'print(' in line:
            assert 'secret)' not in line and ', secret' not in line and '+ secret' not in line


def test_whole_file_replacements_match_the_main_baseline():
    import hashlib
    from scripts.deploy_yandex_login import REPLACED, replacement_needed
    for remote, (local, baseline) in REPLACED.items():
        before = subprocess.run(['git', 'show', f'{BASE_COMMIT}:{local}'], capture_output=True, check=True).stdout
        assert hashlib.sha256(before).hexdigest() == baseline, local
        now = Path(local).read_bytes()
        assert replacement_needed(before, now, baseline) is True
        assert replacement_needed(now, now, baseline) is False
        with pytest.raises(RuntimeError):
            replacement_needed(before + b'\n# hotfix\n', now, baseline)
        with pytest.raises(RuntimeError):
            replacement_needed(None, now, baseline)
