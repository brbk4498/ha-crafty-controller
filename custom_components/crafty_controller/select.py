"""Combined start/stop/restart control for Crafty Controller."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ACTION_RESTART, ACTION_START, ACTION_STOP
from .coordinator import CraftyConfigEntry
from .entity import CraftyServerEntity, server_entities_to_add

PARALLEL_UPDATES = 1

OPTION_RUNNING = "running"
OPTION_STOPPED = "stopped"
OPTION_RESTART = "restart"

OPTION_ACTIONS = {
    OPTION_RUNNING: ACTION_START,
    OPTION_STOPPED: ACTION_STOP,
    OPTION_RESTART: ACTION_RESTART,
}

SELECTS: tuple[SelectEntityDescription, ...] = (
    SelectEntityDescription(
        key="control",
        translation_key="control",
        options=[OPTION_RUNNING, OPTION_STOPPED, OPTION_RESTART],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CraftyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the server control."""
    async_add_entities(
        server_entities_to_add(entry.runtime_data, Platform.SELECT, SELECTS, CraftyServerControl)
    )


class CraftyServerControl(CraftyServerEntity, SelectEntity):
    """Shows Running/Stopped; picking an option starts, stops or restarts the server."""

    @property
    @override
    def current_option(self) -> str | None:
        """Return Running or Stopped from the latest stats."""
        stats = self.server_stats
        if stats is None or stats.running is None:
            return None
        return OPTION_RUNNING if stats.running else OPTION_STOPPED

    @override
    async def async_select_option(self, option: str) -> None:
        """Start, stop or restart the server."""
        await self.async_run_action(OPTION_ACTIONS[option])
