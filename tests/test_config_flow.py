"""Tests for the config, reauth and reconfigure flows."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.crafty_controller.const import (
    AUTH_METHOD_CREDENTIALS,
    AUTH_METHOD_TOKEN,
    CONF_AUTH_METHOD,
    CONF_SERVERS,
    CONF_TOKEN,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from .conftest import BASE, HOST, PORT, SERVER_A, SERVER_B, TOKEN, mock_crafty

USER_INPUT = {CONF_HOST: HOST, CONF_PORT: PORT, CONF_VERIFY_SSL: False}


@pytest.fixture(autouse=True)
def no_setup():
    """Don't set up the entry when a flow creates it (except where tested)."""
    with patch(
        "custom_components.crafty_controller.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


async def test_token_success(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_crafty(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_AUTH_METHOD: AUTH_METHOD_TOKEN}
    )
    assert result["step_id"] == "token"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["step_id"] == "servers"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SERVERS: [SERVER_A]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Crafty ({HOST})"
    assert result["data"][CONF_TOKEN] == TOKEN
    assert result["options"] == {CONF_SERVERS: [SERVER_A]}
    assert result["result"].unique_id == f"{HOST}:{PORT}"
    # Validation order: alive check, then authenticated server list
    paths = [str(call[1].path) for call in aioclient_mock.mock_calls]
    assert paths == ["/api/v2/crafty/check", "/api/v2/servers"]


async def test_credentials_success_defaults_to_all_servers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_crafty(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_AUTH_METHOD: AUTH_METHOD_CREDENTIALS}
    )
    assert result["step_id"] == "credentials"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "pw"}
    )
    assert result["step_id"] == "servers"
    # Submitting the defaults selects every server
    schema_default = next(
        key.default() for key in result["data_schema"].schema if key == CONF_SERVERS
    )
    assert schema_default == [SERVER_A, SERVER_B]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SERVERS: schema_default}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USERNAME] == "admin"
    paths = [str(call[1].path) for call in aioclient_mock.mock_calls]
    assert paths == ["/api/v2/crafty/check", "/api/v2/auth/login", "/api/v2/servers"]


async def test_cannot_connect_then_recover(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{BASE}/api/v2/crafty/check", exc=TimeoutError())
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_AUTH_METHOD: AUTH_METHOD_TOKEN}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    mock_crafty(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["step_id"] == "servers"


@pytest.mark.parametrize(
    ("method", "user_input"),
    [
        (AUTH_METHOD_TOKEN, {CONF_TOKEN: "bad"}),
        (AUTH_METHOD_CREDENTIALS, {CONF_USERNAME: "admin", CONF_PASSWORD: "wrong"}),
    ],
)
async def test_invalid_auth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, method: str, user_input: dict
) -> None:
    aioclient_mock.get(f"{BASE}/api/v2/crafty/check", json={"status": "ok"})
    aioclient_mock.post(
        f"{BASE}/api/v2/auth/login",
        status=HTTPStatus.UNAUTHORIZED,
        json={"status": "error", "error": "INCORRECT_CREDENTIALS"},
    )
    aioclient_mock.get(
        f"{BASE}/api/v2/servers",
        status=HTTPStatus.FORBIDDEN,
        json={"status": "error", "error": "ACCESS_DENIED"},
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_AUTH_METHOD: method}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["errors"] == {"base": "invalid_auth"}


async def test_unknown_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{BASE}/api/v2/crafty/check", json={"status": "ok"})
    aioclient_mock.get(f"{BASE}/api/v2/servers", status=HTTPStatus.CONFLICT, json={})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_AUTH_METHOD: AUTH_METHOD_TOKEN}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["errors"] == {"base": "unknown"}


async def test_duplicate_aborts(hass: HomeAssistant, token_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: HOST.upper(),
            CONF_PORT: PORT,
            CONF_VERIFY_SSL: False,
            CONF_AUTH_METHOD: AUTH_METHOD_TOKEN,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_credentials(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, credentials_entry: MockConfigEntry
) -> None:
    mock_crafty(aioclient_mock)
    result = await credentials_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "new-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert credentials_entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_token_invalid_then_ok(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{BASE}/api/v2/crafty/check", json={"status": "ok"})
    aioclient_mock.get(
        f"{BASE}/api/v2/servers", status=HTTPStatus.FORBIDDEN, json={"status": "error"}
    )
    result = await token_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "x"})
    assert result["errors"] == {"base": "invalid_auth"}
    aioclient_mock.clear_requests()
    mock_crafty(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "new"})
    assert result["reason"] == "reauth_successful"
    assert token_entry.data[CONF_TOKEN] == "new"


async def test_reconfigure_changes_host_and_keeps_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    token_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    reg_entry = entity_registry.async_get_or_create(
        "sensor", DOMAIN, f"{SERVER_A}_cpu", config_entry=token_entry
    )
    new_base = "https://newhost.test:9443"
    aioclient_mock.get(f"{new_base}/api/v2/crafty/check", json={"status": "ok"})
    aioclient_mock.post(
        f"{new_base}/api/v2/auth/login", json={"status": "ok", "data": {"token": "abc"}}
    )
    aioclient_mock.get(f"{new_base}/api/v2/servers", json={"status": "ok", "data": []})

    result = await token_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "newhost.test",
            CONF_PORT: 9443,
            CONF_VERIFY_SSL: True,
            CONF_AUTH_METHOD: AUTH_METHOD_CREDENTIALS,
        },
    )
    assert result["step_id"] == "reconfigure_credentials"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert token_entry.unique_id == "newhost.test:9443"
    assert token_entry.data[CONF_HOST] == "newhost.test"
    assert token_entry.data[CONF_VERIFY_SSL] is True
    assert CONF_TOKEN not in token_entry.data
    # Same entity, same id
    assert entity_registry.async_get(reg_entry.entity_id) is not None
    assert token_entry.options[CONF_SERVERS] == [SERVER_A, SERVER_B]


async def test_reconfigure_blank_token_keeps_stored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, token_entry: MockConfigEntry
) -> None:
    mock_crafty(aioclient_mock)
    result = await token_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: HOST,
            CONF_PORT: PORT,
            CONF_VERIFY_SSL: False,
            CONF_AUTH_METHOD: AUTH_METHOD_TOKEN,
        },
    )
    assert result["step_id"] == "reconfigure_token"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["reason"] == "reconfigure_successful"
    assert token_entry.data[CONF_TOKEN] == TOKEN


async def test_reconfigure_rejects_other_entrys_host(
    hass: HomeAssistant, token_entry: MockConfigEntry
) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="other.test:8443", data={}).add_to_hass(hass)
    result = await token_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "other.test",
            CONF_PORT: 8443,
            CONF_VERIFY_SSL: False,
            CONF_AUTH_METHOD: AUTH_METHOD_TOKEN,
        },
    )
    assert result["errors"] == {"base": "already_configured"}
