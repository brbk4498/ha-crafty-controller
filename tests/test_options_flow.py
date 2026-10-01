"""Tests for the options flow and how option changes apply."""

from __future__ import annotations

from http import HTTPStatus

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.crafty_controller.const import (
    ALL_EVENTS,
    ALL_SERVER_ENTITY_KEYS,
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
    CONF_SERVER_ID,
    CONF_SERVER_SCAN_INTERVAL,
    CONF_SERVER_SETTINGS,
    CONF_SERVERS,
    CONF_TIMEOUT,
    CONF_UNREACHABLE_MINUTES,
    DOMAIN,
    EVENT_PLAYER_JOINED,
    EVENT_SERVER_STARTED,
)
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import BASE, SERVER_A, SERVER_B, SERVER_NEW, mock_crafty, server_item


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _open(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> ConfigFlowResult:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "general",
        "servers",
        "server_select",
        "safety",
        "events",
        "advanced",
    ]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_general(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    result = await _open(hass, token_entry, "general")
    assert result["step_id"] == "general"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 60,
            CONF_TIMEOUT: 20,
            CONF_RETRY_ON_403: False,
            CONF_UNREACHABLE_MINUTES: 5,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert token_entry.options[CONF_SCAN_INTERVAL] == 60
    assert token_entry.options[CONF_SERVERS] == [SERVER_A, SERVER_B]
    # Reloaded with the new interval, no restart needed
    assert token_entry.state is ConfigEntryState.LOADED
    assert token_entry.runtime_data.update_interval.total_seconds() == 60
    assert token_entry.runtime_data.client._timeout.total == 20


async def test_servers_refetches_and_removes_device(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    mock_crafty(aioclient_mock)
    await _setup(hass, token_entry)
    assert device_registry.async_get_device_by_identifier((DOMAIN, SERVER_B), token_entry.entry_id)

    aioclient_mock.clear_requests()
    mock_crafty(
        aioclient_mock,
        servers=[
            server_item(SERVER_A, "Survival"),
            server_item(SERVER_B, "Creative"),
            server_item(SERVER_NEW, "Skyblock"),
        ],
    )
    result = await _open(hass, token_entry, "servers")
    selector = next(v for k, v in result["data_schema"].schema.items() if k == CONF_SERVERS)
    offered = [option["value"] for option in selector.config["options"]]
    assert offered == [SERVER_A, SERVER_B, SERVER_NEW]

    aioclient_mock.get(
        f"{BASE}/api/v2/servers/{SERVER_NEW}/stats",
        json={"status": "ok", "data": {"running": False}},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SERVERS: [SERVER_A, SERVER_NEW]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert (
        device_registry.async_get_device_by_identifier((DOMAIN, SERVER_B), token_entry.entry_id)
        is None
    )
    assert entity_registry.async_get("sensor.creative_cpu_usage") is None
    assert hass.states.get("binary_sensor.skyblock_running").state == "off"
    assert hass.states.get("binary_sensor.survival_running").state == "on"


async def test_servers_cannot_connect_shows_current(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    mock_crafty(aioclient_mock)
    await _setup(hass, token_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/api/v2/crafty/check", status=HTTPStatus.BAD_GATEWAY, text="down")
    result = await _open(hass, token_entry, "servers")
    assert result["errors"] == {"base": "cannot_connect"}
    selector = next(v for k, v in result["data_schema"].schema.items() if k == CONF_SERVERS)
    assert [o["value"] for o in selector.config["options"]] == [SERVER_A, SERVER_B]


async def test_per_server_settings_disable_and_reenable(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    await _setup(hass, token_entry)
    cpu_id = "sensor.survival_cpu_usage"
    assert hass.states.get(cpu_id).state == "12.5"

    result = await _open(hass, token_entry, "server_select")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SERVER_ID: SERVER_A}
    )
    assert result["step_id"] == "server_settings"
    assert result["description_placeholders"] == {"server": "Survival"}
    enabled = [k for k in ALL_SERVER_ENTITY_KEYS if k != "cpu"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_NAME_OVERRIDE: "Main world",
            CONF_ENABLED_ENTITIES: enabled,
            CONF_SERVER_SCAN_INTERVAL: 15,
            CONF_ALLOW_CONSOLE_SERVER: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    settings = token_entry.options[CONF_SERVER_SETTINGS][SERVER_A]
    assert settings[CONF_SERVER_SCAN_INTERVAL] == 15
    assert settings[CONF_ALLOW_CONSOLE_SERVER] is False

    # Disabled, not deleted: same entity id, history kept
    reg = entity_registry.async_get(cpu_id)
    assert reg is not None
    assert reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(cpu_id) is None
    # Entity ids are unchanged by the name override; the device name changes
    assert hass.states.get("binary_sensor.survival_running") is not None
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, SERVER_A), token_entry.entry_id
    )
    assert device.name == "Main world"
    assert token_entry.runtime_data.update_interval.total_seconds() == 15

    # Re-enable
    result = await _open(hass, token_entry, "server_select")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SERVER_ID: SERVER_A}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENABLED_ENTITIES: list(ALL_SERVER_ENTITY_KEYS),
            CONF_SERVER_SCAN_INTERVAL: 0,
            CONF_ALLOW_CONSOLE_SERVER: True,
        },
    )
    await hass.async_block_till_done()
    assert entity_registry.async_get(cpu_id).disabled_by is None
    assert hass.states.get(cpu_id).state == "12.5"
    assert token_entry.options[CONF_SERVER_SETTINGS][SERVER_A][CONF_SERVER_SCAN_INTERVAL] is None


async def test_user_disabled_entity_left_alone(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    await _setup(hass, token_entry)
    entity_registry.async_update_entity(
        "sensor.survival_world_size", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.config_entries.async_reload(token_entry.entry_id)
    await hass.async_block_till_done()
    assert (
        entity_registry.async_get("sensor.survival_world_size").disabled_by
        is er.RegistryEntryDisabler.USER
    )


async def test_new_entity_off_in_options_is_not_created(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    hass.config_entries.async_update_entry(
        token_entry,
        options={
            **token_entry.options,
            CONF_SERVER_SETTINGS: {SERVER_A: {CONF_ENABLED_ENTITIES: ["running"]}},
        },
    )
    await _setup(hass, token_entry)
    assert entity_registry.async_get("sensor.survival_cpu_usage") is None
    assert hass.states.get("binary_sensor.survival_running").state == "on"
    # Creative has no stored settings, so only the defaults are enabled
    assert hass.states.get("sensor.creative_cpu_usage") is None
    assert hass.states.get("binary_sensor.creative_running").state == "on"


async def test_safety(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    result = await _open(hass, token_entry, "safety")
    defaults = {k: k.default() for k in result["data_schema"].schema}
    assert defaults == {
        CONF_ALLOW_KILL: False,
        CONF_ALLOW_STOP_RESTART: True,
        CONF_ALLOW_CONSOLE: True,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ALLOW_KILL: True, CONF_ALLOW_STOP_RESTART: False, CONF_ALLOW_CONSOLE: False},
    )
    await hass.async_block_till_done()
    opts = token_entry.runtime_data.options
    assert (opts.allow_kill, opts.allow_stop_restart, opts.allow_console) == (True, False, False)


async def test_events(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    result = await _open(hass, token_entry, "events")
    assert all(k.default() is True for k in result["data_schema"].schema)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "server_started": True,
            "server_stopped": False,
            "server_crashed": False,
            "player_joined": True,
            "player_left": False,
        },
    )
    await hass.async_block_till_done()
    assert token_entry.options[CONF_EVENTS] == [EVENT_SERVER_STARTED, EVENT_PLAYER_JOINED]
    assert set(token_entry.runtime_data.options.events) == {
        EVENT_SERVER_STARTED,
        EVENT_PLAYER_JOINED,
    }
    assert len(ALL_EVENTS) == 5


async def test_advanced(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    import logging

    await _setup(hass, token_entry)
    assert hass.states.get("sensor.survival_minecraft_version") is not None
    crafty.get(f"{BASE}/metrics/host", text="CPU_Usage 1\nMem_Usage 2\n")
    result = await _open(hass, token_entry, "advanced")
    assert all(
        k.default() is (str(k) == CONF_ENABLE_DIAGNOSTIC_ENTITIES)
        for k in result["data_schema"].schema
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENABLE_TASKS: True,
            CONF_ENABLE_WEBHOOKS: True,
            CONF_ENABLE_LOGS: True,
            CONF_ENABLE_HOST_METRICS: True,
            CONF_ENABLE_DIAGNOSTIC_ENTITIES: False,
            CONF_DEBUG_LOGGING: True,
        },
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.survival_minecraft_version") is None
    assert hass.states.get("sensor.crafty_crafty_test_host_cpu_usage").state == "1.0"
    logger = logging.getLogger("custom_components.crafty_controller")
    assert logger.level == logging.DEBUG

    result = await _open(hass, token_entry, "advanced")
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ENABLE_TASKS: False,
            CONF_ENABLE_WEBHOOKS: False,
            CONF_ENABLE_LOGS: False,
            CONF_ENABLE_HOST_METRICS: False,
            CONF_ENABLE_DIAGNOSTIC_ENTITIES: True,
            CONF_DEBUG_LOGGING: False,
        },
    )
    await hass.async_block_till_done()
    assert logger.level != logging.DEBUG
    assert hass.states.get("sensor.survival_minecraft_version") is not None
    assert hass.states.get("sensor.crafty_crafty_test_host_cpu_usage") is None


async def test_server_select_without_servers(
    hass: HomeAssistant, crafty: AiohttpClientMocker
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="crafty.test:8443",
        data={
            "host": "crafty.test",
            "port": 8443,
            "verify_ssl": False,
            "auth_method": "token",
            "token": "t",
        },
        options={CONF_SERVERS: []},
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    result = await _open(hass, entry, "server_select")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_servers"
