from __future__ import annotations

import json
import os
import platform
import selectors
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import UsageSnapshot
from . import __version__


class CodexClientError(RuntimeError):
    pass


def _epoch_timestamp(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _resolve_executable(executable: str) -> str:
    """Use the npm package's native CLI without depending on its Node launcher."""
    installed = shutil.which(executable)
    if installed is None or platform.system() != "Linux":
        return executable
    launcher = Path(installed).resolve()
    package = launcher.parent.parent
    if (
        launcher.name != "codex.js"
        or launcher.parent.name != "bin"
        or package.name != "codex"
        or package.parent.name != "@openai"
    ):
        return executable

    architecture = {
        "x86_64": ("x64", "x86_64-unknown-linux-musl"),
        "aarch64": ("arm64", "aarch64-unknown-linux-musl"),
    }.get(platform.machine())
    if architecture is None:
        return executable
    suffix, target = architecture
    vendor_roots = (
        package / "node_modules" / "@openai" / f"codex-linux-{suffix}" / "vendor",
        package.parent / f"codex-linux-{suffix}" / "vendor",
        package / "vendor",
    )
    for root in vendor_roots:
        for directory in ("bin", "codex"):
            native = root / target / directory / "codex"
            if native.is_file() and os.access(native, os.X_OK):
                return str(native)
    return executable


class CodexClient:
    def __init__(
        self,
        executable: str = "codex",
        *,
        timeout: float = 10.0,
        activation_timeout: float = 60.0,
        activation_model: str = "gpt-5.6-luna",
    ) -> None:
        self.executable = _resolve_executable(executable)
        self.timeout = timeout
        self.activation_timeout = activation_timeout
        self.activation_model = activation_model

    def read_rate_limits(self) -> UsageSnapshot:
        stderr = tempfile.TemporaryFile()
        try:
            process = subprocess.Popen(
                [self.executable, "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            stderr.close()
            raise CodexClientError(f"cannot start Codex app-server: {exc}") from exc

        try:
            self._send(
                process,
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "codex-widget",
                            "title": "Codex Widget",
                            "version": __version__,
                        }
                    },
                },
            )
            deadline = time.monotonic() + self.timeout
            self._wait_for_response(process, 1, deadline)
            self._send(process, {"method": "initialized"})
            self._send(process, {"method": "account/rateLimits/read", "id": 2})
            response = self._wait_for_response(process, 2, deadline)
            result = response.get("result")
            if not isinstance(result, dict):
                raise CodexClientError("Codex returned no rate-limit result")
            return self._parse_snapshot(result)
        except CodexClientError as exc:
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
            if process.poll() is not None:
                stderr.seek(0, os.SEEK_END)
                stderr.seek(max(0, stderr.tell() - 2048))
                detail = (
                    stderr.read().decode("utf-8", errors="replace").strip().splitlines()
                )
                if detail:
                    raise CodexClientError(f"{exc}: {detail[-1][:500]}") from exc
            raise
        finally:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()
            stderr.close()

    def activate_five_hour_window(self) -> None:
        arguments = [
            self.executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-rules",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--model",
            self.activation_model,
            "-c",
            'model_reasoning_effort="low"',
            "--color",
            "never",
            "--cd",
            "/tmp",
            "Reply exactly OK.",
        ]
        try:
            completed = subprocess.run(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.activation_timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexClientError(
                f"cannot activate five-hour window: {exc}"
            ) from exc
        if completed.returncode == 0:
            return
        detail = completed.stderr.strip().splitlines()
        reason = detail[-1] if detail else f"exit status {completed.returncode}"
        raise CodexClientError(f"five-hour activation failed: {reason}")

    @staticmethod
    def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise CodexClientError("Codex app-server stdin is unavailable")
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CodexClientError("Codex app-server closed unexpectedly") from exc

    @staticmethod
    def _wait_for_response(
        process: subprocess.Popen[str], request_id: int, deadline: float
    ) -> dict[str, Any]:
        if process.stdout is None:
            raise CodexClientError("Codex app-server stdout is unavailable")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodexClientError("Codex app-server request timed out")
                if not selector.select(remaining):
                    raise CodexClientError("Codex app-server request timed out")
                line = process.stdout.readline()
                if not line:
                    raise CodexClientError("Codex app-server exited before responding")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict) or message.get("id") != request_id:
                    continue
                error = message.get("error")
                if error is not None:
                    raise CodexClientError(f"Codex app-server error: {error}")
                return message
        finally:
            selector.close()

    @staticmethod
    def _parse_snapshot(result: dict[str, Any]) -> UsageSnapshot:
        by_id = result.get("rateLimitsByLimitId")
        limits = by_id.get("codex") if isinstance(by_id, dict) else None
        if not isinstance(limits, dict):
            limits = result.get("rateLimits")
        if not isinstance(limits, dict):
            raise CodexClientError("Codex response contains no Codex rate limit")

        windows = [
            candidate
            for key in ("primary", "secondary")
            if isinstance((candidate := limits.get(key)), dict)
        ]
        weekly = next(
            (
                candidate
                for candidate in windows
                if candidate.get("windowDurationMins") == 7 * 24 * 60
            ),
            windows[0] if windows else {},
        )
        five_hour = next(
            (
                candidate
                for candidate in windows
                if candidate.get("windowDurationMins") == 5 * 60
            ),
            {},
        )
        window = weekly.get("windowDurationMins")
        window_minutes = (
            int(window)
            if isinstance(window, (int, float)) and not isinstance(window, bool)
            else None
        )

        reset_credits = result.get("rateLimitResetCredits")
        banked_resets: int | None = None
        expirations: list[datetime] = []
        if isinstance(reset_credits, dict):
            count = reset_credits.get("availableCount")
            if isinstance(count, int) and not isinstance(count, bool):
                banked_resets = count
            credits = reset_credits.get("credits")
            if isinstance(credits, list):
                for credit in credits:
                    if not isinstance(credit, dict):
                        continue
                    expiration = _epoch_timestamp(
                        credit.get("expiresAt") or credit.get("expires_at")
                    )
                    if expiration is not None:
                        expirations.append(expiration)

        return UsageSnapshot(
            used_percent=_number(weekly.get("usedPercent")),
            reset_at=_epoch_timestamp(weekly.get("resetsAt")),
            window_minutes=window_minutes,
            banked_resets=banked_resets,
            banked_reset_expirations=tuple(sorted(expirations)),
            five_hour_used_percent=_number(five_hour.get("usedPercent")),
            five_hour_reset_at=_epoch_timestamp(five_hour.get("resetsAt")),
            five_hour_window_minutes=(
                300
                if five_hour.get("windowDurationMins") == 300
                else None
            ),
        )
