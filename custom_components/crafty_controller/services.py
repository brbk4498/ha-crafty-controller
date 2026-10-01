"""Services for Crafty Controller."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids

from .api import CraftyAuthError, CraftyError
from .const import (
    ACTION_KILL,
    ACTION_RESTART,
    ACTION_STOP,
    DOMAIN,
    SERVICE_CREATE_TASK,
    SERVICE_CREATE_WEBHOOK,
    SERVICE_DELETE_TASK,
    SERVICE_DELETE_WEBHOOK,
    SERVICE_GET_LOGS,
    SERVICE_LIST_WEBHOOKS,
    SERVICE_SEND_COMMAND,
    SERVICE_SET_TASK_ENABLED,
    TASK_ACTIONS,
    TASK_INTERVAL_TYPES,
    WEBHOOK_TYPES,
)
from .coordinator import CraftyConfigEntry, CraftyCoordinator
from .safety import ensure_action_allowed, ensure_console_allowed, ensure_feature_enabled

ATTR_COMMAND = "command"
ATTR_LINES = "lines"
ATTR_FROM_FILE = "from_file"
ATTR_NAME = "name"
ATTR_ACTION = "action"
ATTR_INTERVAL = "interval"
ATTR_INTERVAL_TYPE = "interval_type"
ATTR_CRON = "cron_string"
ATTR_ONE_TIME = "one_time"
ATTR_DELAY = "delay"
ATTR_ENABLED = "enabled"
ATTR_TASK_ID = "task_id"
ATTR_WEBHOOK_ID = "webhook_id"
ATTR_WEBHOOK_TYPE = "webhook_type"
ATTR_URL = "url"
ATTR_BOT_NAME = "bot_name"
ATTR_TRIGGERS = "triggers"
ATTR_BODY = "body"
ATTR_COLOR = "color"

WEBHOOK_TRIGGERS = (
    "start_server",
    "stop_server",
    "crash_detected",
    "backup_server",
    "jar_update",
    "send_command",
    "kill",
)

_GATED_TASK_ACTIONS = (ACTION_STOP, ACTION_RESTART, ACTION_KILL)

SEND_COMMAND_SCHEMA = vol.Schema(
    {**cv.TARGET_SERVICE_FIELDS, vol.Required(ATTR_COMMAND): vol.All(cv.string, vol.Length(min=1))}
)
GET_LOGS_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Optional(ATTR_LINES, default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
        vol.Optional(ATTR_FROM_FILE, default=False): cv.boolean,
    }
)
CREATE_TASK_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_NAME): cv.string,
        vol.Required(ATTR_ACTION): vol.In(TASK_ACTIONS),
        vol.Optional(ATTR_COMMAND): cv.string,
        vol.Optional(ATTR_INTERVAL, default=1): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_INTERVAL_TYPE, default="hours"): vol.In(TASK_INTERVAL_TYPES),
        vol.Optional(ATTR_CRON, default=""): cv.string,
        vol.Optional(ATTR_ONE_TIME, default=False): cv.boolean,
        vol.Optional(ATTR_DELAY, default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
    }
)
SET_TASK_ENABLED_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_TASK_ID): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Required(ATTR_ENABLED): cv.boolean,
    }
)
DELETE_TASK_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_TASK_ID): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)
LIST_WEBHOOKS_SCHEMA = vol.Schema({**cv.TARGET_SERVICE_FIELDS})
CREATE_WEBHOOK_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_NAME): cv.string,
        vol.Required(ATTR_WEBHOOK_TYPE): vol.In(WEBHOOK_TYPES),
        vol.Required(ATTR_URL): cv.url,
        vol.Required(ATTR_TRIGGERS): vol.All(
            cv.ensure_list, [vol.In(WEBHOOK_TRIGGERS)], vol.Length(min=1)
        ),
        vol.Optional(ATTR_BOT_NAME, default="Crafty Controller"): cv.string,
        vol.Optional(ATTR_BODY, default=""): cv.string,
        vol.Optional(ATTR_COLOR, default="#005cd1"): cv.string,
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
    }
)
DELETE_WEBHOOK_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Required(ATTR_WEBHOOK_ID): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)

type _Target = tuple[CraftyCoordinator, str]


@callback
def async_resolve_targets(hass: HomeAssistant, call: ServiceCall) -> list[_Target]:
    """Map a service target (entities, devices, areas) to Crafty servers."""
    selection = TargetSelection(call.data)
    if not selection.has_any_target:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_target")
    selected = async_extract_referenced_entity_ids(hass, selection)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    device_ids = set(selected.referenced_devices) | set(selection.device_ids)
    for entity_id in selected.referenced | selected.indirectly_referenced:
        if (entry := ent_reg.async_get(entity_id)) and entry.device_id:
            device_ids.add(entry.device_id)

    targets: dict[tuple[str, str], _Target] = {}
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            continue
        for domain, server_id in device.identifiers:
            if domain != DOMAIN or server_id.endswith("_host"):
                continue
            entry_id = device.config_entry_id
            config_entry: CraftyConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
            if (
                config_entry is None
                or config_entry.domain != DOMAIN
                or config_entry.state is not ConfigEntryState.LOADED
            ):
                continue
            coordinator = config_entry.runtime_data
            if server_id in coordinator.options.servers:
                targets[(entry_id, server_id)] = (coordinator, server_id)
    if not targets:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_target")
    return list(targets.values())


async def _call_api[T](coordinator: CraftyCoordinator, request: Awaitable[T]) -> T:
    """Run an API call and turn Crafty errors into HomeAssistantError."""
    try:
        return await request
    except CraftyAuthError as err:
        coordinator.config_entry.async_start_reauth(coordinator.hass)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="invalid_auth") from err
    except CraftyError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="request_failed",
            translation_placeholders={"error": str(err)},
        ) from err


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register all services."""

    async def send_command(call: ServiceCall) -> ServiceResponse:
        targets = async_resolve_targets(hass, call)
        for coordinator, server_id in targets:
            ensure_console_allowed(
                coordinator.options, server_id, coordinator.server_name(server_id)
            )
        command: str = call.data[ATTR_COMMAND]
        sent: dict[str, Any] = {}
        for coordinator, server_id in targets:
            sent[server_id] = await _call_api(
                coordinator,
                coordinator.client.async_send_command(server_id, command),
            )
        return {"servers": sent} if call.return_response else None

    async def get_logs(call: ServiceCall) -> ServiceResponse:
        targets = async_resolve_targets(hass, call)
        result: dict[str, Any] = {}
        for coordinator, server_id in targets:
            ensure_feature_enabled(coordinator.options.enable_logs, "logs")
            lines = await _call_api(
                coordinator,
                coordinator.client.async_get_logs(server_id, from_file=call.data[ATTR_FROM_FILE]),
            )
            result[server_id] = {
                "server_name": coordinator.server_name(server_id),
                "lines": lines[-call.data[ATTR_LINES] :],
            }
        return {"servers": result}

    async def create_task(call: ServiceCall) -> ServiceResponse:
        targets = async_resolve_targets(hass, call)
        action: str = call.data[ATTR_ACTION]
        for coordinator, server_id in targets:
            options = coordinator.options
            name = coordinator.server_name(server_id)
            ensure_feature_enabled(options.enable_tasks, "tasks")
            if action == "command":
                ensure_console_allowed(options, server_id, name)
            elif action in _GATED_TASK_ACTIONS:
                ensure_action_allowed(options, action, name)
        if action == "command" and not call.data.get(ATTR_COMMAND):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="command_required"
            )
        interval_type = call.data[ATTR_INTERVAL_TYPE]
        cron = call.data[ATTR_CRON]
        if interval_type == "cron" and not cron:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="cron_required")
        command = call.data.get(ATTR_COMMAND) if action == "command" else action
        payload: dict[str, Any] = {
            "name": call.data[ATTR_NAME],
            "enabled": call.data[ATTR_ENABLED],
            "action": action,
            "interval": call.data[ATTR_INTERVAL],
            "interval_type": "" if interval_type == "cron" else interval_type,
            "command": command,
            "one_time": call.data[ATTR_ONE_TIME],
            "cron_string": cron if interval_type == "cron" else "",
            "delay": call.data[ATTR_DELAY],
        }
        if payload["command"] and action == "command":
            payload["command"] = payload["command"].lstrip("/")
        created: dict[str, Any] = {}
        for coordinator, server_id in targets:
            created[server_id] = {
                "task_id": await _call_api(
                    coordinator,
                    coordinator.client.async_create_task(server_id, payload),
                )
            }
        return {"servers": created} if call.return_response else None

    async def set_task_enabled(call: ServiceCall) -> None:
        for coordinator, server_id in async_resolve_targets(hass, call):
            ensure_feature_enabled(coordinator.options.enable_tasks, "tasks")
            await _call_api(
                coordinator,
                coordinator.client.async_update_task(
                    server_id, call.data[ATTR_TASK_ID], {"enabled": call.data[ATTR_ENABLED]}
                ),
            )

    async def delete_task(call: ServiceCall) -> None:
        for coordinator, server_id in async_resolve_targets(hass, call):
            ensure_feature_enabled(coordinator.options.enable_tasks, "tasks")
            await _call_api(
                coordinator,
                coordinator.client.async_delete_task(server_id, call.data[ATTR_TASK_ID]),
            )

    async def list_webhooks(call: ServiceCall) -> ServiceResponse:
        result: dict[str, Any] = {}
        for coordinator, server_id in async_resolve_targets(hass, call):
            ensure_feature_enabled(coordinator.options.enable_webhooks, "webhooks")
            hooks = await _call_api(
                coordinator,
                coordinator.client.async_list_webhooks(server_id),
            )
            result[server_id] = {
                "server_name": coordinator.server_name(server_id),
                "webhooks": hooks,
            }
        return {"servers": result}

    async def create_webhook(call: ServiceCall) -> ServiceResponse:
        payload = {
            "webhook_type": call.data[ATTR_WEBHOOK_TYPE],
            "name": call.data[ATTR_NAME],
            "url": call.data[ATTR_URL],
            "bot_name": call.data[ATTR_BOT_NAME],
            "trigger": call.data[ATTR_TRIGGERS],
            "body": call.data[ATTR_BODY],
            "color": call.data[ATTR_COLOR],
            "enabled": call.data[ATTR_ENABLED],
        }
        targets = async_resolve_targets(hass, call)
        for coordinator, _server_id in targets:
            ensure_feature_enabled(coordinator.options.enable_webhooks, "webhooks")
        created: dict[str, Any] = {}
        for coordinator, server_id in targets:
            created[server_id] = {
                "webhook_id": await _call_api(
                    coordinator,
                    coordinator.client.async_create_webhook(server_id, payload),
                )
            }
        return {"servers": created} if call.return_response else None

    async def delete_webhook(call: ServiceCall) -> None:
        for coordinator, server_id in async_resolve_targets(hass, call):
            ensure_feature_enabled(coordinator.options.enable_webhooks, "webhooks")
            await _call_api(
                coordinator,
                coordinator.client.async_delete_webhook(server_id, call.data[ATTR_WEBHOOK_ID]),
            )

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_COMMAND, send_command, SEND_COMMAND_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_LOGS, get_logs, GET_LOGS_SCHEMA, SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_TASK, create_task, CREATE_TASK_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_TASK_ENABLED, set_task_enabled, SET_TASK_ENABLED_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_DELETE_TASK, delete_task, DELETE_TASK_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_LIST_WEBHOOKS, list_webhooks, LIST_WEBHOOKS_SCHEMA, SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_WEBHOOK,
        create_webhook,
        CREATE_WEBHOOK_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_WEBHOOK, delete_webhook, DELETE_WEBHOOK_SCHEMA
    )
