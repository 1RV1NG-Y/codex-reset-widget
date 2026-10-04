# Changelog

## 0.2.0 — 2026-10-04

- Automatically detect Claude Code and switch providers by clicking the widget's header. Claude uses orange accents; Codex retains blue accents.
- Show Claude's five-hour and weekly subscription usage and reset countdowns using its existing login, with token renewal, bounded requests and rate-limit backoff.
- Give each provider independent saved usage, auto-roll settings, deadlines, retry state and activation verification. Claude auto-roll starts disabled and uses a tiny Haiku request.
- Observe Claude account resets and notify on usage drops between recent samples. Open Claude's Usage page to view and redeem its account-specific limit-reset offers; the account usage interface does not expose an offer count.
- Keep Tibo's announcement feed specific to Codex, and prevent background responses from replacing the other provider's visible data or controls.
- Read npm-installed Codex through its bundled native executable on Linux, avoiding failures caused by a broken Node launcher after system updates. Include underlying app-server startup errors in the widget status.
- Add documentation and screenshots for both views, and extend the automated suite to 80 tests.
