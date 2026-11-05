"""Pure tests for the versioned frontend release guard."""
import pytest

from scripts.deploy_servgo_frontend_atomic import release_name


def test_versioned_release_name_cannot_target_arbitrary_directory():
    assert release_name('a' * 40, '1761912000') == 'servgo-' + 'a' * 16 + '-1761912000'
    for commit,stamp in [
        ('main', '1761912000'),
        ('a'*40, '../root'),
        ('a'*40, '1; rm -rf /'),
        ('a'*39, '42'),
        ('a'*40, '2026-10-31'),
    ]:
        with pytest.raises(ValueError):
            release_name(commit, stamp)
