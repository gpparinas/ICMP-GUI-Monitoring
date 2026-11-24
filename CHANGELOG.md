# Changelog

## Unreleased

### Added
- Custom logo: upload a PNG, SVG, JPEG, WebP, GIF or ICO image in Settings to replace the
  icon in the top bar and the browser tab.

### Changed
- Simpler dashboard: the page heading names what is down and for how long, the status tiles
  are gone, and all groups share one table.
- Geist and Geist Mono fonts, bundled with the app so no internet access is needed.

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
