"""Shared fixtures for Crafty Controller tests."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.crafty_controller.const import (
    ALL_SERVER_ENTITY_KEYS,
    AUTH_METHOD_CREDENTIALS,
    AUTH_METHOD_TOKEN,
    CONF_AUTH_METHOD,
    CONF_ENABLED_ENTITIES,
    CONF_SERVER_SETTINGS,
    CONF_SERVERS,
    CONF_TOKEN,
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

HOST = "crafty.test"
PORT = 8443
BASE = f"https://{HOST}:{PORT}"
SERVER_A = "aaaaaaaa-1111-2222-3333-444444444444"
SERVER_B = "bbbbbbbb-1111-2222-3333-444444444444"
SERVER_NEW = "cccccccc-1111-2222-3333-444444444444"
TOKEN = "test-token-value"
LOGIN_TOKEN = "login-issued-token"

TOKEN_DATA = {
    CONF_HOST: HOST,
    CONF_PORT: PORT,
    CONF_VERIFY_SSL: False,
    CONF_AUTH_METHOD: AUTH_METHOD_TOKEN,
    CONF_TOKEN: TOKEN,
}
CREDENTIALS_DATA = {
    CONF_HOST: HOST,
    CONF_PORT: PORT,
    CONF_VERIFY_SSL: False,
    CONF_AUTH_METHOD: AUTH_METHOD_CREDENTIALS,
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "hunter2-secret",
}


def all_entities(*server_ids: str) -> dict[str, Any]:
    """Options that switch on every entity for the given servers."""
    return {
        CONF_SERVER_SETTINGS: {
            sid: {CONF_ENABLED_ENTITIES: list(ALL_SERVER_ENTITY_KEYS)} for sid in server_ids
        }
    }


def server_item(server_id: str, name: str) -> dict[str, Any]:
    """A /api/v2/servers list item."""
    return {
        "server_id": server_id,
        "server_name": name,
        "type": "minecraft-java",
        "server_ip": "127.0.0.1",
        "server_port": 25565,
        "executable": "paper.jar",
    }


def stats_fields(**overrides: Any) -> dict[str, Any]:
    """Stats as stored by current Crafty (mem in bytes, players as Python repr)."""
    fields: dict[str, Any] = {
        "started": "2026-09-30 10:00:00",
        "running": True,
        "cpu": 12.5,
        "mem": 1073741824.0,
        "mem_percent": 25.0,
        "world_name": "world",
        "world_size": "512.0MB",
        "server_port": 25565,
        "int_ping_results": "True",
        "online": 2,
        "max": 20,
        "players": "['Steve', 'Alex']",
        "desc": "§aA §lMinecraft§r Server",
        "icon": "",
        "version": "Paper 1.21.4",
        "updating": False,
        "waiting_start": False,
        "first_run": False,
        "crashed": False,
    }
    fields.update(overrides)
    return fields


def stats_nested(**overrides: Any) -> dict[str, Any]:
    """Stats under ``data`` (what Crafty's handler actually returns)."""
    return {"status": "ok", "data": stats_fields(**overrides)}


def stats_flat(**overrides: Any) -> dict[str, Any]:
    """Stats at the top level (the shape some spec examples show)."""
    return {"status": "ok", **stats_fields(**overrides)}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in every test."""


@pytest.fixture
def token_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A token-based entry monitoring two servers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Crafty ({HOST})",
        unique_id=f"{HOST}:{PORT}",
        data=dict(TOKEN_DATA),
        options={CONF_SERVERS: [SERVER_A, SERVER_B], **all_entities(SERVER_A, SERVER_B)},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def credentials_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A credentials-based entry monitoring one server."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Crafty ({HOST})",
        unique_id=f"{HOST}:{PORT}",
        data=dict(CREDENTIALS_DATA),
        options={CONF_SERVERS: [SERVER_A], **all_entities(SERVER_A)},
    )
    entry.add_to_hass(hass)
    return entry


def mock_crafty(
    aioclient_mock: AiohttpClientMocker,
    *,
    servers: list[dict[str, Any]] | None = None,
    stats: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Register the standard happy-path endpoints."""
    aioclient_mock.get(f"{BASE}/api/v2/crafty/check", json={"status": "ok"})
    aioclient_mock.post(
        f"{BASE}/api/v2/auth/login",
        json={"status": "ok", "data": {"token": LOGIN_TOKEN, "user_id": "1"}},
    )
    aioclient_mock.get(
        f"{BASE}/api/v2/servers",
        json={
            "status": "ok",
            "data": servers
            if servers is not None
            else [server_item(SERVER_A, "Survival"), server_item(SERVER_B, "Creative")],
        },
    )
    stats = stats if stats is not None else {SERVER_A: stats_nested(), SERVER_B: stats_flat()}
    for server_id, payload in stats.items():
        aioclient_mock.get(f"{BASE}/api/v2/servers/{server_id}/stats", json=payload)


@pytest.fixture
def crafty(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Happy-path Crafty mock."""
    mock_crafty(aioclient_mock)
    return aioclient_mock


def calls_to(aioclient_mock: AiohttpClientMocker, method: str, path: str) -> list[Any]:
    """Return recorded calls for a method and path."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method.lower() and str(call[1].path) == path
    ]
