"""Constants for the Crafty Controller integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "crafty_controller"
LOGGER = logging.getLogger(__package__)

MANUFACTURER: Final = "Arcadia Technology"
MODEL: Final = "Crafty Controller"

DEFAULT_PORT: Final = 8443

# Config entry data keys
CONF_AUTH_METHOD: Final = "auth_method"
CONF_TOKEN: Final = "token"
AUTH_METHOD_CREDENTIALS: Final = "credentials"
AUTH_METHOD_TOKEN: Final = "token"

# Option keys: general
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TIMEOUT: Final = "timeout"
CONF_RETRY_ON_403: Final = "retry_on_403"
CONF_UNREACHABLE_MINUTES: Final = "unreachable_minutes"

DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 300
DEFAULT_TIMEOUT: Final = 10
MIN_TIMEOUT: Final = 5
MAX_TIMEOUT: Final = 60
DEFAULT_RETRY_ON_403: Final = True
DEFAULT_UNREACHABLE_MINUTES: Final = 15

# Option keys: servers and per-server settings
CONF_SERVERS: Final = "servers"
CONF_SERVER_SETTINGS: Final = "server_settings"
CONF_SERVER_ID: Final = "server_id"
CONF_NAME_OVERRIDE: Final = "name_override"
CONF_ENABLED_ENTITIES: Final = "enabled_entities"
CONF_SERVER_SCAN_INTERVAL: Final = "server_scan_interval"
CONF_ALLOW_CONSOLE_SERVER: Final = "allow_console_server"

# Option keys: safety
CONF_ALLOW_KILL: Final = "allow_kill"
CONF_ALLOW_STOP_RESTART: Final = "allow_stop_restart"
CONF_ALLOW_CONSOLE: Final = "allow_console"
DEFAULT_ALLOW_KILL: Final = False
DEFAULT_ALLOW_STOP_RESTART: Final = True
DEFAULT_ALLOW_CONSOLE: Final = True

# Option keys: events
CONF_EVENTS: Final = "events"
EVENT_SERVER_STARTED: Final = f"{DOMAIN}_server_started"
EVENT_SERVER_STOPPED: Final = f"{DOMAIN}_server_stopped"
EVENT_SERVER_CRASHED: Final = f"{DOMAIN}_server_crashed"
EVENT_PLAYER_JOINED: Final = f"{DOMAIN}_player_joined"
EVENT_PLAYER_LEFT: Final = f"{DOMAIN}_player_left"
ALL_EVENTS: Final = [
    EVENT_SERVER_STARTED,
    EVENT_SERVER_STOPPED,
    EVENT_SERVER_CRASHED,
    EVENT_PLAYER_JOINED,
    EVENT_PLAYER_LEFT,
]
# Short keys used in the options form
EVENT_KEYS: Final = {
    "server_started": EVENT_SERVER_STARTED,
    "server_stopped": EVENT_SERVER_STOPPED,
    "server_crashed": EVENT_SERVER_CRASHED,
    "player_joined": EVENT_PLAYER_JOINED,
    "player_left": EVENT_PLAYER_LEFT,
}

# Option keys: advanced
CONF_ENABLE_TASKS: Final = "enable_tasks"
CONF_ENABLE_WEBHOOKS: Final = "enable_webhooks"
CONF_ENABLE_LOGS: Final = "enable_logs"
CONF_ENABLE_HOST_METRICS: Final = "enable_host_metrics"
CONF_ENABLE_DIAGNOSTIC_ENTITIES: Final = "enable_diagnostic_entities"
CONF_DEBUG_LOGGING: Final = "debug_logging"

# Server actions
ACTION_START: Final = "start_server"
ACTION_STOP: Final = "stop_server"
ACTION_RESTART: Final = "restart_server"
ACTION_KILL: Final = "kill_server"
ACTION_BACKUP: Final = "backup_server"
SERVER_ACTIONS: Final = (
    ACTION_START,
    ACTION_STOP,
    ACTION_RESTART,
    ACTION_KILL,
    ACTION_BACKUP,
)

# Entity keys (unique_id = f"{server_id}_{key}")
BINARY_SENSOR_KEYS: Final = ("running", "crashed", "updating")
SENSOR_KEYS: Final = (
    "players_online",
    "max_players",
    "player_names",
    "cpu",
    "memory_percent",
    "memory_used",
    "world_size",
    "version",
    "motd",
)
BUTTON_KEYS: Final = ("start", "stop", "restart", "kill", "backup")
SELECT_KEYS: Final = ("control",)
ALL_SERVER_ENTITY_KEYS: Final = SELECT_KEYS + BINARY_SENSOR_KEYS + SENSOR_KEYS + BUTTON_KEYS
# Only these are enabled for a new server; everything else can be switched on later
DEFAULT_ENABLED_KEYS: Final = ("control", "running")
DIAGNOSTIC_ENTITY_KEYS: Final = ("version", "motd")

# Delay before refreshing after a button press, so Crafty has time to act
ACTION_REFRESH_DELAY: Final = 3

# How often (in coordinator updates) to look for newly created servers
DISCOVERY_EVERY_N_UPDATES: Final = 10

# Service names
SERVICE_SEND_COMMAND: Final = "send_command"
SERVICE_GET_LOGS: Final = "get_logs"
SERVICE_CREATE_TASK: Final = "create_task"
SERVICE_SET_TASK_ENABLED: Final = "set_task_enabled"
SERVICE_DELETE_TASK: Final = "delete_task"
SERVICE_LIST_WEBHOOKS: Final = "list_webhooks"
SERVICE_CREATE_WEBHOOK: Final = "create_webhook"
SERVICE_DELETE_WEBHOOK: Final = "delete_webhook"

WEBHOOK_TYPES: Final = ("Discord", "Mattermost", "Slack", "Teams")
TASK_INTERVAL_TYPES: Final = ("minutes", "hours", "days", "reaction", "cron")
TASK_ACTIONS: Final = (
    "command",
    ACTION_START,
    ACTION_STOP,
    ACTION_RESTART,
    ACTION_KILL,
    ACTION_BACKUP,
)
