"""Scoped production rollout must only touch the reviewed baseline files."""
import hashlib
import subprocess

import pytest

from scripts.deploy_business_creation_and_reminders import BASELINE, classify


def test_baseline_hashes_match_main_before_the_change():
    for path, expected in BASELINE.items():
        shown = subprocess.run(['git', 'show', 'e91df5c:' + path], capture_output=True, check=True).stdout
        assert hashlib.sha256(shown).hexdigest() == expected, path


def test_classify_deploys_only_baseline_and_skips_already_new():
    assert classify('old', 'old', 'new') == 'deploy'
    assert classify('new', 'old', 'new') == 'skip'


def test_classify_refuses_a_diverged_production_file():
    with pytest.raises(RuntimeError):
        classify('hotfix-on-server', 'old', 'new')
