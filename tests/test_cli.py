from __future__ import annotations

import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codex_widget.cli import _prefer_x11_backend, _start_installed_service


class BackendSelectionTests(unittest.TestCase):
    def test_x11_is_preferred_when_xwayland_is_available(self):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            _prefer_x11_backend()
            self.assertEqual(os.environ["GDK_BACKEND"], "x11")

    def test_explicit_backend_is_preserved(self):
        with patch.dict(
            os.environ,
            {"DISPLAY": ":0", "GDK_BACKEND": "wayland"},
            clear=True,
        ):
            _prefer_x11_backend()
            self.assertEqual(os.environ["GDK_BACKEND"], "wayland")

    def test_backend_is_unchanged_without_xwayland(self):
        with patch.dict(os.environ, {}, clear=True):
            _prefer_x11_backend()
            self.assertNotIn("GDK_BACKEND", os.environ)


class ServiceLaunchTests(unittest.TestCase):
    def test_waits_for_enabled_service_before_launch(self):
        with patch.dict(os.environ, {}, clear=True), patch("codex_widget.cli.subprocess.run") as run:
            run.side_effect = [SimpleNamespace(returncode=0, stdout="enabled\n"), SimpleNamespace(returncode=0)]
            _start_installed_service([])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0], ["systemctl", "--user", "start", "codex-widget.service"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_daemon_quit_help_unknown_and_development_launch_do_not_start_service(self):
        with patch.dict(os.environ, {}, clear=True), patch("codex_widget.cli.subprocess.run") as run:
            for args in (["--daemon"], ["--quit"], ["--help"], ["--unknown"]):
                _start_installed_service(args)
            with patch.dict(os.environ, {"CODEX_WIDGET_NO_SERVICE": "1"}):
                _start_installed_service([])
        run.assert_not_called()

    def test_disabled_missing_or_masked_service_keeps_direct_launch(self):
        for state in ("disabled", "masked", "", "not-found"):
            with self.subTest(state=state), patch.dict(os.environ, {}, clear=True), patch("codex_widget.cli.subprocess.run") as run:
                run.return_value = SimpleNamespace(returncode=0, stdout=state)
                _start_installed_service([])
                self.assertEqual(run.call_count, 1)

    def test_missing_systemd_or_timeout_does_not_prevent_direct_launch(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("systemctl", 3)):
            with patch.dict(os.environ, {}, clear=True), patch("codex_widget.cli.subprocess.run", side_effect=error), patch("sys.stderr"):
                _start_installed_service([])


if __name__ == "__main__":
    unittest.main()
