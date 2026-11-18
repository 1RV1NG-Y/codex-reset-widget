from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from codex_widget.app import CodexWidgetApplication
from codex_widget.models import AppState, ResetEvent, UsageSnapshot, utc_now
from codex_widget.panel_service import PanelService, build_state
from codex_widget.state import StateStore


class PanelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with patch("codex_widget.app.detect_claude", return_value="/test/claude"):
            self.app = CodexWidgetApplication()
        self.app.state_store = StateStore(Path(directory.name) / "state.json")
        self.app.claude_keeper.state_store = StateStore(Path(directory.name) / "claude-state.json")
        self.app.selected_provider = "codex"
        self.app.show_widget = Mock()
        self.app._run_async = Mock()
        self.usage = UsageSnapshot(
            used_percent=71, reset_at=utc_now() + timedelta(days=1),
            window_minutes=10080, banked_resets=3,
            five_hour_used_percent=8, five_hour_reset_at=utc_now() + timedelta(hours=4),
            five_hour_window_minutes=300,
        )

    def test_state_snapshot_covers_both_providers_from_cache(self):
        event = ResetEvent("e1", utc_now() - timedelta(days=2), "Reset", "https://example.test/r")
        self.app.state_store.save(AppState(last_known_usage=self.usage, last_global_reset=event))
        self.app._usage_refreshing = True
        self.app._provider_errors["claude"] = "Sign in to Claude"

        state = json.loads(json.dumps(build_state(self.app)))

        self.assertEqual(state["selected"], "codex")
        self.assertTrue(state["claude_available"])
        codex = state["providers"]["codex"]
        self.assertTrue(codex["loading"])
        self.assertEqual(codex["usage"]["five_hour_percent"], 8)
        self.assertEqual(codex["usage"]["banked_resets"], 3)
        self.assertAlmostEqual(codex["usage"]["weekly_reset_at"], self.usage.reset_at.timestamp())
        self.assertEqual(codex["last_reset"]["url"], "https://example.test/r")
        self.assertFalse(codex["auto_roll"]["enabled"])
        claude = state["providers"]["claude"]
        self.assertIsNone(claude["usage"])
        self.assertEqual(claude["error"], "Sign in to Claude")
        self.app._run_async.assert_not_called()

    def test_snapshot_omits_claude_when_not_installed(self):
        self.app.claude_executable = None
        self.app.selected_provider = "claude"
        state = build_state(self.app)
        self.assertEqual(set(state["providers"]), {"codex"})
        self.assertEqual(state["selected"], "codex")

    def test_selecting_provider_from_panel_does_not_open_widget(self):
        self.app.window = None
        with patch("codex_widget.app.detect_claude", return_value="/test/claude"):
            self.app.select_provider("claude")
        self.assertEqual(self.app.selected_provider, "claude")
        self.assertEqual(self.app.state_store.load().selected_provider, "claude")
        self.app.show_widget.assert_not_called()

    def test_panel_refresh_skips_fresh_usage_unless_forced(self):
        self.app.state_store.save(AppState(last_known_usage=self.usage))
        self.app._poll_tick = Mock()
        self.app.panel_refresh("codex", force=False)
        self.app._run_async.assert_not_called()
        self.app.panel_refresh("codex", force=True)
        self.assertEqual(self.app._run_async.call_count, 1)
        self.assertTrue(self.app._usage_refreshing)
        self.app.panel_refresh("codex", force=True)
        self.assertEqual(self.app._run_async.call_count, 1)

    def test_refresh_result_reaches_panel_without_a_window(self):
        self.app.window = None
        self.app._usage_refreshing = True
        publish = Mock()
        self.app.panel.publish = publish
        self.app._usage_refresh_finished(None, RuntimeError("offline"))
        self.assertEqual(self.app._provider_errors["codex"], "offline")
        publish.assert_called()
        self.app._usage_refresh_finished(self.usage, None)
        self.assertIsNone(self.app._provider_errors["codex"])

    def test_auto_roll_targets_named_provider(self):
        self.app.window = None
        self.app.claude_keeper._start_window_keeper_watchdog = Mock()
        self.app.claude_keeper._refresh_window_keeper_schedule = Mock()
        self.app.set_provider_auto_roll("claude", True)
        self.assertTrue(self.app.claude_keeper.state_store.load().keep_five_hour_window_active)
        self.assertFalse(self.app.state_store.load().keep_five_hour_window_active)

    def test_attaching_panel_hides_tray_and_detaching_restores_it(self):
        attachments = []
        self.app.panel_attachment_changed = attachments.append
        service = PanelService(self.app)
        service._set_panel_owner(":1.42")
        self.assertTrue(service.attached)
        service._set_panel_owner(None)
        self.assertFalse(service.attached)
        self.assertEqual(attachments, [True, False])


if __name__ == "__main__":
    unittest.main()
