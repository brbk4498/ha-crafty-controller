"""Tests for the default-enabled entities."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.crafty_controller.const import (
    ALL_SERVER_ENTITY_KEYS,
    CONF_ENABLED_ENTITIES,
    CONF_SERVER_ID,
    CONF_SERVERS,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import SERVER_A, TOKEN_DATA, mock_crafty, stats_nested

DEFAULT_VISIBLE = {
    "binary_sensor.survival_running",
    "button.survival_start",
    "button.survival_stop",
    "button.survival_restart",
}


@pytest.fixture
def bare_entry(hass: HomeAssistant) -> MockConfigEntry:
    """An entry that never opened the options (pure defaults)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Crafty",
        unique_id="crafty.test:8443",
        data=dict(TOKEN_DATA),
        options={CONF_SERVERS: [SERVER_A]},
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_only_buttons_and_running_by_default(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    bare_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    mock_crafty(aioclient_mock, stats={SERVER_A: stats_nested()})
    await _setup(hass, bare_entry)
    visible = {state.entity_id for state in hass.states.async_all()}
    assert visible == DEFAULT_VISIBLE
    # Everything else exists in the registry, disabled, ready to switch on
    entries = er.async_entries_for_config_entry(entity_registry, bare_entry.entry_id)
    assert len(entries) == len(ALL_SERVER_ENTITY_KEYS)
    disabled = {
        e.unique_id for e in entries if e.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    }
    assert f"{SERVER_A}_cpu" in disabled
    assert f"{SERVER_A}_kill" in disabled
    assert f"{SERVER_A}_start" not in disabled


async def test_user_enabled_entity_survives_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    bare_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    mock_crafty(aioclient_mock, stats={SERVER_A: stats_nested()})
    await _setup(hass, bare_entry)
    entity_registry.async_update_entity("sensor.survival_cpu_usage", disabled_by=None)
    await hass.config_entries.async_reload(bare_entry.entry_id)
    await hass.async_block_till_done()
    assert entity_registry.async_get("sensor.survival_cpu_usage").disabled_by is None
    assert hass.states.get("sensor.survival_cpu_usage").state == "12.5"


async def test_options_pretick_matches_registry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, bare_entry: MockConfigEntry
) -> None:
    mock_crafty(aioclient_mock, stats={SERVER_A: stats_nested()})
    await _setup(hass, bare_entry)
    result = await hass.config_entries.options.async_init(bare_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "server_select"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SERVER_ID: SERVER_A}
    )
    default = next(k.default() for k in result["data_schema"].schema if k == CONF_ENABLED_ENTITIES)
    assert default == ["running", "start", "stop", "restart"]
