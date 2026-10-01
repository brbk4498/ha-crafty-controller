"""Tests for the API client and parsers."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.crafty_controller.api import (
    CraftyApiError,
    CraftyAuthError,
    CraftyClient,
    CraftyConnectionError,
    CraftyPermissionError,
    normalize_command,
    parse_players,
    parse_prometheus,
    parse_server_stats,
    parse_size,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import (
    BASE,
    HOST,
    LOGIN_TOKEN,
    PORT,
    SERVER_A,
    stats_fields,
    stats_flat,
    stats_nested,
)


@pytest.mark.parametrize("payload", [stats_nested(), stats_flat()], ids=["nested", "flat"])
def test_parse_stats_both_shapes(payload: dict[str, Any]) -> None:
    stats = parse_server_stats(payload)
    assert stats.running is True
    assert stats.crashed is False
    assert stats.cpu == 12.5
    assert stats.mem_bytes == 1073741824.0
    assert stats.mem_percent == 25.0
    assert stats.world_size_bytes == 512 * 1024 * 1024
    assert stats.online == 2
    assert stats.max_players == 20
    assert stats.players == ["Steve", "Alex"]
    assert stats.motd == "A Minecraft Server"
    assert stats.version == "Paper 1.21.4"


def test_parse_stats_data_wins_over_top_level() -> None:
    payload = {"status": "ok", "running": False, "data": stats_fields(running=True)}
    assert parse_server_stats(payload).running is True


def test_parse_stats_falls_back_when_data_lacks_key() -> None:
    payload = {"status": "ok", "data": {"server_id": {"server_id": SERVER_A}}, "online": 3}
    assert parse_server_stats(payload).online == 3


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "nonsense",
        [],
        {"data": "string"},
        {"data": stats_fields(cpu="abc", mem="lots", players="{{", online="x", running="maybe")},
        {
            "data": stats_fields(
                cpu=-1, mem=-1, mem_percent=-1, players=False, desc=False, version=False
            )
        },
    ],
)
def test_parse_stats_never_raises(payload: Any) -> None:
    stats = parse_server_stats(payload)
    assert stats.cpu is None or isinstance(stats.cpu, float)


def test_parse_stats_odd_values() -> None:
    stats = parse_server_stats(
        {
            "data": stats_fields(
                cpu=-1, mem=-1, players=False, desc=False, version=False, running="False"
            )
        }
    )
    assert stats.cpu is None
    assert stats.mem_bytes is None
    assert stats.players is None
    assert stats.motd is None
    assert stats.version is None
    assert stats.running is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("42.2MB", 42.2 * 1024**2),
        (" 1.0KB", 1024.0),
        ("512.0B", 512.0),
        ("1.5 GiB", 1.5 * 1024**3),
        ("2GB", 2 * 1024**3),
        (123456, 123456.0),
        ("100", 100.0),
        ("", None),
        ("lots", None),
        (None, None),
        (False, None),
        (-1, None),
    ],
)
def test_parse_size(value: Any, expected: float | None) -> None:
    result = parse_size(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("[]", []),
        ("['Steve', 'Alex']", ["Steve", "Alex"]),
        ('["Steve"]', ["Steve"]),
        (["Notch"], ["Notch"]),
        ('{"players": ["A"]}', ["A"]),
        ("Steve, Alex", ["Steve", "Alex"]),
        ("", []),
        (False, None),
        (None, None),
        (5, None),
    ],
)
def test_parse_players(value: Any, expected: list[str] | None) -> None:
    assert parse_players(value) == expected


def test_parse_prometheus() -> None:
    text = (
        "# HELP CPU_Usage The CPU usage of the server\n"
        "# TYPE CPU_Usage gauge\n"
        "CPU_Usage 1.54\n"
        'Mem_Usage{server_id="x"} 63.8\n'
        "garbage line\n"
    )
    assert parse_prometheus(text) == {"CPU_Usage": 1.54, "Mem_Usage": 63.8}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("/say hi", "say hi"), ("say hi", "say hi"), ("  //op Steve ", "op Steve")],
)
def test_normalize_command(raw: str, expected: str) -> None:
    assert normalize_command(raw) == expected


def _client(hass: HomeAssistant, **kwargs: Any) -> CraftyClient:
    return CraftyClient(async_get_clientsession(hass), HOST, PORT, **kwargs)


async def test_relogin_on_403_and_retry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    attempts: list[str | None] = []

    async def stats_side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        auth = aioclient_mock.mock_calls[-1][3].get("Authorization")
        attempts.append(auth)
        if auth == f"Bearer {LOGIN_TOKEN}" and len(attempts) > 1:
            return AiohttpClientMockResponse(method, url, json=stats_nested())
        return AiohttpClientMockResponse(
            method,
            url,
            status=HTTPStatus.FORBIDDEN,
            json={"status": "error", "error": "ACCESS_DENIED"},
        )

    aioclient_mock.post(
        f"{BASE}/api/v2/auth/login", json={"status": "ok", "data": {"token": LOGIN_TOKEN}}
    )
    aioclient_mock.get(f"{BASE}/api/v2/servers/{SERVER_A}/stats", side_effect=stats_side_effect)
    client = _client(hass, username="admin", password="pw")
    stats = await client.async_get_server_stats(SERVER_A)
    assert stats.running is True
    assert len(attempts) == 2


async def test_no_relogin_with_token_only(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{BASE}/api/v2/servers/{SERVER_A}/stats",
        status=HTTPStatus.FORBIDDEN,
        json={"status": "error", "error": "ACCESS_DENIED"},
    )
    with pytest.raises(CraftyAuthError):
        await _client(hass, token="t").async_get_server_stats(SERVER_A)


@pytest.mark.parametrize(
    "status", [HTTPStatus.BAD_REQUEST, HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN]
)
async def test_login_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: HTTPStatus
) -> None:
    aioclient_mock.post(
        f"{BASE}/api/v2/auth/login",
        status=status,
        json={"status": "error", "error": "INCORRECT_CREDENTIALS"},
    )
    with pytest.raises(CraftyAuthError):
        await _client(hass, username="a", password="b").async_login()


async def test_connection_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/api/v2/crafty/check", exc=TimeoutError())
    with pytest.raises(CraftyConnectionError):
        await _client(hass, token="t").async_check()


async def test_not_authorized_is_permission_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{BASE}/api/v2/servers/{SERVER_A}/stats",
        status=HTTPStatus.BAD_REQUEST,
        json={"status": "error", "error": "NOT_AUTHORIZED"},
    )
    with pytest.raises(CraftyPermissionError):
        await _client(hass, token="t").async_get_server_stats(SERVER_A)


async def test_stdin_server_not_running(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        f"{BASE}/api/v2/servers/{SERVER_A}/stdin",
        json={"status": "error", "error": "SERVER_NOT_RUNNING", "error_data": "SERVER NOT RUNNING"},
    )
    with pytest.raises(CraftyApiError) as err:
        await _client(hass, token="t").async_send_command(SERVER_A, "/say hi")
    assert err.value.code == "SERVER_NOT_RUNNING"
    assert aioclient_mock.mock_calls[-1][2] == "say hi"
    assert aioclient_mock.mock_calls[-1][3]["Content-Type"] == "text/plain"


async def test_webhook_delete_uses_plural_path(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.delete(f"{BASE}/api/v2/servers/{SERVER_A}/webhook/7", json={"status": "ok"})
    await _client(hass, token="t").async_delete_webhook(SERVER_A, 7)
    assert aioclient_mock.call_count == 1


async def test_host_metrics(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/metrics/host", text="CPU_Usage 1.5\nMem_Usage 60\n")
    assert await _client(hass, token="t").async_get_host_metrics() == {
        "CPU_Usage": 1.5,
        "Mem_Usage": 60.0,
    }
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{BASE}/metrics/host", status=HTTPStatus.BAD_REQUEST, json={"status": "error"}
    )
    with pytest.raises(CraftyPermissionError):
        await _client(hass, token="t").async_get_host_metrics()


def test_base_url_strips_scheme() -> None:
    client = CraftyClient(None, "https://crafty.lan/", 8443)
    assert client.base_url == "https://crafty.lan:8443"
