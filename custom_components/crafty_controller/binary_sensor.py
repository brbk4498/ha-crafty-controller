"""Binary sensors for Crafty Controller."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ServerStats
from .coordinator import CraftyConfigEntry
from .entity import CraftyServerEntity, server_entities_to_add

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CraftyBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Crafty binary sensor."""

    value_fn: Callable[[ServerStats], bool | None]


BINARY_SENSORS: tuple[CraftyBinarySensorEntityDescription, ...] = (
    CraftyBinarySensorEntityDescription(
        key="running",
        translation_key="running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda stats: stats.running,
    ),
    CraftyBinarySensorEntityDescription(
        key="crashed",
        translation_key="crashed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda stats: stats.crashed,
    ),
    CraftyBinarySensorEntityDescription(
        key="updating",
        translation_key="updating",
        device_class=BinarySensorDeviceClass.UPDATE,
        value_fn=lambda stats: stats.updating,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CraftyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    async_add_entities(
        server_entities_to_add(
            entry.runtime_data, Platform.BINARY_SENSOR, BINARY_SENSORS, CraftyBinarySensor
        )
    )


class CraftyBinarySensor(CraftyServerEntity, BinarySensorEntity):
    """A Crafty server binary sensor."""

    entity_description: CraftyBinarySensorEntityDescription

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the state."""
        stats = self.server_stats
        return None if stats is None else self.entity_description.value_fn(stats)
