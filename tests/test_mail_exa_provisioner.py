"""Offline tests; no real API credential, network or service operations."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from deploy.ops import provision_mail_exa as p


KEY = "fixture-exa-key-not-real"


def test_optional_key_noop(monkeypatch):
    validate = Mock(side_effect=AssertionError("network must not run"))
    monkeypatch.setattr(p, "validate_exa", validate)
    assert p.provision("", apply=True) == "EXA_NOT_CONFIGURED_NO_CHANGES"
    validate.assert_not_called()


@pytest.mark.parametrize("bad", ["short", "abc\ninjected=1", "a" * 513, "abc;defghijkl"])
def test_invalid_key_no_changes(monkeypatch, bad):
    validate = Mock(side_effect=AssertionError("network must not run"))
    monkeypatch.setattr(p, "validate_exa", validate)
    with pytest.raises(RuntimeError, match="EXA_KEY_FORMAT_INVALID"):
        p.provision(bad, apply=True)
    validate.assert_not_called()


def test_default_validates_only(monkeypatch):
    validate = Mock()
    monkeypatch.setattr(p, "validate_exa", validate)
    monkeypatch.setattr(p, "system", Mock(side_effect=AssertionError("service must not change")))
    assert p.provision(KEY) == "EXA_VALIDATED_NO_CHANGES"
    validate.assert_called_once_with(KEY)


@pytest.fixture
def local(tmp_path, monkeypatch):
    secret = tmp_path / "env" / "mail-exa.env"
    drop = tmp_path / "drop" / "90-servgo-mail-exa.conf"
    monkeypatch.setattr(p, "SECRET", secret)
    monkeypatch.setattr(p, "DROPIN", drop)
    monkeypatch.setattr(p, "validate_exa", Mock())
    monkeypatch.setattr(p.os, "geteuid", lambda: 0)
    monkeypatch.setattr(p, "secure_directory", lambda d: d.mkdir(exist_ok=True))
    def snapshot(path):
        return (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
    monkeypatch.setattr(p, "snapshot", snapshot)
    monkeypatch.setattr(p, "open", lambda *a: (tmp_path / "lock").open("a"), raising=False)
    calls = []
    def system(*args):
        calls.append(args)
        return "active" if args[0] == "is-active" else ""
    monkeypatch.setattr(p, "system", system)
    monkeypatch.setattr(p, "healthy", lambda: True)
    monkeypatch.setattr(p, "runtime_matches", lambda key: True)
    monkeypatch.setattr(p, "wait_for", lambda predicate: predicate())
    return secret, drop, calls


def test_install_and_repeat(local):
    secret, drop, calls = local
    assert p.provision(KEY, apply=True) == "EXA_ACTIVE_API_HEALTHY"
    assert secret.read_text() == "EXA_API_KEY=" + KEY + "\n"
    assert secret.stat().st_mode & 0o777 == 0o600
    assert drop.read_text() == "[Service]\nEnvironmentFile=" + str(secret) + "\n"
    assert p.provision(KEY, apply=True) == "EXA_ALREADY_ACTIVE"
    assert calls.count(("restart", p.SERVICE)) == 1


def test_rollback_preserves_previous_config(local, monkeypatch):
    secret, drop, calls = local
    secret.parent.mkdir()
    drop.parent.mkdir()
    p.atomic_write(secret, b"EXA_API_KEY=old-fixture\n", 0o600)
    p.atomic_write(drop, b"[Service]\n# old fixture\n", 0o644)
    monkeypatch.setattr(p, "runtime_matches", lambda key: False)
    with pytest.raises(RuntimeError, match="EXA_ROLLED_BACK_API_HEALTHY"):
        p.provision(KEY, apply=True)
    assert secret.read_bytes() == b"EXA_API_KEY=old-fixture\n"
    assert drop.read_bytes() == b"[Service]\n# old fixture\n"
    assert calls.count(("restart", p.SERVICE)) == 2


def test_rollback_removes_new_files(local, monkeypatch):
    secret, drop, _ = local
    monkeypatch.setattr(p, "runtime_matches", lambda key: False)
    with pytest.raises(RuntimeError, match="EXA_ROLLED_BACK_API_HEALTHY"):
        p.provision(KEY, apply=True)
    assert not secret.exists()
    assert not drop.exists()
