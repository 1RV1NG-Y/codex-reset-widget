"""D-Bus bridge that lets the GNOME Shell panel extension render the app.

The daemon owns all account access and scheduling; the extension only reads a
JSON snapshot and sends small commands back. Nothing here blocks on network or
subprocess work: ``GetState`` serves cached state and refreshes run async.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from gi.repository import Gio, GLib

from .models import AppState, ResetEvent

OBJECT_PATH = "/io/github/codex_widget/CodexWidget/Panel"
INTERFACE = "io.github.codex_widget.Panel1"
STATE_VERSION = 1

_INTROSPECTION = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="GetState">
      <arg type="s" name="state" direction="out"/>
    </method>
    <method name="Refresh">
      <arg type="s" name="provider" direction="in"/>
      <arg type="b" name="force" direction="in"/>
    </method>
    <method name="SetProvider">
      <arg type="s" name="provider" direction="in"/>
    </method>
    <method name="SetAutoRoll">
      <arg type="s" name="provider" direction="in"/>
      <arg type="b" name="enabled" direction="in"/>
    </method>
    <method name="OpenWidget"/>
    <method name="AttachPanel">
      <arg type="b" name="attached" direction="in"/>
    </method>
    <signal name="StateChanged">
      <arg type="s" name="state"/>
    </signal>
  </interface>
</node>
"""

PROVIDER_LABELS = {"codex": "Codex", "claude": "Claude"}


def _epoch(value: datetime | None) -> float | None:
    return value.timestamp() if value is not None else None


def _reset_payload(event: ResetEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "at": _epoch(event.effective_at or event.announced_at),
        "summary": event.summary,
        "url": event.url,
        "confirmed": event.confirmed,
    }


def provider_payload(
    state: AppState,
    *,
    loading: bool,
    error: str | None,
    auto_roll_message: str,
) -> dict[str, Any]:
    usage = state.last_known_usage
    usage_data = None
    if usage is not None:
        usage_data = {
            "five_hour_percent": usage.five_hour_used_percent,
            "five_hour_reset_at": _epoch(usage.five_hour_reset_at),
            "five_hour_window_minutes": usage.five_hour_window_minutes,
            "weekly_percent": usage.used_percent,
            "weekly_reset_at": _epoch(usage.reset_at),
            "weekly_window_minutes": usage.window_minutes,
            "banked_resets": usage.banked_resets,
            "checked_at": _epoch(usage.checked_at),
        }
    return {
        "loading": loading,
        "error": error,
        "usage": usage_data,
        "last_reset": _reset_payload(state.last_global_reset),
        "auto_roll": {
            "enabled": state.keep_five_hour_window_active,
            "message": auto_roll_message,
        },
    }


def build_state(application: Any) -> dict[str, Any]:
    """Snapshot both providers from cached state only (never blocks)."""
    claude_available = application.claude_executable is not None
    providers = {
        "codex": provider_payload(
            application.state_store.load(),
            loading=bool(application._usage_refreshing),
            error=application._provider_errors.get("codex"),
            auto_roll_message=application._window_keeper_message,
        )
    }
    if claude_available:
        keeper = application.claude_keeper
        providers["claude"] = provider_payload(
            keeper.state_store.load(),
            loading=bool(application._claude_refreshing),
            error=application._provider_errors.get("claude"),
            auto_roll_message=keeper._window_keeper_message,
        )
    selected = application.selected_provider
    if selected not in providers:
        selected = "codex"
    return {
        "version": STATE_VERSION,
        "selected": selected,
        "claude_available": claude_available,
        "providers": providers,
    }


class PanelService:
    """Exports ``io.github.codex_widget.Panel1`` on the application's bus."""

    def __init__(self, application: Any) -> None:
        self.application = application
        self._connection: Gio.DBusConnection | None = None
        self._registration_id = 0
        self._emit_source: int | None = None
        self._panel_owner: str | None = None
        self._owner_watch = 0

    @property
    def attached(self) -> bool:
        return self._panel_owner is not None

    def register(self, connection: Gio.DBusConnection | None) -> None:
        if connection is None or self._registration_id:
            return
        node = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)
        self._connection = connection
        self._registration_id = connection.register_object(
            OBJECT_PATH,
            node.interfaces[0],
            self._on_method_call,
            None,
            None,
        )

    def unregister(self) -> None:
        if self._emit_source is not None:
            GLib.source_remove(self._emit_source)
            self._emit_source = None
        self._unwatch_owner()
        if self._connection is not None and self._registration_id:
            self._connection.unregister_object(self._registration_id)
        self._registration_id = 0
        self._connection = None

    def state_json(self) -> str:
        return json.dumps(build_state(self.application), separators=(",", ":"))

    def publish(self) -> None:
        """Coalesce bursts of updates into one StateChanged emission."""
        if self._connection is None or self._emit_source is not None:
            return
        self._emit_source = GLib.idle_add(self._emit)

    def _emit(self) -> bool:
        self._emit_source = None
        if self._connection is not None:
            try:
                self._connection.emit_signal(
                    None,
                    OBJECT_PATH,
                    INTERFACE,
                    "StateChanged",
                    GLib.Variant("(s)", (self.state_json(),)),
                )
            except GLib.Error:
                pass
        return GLib.SOURCE_REMOVE

    def _on_method_call(
        self,
        _connection: Gio.DBusConnection,
        sender: str,
        _object_path: str,
        _interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        app = self.application
        args = parameters.unpack()
        try:
            if method_name == "GetState":
                invocation.return_value(GLib.Variant("(s)", (self.state_json(),)))
                return
            if method_name == "Refresh":
                provider, force = args
                app.panel_refresh(provider or app.selected_provider, force=force)
            elif method_name == "SetProvider":
                app.select_provider(args[0])
            elif method_name == "SetAutoRoll":
                provider, enabled = args
                app.set_provider_auto_roll(provider, enabled)
            elif method_name == "OpenWidget":
                app.show_widget()
            elif method_name == "AttachPanel":
                self._set_panel_owner(sender if args[0] else None)
            else:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod", method_name
                )
                return
        except Exception as exc:  # Report, never crash the daemon.
            invocation.return_dbus_error(f"{INTERFACE}.Error", str(exc))
            return
        invocation.return_value(None)
        self.publish()

    def _set_panel_owner(self, owner: str | None) -> None:
        if owner == self._panel_owner:
            return
        self._unwatch_owner()
        self._panel_owner = owner
        if owner is not None and self._connection is not None:
            self._owner_watch = Gio.bus_watch_name_on_connection(
                self._connection,
                owner,
                Gio.BusNameWatcherFlags.NONE,
                None,
                lambda *_args: self._set_panel_owner(None),
            )
        self.application.panel_attachment_changed(owner is not None)

    def _unwatch_owner(self) -> None:
        if self._owner_watch:
            Gio.bus_unwatch_name(self._owner_watch)
            self._owner_watch = 0
