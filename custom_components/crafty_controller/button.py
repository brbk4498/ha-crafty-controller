"""Buttons for Crafty Controller server actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ACTION_BACKUP, ACTION_KILL, ACTION_RESTART, ACTION_START, ACTION_STOP
from .coordinator import CraftyConfigEntry
from .entity import CraftyServerEntity, server_entities_to_add

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class CraftyButtonEntityDescription(ButtonEntityDescription):
    """Describes a Crafty action button."""

    action: str


BUTTONS: tuple[CraftyButtonEntityDescription, ...] = (
    CraftyButtonEntityDescription(key="start", translation_key="start", action=ACTION_START),
    CraftyButtonEntityDescription(key="stop", translation_key="stop", action=ACTION_STOP),
    CraftyButtonEntityDescription(key="restart", translation_key="restart", action=ACTION_RESTART),
    CraftyButtonEntityDescription(key="kill", translation_key="kill", action=ACTION_KILL),
    CraftyButtonEntityDescription(key="backup", translation_key="backup", action=ACTION_BACKUP),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CraftyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    async_add_entities(
        server_entities_to_add(entry.runtime_data, Platform.BUTTON, BUTTONS, CraftyButton)
    )


class CraftyButton(CraftyServerEntity, ButtonEntity):
    """Runs a server action, then refreshes shortly after."""

    entity_description: CraftyButtonEntityDescription

    @override
    async def async_press(self) -> None:
        """Send the action to Crafty."""
        await self.async_run_action(self.entity_description.action)
