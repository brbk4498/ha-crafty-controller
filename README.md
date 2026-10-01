# Crafty Controller for Home Assistant

A custom integration for [Crafty Controller 4](https://craftycontrol.com). Each Crafty server becomes a Home Assistant device with sensors, binary sensors and buttons. It also adds services for console commands, logs, schedules and webhooks. Everything is set up from the UI, and nothing goes in `configuration.yaml`.

It needs Home Assistant 2026.9 or newer (Python 3.14) and talks to the Crafty v2 API.

**Current version: 1.0.2.** See [CHANGELOG.md](CHANGELOG.md) for what changed in each release.

> **Made with AI tools.** This integration was written with the help of AI coding tools (Anthropic's Claude) and reviewed by the repository owner. It is free software provided **as is, without warranty**. It is not affiliated with Crafty Controller, Arcadia Technology, Mojang, Microsoft or Home Assistant. See [NOTICE.md](NOTICE.md) for the full disclaimers, and for how to report code that you believe was copied from your work.

## Installation

### HACS (custom repository)

1. In HACS, open the menu (⋮) and choose **Custom repositories**.
2. Add `https://github.com/brbk4498/ha-crafty-controller` and pick the **Integration** category.
3. Search for **Crafty Controller** in HACS, install it, then restart Home Assistant.

HACS offers updates when a new GitHub release is published, for example `v1.0.3`.

### Manual

1. Copy `custom_components/crafty_controller/` into `<config>/custom_components/`, so you end up with `<config>/custom_components/crafty_controller/manifest.json`.
2. Restart Home Assistant.

## Setup

Go to **Settings → Devices & services → Add integration → Crafty Controller** and fill in these fields:

| Field | What to enter |
|---|---|
| Host | The hostname or IP of the machine running Crafty, without `https://` |
| Port | The Crafty panel's HTTPS port. The default is **8443**. |
| Verify SSL certificate | Leave this **off** for Crafty's default self-signed certificate |
| Authentication method | Username and password, or API token |

The integration checks `/api/v2/crafty/check`, then logs in, then lists your servers. Every server is selected by default, and you can untick any you don't want.

### Creating an API token in Crafty

1. In Crafty, open **Panel → Users**, select your user, then open **API Keys**.
2. Create a key. Give it access to the servers you want and the permissions you plan to use. For example, *Commands* is needed for buttons and `send_command`, *Logs* for `get_logs`, *Schedule* for tasks, and *Config* for webhooks.
3. Copy the token and paste it into the **API token** step.

**Username and password or token?** With a username and password, the integration logs in for you and logs in again on its own when a short-lived token expires. With an API token, you manage expiry yourself in Crafty. If Crafty rejects the token, Home Assistant asks you to re-authenticate.

Use **Reconfigure** on the integration to change the host, port, SSL setting or credentials later. Devices, entity IDs and history are kept.

## Options

Open **Settings → Devices & services → Crafty Controller → Configure**. Each section is saved on its own and applied immediately, with no restart needed.

### General
- **Poll interval:** 10 to 300 seconds, default 30.
- **Request timeout:** 5 to 60 seconds, default 10.
- **Log in again when the token expires:** default on. This only applies to username and password logins. On a 403, the integration logs in once and retries the request.
- **Report Crafty as unreachable after:** how many minutes without contact before a repair issue is created. The default is 15.

### Servers
Add or remove monitored servers. The list is fetched from Crafty every time you open this step, so newly created servers show up. Unticking a server removes its device and entities.

### Per-server settings
Pick a server, then set:
- **Display name:** overrides the device name.
- **Enabled entities:** these start out ticked to match what is currently enabled. An unticked entity is *disabled*, not deleted, so its entity ID and history are kept. Ticking it again brings it back. Until you save this section for a server, the integration leaves your own enable and disable choices in **Settings → Entities** alone.
- **Poll interval override:** polls this server at its own rate. 0 uses the General interval.
- **Allow console commands for this server:** this only works if console commands are also allowed in **Safety**.

### Safety
| Toggle | Default | Blocks |
|---|---|---|
| Allow kill server | **Off** | The Kill button, and tasks with the `kill_server` action |
| Allow stop/restart | On | The Stop and Restart buttons, and tasks with those actions |
| Allow console commands | On | `send_command` and `command` tasks |

A blocked action raises a clear error in the UI or automation trace. It never fails silently.

### Events
Toggle each Home Assistant bus event listed below. All are on by default.

### Advanced
| Toggle | Default | Effect |
|---|---|---|
| Scheduled task services | Off | Enables `create_task`, `set_task_enabled` and `delete_task` |
| Webhook services | Off | Enables `list_webhooks`, `create_webhook` and `delete_webhook` |
| Log service | Off | Enables `get_logs` |
| Host CPU and memory sensors | Off | Reads `/metrics/host`. The account needs global access. If Crafty refuses, the feature switches itself off and logs a warning once. |
| Diagnostic entities | On | The Minecraft version and MOTD sensors |
| Debug logging | Off | Raises only this integration's logger to debug. Tokens and passwords are never logged. |

## Entities

Each entity's unique ID is `<server_id>_<key>`.

**Enabled by default:** each new server shows only four entities: the **Start**, **Stop** and **Restart** buttons and **Running**. Everything else is created but disabled. You can turn entities on in either of two places:
- **Settings → Entities**, one entity at a time
- **Configure → Per-server settings → Enabled entities**

### Start, Stop and Restart side by side

Home Assistant can't put three buttons inside one entity, but a dashboard card can show them in one row. Edit a dashboard, add a **Manual** card and paste this, replacing `survival` with your server's entity ID prefix:

```yaml
type: vertical-stack
cards:
  - type: tile
    entity: binary_sensor.survival_running
  - type: horizontal-stack
    cards:
      - type: button
        entity: button.survival_start
        name: Start
        icon: mdi:play
        tap_action: {action: perform-action, perform_action: button.press, target: {entity_id: button.survival_start}}
      - type: button
        entity: button.survival_stop
        name: Stop
        icon: mdi:stop
        tap_action: {action: perform-action, perform_action: button.press, target: {entity_id: button.survival_stop}}
      - type: button
        entity: button.survival_restart
        name: Restart
        icon: mdi:restart
        tap_action: {action: perform-action, perform_action: button.press, target: {entity_id: button.survival_restart}}
```

Stop and Restart obey the **Allow stop/restart** safety toggle.

| Entity | Key | Notes |
|---|---|---|
| Running (binary sensor, running) | `running` | **Enabled by default** |
| Crashed (binary sensor, problem) | `crashed` | |
| Updating (binary sensor, update) | `updating` | |
| Players online | `players_online` | 0 when the server is stopped |
| Max players | `max_players` | |
| Player names | `player_names` | The state is the player count. Attributes are `players` (a list) and `player_names` (comma separated, capped at 255 characters). |
| CPU usage | `cpu` | %, measurement |
| Memory usage | `memory_percent` | % |
| Memory used | `memory_used` | Data size, shown in MiB |
| World size | `world_size` | Data size, shown in MiB |
| Minecraft version | `version` | Diagnostic. Also used as the device's software version. |
| MOTD | `motd` | Diagnostic, disabled by default. Colour codes are removed. |
| Start, Stop, Restart buttons | `start`, `stop`, `restart` | **Enabled by default.** After a press, the integration refreshes that server after 3 seconds. |
| Backup button | `backup` | |
| Kill button | `kill` | Disabled by default, and also needs **Allow kill server** |
| Host CPU usage, Host memory usage | `host_cpu`, `host_memory` | Only when host metrics are enabled. They sit on a separate "host" device. |

When a server can't be reached, its entities become **unavailable**, so old values are never shown as current. One unreachable server doesn't affect the others.

## Services

All services target Crafty servers. You can pick a Crafty server device, any of its entities, or an area.

| Service | Fields | Response | Gate |
|---|---|---|---|
| `crafty_controller.send_command` | `command` (a leading `/` is removed) | Optional | Safety: console |
| `crafty_controller.get_logs` | `lines` (default 50), `from_file` | **Required** | Advanced: logs |
| `crafty_controller.create_task` | `name`, `action` (command, start, stop, restart, kill or backup), `command`, `interval`, `interval_type` (minutes, hours, days, reaction or cron), `cron_string`, `one_time`, `delay`, `enabled` | Optional (`task_id`) | Advanced: tasks, plus the Safety gates for the action |
| `crafty_controller.set_task_enabled` | `task_id`, `enabled` | | Advanced: tasks |
| `crafty_controller.delete_task` | `task_id` | | Advanced: tasks |
| `crafty_controller.list_webhooks` | | **Required** | Advanced: webhooks |
| `crafty_controller.create_webhook` | `name`, `webhook_type` (Discord, Mattermost, Slack or Teams), `url`, `triggers`, `bot_name`, `body`, `color`, `enabled` | Optional (`webhook_id`) | Advanced: webhooks |
| `crafty_controller.delete_webhook` | `webhook_id` | | Advanced: webhooks |

Example:

```yaml
action: crafty_controller.send_command
target:
  device_id: <your Crafty server device>
data:
  command: say Dinner time!
```

## Events

| Event | Data |
|---|---|
| `crafty_controller_server_started` | `server_id`, `server_name`, `config_entry_id` |
| `crafty_controller_server_stopped` | same as above |
| `crafty_controller_server_crashed` | same as above |
| `crafty_controller_player_joined` | same as above, plus `player` |
| `crafty_controller_player_left` | same as above, plus `player` |

Events come from comparing two consecutive polls, which has two limits:
- **A short session between two polls can be missed.** For example, a player who joins and leaves within the poll interval produces no event. Lower the poll interval, or use a per-server override, if this matters.
- Minecraft's status ping only reports a *sample* of player names (usually up to 12). On busy servers, joins and leaves beyond that sample are not seen. The player count is still accurate.

## Repairs

| Issue | When it appears | When it clears |
|---|---|---|
| Crafty unreachable | Crafty has been unreachable longer than the General threshold | When Crafty answers again |
| Login failing | Crafty rejects the login, even after a re-login. Home Assistant also offers re-authentication. | After a successful update |
| Server not found | A monitored server is deleted in Crafty, or the account loses access. Its entities go unavailable. | When the server is back, or you remove it in the options |
| New servers available | Crafty has servers you don't monitor yet. The check runs about every 10 polls. | When you add them, or they disappear |

New servers are never added automatically. Add them under **Configure → Servers**.

## Troubleshooting

- **SSL errors or "cannot connect" with a self-signed certificate.** Crafty ships with a self-signed certificate, so turn **Verify SSL certificate** off. Turn it on only if Crafty sits behind a certificate Home Assistant trusts, such as a reverse proxy with a public certificate.
- **403, "invalid auth", or entities going unavailable after a while.** API tokens can expire or be revoked, and a user's sessions end when they are invalidated in Crafty. Username and password logins re-login automatically. With an API token, create a new key and use **Re-authenticate**. A disabled Crafty account, or a superuser who must use 2FA, also returns 403. In that case use an API key, because the login step doesn't support TOTP.
- **Wrong port.** The panel and API listen on the HTTPS port (8443 by default), not on the Minecraft port (25565). If you changed `https_port` in Crafty's `config.json`, or run Crafty in Docker with a different published port, use the port Home Assistant can actually reach.
- **Home Assistant can't reach Crafty.** Test from Home Assistant's network, for example with `curl -k https://<host>:8443/api/v2/crafty/check` from the Terminal add-on. Docker networks, VLANs and firewalls are the usual culprits. If Crafty is in Docker on the same host, use the host's LAN IP rather than `localhost`.
- **Server "not found".** Crafty answers HTTP 400 `NOT_AUTHORIZED` when the account can't see a server. Check the API key's server access.
- **Host sensors stay unavailable.** `/metrics/host` needs an account with global access. The log shows one warning when Crafty refuses.
- **More detail.** Turn on **Advanced → Debug logging**, then download the diagnostics from the integration page. Secrets are redacted.

## Development

```bash
python3.14 -m venv venv && . venv/bin/activate
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
mypy custom_components tests
```

Tests use `pytest-homeassistant-custom-component` pinned to Home Assistant 2026.9.4, and they mock all HTTP.

## Releasing a new version

1. Bump `version` in `custom_components/crafty_controller/manifest.json` and in `pyproject.toml`.
2. Move the items under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) into a new version section.
3. Commit, then publish a GitHub release tagged `vX.Y.Z` with that changelog section as its notes.

## License and disclaimers

This project is released under the [MIT License](LICENSE) and comes with **no warranty**. It was made with AI tools.

If you find code here that you believe was copied from your work, please open an issue as described in [NOTICE.md](NOTICE.md). The owner will review it and take it down if needed.
