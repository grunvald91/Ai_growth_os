"""The SMTP rollout replaces only the reviewed baseline of one file."""
import hashlib
import subprocess

from scripts.deploy_smtp_loopback_fix import BASELINE, PATH


def test_baseline_matches_main_before_the_fix():
    shown = subprocess.run(['git', 'show', '68e418c:' + PATH], capture_output=True, check=True).stdout
    assert hashlib.sha256(shown).hexdigest() == BASELINE
