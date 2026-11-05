"""Production notify hotfix moves only the booking-created block."""
import subprocess
from pathlib import Path

import pytest

from scripts.deploy_booking_notify_fix import patch_admin_source


def _buggy_admin():
    return subprocess.run(['git', 'show', 'eb1608c:web/api/routes/admin.py'],
                          capture_output=True, text=True, check=True).stdout


def test_patch_turns_buggy_admin_into_reviewed_fix():
    patched, changed = patch_admin_source(_buggy_admin())
    assert changed
    fixed = Path('web/api/routes/admin.py').read_text(encoding='utf-8')
    later = subprocess.run(['git', 'show', 'c7f8dcf~1:web/api/routes/admin.py'],
                           capture_output=True, text=True, check=True).stdout
    expected, _ = patch_admin_source(later)
    # Same result as the reviewed fix, apart from its list() wrapper.
    assert expected.replace('    bookings = result.scalars().all()\n\n    try:',
                            '    bookings = list(result.scalars().all())\n\n    try:') == fixed


def test_patch_is_idempotent_and_preserves_production_only_code():
    source = _buggy_admin() + '\n\nasync def production_only():\n    return "keep"\n'
    patched, _ = patch_admin_source(source)
    assert patched.endswith('async def production_only():\n    return "keep"\n')
    again, changed = patch_admin_source(patched)
    assert not changed and again == patched


def test_patch_refuses_unexpected_structure():
    source = Path('web/api/routes/admin.py').read_text(encoding='utf-8')
    broken = source.replace('async def create_manual_booking', 'async def create_manual_booking_v2')
    with pytest.raises(RuntimeError):
        patch_admin_source(broken)
