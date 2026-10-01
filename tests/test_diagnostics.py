"""Tests for diagnostics and device removal."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.crafty_controller import async_remove_config_entry_device
from custom_components.crafty_controller.const import CONF_SERVERS, DOMAIN
from custom_components.crafty_controller.diagnostics import async_get_config_entry_diagnostics
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import LOGIN_TOKEN, SERVER_A, SERVER_B, TOKEN


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant, crafty: AiohttpClientMocker, credentials_entry: MockConfigEntry
) -> None:
    await hass.config_entries.async_setup(credentials_entry.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, credentials_entry)
    dumped = json.dumps(diag, default=str)
    assert "hunter2-secret" not in dumped
    assert LOGIN_TOKEN not in dumped
    assert TOKEN not in dumped
    assert diag["entry"]["data"]["password"] == "**REDACTED**"
    assert diag["entry"]["data"]["username"] == "**REDACTED**"
    assert diag["servers"][SERVER_A]["stats"]["running"] is True


async def test_remove_device_only_when_unmonitored(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    await hass.config_entries.async_setup(token_entry.entry_id)
    await hass.async_block_till_done()
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, SERVER_B), token_entry.entry_id
    )
    assert not await async_remove_config_entry_device(hass, token_entry, device)
    hass.config_entries.async_update_entry(token_entry, options={CONF_SERVERS: [SERVER_A]})
    assert await async_remove_config_entry_device(hass, token_entry, device)
