# Changelog

## 0.3.0 — 2026-11-17

- Embed the app in the GNOME top bar with a bundled Shell extension. The panel shows the selected provider's five-hour percentage beside the icon. Clicking it opens a native dropdown with usage rings and time-elapsed markers, reset countdowns, banked or account resets, the Claude usage link, the auto-roll switch, and a button that opens the standalone widget.
- Expose a `io.github.codex_widget.Panel1` D-Bus interface from the daemon. It serves cached state, emits change signals, and accepts refresh, provider, auto-roll and open-widget commands. Usage results and errors now reach the panel even when the widget window is hidden.
- Hide the AppIndicator automatically while the panel extension is attached, and restore it if the extension or shell goes away.
- Restyle the standalone widget to match the panel: softer surfaces, pill controls, thicker progress bars and refreshed Codex blue and Claude terra-cotta accents.
- Install and enable the extension from `install-user.sh`.
- Write state files atomically so readers never see a partial file.
- Rewrite the README around the top-bar panel, with new panel, top-bar and widget screenshots. Move the notification screenshots into `docs/images`.

## 0.2.2 — 2026-10-05

- Make launchers wait for the enabled background service to own the application connection, preventing launch/restart races. Start the service with the graphical session, restart the installed app when updating, and allow rapid manual restarts while retaining ten-second crash retry backoff.
- Request focus with a fresh timestamp, allow launcher focus transitions to settle, and cancel pending dismissal timers when reopened or focused.
- Keep usage updates from raising or reopening the window. Preserve normal click-outside dismissal, pinning and Escape behavior.

## 0.2.1 — 2026-10-04

- Fix Claude token renewal requests by including saved OAuth scopes and the widget user agent, and save rotated access/refresh tokens with both expiry times.
- Check actual usage authorization before renewing; stale local expiry timestamps no longer interrupt working tokens. Distinguish temporary HTTP failures from an explicitly expired login, and respect renewal rate-limit backoff.
- Match Claude progress bar borders to the orange fill instead of inheriting the GTK theme's blue border.

## 0.2.0 — 2026-10-04

- Automatically detect Claude Code and switch providers by clicking the widget's header. Claude uses orange accents; Codex retains blue accents.
- Show Claude's five-hour and weekly subscription usage and reset countdowns using its existing login, with token renewal, bounded requests and rate-limit backoff.
- Give each provider independent saved usage, auto-roll settings, deadlines, retry state and activation verification. Claude auto-roll starts disabled and uses a tiny Haiku request.
- Observe Claude account resets and notify on usage drops between recent samples. Open Claude's Usage page to view and redeem its account-specific limit-reset offers; the account usage interface does not expose an offer count.
- Keep Tibo's announcement feed specific to Codex, and prevent background responses from replacing the other provider's visible data or controls.
- Read npm-installed Codex through its bundled native executable on Linux, avoiding failures caused by a broken Node launcher after system updates. Include underlying app-server startup errors in the widget status.
- Add documentation and screenshots for both views, and extend the automated suite to 80 tests.
