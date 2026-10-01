"""Safety gates shared by buttons and services."""

from __future__ import annotations

from homeassistant.exceptions import ServiceValidationError

from .const import ACTION_KILL, ACTION_RESTART, ACTION_STOP, DOMAIN
from .options import CraftyOptions


def ensure_action_allowed(options: CraftyOptions, action: str, server_name: str) -> None:
    """Raise a clear error when the options block a server action."""
    if action == ACTION_KILL and not options.allow_kill:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="kill_blocked",
            translation_placeholders={"server": server_name},
        )
    if action in (ACTION_STOP, ACTION_RESTART) and not options.allow_stop_restart:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="stop_restart_blocked",
            translation_placeholders={"server": server_name},
        )


def ensure_console_allowed(options: CraftyOptions, server_id: str, server_name: str) -> None:
    """Raise when console commands are blocked globally or for this server."""
    if not options.console_allowed(server_id):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="console_blocked",
            translation_placeholders={"server": server_name},
        )


def ensure_feature_enabled(enabled: bool, feature: str) -> None:
    """Raise when an Advanced feature is switched off."""
    if not enabled:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="feature_disabled",
            translation_placeholders={"feature": feature},
        )
