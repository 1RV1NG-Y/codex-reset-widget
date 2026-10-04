from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from codex_widget.claude_client import ClaudeClient, ClaudeClientError, USAGE_URL, detect_claude


def response(data):
    return io.BytesIO(json.dumps(data).encode())


class ClaudeClientTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / ".credentials.json"
        self.path.write_text(json.dumps({
            "otherCredential": {"preserve": True},
            "claudeAiOauth": {
                "accessToken": "test-access", "refreshToken": "test-refresh",
                "expiresAt": 9_999_999_999_999, "subscriptionType": "pro",
            },
        }))
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.data = {
            "five_hour": {"utilization": 12.5, "resets_at": "2026-10-04T23:00:00Z"},
            "seven_day": {"utilization": 42, "resets_at": "2026-10-09T22:00:00+00:00"},
        }

    def client(self, opener):
        return ClaudeClient(credentials_path=self.path, opener=opener)

    def test_parses_separate_windows_without_inventing_reset_credits(self):
        usage = ClaudeClient._parse_snapshot(self.data)
        self.assertEqual(usage.used_percent, 42)
        self.assertEqual(usage.five_hour_used_percent, 12.5)
        self.assertEqual(usage.five_hour_reset_at, datetime(2026, 10, 4, 23, tzinfo=UTC))
        self.assertEqual(usage.five_hour_window_minutes, 300)
        self.assertIsNone(usage.banked_resets)

    def test_null_windows_and_invalid_values_are_not_zero_usage(self):
        usage = ClaudeClient._parse_snapshot({"five_hour": None, "seven_day": None})
        self.assertIsNone(usage.used_percent)
        self.assertIsNone(usage.five_hour_used_percent)
        for value in (True, "0", float("nan"), float("inf"), -1, 101):
            with self.subTest(value=value):
                usage = ClaudeClient._parse_snapshot({"five_hour": {"utilization": value}})
                self.assertIsNone(usage.five_hour_used_percent)

    def test_rejects_malformed_response(self):
        for data in ({}, {"error": "failure"}, {"five_hour": [1]}):
            with self.subTest(data=data), self.assertRaises(ClaudeClientError):
                ClaudeClient._parse_snapshot(data)

    def test_reads_usage_without_persisting_tokens_and_caches_normal_refresh(self):
        opener = Mock(side_effect=lambda *_a, **_kw: response(self.data))
        client = self.client(opener)
        before = self.path.read_text()
        first = client.read_rate_limits()
        self.assertIs(first, client.read_rate_limits())
        self.assertEqual(opener.call_count, 1)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, USAGE_URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-access")
        self.assertEqual(self.path.read_text(), before)
        client.read_rate_limits(force=True)
        self.assertEqual(opener.call_count, 2)

    def test_refreshes_expired_credentials_and_preserves_other_fields(self):
        document = json.loads(self.path.read_text())
        document["claudeAiOauth"]["expiresAt"] = 0
        self.path.write_text(json.dumps(document))
        opener = Mock(side_effect=[
            response({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}),
            response(self.data),
        ])
        self.client(opener).read_rate_limits()
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["claudeAiOauth"]["accessToken"], "new-access")
        self.assertEqual(saved["claudeAiOauth"]["subscriptionType"], "pro")
        self.assertEqual(saved["otherCredential"], {"preserve": True})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(opener.call_args.args[0].get_header("Authorization"), "Bearer new-access")

    def test_unauthorized_usage_refreshes_once(self):
        opener = Mock(side_effect=[
            HTTPError(USAGE_URL, 401, "Unauthorized", {}, None),
            response({"access_token": "new-access", "expires_in": 3600}),
            response(self.data),
        ])
        self.assertEqual(self.client(opener).read_rate_limits().used_percent, 42)
        self.assertEqual(opener.call_count, 3)

    def test_failed_login_never_leaks_token_error_bodies(self):
        opener = Mock(side_effect=[
            HTTPError(USAGE_URL, 401, "test-access", {}, None),
            HTTPError(USAGE_URL, 400, "test-refresh", {}, None),
        ])
        with self.assertRaisesRegex(ClaudeClientError, "claude auth login") as caught:
            self.client(opener).read_rate_limits()
        self.assertNotIn("test-access", str(caught.exception))
        self.assertNotIn("test-refresh", str(caught.exception))

    def test_rate_limit_blocks_even_forced_verification_reads(self):
        opener = Mock(side_effect=HTTPError(USAGE_URL, 429, "slow down", {}, None))
        client = self.client(opener)
        for force in (False, False, True):
            with self.assertRaisesRegex(ClaudeClientError, "rate limited"):
                client.read_rate_limits(force=force)
        self.assertEqual(opener.call_count, 1)

    def test_network_errors_are_actionable(self):
        opener = Mock(side_effect=URLError("network down"))
        with self.assertRaisesRegex(ClaudeClientError, "connection"):
            self.client(opener).read_rate_limits()

    def test_missing_credentials_requests_login(self):
        self.path.unlink()
        with self.assertRaisesRegex(ClaudeClientError, "claude auth login"):
            self.client(Mock()).read_rate_limits()

    def test_activation_isolated_from_api_billing_and_local_tools(self):
        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "CLAUDE_CODE_USE_VERTEX": "1"}),
            patch("codex_widget.claude_client.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run,
        ):
            self.client(Mock()).activate_five_hour_window()
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["claude", "--print"])
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", args)
        self.assertNotIn("ANTHROPIC_API_KEY", run.call_args.kwargs["env"])
        self.assertNotIn("CLAUDE_CODE_USE_VERTEX", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_installed_claude_detected_without_running_inference(self):
        with patch("codex_widget.claude_client.shutil.which", return_value="/test/claude"):
            self.assertEqual(detect_claude(), "/test/claude")

    def test_detects_native_install_outside_service_path_and_rejects_missing_cli(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("codex_widget.claude_client.shutil.which", return_value=None),
            patch("codex_widget.claude_client.Path.home", return_value=Path(directory)),
        ):
            self.assertIsNone(detect_claude())
            native = Path(directory) / ".local" / "bin" / "claude"
            native.parent.mkdir(parents=True)
            native.touch()
            self.assertIsNone(detect_claude())
            native.chmod(0o755)
            self.assertEqual(detect_claude(), str(native))


if __name__ == "__main__":
    unittest.main()
