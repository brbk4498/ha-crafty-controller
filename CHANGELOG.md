# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.3] - 2026-10-01

### Removed
- The **Server control** dropdown. The Start, Stop and Restart buttons replace it.

### Changed
- New servers now start with the **Start**, **Stop** and **Restart** buttons and **Running** enabled.
- The README has a dashboard card that shows the three buttons side by side.

## [1.0.2] - 2026-10-01

First public release.

### Added
- **Server control** select for each server. It shows Running or Stopped. Picking an option starts, stops or restarts the server, and stop and restart obey the Safety options.
- New servers start with only **Server control** and **Running** enabled. Every other entity is created disabled and can be switched on in Settings → Entities or in the per-server options.
- UI setup with either a username and password or an API token. Setup checks reachability first, then authentication, then the server list. Duplicate host and port combinations are rejected.
- Reauthentication and reconfiguration flows. Changing the host, port, SSL setting or credentials keeps devices, entity IDs and history.
- An automatic re-login and single retry when a username/password session expires (HTTP 403).
- One coordinator per Crafty instance that fetches stats for every server concurrently. One failing server doesn't affect the others, and each server can have its own poll interval.
- Entities:
  - Binary sensors: running, crashed and updating.
  - Sensors: players online, max players, player names, CPU, memory percentage, memory used, world size, Minecraft version and MOTD.
  - Buttons: start, stop, restart, kill and backup.
  - Optional host CPU and memory sensors, read from `/metrics/host`.
- An options menu with six sections: General, Servers, Per-server settings, Safety, Events and Advanced.
- Services:
  - `send_command` and `get_logs`
  - Scheduled tasks: `create_task`, `set_task_enabled` and `delete_task`
  - Webhooks: `list_webhooks`, `create_webhook` and `delete_webhook`
- Home Assistant events for server started, stopped and crashed, and player joined and left.
- Repair issues for an unreachable Crafty, failing authentication, a server that is no longer found, and new servers that are available to add.
- Diagnostics with secrets redacted, plus an opt-in debug logging toggle.
- The license (MIT), notices about AI-assisted authorship, a no-warranty disclaimer, and a takedown contact.

### Notes
- Requires Home Assistant 2026.9 or newer.
- Works around these differences between the Crafty OpenAPI spec and Crafty 4's behavior:
  - Stats come back under `data`. The integration also accepts them at the top level.
  - `mem` is reported in bytes. Older string forms such as "42.2MB" are still parsed.
  - `players` is a Python-repr string.
  - The single-webhook path is `servers/{id}/webhook/{wid}` (plural `servers`).
  - A failed login returns 401.
  - A missing permission returns 400.

[Unreleased]: https://github.com/brbk4498/ha-crafty-controller/compare/v1.0.3...HEAD
[1.0.3]: https://github.com/brbk4498/ha-crafty-controller/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/brbk4498/ha-crafty-controller/releases/tag/v1.0.2
