"""Tests for services and their safety gates."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.crafty_controller.const import (
    CONF_ALLOW_CONSOLE,
    CONF_ALLOW_CONSOLE_SERVER,
    CONF_ALLOW_STOP_RESTART,
    CONF_ENABLE_LOGS,
    CONF_ENABLE_TASKS,
    CONF_ENABLE_WEBHOOKS,
    CONF_SERVER_SETTINGS,
    DOMAIN,
)
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .conftest import BASE, SERVER_A, SERVER_B, calls_to

STDIN_A = f"/api/v2/servers/{SERVER_A}/stdin"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, **options: Any) -> None:
    if options:
        hass.config_entries.async_update_entry(entry, options={**entry.options, **options})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_send_command_strips_slash(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    crafty.post(f"{BASE}{STDIN_A}", json={"status": "ok"})
    response = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {ATTR_ENTITY_ID: "sensor.survival_cpu_usage", "command": "/say Hello"},
        blocking=True,
        return_response=True,
    )
    calls = calls_to(crafty, "post", STDIN_A)
    assert len(calls) == 1
    assert calls[0][2] == "say Hello"
    assert response == {"servers": {SERVER_A: "say Hello"}}


async def test_send_command_by_device(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    await _setup(hass, token_entry)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, SERVER_B), token_entry.entry_id
    )
    path = f"/api/v2/servers/{SERVER_B}/stdin"
    crafty.post(f"{BASE}{path}", json={"status": "ok"})
    await hass.services.async_call(
        DOMAIN, "send_command", {ATTR_DEVICE_ID: device.id, "command": "list"}, blocking=True
    )
    assert calls_to(crafty, "post", path)[0][2] == "list"
    assert not calls_to(crafty, "post", STDIN_A)


async def test_send_command_server_not_running(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    crafty.post(f"{BASE}{STDIN_A}", json={"status": "error", "error": "SERVER_NOT_RUNNING"})
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "send_command",
            {ATTR_ENTITY_ID: "sensor.survival_cpu_usage", "command": "list"},
            blocking=True,
        )
    assert err.value.translation_key == "request_failed"


@pytest.mark.parametrize(
    "options",
    [
        {CONF_ALLOW_CONSOLE: False},
        {CONF_SERVER_SETTINGS: {SERVER_A: {CONF_ALLOW_CONSOLE_SERVER: False}}},
    ],
    ids=["global", "per_server"],
)
async def test_send_command_blocked(
    hass: HomeAssistant,
    crafty: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    options: dict[str, Any],
) -> None:
    await _setup(hass, token_entry, **options)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "send_command",
            {ATTR_ENTITY_ID: "sensor.survival_cpu_usage", "command": "op Steve"},
            blocking=True,
        )
    assert err.value.translation_key == "console_blocked"
    assert not calls_to(crafty, "post", STDIN_A)


async def test_no_target(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "send_command",
            {ATTR_ENTITY_ID: "sensor.does_not_exist", "command": "list"},
            blocking=True,
        )


async def test_get_logs_feature_gate(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "get_logs",
            {ATTR_ENTITY_ID: "sensor.survival_cpu_usage"},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "feature_disabled"


async def test_get_logs(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry, **{CONF_ENABLE_LOGS: True})
    crafty.get(
        f"{BASE}/api/v2/servers/{SERVER_A}/logs",
        json={"status": "ok", "data": [f"line {i}" for i in range(10)]},
    )
    response = await hass.services.async_call(
        DOMAIN,
        "get_logs",
        {ATTR_ENTITY_ID: "sensor.survival_cpu_usage", "lines": 3},
        blocking=True,
        return_response=True,
    )
    assert response == {
        "servers": {SERVER_A: {"server_name": "Survival", "lines": ["line 7", "line 8", "line 9"]}}
    }
    query = calls_to(crafty, "get", f"/api/v2/servers/{SERVER_A}/logs")[0][1].query
    assert query["file"] == "false"


async def test_tasks(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry, **{CONF_ENABLE_TASKS: True})
    crafty.post(
        f"{BASE}/api/v2/servers/{SERVER_A}/tasks",
        json={"status": "ok", "data": {"schedule_id": "4"}},
    )
    crafty.patch(f"{BASE}/api/v2/servers/{SERVER_A}/tasks/4", json={"status": "ok"})
    crafty.delete(f"{BASE}/api/v2/servers/{SERVER_A}/tasks/4", json={"status": "ok"})
    target = {ATTR_ENTITY_ID: "sensor.survival_cpu_usage"}
    response = await hass.services.async_call(
        DOMAIN,
        "create_task",
        {
            **target,
            "name": "Hello",
            "action": "command",
            "command": "/say hi",
            "interval": 5,
            "interval_type": "minutes",
        },
        blocking=True,
        return_response=True,
    )
    assert response == {"servers": {SERVER_A: {"task_id": "4"}}}
    body = calls_to(crafty, "post", f"/api/v2/servers/{SERVER_A}/tasks")[0][2]
    assert body == {
        "name": "Hello",
        "enabled": True,
        "action": "command",
        "interval": 5,
        "interval_type": "minutes",
        "command": "say hi",
        "one_time": False,
        "cron_string": "",
        "delay": 0,
    }
    await hass.services.async_call(
        DOMAIN, "set_task_enabled", {**target, "task_id": 4, "enabled": False}, blocking=True
    )
    assert calls_to(crafty, "patch", f"/api/v2/servers/{SERVER_A}/tasks/4")[0][2] == {
        "enabled": False
    }
    await hass.services.async_call(DOMAIN, "delete_task", {**target, "task_id": 4}, blocking=True)
    assert calls_to(crafty, "delete", f"/api/v2/servers/{SERVER_A}/tasks/4")


async def test_task_cron_and_gates(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry, **{CONF_ENABLE_TASKS: True, CONF_ALLOW_STOP_RESTART: False})
    target = {ATTR_ENTITY_ID: "sensor.survival_cpu_usage"}
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "create_task",
            {**target, "name": "x", "action": "restart_server"},
            blocking=True,
        )
    assert err.value.translation_key == "stop_restart_blocked"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "create_task", {**target, "name": "x", "action": "kill_server"}, blocking=True
        )
    assert err.value.translation_key == "kill_blocked"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "create_task",
            {**target, "name": "x", "action": "backup_server", "interval_type": "cron"},
            blocking=True,
        )
    assert err.value.translation_key == "cron_required"
    crafty.post(
        f"{BASE}/api/v2/servers/{SERVER_A}/tasks", json={"status": "ok", "data": {"schedule_id": 9}}
    )
    await hass.services.async_call(
        DOMAIN,
        "create_task",
        {
            **target,
            "name": "Backup",
            "action": "backup_server",
            "interval_type": "cron",
            "cron_string": "0 4 * * *",
        },
        blocking=True,
    )
    body = calls_to(crafty, "post", f"/api/v2/servers/{SERVER_A}/tasks")[0][2]
    assert body["interval_type"] == ""
    assert body["cron_string"] == "0 4 * * *"
    assert body["command"] == "backup_server"


async def test_tasks_feature_gate(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "delete_task",
            {ATTR_ENTITY_ID: "sensor.survival_cpu_usage", "task_id": 1},
            blocking=True,
        )


async def test_webhooks(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await _setup(hass, token_entry, **{CONF_ENABLE_WEBHOOKS: True})
    hook = {
        "webhook_type": "Discord",
        "name": "Alerts",
        "url": "https://discord.test/x",
        "trigger": ["start_server"],
    }
    crafty.get(
        f"{BASE}/api/v2/servers/{SERVER_A}/webhook", json={"status": "ok", "data": {"1": hook}}
    )
    crafty.post(
        f"{BASE}/api/v2/servers/{SERVER_A}/webhook",
        json={"status": "ok", "data": {"webhook_id": 2}},
    )
    crafty.delete(f"{BASE}/api/v2/servers/{SERVER_A}/webhook/2", json={"status": "ok"})
    target = {ATTR_ENTITY_ID: "sensor.survival_cpu_usage"}
    listed = await hass.services.async_call(
        DOMAIN, "list_webhooks", target, blocking=True, return_response=True
    )
    assert listed == {"servers": {SERVER_A: {"server_name": "Survival", "webhooks": {"1": hook}}}}
    created = await hass.services.async_call(
        DOMAIN,
        "create_webhook",
        {
            **target,
            "name": "Alerts",
            "webhook_type": "Discord",
            "url": "https://discord.test/x",
            "triggers": ["start_server", "crash_detected"],
        },
        blocking=True,
        return_response=True,
    )
    assert created == {"servers": {SERVER_A: {"webhook_id": "2"}}}
    body = calls_to(crafty, "post", f"/api/v2/servers/{SERVER_A}/webhook")[0][2]
    assert body["trigger"] == ["start_server", "crash_detected"]
    assert json.dumps(body)  # serializable
    await hass.services.async_call(
        DOMAIN, "delete_webhook", {**target, "webhook_id": 2}, blocking=True
    )
    assert calls_to(crafty, "delete", f"/api/v2/servers/{SERVER_A}/webhook/2")
