"""The master-cabinet rollout replaces only reviewed baselines and adds one new file."""
import hashlib
import subprocess

from scripts.deploy_business_creation_and_reminders import classify
from scripts.deploy_schedule_changes import BASELINE, MIGRATION, NEW_FILES


def test_baselines_match_main_before_the_change():
    for path, expected in BASELINE.items():
        shown = subprocess.run(['git', 'show', '3c26157:' + path], capture_output=True, check=True).stdout
        assert hashlib.sha256(shown).hexdigest() == expected, path


def test_only_reviewed_files_are_touched():
    assert set(BASELINE) == {'bot/handlers/admin.py', 'bot/keyboards/master_kb.py', 'web/api/routes/master.py', 'web/api/sales/policy.py', 'database/database.py', 'web/api/sales/provider.py', 'web/api/sales/stages.py', 'web/api/sales/engine.py', 'database/models.py', 'web/api/routes/admin.py', 'web/api/schemas.py', 'bot/handlers/master.py'}
    assert NEW_FILES == ['database/booking_changes.py']


def test_migration_is_additive_and_idempotent():
    assert 'ADD COLUMN IF NOT EXISTS finished_at' in MIGRATION
    assert 'DROP' not in MIGRATION.upper() and 'DELETE' not in MIGRATION.upper()
    assert 'UPDATE' not in MIGRATION.upper()


def test_unknown_production_file_is_refused():
    try:
        classify('other', 'baseline', 'new')
    except RuntimeError:
        return
    raise AssertionError('a file that is neither baseline nor new must stop the rollout')
