"""Diagnostics for Crafty Controller."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN
from .coordinator import CraftyConfigEntry

TO_REDACT = {
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_HOST,
    "token",
    "icon",
    "unique_id",
    "title",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CraftyConfigEntry
) -> dict[str, Any]:
    """Return diagnostics with secrets redacted."""
    coordinator = entry.runtime_data
    servers: dict[str, Any] = {}
    for server_id, state in coordinator.data.servers.items():
        stats = asdict(state.stats) if state.stats else None
        if stats is not None:
            stats["raw"] = async_redact_data(stats.get("raw") or {}, TO_REDACT)
        servers[server_id] = {
            "name": coordinator.server_name(server_id),
            "available": state.available,
            "not_found": state.not_found,
            "error": state.error,
            "stats": stats,
        }
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "update_interval": str(coordinator.update_interval),
        "host_metrics_supported": coordinator.host_metrics_supported,
        "known_servers": {
            sid: {"name": info.name, "type": info.type}
            for sid, info in coordinator.server_info.items()
        },
        "servers": servers,
        "host": coordinator.data.host,
    }
