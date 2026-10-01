"""Tests for setup, the coordinator, repairs and events."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.crafty_controller.const import (
    CONF_ENABLE_HOST_METRICS,
    CONF_EVENTS,
    CONF_SERVER_SCAN_INTERVAL,
    CONF_SERVER_SETTINGS,
    CONF_SERVERS,
    DISCOVERY_EVERY_N_UPDATES,
    DOMAIN,
    EVENT_PLAYER_JOINED,
    EVENT_PLAYER_LEFT,
    EVENT_SERVER_CRASHED,
    EVENT_SERVER_STARTED,
    EVENT_SERVER_STOPPED,
)
from custom_components.crafty_controller.coordinator import (
    CraftyCoordinator,
    issue_id_new_servers,
    issue_id_not_found,
    issue_id_unreachable,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .conftest import (
    BASE,
    LOGIN_TOKEN,
    SERVER_A,
    SERVER_B,
    SERVER_NEW,
    calls_to,
    mock_crafty,
    server_item,
    stats_nested,
)

STATS_A = f"/api/v2/servers/{SERVER_A}/stats"
STATS_B = f"/api/v2/servers/{SERVER_B}/stats"


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> CraftyCoordinator | None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return getattr(entry, "runtime_data", None)


async def refresh_all(coordinator: CraftyCoordinator) -> None:
    for server_id in coordinator.options.servers:
        coordinator.force_refresh_server(server_id)
    await coordinator.async_refresh()
    await coordinator.hass.async_block_till_done()


def set_stats(
    aioclient_mock: AiohttpClientMocker, stats: dict[str, Any], servers: list | None = None
) -> None:
    aioclient_mock.clear_requests()
    mock_crafty(aioclient_mock, stats=stats, servers=servers)


async def test_setup_both_shapes(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    """Server A answers nested stats, server B flat stats; both map the same."""
    await setup_entry(hass, token_entry)
    assert token_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.survival_running").state == "on"
    assert hass.states.get("binary_sensor.creative_running").state == "on"
    assert hass.states.get("sensor.survival_players_online").state == "2"
    assert hass.states.get("sensor.creative_players_online").state == "2"


async def test_one_failing_server(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    mock_crafty(aioclient_mock, stats={SERVER_A: stats_nested()})
    aioclient_mock.get(f"{BASE}{STATS_B}", exc=TimeoutError())
    await setup_entry(hass, token_entry)
    assert token_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.survival_running").state == "on"
    assert hass.states.get("binary_sensor.creative_running").state == STATE_UNAVAILABLE
    assert hass.states.get("button.creative_start").state == STATE_UNAVAILABLE


async def test_all_servers_unreachable_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{BASE}/api/v2/servers", exc=TimeoutError())
    await setup_entry(hass, token_entry)
    assert token_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unreachable_after_setup_and_repair_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    mock_crafty(aioclient_mock)
    coordinator = await setup_entry(hass, token_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}{STATS_A}", exc=TimeoutError())
    aioclient_mock.get(f"{BASE}{STATS_B}", exc=TimeoutError())
    await refresh_all(coordinator)
    assert not coordinator.last_update_success
    assert hass.states.get("sensor.survival_cpu_usage").state == STATE_UNAVAILABLE
    # Not long enough yet for an issue
    assert (
        issue_registry.async_get_issue(DOMAIN, issue_id_unreachable(token_entry.entry_id)) is None
    )
    coordinator._unreachable_since = (coordinator._unreachable_since or 0) - 3600
    await refresh_all(coordinator)
    assert issue_registry.async_get_issue(DOMAIN, issue_id_unreachable(token_entry.entry_id))
    # Recovery clears the issue and the entities come back
    set_stats(aioclient_mock, {SERVER_A: stats_nested(), SERVER_B: stats_nested()})
    await refresh_all(coordinator)
    assert (
        issue_registry.async_get_issue(DOMAIN, issue_id_unreachable(token_entry.entry_id)) is None
    )
    assert hass.states.get("sensor.survival_cpu_usage").state == "12.5"


async def test_403_then_relogin(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, credentials_entry: MockConfigEntry
) -> None:
    mock_crafty(aioclient_mock, servers=[server_item(SERVER_A, "Survival")], stats={})
    expired = {"value": True}

    async def stats_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        if expired["value"]:
            expired["value"] = False
            return AiohttpClientMockResponse(
                method,
                url,
                status=HTTPStatus.FORBIDDEN,
                json={"status": "error", "error": "ACCESS_DENIED"},
            )
        return AiohttpClientMockResponse(method, url, json=stats_nested())

    aioclient_mock.get(f"{BASE}{STATS_A}", side_effect=stats_effect)
    await setup_entry(hass, credentials_entry)
    assert credentials_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.survival_running").state == "on"
    # Initial login plus one re-login after the 403
    assert len(calls_to(aioclient_mock, "post", "/api/v2/auth/login")) == 2
    last_stats_call = calls_to(aioclient_mock, "get", STATS_A)[-1]
    assert last_stats_call[3]["Authorization"] == f"Bearer {LOGIN_TOKEN}"


async def test_persistent_403_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, credentials_entry: MockConfigEntry
) -> None:
    mock_crafty(aioclient_mock, servers=[server_item(SERVER_A, "Survival")], stats={})
    aioclient_mock.get(
        f"{BASE}{STATS_A}",
        status=HTTPStatus.FORBIDDEN,
        json={"status": "error", "error": "ACCESS_DENIED"},
    )
    await setup_entry(hass, credentials_entry)
    assert credentials_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_server_removed_in_crafty(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    mock_crafty(
        aioclient_mock,
        servers=[server_item(SERVER_A, "Survival")],
        stats={SERVER_A: stats_nested()},
    )
    aioclient_mock.get(
        f"{BASE}{STATS_B}",
        status=HTTPStatus.BAD_REQUEST,
        json={"status": "error", "error": "NOT_AUTHORIZED"},
    )
    await setup_entry(hass, token_entry)
    assert token_entry.state is ConfigEntryState.LOADED
    assert issue_registry.async_get_issue(
        DOMAIN, issue_id_not_found(token_entry.entry_id, SERVER_B)
    )
    assert hass.states.get("binary_sensor.survival_running").state == "on"


async def test_new_server_discovered_not_added(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    mock_crafty(aioclient_mock)
    coordinator = await setup_entry(hass, token_entry)
    assert (
        issue_registry.async_get_issue(DOMAIN, issue_id_new_servers(token_entry.entry_id)) is None
    )
    servers = [
        server_item(SERVER_A, "Survival"),
        server_item(SERVER_B, "Creative"),
        server_item(SERVER_NEW, "Skyblock"),
    ]
    set_stats(aioclient_mock, {SERVER_A: stats_nested(), SERVER_B: stats_nested()}, servers=servers)
    for _ in range(DISCOVERY_EVERY_N_UPDATES):
        await refresh_all(coordinator)
    issue = issue_registry.async_get_issue(DOMAIN, issue_id_new_servers(token_entry.entry_id))
    assert issue is not None
    assert "Skyblock" in issue.translation_placeholders["servers"]
    assert SERVER_NEW not in coordinator.data.servers
    assert not calls_to(aioclient_mock, "get", f"/api/v2/servers/{SERVER_NEW}/stats")


async def test_per_server_interval(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="x:1",
        data={
            "host": "crafty.test",
            "port": 8443,
            "verify_ssl": False,
            "auth_method": "token",
            "token": "t",
        },
        options={
            CONF_SERVERS: [SERVER_A, SERVER_B],
            CONF_SERVER_SETTINGS: {SERVER_B: {CONF_SERVER_SCAN_INTERVAL: 10}},
        },
    )
    entry.add_to_hass(hass)
    mock_crafty(aioclient_mock)
    coordinator = await setup_entry(hass, entry)
    assert coordinator.update_interval.total_seconds() == 10
    assert coordinator.options.interval_for(SERVER_A) == 30
    # Pretend 15 seconds passed: B is due, A is not
    for state in coordinator.data.servers.values():
        state.last_fetch -= 15
    aioclient_mock.mock_calls.clear()
    await coordinator.async_refresh()
    assert calls_to(aioclient_mock, "get", STATS_B)
    assert not calls_to(aioclient_mock, "get", STATS_A)
    # A keeps its last good value meanwhile
    assert hass.states.get("binary_sensor.survival_running").state == "on"


async def test_host_metrics_400_disables(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Crafty",
        unique_id="x:1",
        data={
            "host": "crafty.test",
            "port": 8443,
            "verify_ssl": False,
            "auth_method": "token",
            "token": "t",
        },
        options={CONF_SERVERS: [SERVER_A], CONF_ENABLE_HOST_METRICS: True},
    )
    entry.add_to_hass(hass)
    mock_crafty(aioclient_mock, stats={SERVER_A: stats_nested()})
    aioclient_mock.get(
        f"{BASE}/metrics/host", status=HTTPStatus.BAD_REQUEST, json={"status": "error"}
    )
    coordinator = await setup_entry(hass, entry)
    assert coordinator.host_metrics_supported is False
    await refresh_all(coordinator)
    assert len(calls_to(aioclient_mock, "get", "/metrics/host")) == 1
    assert caplog.text.count("lacks global access") == 1
    assert hass.states.get("sensor.crafty_host_cpu_usage").state == STATE_UNAVAILABLE


async def test_host_metrics_ok(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Crafty",
        unique_id="x:1",
        data={
            "host": "crafty.test",
            "port": 8443,
            "verify_ssl": False,
            "auth_method": "token",
            "token": "t",
        },
        options={CONF_SERVERS: [SERVER_A], CONF_ENABLE_HOST_METRICS: True},
    )
    entry.add_to_hass(hass)
    mock_crafty(aioclient_mock, stats={SERVER_A: stats_nested()})
    aioclient_mock.get(f"{BASE}/metrics/host", text="CPU_Usage 4.5\nMem_Usage 61.2\n")
    await setup_entry(hass, entry)
    assert hass.states.get("sensor.crafty_host_cpu_usage").state == "4.5"
    assert hass.states.get("sensor.crafty_host_memory_usage").state == "61.2"


async def test_events_fired_on_diffs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    mock_crafty(
        aioclient_mock,
        stats={
            SERVER_A: stats_nested(),
            SERVER_B: stats_nested(running=False, players="[]", online=0),
        },
    )
    coordinator = await setup_entry(hass, token_entry)
    events = {
        name: async_capture_events(hass, name)
        for name in (
            EVENT_SERVER_STARTED,
            EVENT_SERVER_STOPPED,
            EVENT_SERVER_CRASHED,
            EVENT_PLAYER_JOINED,
            EVENT_PLAYER_LEFT,
        )
    }
    set_stats(
        aioclient_mock,
        {
            SERVER_A: stats_nested(players="['Steve', 'Herobrine']", crashed=True),
            SERVER_B: stats_nested(players="['Alex']"),
        },
    )
    await refresh_all(coordinator)
    assert [e.data["server_id"] for e in events[EVENT_SERVER_STARTED]] == [SERVER_B]
    assert events[EVENT_SERVER_STARTED][0].data["server_name"] == "Creative"
    assert [e.data["server_id"] for e in events[EVENT_SERVER_CRASHED]] == [SERVER_A]
    joined = {(e.data["server_id"], e.data["player"]) for e in events[EVENT_PLAYER_JOINED]}
    assert joined == {(SERVER_A, "Herobrine"), (SERVER_B, "Alex")}
    assert [(e.data["server_id"], e.data["player"]) for e in events[EVENT_PLAYER_LEFT]] == [
        (SERVER_A, "Alex")
    ]
    assert not events[EVENT_SERVER_STOPPED]

    set_stats(
        aioclient_mock,
        {
            SERVER_A: stats_nested(running=False, players=False),
            SERVER_B: stats_nested(players="['Alex']"),
        },
    )
    await refresh_all(coordinator)
    assert [e.data["server_id"] for e in events[EVENT_SERVER_STOPPED]] == [SERVER_A]
    left = {e.data["player"] for e in events[EVENT_PLAYER_LEFT] if e.data["server_id"] == SERVER_A}
    assert left == {"Alex", "Steve", "Herobrine"}


async def test_events_respect_options(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        token_entry, options={**token_entry.options, CONF_EVENTS: [EVENT_SERVER_STOPPED]}
    )
    mock_crafty(aioclient_mock)
    coordinator = await setup_entry(hass, token_entry)
    joined = async_capture_events(hass, EVENT_PLAYER_JOINED)
    stopped = async_capture_events(hass, EVENT_SERVER_STOPPED)
    set_stats(
        aioclient_mock,
        {
            SERVER_A: stats_nested(players="['Steve','Alex','Bob']"),
            SERVER_B: stats_nested(running=False),
        },
    )
    await refresh_all(coordinator)
    assert not joined
    assert len(stopped) == 1


async def test_unload(
    hass: HomeAssistant, crafty: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, token_entry)
    assert await hass.config_entries.async_unload(token_entry.entry_id)
    assert token_entry.state is ConfigEntryState.NOT_LOADED
