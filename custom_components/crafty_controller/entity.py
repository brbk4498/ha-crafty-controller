"""Base entities for Crafty Controller."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import override

from homeassistant.const import Platform
from homeassistant.core import HassJob
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CraftyAuthError, CraftyError, ServerStats
from .const import (
    ACTION_REFRESH_DELAY,
    DEFAULT_ENABLED_KEYS,
    DIAGNOSTIC_ENTITY_KEYS,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import CraftyCoordinator
from .safety import ensure_action_allowed


class CraftyServerEntity(CoordinatorEntity[CraftyCoordinator]):
    """An entity that belongs to one Crafty server (one HA device)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CraftyCoordinator,
        server_id: str,
        description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self.server_id = server_id
        self._attr_unique_id = f"{server_id}_{description.key}"
        self._attr_entity_registry_enabled_default = description.key in DEFAULT_ENABLED_KEYS
        stats = self.server_stats
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, server_id)},
            name=coordinator.server_name(server_id),
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=stats.version if stats else None,
            configuration_url=f"{coordinator.client.base_url}/panel/server_detail?id={server_id}",
        )

    @property
    def server_stats(self) -> ServerStats | None:
        """Return the latest stats for this server, or None."""
        state = self.coordinator.data.servers.get(self.server_id)
        if state is None or not state.available:
            return None
        return state.stats

    @property
    @override
    def available(self) -> bool:
        """Unavailable whenever this server's last fetch failed."""
        return super().available and self.server_stats is not None

    async def async_run_action(self, action: str) -> None:
        """Run a server action (after the safety gates), then refresh shortly after."""
        coordinator = self.coordinator
        ensure_action_allowed(coordinator.options, action, coordinator.server_name(self.server_id))
        try:
            await coordinator.client.async_server_action(self.server_id, action)
        except CraftyAuthError as err:
            coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except CraftyError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={"action": action, "error": str(err)},
            ) from err

        coordinator.force_refresh_server(self.server_id)

        async def _refresh(_now: datetime) -> None:
            await coordinator.async_request_refresh()

        self.async_on_remove(
            async_call_later(
                self.hass, ACTION_REFRESH_DELAY, HassJob(_refresh, cancel_on_shutdown=True)
            )
        )


def server_entities_to_add[D: EntityDescription, T: CraftyServerEntity](
    coordinator: CraftyCoordinator,
    platform: Platform,
    descriptions: Iterable[D],
    factory: Callable[[CraftyCoordinator, str, D], T],
) -> list[T]:
    """Build entities, skipping new ones switched off in the options.

    Entities already in the registry are always created; the registry's
    ``disabled_by`` (managed in ``__init__``) decides whether they load. That
    keeps unique ids and history stable across option changes.
    """
    ent_reg = er.async_get(coordinator.hass)
    options = coordinator.options
    entities: list[T] = []
    for server_id in options.servers:
        enabled = options.settings_for(server_id).enabled_entities
        for description in descriptions:
            diagnostic_ok = (
                options.enable_diagnostic_entities or description.key not in DIAGNOSTIC_ENTITY_KEYS
            )
            exists = ent_reg.async_get_entity_id(platform, DOMAIN, f"{server_id}_{description.key}")
            if enabled is None:
                # Not configured: create everything, entity defaults decide what is enabled
                if diagnostic_ok or exists:
                    entities.append(factory(coordinator, server_id, description))
                continue
            wanted = description.key in enabled and diagnostic_ok
            if wanted or exists:
                entity = factory(coordinator, server_id, description)
                # Explicit per-server choice overrides the default for new entities
                entity._attr_entity_registry_enabled_default = wanted
                entities.append(entity)
    return entities
