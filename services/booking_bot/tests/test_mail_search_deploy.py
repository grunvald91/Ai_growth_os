"""The mail rollout replaces a file only when the server holds exactly the version it was built on."""
import ast
import hashlib
from pathlib import Path

from scripts import deploy_mail_search as d


def h(b):
    return hashlib.sha256(b).hexdigest()


def test_replaces_the_expected_old_version():
    assert d.decide(b"old", h(b"old"), b"new") == "copy"


def test_refuses_a_file_changed_on_the_server():
    assert d.decide(b"somebody edited", h(b"old"), b"new") == "refuse"


def test_already_new_is_left_alone_and_rerun_is_safe():
    assert d.decide(b"new", h(b"old"), b"new") == "same"


def test_new_file_is_created_only_when_absent():
    assert d.decide(None, None, b"new") == "copy"
    assert d.decide(b"unknown", None, b"new") == "refuse"
    assert d.decide(None, h(b"old"), b"new") == "refuse"


def test_units_are_found_in_cgroup_text():
    text = "0::/system.slice/booking-web-api.service\n1:name=x:/system.slice/servgo-mail-worker.service"
    assert d.units_from_cgroup(text) == {"booking-web-api.service", "servgo-mail-worker.service"}


def test_every_file_of_the_rollout_exists_and_compiles():
    for rel in d.EXPECTED_OLD:
        source = Path(rel).read_text(encoding="utf-8")
        if rel.endswith(".py"):
            ast.parse(source)
        assert source


def test_discovery_script_is_valid_python():
    ast.parse(d.DISCOVER % (d.SETTINGS_TO_REPORT,))
