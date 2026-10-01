"""The Crafty Controller integration."""

from __future__ import annotations

import logging

from homeassistant.const import (
    ATTR_RESTORED,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import CraftyClient
from .const import (
    ALL_SERVER_ENTITY_KEYS,
    AUTH_METHOD_CREDENTIALS,
    CONF_AUTH_METHOD,
    CONF_TOKEN,
    DIAGNOSTIC_ENTITY_KEYS,
    DOMAIN,
    LOGGER,
)
from .coordinator import CraftyConfigEntry, CraftyCoordinator
from .options import CraftyOptions
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Level the integration logger had before any entry switched on debug logging
_ORIGINAL_LOG_LEVEL: int | None = None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services once for the whole integration."""
    async_setup_services(hass)
    return True


def build_client(
    hass: HomeAssistant, entry: CraftyConfigEntry, options: CraftyOptions
) -> CraftyClient:
    """Create an API client for a config entry."""
    data = entry.data
    session = async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, False))
    use_credentials = data.get(CONF_AUTH_METHOD) == AUTH_METHOD_CREDENTIALS
    return CraftyClient(
        session,
        data[CONF_HOST],
        data[CONF_PORT],
        token=None if use_credentials else data.get(CONF_TOKEN),
        username=data.get(CONF_USERNAME) if use_credentials else None,
        password=data.get(CONF_PASSWORD) if use_credentials else None,
        timeout=options.timeout,
        relogin_on_403=options.retry_on_403,
    )


async def async_setup_entry(hass: HomeAssistant, entry: CraftyConfigEntry) -> bool:
    """Set up Crafty Controller from a config entry."""
    options = CraftyOptions.from_entry_options(entry.options)
    _apply_debug_logging(hass, options)

    coordinator = CraftyCoordinator(hass, entry, build_client(hass, entry, options), options)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _async_sync_registries(hass, entry, options)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CraftyConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: CraftyConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device only when its server is no longer monitored."""
    server_ids = {
        identifier[1] for identifier in device_entry.identifiers if identifier[0] == DOMAIN
    }
    monitored = set(CraftyOptions.from_entry_options(entry.options).servers)
    return not server_ids & monitored


def _apply_debug_logging(hass: HomeAssistant, options: CraftyOptions) -> None:
    """Raise only this integration's logger to DEBUG when asked to.

    Tokens and passwords are never logged at any level.
    """
    global _ORIGINAL_LOG_LEVEL  # noqa: PLW0603
    logger = logging.getLogger(__package__)
    if options.debug_logging:
        if _ORIGINAL_LOG_LEVEL is None:
            _ORIGINAL_LOG_LEVEL = logger.level
        logger.setLevel(logging.DEBUG)
        LOGGER.debug("Debug logging enabled from the integration options")
        return
    if _ORIGINAL_LOG_LEVEL is not None and not any(
        CraftyOptions.from_entry_options(other.options).debug_logging
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        logger.setLevel(_ORIGINAL_LOG_LEVEL)
        _ORIGINAL_LOG_LEVEL = None


def _async_sync_registries(
    hass: HomeAssistant, entry: CraftyConfigEntry, options: CraftyOptions
) -> None:
    """Apply option changes to devices and entities without losing history.

    - Servers removed from the monitored list lose their device (and entities).
    - Entities switched off in the options are disabled (not deleted) so their
      entity_id and history survive; switching them back on re-enables them.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    monitored = set(options.servers)

    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        server_ids = {i[1] for i in device.identifiers if i[0] == DOMAIN}
        if (
            server_ids
            and not server_ids & monitored
            and not any(sid.endswith("_host") for sid in server_ids)
        ):
            dev_reg.async_remove_device(device.id)

    for key in ("host_cpu", "host_memory"):
        _set_enabled(
            hass,
            ent_reg,
            ent_reg.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{entry.entry_id}_{key}"),
            options.enable_host_metrics,
        )

    for server_id in monitored:
        enabled = options.settings_for(server_id).enabled_entities
        for key in ALL_SERVER_ENTITY_KEYS:
            diagnostic_ok = options.enable_diagnostic_entities or key not in DIAGNOSTIC_ENTITY_KEYS
            if enabled is None:
                # Not configured in the options: leave the user's own choices alone,
                # only switch diagnostics off when asked to.
                if diagnostic_ok:
                    continue
                want_enabled = False
            else:
                want_enabled = key in enabled and diagnostic_ok
            for platform in PLATFORMS:
                _set_enabled(
                    hass,
                    ent_reg,
                    ent_reg.async_get_entity_id(platform, DOMAIN, f"{server_id}_{key}"),
                    want_enabled,
                )


def _set_enabled(
    hass: HomeAssistant, ent_reg: er.EntityRegistry, entity_id: str | None, enabled: bool
) -> None:
    """Disable an entity, or re-enable it if this integration disabled it.

    Entities the user disabled themselves are left alone.
    """
    if entity_id is None or (reg_entry := ent_reg.async_get(entity_id)) is None:
        return
    if not enabled and reg_entry.disabled_by is None:
        LOGGER.debug("Disabling %s from options", entity_id)
        ent_reg.async_update_entity(entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
        # Unloading left an 'unavailable (restored)' placeholder; a disabled
        # entity should have no state at all, like a user-disabled one.
        if (state := hass.states.get(entity_id)) and state.attributes.get(ATTR_RESTORED):
            hass.states.async_remove(entity_id)
    elif enabled and reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
        LOGGER.debug("Re-enabling %s from options", entity_id)
        ent_reg.async_update_entity(entity_id, disabled_by=None)
