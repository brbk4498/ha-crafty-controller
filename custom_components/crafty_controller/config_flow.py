"""Config, reauth, reconfigure and options flows for Crafty Controller."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CraftyAuthError, CraftyClient, CraftyConnectionError, CraftyError, ServerInfo
from .const import (
    ALL_SERVER_ENTITY_KEYS,
    AUTH_METHOD_CREDENTIALS,
    AUTH_METHOD_TOKEN,
    CONF_ALLOW_CONSOLE,
    CONF_ALLOW_CONSOLE_SERVER,
    CONF_ALLOW_KILL,
    CONF_ALLOW_STOP_RESTART,
    CONF_AUTH_METHOD,
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
    CONF_TOKEN,
    CONF_UNREACHABLE_MINUTES,
    DEFAULT_ENABLED_KEYS,
    DEFAULT_PORT,
    DOMAIN,
    EVENT_KEYS,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MAX_TIMEOUT,
    MIN_SCAN_INTERVAL,
    MIN_TIMEOUT,
)
from .coordinator import CraftyConfigEntry
from .options import CraftyOptions

ENTITY_PLATFORMS = (Platform.SELECT, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON)

AUTH_METHOD_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[AUTH_METHOD_CREDENTIALS, AUTH_METHOD_TOKEN],
        translation_key="auth_method",
        mode=SelectSelectorMode.LIST,
    )
)
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, False)): bool,
            vol.Required(
                CONF_AUTH_METHOD,
                default=defaults.get(CONF_AUTH_METHOD, AUTH_METHOD_CREDENTIALS),
            ): AUTH_METHOD_SELECTOR,
        }
    )


def _credentials_schema(
    defaults: Mapping[str, Any], *, password_optional: bool = False
) -> vol.Schema:
    password_key = vol.Optional(CONF_PASSWORD) if password_optional else vol.Required(CONF_PASSWORD)
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            password_key: PASSWORD_SELECTOR,
        }
    )


def _token_schema(*, optional: bool = False) -> vol.Schema:
    key = vol.Optional(CONF_TOKEN) if optional else vol.Required(CONF_TOKEN)
    return vol.Schema({key: PASSWORD_SELECTOR})


def _unique_id(host: str, port: int) -> str:
    host = host.strip().lower()
    for prefix in ("https://", "http://"):
        host = host.removeprefix(prefix)
    return f"{host.rstrip('/')}:{port}"


def _client_from_data(
    hass: HomeAssistant, data: Mapping[str, Any], timeout: float = 10
) -> CraftyClient:
    session = async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, False))
    use_credentials = data.get(CONF_AUTH_METHOD) == AUTH_METHOD_CREDENTIALS
    return CraftyClient(
        session,
        data[CONF_HOST],
        int(data[CONF_PORT]),
        token=None if use_credentials else data.get(CONF_TOKEN),
        username=data.get(CONF_USERNAME) if use_credentials else None,
        password=data.get(CONF_PASSWORD) if use_credentials else None,
        timeout=timeout,
    )


async def validate_connection(hass: HomeAssistant, data: Mapping[str, Any]) -> list[ServerInfo]:
    """Check reachability, then authenticate, then list servers.

    Raises CraftyConnectionError, CraftyAuthError or CraftyError.
    """
    client = _client_from_data(hass, data)
    await client.async_check()
    if client.has_credentials:
        await client.async_login()
    return await client.async_get_servers()


async def _validate(
    hass: HomeAssistant, data: Mapping[str, Any], errors: dict[str, str]
) -> list[ServerInfo] | None:
    try:
        return await validate_connection(hass, data)
    except CraftyConnectionError:
        errors["base"] = "cannot_connect"
    except CraftyAuthError:
        errors["base"] = "invalid_auth"
    except CraftyError:
        LOGGER.exception("Unexpected Crafty response during validation")
        errors["base"] = "unknown"
    except Exception:
        LOGGER.exception("Unexpected error during validation")
        errors["base"] = "unknown"
    return None


def _server_options(servers: list[ServerInfo]) -> list[SelectOptionDict]:
    return [SelectOptionDict(value=s.server_id, label=s.name) for s in servers]


class CraftyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Crafty Controller config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._servers: list[ServerInfo] = []

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: CraftyConfigEntry) -> CraftyOptionsFlow:
        """Return the options flow."""
        return CraftyOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for host, port, SSL verification and auth method."""
        if user_input is not None:
            await self.async_set_unique_id(_unique_id(user_input[CONF_HOST], user_input[CONF_PORT]))
            self._abort_if_unique_id_configured()
            self._data = {**user_input, CONF_HOST: user_input[CONF_HOST].strip()}
            if user_input[CONF_AUTH_METHOD] == AUTH_METHOD_TOKEN:
                return await self.async_step_token()
            return await self.async_step_credentials()
        return self.async_show_form(step_id="user", data_schema=_connection_schema({}))

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for username and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**self._data, **user_input}
            if (servers := await _validate(self.hass, data, errors)) is not None:
                self._data, self._servers = data, servers
                return await self.async_step_servers()
        return self.async_show_form(
            step_id="credentials",
            data_schema=self.add_suggested_values_to_schema(
                _credentials_schema({}), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for an API token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**self._data, **user_input}
            if (servers := await _validate(self.hass, data, errors)) is not None:
                self._data, self._servers = data, servers
                return await self.async_step_servers()
        return self.async_show_form(step_id="token", data_schema=_token_schema(), errors=errors)

    async def async_step_servers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which servers to monitor (all by default)."""
        if user_input is not None:
            return self.async_create_entry(
                title=f"Crafty ({self._data[CONF_HOST]})",
                data=self._data,
                options={CONF_SERVERS: user_input.get(CONF_SERVERS, [])},
            )
        return self.async_show_form(
            step_id="servers",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SERVERS, default=[s.server_id for s in self._servers]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_server_options(self._servers),
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            description_placeholders={"count": str(len(self._servers))},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials or a new token."""
        entry = self._get_reauth_entry()
        uses_token = entry.data.get(CONF_AUTH_METHOD) == AUTH_METHOD_TOKEN
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            if await _validate(self.hass, data, errors) is not None:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        schema = _token_schema() if uses_token else _credentials_schema(entry.data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, port, SSL or auth method without removing the entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            new_uid = _unique_id(user_input[CONF_HOST], user_input[CONF_PORT])
            if any(
                other.unique_id == new_uid and other.entry_id != entry.entry_id
                for other in self._async_current_entries(include_ignore=False)
            ):
                errors["base"] = "already_configured"
            else:
                self._data = {**entry.data, **user_input, CONF_HOST: user_input[CONF_HOST].strip()}
                if user_input[CONF_AUTH_METHOD] == AUTH_METHOD_TOKEN:
                    return await self.async_step_reconfigure_token()
                return await self.async_step_reconfigure_credentials()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema(entry.data), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reconfigure_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Credentials for reconfigure; a blank password keeps the stored one."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**self._data, **user_input}
            if not data.get(CONF_PASSWORD):
                data[CONF_PASSWORD] = entry.data.get(CONF_PASSWORD)
            data.pop(CONF_TOKEN, None)
            if not data.get(CONF_PASSWORD):
                errors["base"] = "invalid_auth"
            elif await _validate(self.hass, data, errors) is not None:
                return self._finish_reconfigure(data)
        return self.async_show_form(
            step_id="reconfigure_credentials",
            data_schema=_credentials_schema(self._data, password_optional=True),
            errors=errors,
        )

    async def async_step_reconfigure_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Token for reconfigure; blank keeps the stored token."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**self._data, **user_input}
            if not data.get(CONF_TOKEN):
                data[CONF_TOKEN] = entry.data.get(CONF_TOKEN)
            data.pop(CONF_USERNAME, None)
            data.pop(CONF_PASSWORD, None)
            if not data.get(CONF_TOKEN):
                errors["base"] = "invalid_auth"
            elif await _validate(self.hass, data, errors) is not None:
                return self._finish_reconfigure(data)
        return self.async_show_form(
            step_id="reconfigure_token",
            data_schema=_token_schema(optional=True),
            errors=errors,
        )

    def _finish_reconfigure(self, data: dict[str, Any]) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        return self.async_update_reload_and_abort(
            entry,
            unique_id=_unique_id(data[CONF_HOST], data[CONF_PORT]),
            title=f"Crafty ({data[CONF_HOST]})",
            data=data,
        )


class CraftyOptionsFlow(OptionsFlowWithReload):
    """Options menu. Saving any section reloads the entry automatically."""

    def __init__(self) -> None:
        """Initialize."""
        self._server_id: str | None = None

    @property
    def _opts(self) -> CraftyOptions:
        return CraftyOptions.from_entry_options(self.config_entry.options)

    def _save(self, changes: Mapping[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "servers", "server_select", "safety", "events", "advanced"],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Polling, timeout and re-login settings."""
        if user_input is not None:
            return self._save(
                {
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_TIMEOUT: int(user_input[CONF_TIMEOUT]),
                    CONF_RETRY_ON_403: user_input[CONF_RETRY_ON_403],
                    CONF_UNREACHABLE_MINUTES: int(user_input[CONF_UNREACHABLE_MINUTES]),
                }
            )
        opts = self._opts
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=opts.scan_interval): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_TIMEOUT, default=opts.timeout): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_TIMEOUT,
                        max=MAX_TIMEOUT,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_RETRY_ON_403, default=opts.retry_on_403): BooleanSelector(),
                vol.Required(
                    CONF_UNREACHABLE_MINUTES, default=opts.unreachable_minutes
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=1440,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_servers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add or remove monitored servers (list fetched fresh each time)."""
        if user_input is not None:
            return self._save({CONF_SERVERS: list(user_input.get(CONF_SERVERS, []))})
        errors: dict[str, str] = {}
        opts = self._opts
        servers = await _validate(self.hass, self.config_entry.data, errors) or []
        known = {s.server_id for s in servers}
        # Keep monitored servers selectable even if Crafty no longer lists them
        servers += [
            ServerInfo(server_id=sid, name=f"{sid} (not found)")
            for sid in opts.servers
            if sid not in known
        ]
        schema = vol.Schema(
            {
                vol.Optional(CONF_SERVERS, default=list(opts.servers)): SelectSelector(
                    SelectSelectorConfig(
                        options=_server_options(servers),
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(step_id="servers", data_schema=schema, errors=errors)

    async def async_step_server_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a monitored server to configure."""
        opts = self._opts
        if not opts.servers:
            return self.async_abort(reason="no_servers")
        if user_input is not None:
            self._server_id = user_input[CONF_SERVER_ID]
            return await self.async_step_server_settings()
        names = self._server_names()
        schema = vol.Schema(
            {
                vol.Required(CONF_SERVER_ID, default=opts.servers[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=sid, label=names.get(sid, sid))
                            for sid in opts.servers
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="server_select", data_schema=schema)

    async def async_step_server_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Per-server name, entities, poll interval and console access."""
        assert self._server_id is not None
        server_id = self._server_id
        opts = self._opts
        current = opts.settings_for(server_id)
        if user_input is not None:
            all_settings = dict(self.config_entry.options.get(CONF_SERVER_SETTINGS) or {})
            interval = int(user_input.get(CONF_SERVER_SCAN_INTERVAL) or 0)
            all_settings[server_id] = {
                CONF_NAME_OVERRIDE: (user_input.get(CONF_NAME_OVERRIDE) or "").strip(),
                CONF_ENABLED_ENTITIES: list(user_input.get(CONF_ENABLED_ENTITIES, [])),
                CONF_SERVER_SCAN_INTERVAL: interval or None,
                CONF_ALLOW_CONSOLE_SERVER: user_input[CONF_ALLOW_CONSOLE_SERVER],
            }
            return self._save({CONF_SERVER_SETTINGS: all_settings})
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_NAME_OVERRIDE,
                    description={"suggested_value": current.name_override or ""},
                ): str,
                vol.Optional(
                    CONF_ENABLED_ENTITIES,
                    default=self._enabled_keys(server_id, current.enabled_entities),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(ALL_SERVER_ENTITY_KEYS),
                        multiple=True,
                        translation_key="entity_keys",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(
                    CONF_SERVER_SCAN_INTERVAL, default=current.scan_interval or 0
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_ALLOW_CONSOLE_SERVER, default=current.allow_console
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="server_settings",
            data_schema=schema,
            description_placeholders={"server": self._server_names().get(server_id, server_id)},
        )

    async def async_step_safety(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Allow or block dangerous actions."""
        if user_input is not None:
            return self._save(user_input)
        opts = self._opts
        schema = vol.Schema(
            {
                vol.Required(CONF_ALLOW_KILL, default=opts.allow_kill): BooleanSelector(),
                vol.Required(
                    CONF_ALLOW_STOP_RESTART, default=opts.allow_stop_restart
                ): BooleanSelector(),
                vol.Required(CONF_ALLOW_CONSOLE, default=opts.allow_console): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="safety", data_schema=schema)

    async def async_step_events(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Toggle Home Assistant bus events."""
        if user_input is not None:
            return self._save(
                {CONF_EVENTS: [event for key, event in EVENT_KEYS.items() if user_input.get(key)]}
            )
        enabled = self._opts.events
        schema = vol.Schema(
            {
                vol.Required(key, default=event in enabled): BooleanSelector()
                for key, event in EVENT_KEYS.items()
            }
        )
        return self.async_show_form(step_id="events", data_schema=schema)

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Extra management features, diagnostic entities and debug logging."""
        if user_input is not None:
            return self._save(user_input)
        opts = self._opts
        schema = vol.Schema(
            {
                vol.Required(CONF_ENABLE_TASKS, default=opts.enable_tasks): BooleanSelector(),
                vol.Required(CONF_ENABLE_WEBHOOKS, default=opts.enable_webhooks): BooleanSelector(),
                vol.Required(CONF_ENABLE_LOGS, default=opts.enable_logs): BooleanSelector(),
                vol.Required(
                    CONF_ENABLE_HOST_METRICS, default=opts.enable_host_metrics
                ): BooleanSelector(),
                vol.Required(
                    CONF_ENABLE_DIAGNOSTIC_ENTITIES, default=opts.enable_diagnostic_entities
                ): BooleanSelector(),
                vol.Required(CONF_DEBUG_LOGGING, default=opts.debug_logging): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="advanced", data_schema=schema)

    def _enabled_keys(self, server_id: str, configured: frozenset[str] | None) -> list[str]:
        """Keys to pre-tick: the stored choice, else what is enabled right now."""
        if configured is not None:
            return [k for k in ALL_SERVER_ENTITY_KEYS if k in configured]
        ent_reg = er.async_get(self.hass)
        keys: list[str] = []
        for key in ALL_SERVER_ENTITY_KEYS:
            reg_entry = next(
                (
                    ent_reg.async_get(entity_id)
                    for platform in ENTITY_PLATFORMS
                    if (
                        entity_id := ent_reg.async_get_entity_id(
                            platform, DOMAIN, f"{server_id}_{key}"
                        )
                    )
                ),
                None,
            )
            if (reg_entry is None and key in DEFAULT_ENABLED_KEYS) or (
                reg_entry is not None and reg_entry.disabled_by is None
            ):
                keys.append(key)
        return keys

    def _server_names(self) -> dict[str, str]:
        entry: CraftyConfigEntry = self.config_entry
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is None:
            return {}
        return {sid: coordinator.server_name(sid) for sid in self._opts.servers}
