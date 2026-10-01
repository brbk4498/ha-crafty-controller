"""Data update coordinator for Crafty Controller."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import time
from typing import Any, NoReturn, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CraftyApiError,
    CraftyAuthError,
    CraftyClient,
    CraftyConnectionError,
    CraftyError,
    CraftyPermissionError,
    ServerInfo,
    ServerStats,
)
from .const import (
    DISCOVERY_EVERY_N_UPDATES,
    DOMAIN,
    EVENT_PLAYER_JOINED,
    EVENT_PLAYER_LEFT,
    EVENT_SERVER_CRASHED,
    EVENT_SERVER_STARTED,
    EVENT_SERVER_STOPPED,
    LOGGER,
)
from .options import CraftyOptions

type CraftyConfigEntry = ConfigEntry[CraftyCoordinator]


@dataclass(slots=True)
class ServerState:
    """Latest known state of one server."""

    stats: ServerStats | None = None
    available: bool = False
    not_found: bool = False
    error: str | None = None
    last_fetch: float = 0.0


@dataclass(slots=True)
class CraftyData:
    """Everything the coordinator publishes."""

    servers: dict[str, ServerState] = field(default_factory=dict)
    host: dict[str, float] | None = None


def issue_id_unreachable(entry_id: str) -> str:
    """Issue id for 'Crafty unreachable'."""
    return f"unreachable_{entry_id}"


def issue_id_auth(entry_id: str) -> str:
    """Issue id for 'authentication failing'."""
    return f"auth_failed_{entry_id}"


def issue_id_not_found(entry_id: str, server_id: str) -> str:
    """Issue id for 'server not found'."""
    return f"server_not_found_{entry_id}_{server_id}"


def issue_id_new_servers(entry_id: str) -> str:
    """Issue id for 'new servers available'."""
    return f"new_servers_{entry_id}"


class CraftyCoordinator(DataUpdateCoordinator[CraftyData]):
    """Fetch stats for every monitored server of one Crafty instance."""

    config_entry: CraftyConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: CraftyConfigEntry,
        client: CraftyClient,
        options: CraftyOptions,
    ) -> None:
        """Initialize the coordinator."""
        intervals = [options.interval_for(server_id) for server_id in options.servers]
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {config_entry.title}",
            update_interval=timedelta(seconds=min([options.scan_interval, *intervals])),
        )
        self.client = client
        self.options = options
        self.server_info: dict[str, ServerInfo] = {}
        self.host_metrics_supported = options.enable_host_metrics
        self._forced: set[str] = set()
        self._update_count = 0
        self._unreachable_since: float | None = None

    @override
    async def _async_setup(self) -> None:
        """Log in (when using credentials) and load the server list once."""
        try:
            if self.client.has_credentials:
                await self.client.async_login()
            servers = await self.client.async_get_servers()
        except CraftyAuthError as err:
            self._raise_auth_failed(err)
        except CraftyError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        self._apply_server_list(servers)

    def server_name(self, server_id: str) -> str:
        """Return the display name for a server (user override first)."""
        override = self.options.settings_for(server_id).name_override
        if override:
            return override
        info = self.server_info.get(server_id)
        return info.name if info else server_id

    def force_refresh_server(self, server_id: str) -> None:
        """Make the next update fetch this server even if it is not due."""
        self._forced.add(server_id)

    @override
    async def _async_update_data(self) -> CraftyData:
        """Fetch due servers concurrently; one failure never breaks the others."""
        now = time.monotonic()
        previous = self.data or CraftyData()
        new = CraftyData(
            servers={
                server_id: previous.servers.get(server_id) or ServerState()
                for server_id in self.options.servers
            },
            host=previous.host,
        )
        due = [
            server_id
            for server_id, state in new.servers.items()
            if server_id in self._forced
            or state.last_fetch == 0.0
            or now - state.last_fetch >= self.options.interval_for(server_id) - 1
        ]
        self._forced.difference_update(due)

        results = await asyncio.gather(
            *(self.client.async_get_server_stats(server_id) for server_id in due),
            return_exceptions=True,
        )

        auth_error: CraftyAuthError | None = None
        connection_errors = 0
        for server_id, result in zip(due, results, strict=True):
            state = ServerState(stats=None, last_fetch=now)
            if isinstance(result, ServerStats):
                state.stats = result
                state.available = True
                self._delete_issue(issue_id_not_found(self.config_entry.entry_id, server_id))
            elif isinstance(result, CraftyAuthError):
                auth_error = result
                state.error = "auth"
            elif isinstance(result, CraftyPermissionError):
                state.not_found = True
                state.error = str(result)
                self._create_not_found_issue(server_id)
            elif isinstance(result, CraftyConnectionError):
                connection_errors += 1
                state.error = str(result)
            elif isinstance(result, (CraftyApiError, CraftyError)):
                state.error = str(result)
            elif isinstance(result, BaseException):
                LOGGER.exception("Unexpected error fetching %s", server_id, exc_info=result)
                state.error = repr(result)
            if not state.available:
                LOGGER.debug("Server %s unavailable: %s", server_id, state.error)
            new.servers[server_id] = state

        if auth_error is not None:
            self._raise_auth_failed(auth_error)
        self._delete_issue(issue_id_auth(self.config_entry.entry_id))

        if due and connection_errors == len(due):
            self._mark_unreachable(now)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": new.servers[due[0]].error or ""},
            )
        self._mark_reachable()

        if self.options.enable_host_metrics and self.host_metrics_supported:
            new.host = await self._async_fetch_host_metrics()

        self._update_count += 1
        if self._update_count % DISCOVERY_EVERY_N_UPDATES == 0:
            await self._async_discover_servers()

        if self.data is not None:
            self._fire_events(self.data, new)
        return new

    async def _async_fetch_host_metrics(self) -> dict[str, float] | None:
        try:
            return await self.client.async_get_host_metrics()
        except CraftyPermissionError:
            LOGGER.warning(
                "Crafty refused /metrics/host (the account lacks global access); "
                "host metrics are disabled until the integration is reloaded"
            )
            self.host_metrics_supported = False
        except CraftyAuthError as err:
            self._raise_auth_failed(err)
        except CraftyError as err:
            LOGGER.debug("Host metrics unavailable: %s", err)
        return None

    async def _async_discover_servers(self) -> None:
        """Refresh the server list; report new servers instead of adding them."""
        try:
            servers = await self.client.async_get_servers()
        except CraftyError as err:
            LOGGER.debug("Server discovery failed: %s", err)
            return
        self._apply_server_list(servers)

    def _apply_server_list(self, servers: list[ServerInfo]) -> None:
        self.server_info = {server.server_id: server for server in servers}
        entry_id = self.config_entry.entry_id
        monitored = set(self.options.servers)
        new_ids = sorted(set(self.server_info) - monitored)
        if new_ids:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id_new_servers(entry_id),
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="new_servers",
                translation_placeholders={
                    "entry_title": self.config_entry.title,
                    "servers": ", ".join(self.server_info[sid].name for sid in new_ids),
                },
                data={"servers": ",".join(new_ids)},
            )
        else:
            self._delete_issue(issue_id_new_servers(entry_id))
        for server_id in monitored - set(self.server_info):
            self._create_not_found_issue(server_id)

    def _create_not_found_issue(self, server_id: str) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id_not_found(self.config_entry.entry_id, server_id),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="server_not_found",
            translation_placeholders={
                "server": self.server_name(server_id),
                "entry_title": self.config_entry.title,
            },
        )

    def _mark_unreachable(self, now: float) -> None:
        if self._unreachable_since is None:
            self._unreachable_since = now
        minutes = (now - self._unreachable_since) / 60
        if minutes >= self.options.unreachable_minutes:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id_unreachable(self.config_entry.entry_id),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="unreachable",
                translation_placeholders={
                    "entry_title": self.config_entry.title,
                    "minutes": str(self.options.unreachable_minutes),
                    "url": self.client.base_url,
                },
            )

    def _mark_reachable(self) -> None:
        self._unreachable_since = None
        self._delete_issue(issue_id_unreachable(self.config_entry.entry_id))

    def _raise_auth_failed(self, err: CraftyAuthError) -> NoReturn:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id_auth(self.config_entry.entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="auth_failed",
            translation_placeholders={"entry_title": self.config_entry.title},
        )
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_auth",
        ) from err

    def _delete_issue(self, issue_id: str) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _fire_events(self, old: CraftyData, new: CraftyData) -> None:
        """Fire bus events for changes between two consecutive updates."""
        enabled = self.options.events
        if not enabled:
            return
        for server_id, state in new.servers.items():
            before = old.servers.get(server_id)
            if (
                before is None
                or not before.available
                or not state.available
                or before.stats is None
                or state.stats is None
            ):
                continue
            base: dict[str, Any] = {
                "server_id": server_id,
                "server_name": self.server_name(server_id),
                "config_entry_id": self.config_entry.entry_id,
            }
            old_stats, new_stats = before.stats, state.stats
            if old_stats.running is False and new_stats.running is True:
                self._fire(EVENT_SERVER_STARTED, base)
            if old_stats.running is True and new_stats.running is False:
                self._fire(EVENT_SERVER_STOPPED, base)
            if old_stats.crashed is not True and new_stats.crashed is True:
                self._fire(EVENT_SERVER_CRASHED, base)
            old_list = _effective_players(old_stats)
            new_list = _effective_players(new_stats)
            if old_list is not None and new_list is not None:
                old_players = set(old_list)
                new_players = set(new_list)
                for player in sorted(new_players - old_players):
                    self._fire(EVENT_PLAYER_JOINED, {**base, "player": player})
                for player in sorted(old_players - new_players):
                    self._fire(EVENT_PLAYER_LEFT, {**base, "player": player})

    def _fire(self, event_type: str, data: dict[str, Any]) -> None:
        if event_type in self.options.events:
            self.hass.bus.async_fire(event_type, data)


def _effective_players(stats: ServerStats) -> list[str] | None:
    """Players for diffing: a stopped server has nobody online."""
    if stats.running is False:
        return []
    return stats.players
