# Changelog

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
