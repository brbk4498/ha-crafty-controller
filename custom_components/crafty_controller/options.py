"""Typed view over the config entry options, with defaults applied."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .const import (
    ALL_EVENTS,
    CONF_ALLOW_CONSOLE,
    CONF_ALLOW_CONSOLE_SERVER,
    CONF_ALLOW_KILL,
    CONF_ALLOW_STOP_RESTART,
    CONF_DEBUG_LOGGING,
    CONF_ENABLE_DIAGNOSTIC_ENTITIES,
    CONF_ENABLE_HOST_METRICS,
    CONF_ENABLE_LOGS,
    CONF_ENABLE_TASKS,
    CONF_ENABLE_WEBHOOKS,
    CONF_ENABLED_ENTITIES,
    CONF_EVENTS,
    CONF_NAME_OVERRIDE,
    CONF_RETRY_ON_403,
    CONF_SCAN_INTERVAL,
    CONF_SERVER_SCAN_INTERVAL,
    CONF_SERVER_SETTINGS,
    CONF_SERVERS,
    CONF_TIMEOUT,
    CONF_UNREACHABLE_MINUTES,
    DEFAULT_ALLOW_CONSOLE,
    DEFAULT_ALLOW_KILL,
    DEFAULT_ALLOW_STOP_RESTART,
    DEFAULT_RETRY_ON_403,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_UNREACHABLE_MINUTES,
)


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Per-server settings."""

    name_override: str | None = None
    # None means "not configured": entity defaults and the user's own registry choices apply
    enabled_entities: frozenset[str] | None = None
    scan_interval: int | None = None
    allow_console: bool = True

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> ServerSettings:
        """Build from stored options."""
        if not raw:
            return cls()
        enabled = raw.get(CONF_ENABLED_ENTITIES)
        interval = raw.get(CONF_SERVER_SCAN_INTERVAL)
        return cls(
            name_override=(raw.get(CONF_NAME_OVERRIDE) or "").strip() or None,
            enabled_entities=frozenset(enabled) if isinstance(enabled, list) else None,
            scan_interval=int(interval) if interval else None,
            allow_console=bool(raw.get(CONF_ALLOW_CONSOLE_SERVER, True)),
        )


@dataclass(frozen=True, slots=True)
class CraftyOptions:
    """All options for one config entry."""

    servers: tuple[str, ...] = ()
    scan_interval: int = DEFAULT_SCAN_INTERVAL
    timeout: int = DEFAULT_TIMEOUT
    retry_on_403: bool = DEFAULT_RETRY_ON_403
    unreachable_minutes: int = DEFAULT_UNREACHABLE_MINUTES
    allow_kill: bool = DEFAULT_ALLOW_KILL
    allow_stop_restart: bool = DEFAULT_ALLOW_STOP_RESTART
    allow_console: bool = DEFAULT_ALLOW_CONSOLE
    events: frozenset[str] = frozenset(ALL_EVENTS)
    enable_tasks: bool = False
    enable_webhooks: bool = False
    enable_logs: bool = False
    enable_host_metrics: bool = False
    enable_diagnostic_entities: bool = True
    debug_logging: bool = False
    server_settings: dict[str, ServerSettings] = field(default_factory=dict)

    @classmethod
    def from_entry_options(cls, options: Mapping[str, Any]) -> CraftyOptions:
        """Build from ``entry.options``."""
        raw_settings = options.get(CONF_SERVER_SETTINGS) or {}
        events = options.get(CONF_EVENTS)
        return cls(
            servers=tuple(options.get(CONF_SERVERS) or ()),
            scan_interval=int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            timeout=int(options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)),
            retry_on_403=bool(options.get(CONF_RETRY_ON_403, DEFAULT_RETRY_ON_403)),
            unreachable_minutes=int(
                options.get(CONF_UNREACHABLE_MINUTES, DEFAULT_UNREACHABLE_MINUTES)
            ),
            allow_kill=bool(options.get(CONF_ALLOW_KILL, DEFAULT_ALLOW_KILL)),
            allow_stop_restart=bool(
                options.get(CONF_ALLOW_STOP_RESTART, DEFAULT_ALLOW_STOP_RESTART)
            ),
            allow_console=bool(options.get(CONF_ALLOW_CONSOLE, DEFAULT_ALLOW_CONSOLE)),
            events=frozenset(events) if isinstance(events, list) else frozenset(ALL_EVENTS),
            enable_tasks=bool(options.get(CONF_ENABLE_TASKS, False)),
            enable_webhooks=bool(options.get(CONF_ENABLE_WEBHOOKS, False)),
            enable_logs=bool(options.get(CONF_ENABLE_LOGS, False)),
            enable_host_metrics=bool(options.get(CONF_ENABLE_HOST_METRICS, False)),
            enable_diagnostic_entities=bool(options.get(CONF_ENABLE_DIAGNOSTIC_ENTITIES, True)),
            debug_logging=bool(options.get(CONF_DEBUG_LOGGING, False)),
            server_settings={
                server_id: ServerSettings.from_dict(settings)
                for server_id, settings in raw_settings.items()
            },
        )

    def settings_for(self, server_id: str) -> ServerSettings:
        """Return settings for a server, falling back to defaults."""
        return self.server_settings.get(server_id) or ServerSettings()

    def interval_for(self, server_id: str) -> int:
        """Return the poll interval in seconds for a server."""
        return self.settings_for(server_id).scan_interval or self.scan_interval

    def console_allowed(self, server_id: str) -> bool:
        """Return True if console commands may be sent to a server."""
        return self.allow_console and self.settings_for(server_id).allow_console
