"""Deterministic offline checks for the independent ServGo SOCKS5 monitor."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/socks5_telegram_health.py"
spec = importlib.util.spec_from_file_location("socks5_telegram_health", SCRIPT)
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


def sample(ok=True, code="302", exit_code=0):
    return {"ok": ok, "http_code": code, "curl_exit": exit_code,
            "duration_ms": 325}


class TestSockHealth(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "status.json"
        self.stdout = io.StringIO()

    def tearDown(self):
        self.dir.cleanup()

    def check(self, probes, now="2026-11-20T20:40:00+00:00"):
        iterator = iter(probes)
        with redirect_stdout(self.stdout):
            return health.run_check(
                probe=lambda: next(iterator), sleep=lambda seconds: None,
                path=self.path, now=lambda: now,
                diagnose_failure=lambda last: {
                    "reason": "telegram_socks_timeout",
                    "local_socks_listener": True,
                    "tunnel_service_active": True,
                    "relay_ssh_tcp": True})

    def test_immediate_success_requires_real_http_302(self):
        result = self.check([sample()])
        self.assertEqual((result["state"], result["attempts"]), ("up", 1))
        self.assertEqual(result["consecutive_failures"], 0)
        self.assertEqual(result["reason"], "ok")
        self.assertEqual(health.load_previous(self.path)["state"], "up")

    def test_retry_recovers_transient_network_hiccup(self):
        result = self.check([sample(False, "000", 28), sample()])
        self.assertEqual((result["state"], result["attempts"]), ("up", 2))
        self.assertEqual(result["consecutive_failures"], 0)

    def test_two_bad_ticks_mark_down_and_success_recovers(self):
        self.check([sample(False, "000", 28), sample(False, "000", 28)])
        self.assertEqual(health.load_previous(self.path)["state"], "suspect")
        result = self.check([sample(False, "000", 28), sample(False, "000", 28)],
                            now="2026-11-20T20:41:00+00:00")
        self.assertEqual(result["state"], "down")
        self.assertEqual(result["consecutive_failures"], 2)
        self.assertEqual(result["first_failure_at"], "2026-11-20T20:40:00+00:00")
        self.assertEqual(self.stdout.getvalue().count("SOCKS5_TELEGRAM_OUTAGE"), 1)
        result = self.check([sample()], now="2026-11-20T20:42:00+00:00")
        self.assertEqual(result["state"], "up")
        self.assertIsNone(result["first_failure_at"])
        self.assertIn("SOCKS5_TELEGRAM_RECOVERED", self.stdout.getvalue())

    def test_invalid_state_does_not_make_monitor_crash(self):
        self.path.write_text('{"consecutive_failures": "corrupt", "state":"down"}')
        result = self.check([sample(False, "000", 28), sample(False, "000", 28)])
        self.assertEqual(result["consecutive_failures"], 1)
        self.assertEqual(result["state"], "suspect")

    def test_stateless_actions_probe_never_writes_status_file(self):
        with redirect_stdout(self.stdout):
            rc = health.stateless_check(
                probe=lambda: sample(), sleep=lambda x: None)
        self.assertEqual(rc, 0)
        self.assertFalse(self.path.exists())

    def test_stateless_probe_returns_nonzero_for_unavailable_telegram(self):
        with redirect_stdout(self.stdout):
            rc = health.stateless_check(
                probe=lambda: sample(False, "000", 28), sleep=lambda x: None,
                diagnose_failure=lambda last: {"reason": "telegram_socks_timeout"})
        self.assertEqual(rc, 1)
        self.assertIn("SOCKS5_TELEGRAM_STATELESS", self.stdout.getvalue())

    def test_curl_must_use_remote_dns_verified_https_and_no_token(self):
        mock_result = subprocess.CompletedProcess(args=[], returncode=0,
                                                  stdout="302 0.311", stderr="")
        with patch.object(health.subprocess, "run", return_value=mock_result) as runner:
            sample_result = health.curl_probe()
        self.assertTrue(sample_result["ok"])
        argv = runner.call_args.args[0]
        self.assertEqual(argv[:2], ["curl", "-q"])
        self.assertEqual(argv[argv.index("--proxy") + 1], "socks5h://127.0.0.1:1089")
        self.assertEqual(argv[argv.index("--noproxy") + 1], "")
        self.assertEqual(argv[-1], "https://api.telegram.org/")
        self.assertNotIn("getMe", " ".join(argv))
        self.assertEqual(sample_result["duration_ms"], 311)

    def test_non_302_or_curl_error_must_fail(self):
        for code, exit_code in (("200", 0), ("000", 28), ("403", 0)):
            with self.subTest(code=code, exit_code=exit_code):
                value = subprocess.CompletedProcess(args=[], returncode=exit_code,
                                                    stdout=code + " 0.1", stderr="private")
                with patch.object(health.subprocess, "run", return_value=value):
                    response = health.curl_probe()
                self.assertFalse(response["ok"])
                self.assertNotIn("stderr", response)

    def test_diagnostics_do_not_start_or_restart_services(self):
        with patch.object(health, "reachable", side_effect=[True, True]), \
             patch.object(health, "service_active", return_value=True):
            diag = health.diagnose(sample(False, "000", 28))
        self.assertEqual(diag["reason"], "telegram_socks_timeout")
        self.assertTrue(diag["relay_ssh_tcp"])


if __name__ == "__main__":
    unittest.main()
