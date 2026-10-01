"""Sensors for Crafty Controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, Platform, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ServerStats
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import CraftyConfigEntry, CraftyCoordinator
from .entity import CraftyServerEntity, server_entities_to_add

PARALLEL_UPDATES = 0

PLAYER_NAMES_MAX_LEN = 255


def _players_online(stats: ServerStats) -> int | None:
    if stats.running is False:
        return 0
    if stats.online is not None:
        return stats.online
    return len(stats.players) if stats.players is not None else None


def _player_list(stats: ServerStats) -> list[str]:
    if stats.running is False or stats.players is None:
        return []
    return stats.players


def player_names_string(players: list[str]) -> str:
    """Join names with commas, capped at 255 characters."""
    joined = ", ".join(players)
    if len(joined) <= PLAYER_NAMES_MAX_LEN:
        return joined
    return joined[: PLAYER_NAMES_MAX_LEN - 1].rstrip(", ") + "…"


@dataclass(frozen=True, kw_only=True)
class CraftySensorEntityDescription(SensorEntityDescription):
    """Describes a Crafty sensor."""

    value_fn: Callable[[ServerStats], Any]
    attrs_fn: Callable[[ServerStats], dict[str, Any]] | None = None


SENSORS: tuple[CraftySensorEntityDescription, ...] = (
    CraftySensorEntityDescription(
        key="players_online",
        translation_key="players_online",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_players_online,
    ),
    CraftySensorEntityDescription(
        key="max_players",
        translation_key="max_players",
        value_fn=lambda stats: stats.max_players,
    ),
    CraftySensorEntityDescription(
        key="player_names",
        translation_key="player_names",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stats: len(_player_list(stats)),
        attrs_fn=lambda stats: {
            "players": _player_list(stats),
            "player_names": player_names_string(_player_list(stats)),
        },
    ),
    CraftySensorEntityDescription(
        key="cpu",
        translation_key="cpu",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda stats: stats.cpu,
    ),
    CraftySensorEntityDescription(
        key="memory_percent",
        translation_key="memory_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda stats: stats.mem_percent,
    ),
    CraftySensorEntityDescription(
        key="memory_used",
        translation_key="memory_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stats: stats.mem_bytes,
    ),
    CraftySensorEntityDescription(
        key="world_size",
        translation_key="world_size",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda stats: stats.world_size_bytes,
    ),
    CraftySensorEntityDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda stats: stats.version,
    ),
    CraftySensorEntityDescription(
        key="motd",
        translation_key="motd",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda stats: stats.motd[:255] if stats.motd else None,
    ),
)


@dataclass(frozen=True, kw_only=True)
class CraftyHostSensorEntityDescription(SensorEntityDescription):
    """Describes a host metrics sensor."""

    metric: str


HOST_SENSORS: tuple[CraftyHostSensorEntityDescription, ...] = (
    CraftyHostSensorEntityDescription(
        key="host_cpu",
        translation_key="host_cpu",
        metric="CPU_Usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    CraftyHostSensorEntityDescription(
        key="host_memory",
        translation_key="host_memory",
        metric="Mem_Usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CraftyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = list(
        server_entities_to_add(coordinator, Platform.SENSOR, SENSORS, CraftySensor)
    )
    if coordinator.options.enable_host_metrics:
        entities.extend(CraftyHostSensor(coordinator, description) for description in HOST_SENSORS)
    async_add_entities(entities)


class CraftySensor(CraftyServerEntity, SensorEntity):
    """A Crafty server sensor."""

    entity_description: CraftySensorEntityDescription

    @property
    @override
    def native_value(self) -> Any:
        """Return the state."""
        stats = self.server_stats
        return None if stats is None else self.entity_description.value_fn(stats)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        stats = self.server_stats
        if stats is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(stats)


class CraftyHostSensor(CoordinatorEntity[CraftyCoordinator], SensorEntity):
    """Host CPU/memory from ``/metrics/host``."""

    _attr_has_entity_name = True
    entity_description: CraftyHostSensorEntityDescription

    def __init__(
        self,
        coordinator: CraftyCoordinator,
        description: CraftyHostSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_host")},
            name=coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model=f"{MODEL} host",
            configuration_url=coordinator.client.base_url,
        )

    @property
    @override
    def available(self) -> bool:
        """Unavailable when host metrics are missing or unsupported."""
        host = self.coordinator.data.host
        return super().available and host is not None and self.entity_description.metric in host

    @property
    @override
    def native_value(self) -> float | None:
        """Return the metric value."""
        host = self.coordinator.data.host
        return host.get(self.entity_description.metric) if host else None
