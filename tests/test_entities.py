"""Tests for entity state mapping, devices and buttons."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.crafty_controller.const import (
    ACTION_REFRESH_DELAY,
    CONF_ALLOW_KILL,
    CONF_ALLOW_STOP_RESTART,
    DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import BASE, SERVER_A, SERVER_B, calls_to, mock_crafty, stats_nested

BUTTON_DOMAIN = "button"
SERVICE_PRESS = "press"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_state_mapping(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    assert hass.states.get("binary_sensor.survival_running").state == "on"
    assert hass.states.get("binary_sensor.survival_crashed").state == "off"
    assert hass.states.get("binary_sensor.survival_updating").state == "off"
    assert hass.states.get("sensor.survival_players_online").state == "2"
    assert hass.states.get("sensor.survival_max_players").state == "20"
    names = hass.states.get("sensor.survival_player_names")
    assert names.state == "2"
    assert names.attributes["players"] == ["Steve", "Alex"]
    assert names.attributes["player_names"] == "Steve, Alex"
    assert hass.states.get("sensor.survival_cpu_usage").state == "12.5"
    assert hass.states.get("sensor.survival_memory_usage").state == "25.0"
    memory = hass.states.get("sensor.survival_memory_used")
    assert float(memory.state) == pytest.approx(1024.0)
    assert memory.attributes["unit_of_measurement"] == "MiB"
    assert float(hass.states.get("sensor.survival_world_size").state) == pytest.approx(512.0)
    assert hass.states.get("sensor.survival_minecraft_version").state == "Paper 1.21.4"
    assert hass.states.get("sensor.survival_motd").state == "A Minecraft Server"
    assert hass.states.get("button.survival_start").state == "unknown"


async def test_stopped_server_has_zero_players(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    mock_crafty(
        aioclient_mock,
        stats={
            SERVER_A: stats_nested(running=False, online=False, players=False, cpu=0, mem=0),
            SERVER_B: stats_nested(),
        },
    )
    await _setup(hass, token_entry)
    assert hass.states.get("binary_sensor.survival_running").state == "off"
    assert hass.states.get("sensor.survival_players_online").state == "0"
    assert hass.states.get("sensor.survival_player_names").attributes["players"] == []


def test_player_names_capped() -> None:
    from custom_components.crafty_controller.sensor import player_names_string

    result = player_names_string([f"Player{i:03d}" for i in range(100)])
    assert len(result) <= 255
    assert result.endswith("…")


async def test_device_info(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    await _setup(hass, token_entry)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, SERVER_A), token_entry.entry_id
    )
    assert device is not None
    assert device.name == "Survival"
    assert device.manufacturer == "Arcadia Technology"
    assert device.model == "Crafty Controller"
    assert device.sw_version == "Paper 1.21.4"
    assert device.configuration_url == f"{BASE}/panel/server_detail?id={SERVER_A}"


async def test_unique_ids(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    await _setup(hass, token_entry)
    assert entity_registry.async_get("sensor.survival_cpu_usage").unique_id == f"{SERVER_A}_cpu"
    assert entity_registry.async_get("button.creative_backup").unique_id == f"{SERVER_B}_backup"


@pytest.mark.parametrize(
    ("entity_id", "action"),
    [
        ("button.survival_start", "start_server"),
        ("button.survival_stop", "stop_server"),
        ("button.survival_restart", "restart_server"),
        ("button.survival_backup", "backup_server"),
    ],
)
async def test_buttons_call_action(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    entity_id: str,
    action: str,
) -> None:
    await _setup(hass, token_entry)
    path = f"/api/v2/servers/{SERVER_A}/action/{action}"
    crafty.post(f"{BASE}{path}", json={"status": "ok"})
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    assert len(calls_to(crafty, "post", path)) == 1
    # Refresh happens after a short delay and fetches that server again
    before = len(calls_to(crafty, "get", f"/api/v2/servers/{SERVER_A}/stats"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=ACTION_REFRESH_DELAY + 1))
    await hass.async_block_till_done()
    assert len(calls_to(crafty, "get", f"/api/v2/servers/{SERVER_A}/stats")) == before + 1


async def test_kill_blocked_by_default(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    entity_registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{SERVER_A}_kill",
        suggested_object_id="survival_kill",
        config_entry=token_entry,
    )
    await _setup(hass, token_entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.survival_kill"}, blocking=True
        )
    assert err.value.translation_key == "kill_blocked"
    assert not calls_to(crafty, "post", f"/api/v2/servers/{SERVER_A}/action/kill_server")


async def test_kill_allowed(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    hass.config_entries.async_update_entry(
        token_entry, options={**token_entry.options, CONF_ALLOW_KILL: True}
    )
    entity_registry.async_get_or_create(
        "button",
        DOMAIN,
        f"{SERVER_A}_kill",
        suggested_object_id="survival_kill",
        config_entry=token_entry,
    )
    await _setup(hass, token_entry)
    path = f"/api/v2/servers/{SERVER_A}/action/kill_server"
    crafty.post(f"{BASE}{path}", json={"status": "ok"})
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.survival_kill"}, blocking=True
    )
    assert len(calls_to(crafty, "post", path)) == 1


async def test_stop_blocked(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        token_entry, options={**token_entry.options, CONF_ALLOW_STOP_RESTART: False}
    )
    await _setup(hass, token_entry)
    for entity_id in ("button.survival_stop", "button.survival_restart"):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
            )
        assert err.value.translation_key == "stop_restart_blocked"
    # Start is never gated
    crafty.post(f"{BASE}/api/v2/servers/{SERVER_A}/action/start_server", json={"status": "ok"})
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.survival_start"}, blocking=True
    )


async def test_unavailable_server_buttons(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    mock_crafty(aioclient_mock, stats={SERVER_A: stats_nested()})
    aioclient_mock.get(f"{BASE}/api/v2/servers/{SERVER_B}/stats", exc=TimeoutError())
    await _setup(hass, token_entry)
    assert hass.states.get("button.creative_start").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.creative_players_online").state == STATE_UNAVAILABLE
