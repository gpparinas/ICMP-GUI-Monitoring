# Changelog

## Unreleased

### Changed
- New dashboard design. Every monitor is a port on a patch panel with a link light; the page
  opens with a plain sentence saying what is wrong ("Front desk printer is down"). Status is
  shown by shape as well as colour, so it does not depend on telling red from green.
- The Archivo typeface is bundled, so the dashboard still needs no internet access.

### Added
- Wall screen (`#/wall`): a full-screen panel for a TV or spare monitor. Press Esc to leave.

## 2.0.0

A complete rewrite. The desktop window became a self-hosted service with a web dashboard.

### Added
- Background monitoring engine: checks run concurrently, each on its own schedule, whether
  or not anyone has the dashboard open.
- TCP port and DNS checks alongside ping and HTTP(S); keyword matching, accepted status
  codes and TLS certificate expiry warnings for websites.
- Retries before a monitor is declared down, and a "degraded" state for slow responses.
- History in SQLite: response-time charts, uptime over 24 hours / 7 days / 30 days, an
  incident log and a CSV uptime report.
- Alerts by email (SMTP), Slack, Microsoft Teams, Discord, Telegram and generic webhook.
- Monitors, groups, alert channels and settings are managed in the browser.
- Admin password, optional read-only public view, light and dark themes, mobile layout.
- `netmon` command, Dockerfile, docker-compose file, systemd unit, test suite and CI.
- Import of the version 1 `config.json`.

### Changed
- Ping no longer needs Administrator/root rights: it falls back to the system `ping` command.

### Removed
- The tkinter application (`Ping_Monitoring.py`) and the PyInstaller `version_info.txt`.
  `config.json` moved to `examples/config.v1.json`.

## 1.0.0

Tkinter desktop window showing ping and HTTP status for the entries in `config.json`.
