"""Async client and response parsers for the Crafty Controller 4 v2 API."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass, field
import json
import logging
import math
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class CraftyError(Exception):
    """Base error for the Crafty client."""


class CraftyConnectionError(CraftyError):
    """Crafty could not be reached (network, TLS, timeout or 5xx)."""


class CraftyAuthError(CraftyError):
    """Authentication was rejected (bad credentials or expired token)."""


class CraftyPermissionError(CraftyError):
    """Crafty refused the request (HTTP 400 NOT_AUTHORIZED, unknown server, no global access)."""


class CraftyApiError(CraftyError):
    """Crafty answered with ``status: error`` for another reason."""

    def __init__(self, message: str, code: str | None = None) -> None:
        """Store the Crafty error code next to the message."""
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Defensive parsers. None of these raise on odd input.
# --------------------------------------------------------------------------

_SIZE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([KMGTPEZY]?)(i?)B?\s*$", re.IGNORECASE)
_SIZE_POWERS = {"": 0, "K": 1, "M": 2, "G": 3, "T": 4, "P": 5, "E": 6, "Z": 7, "Y": 8}
_MC_FORMAT_RE = re.compile(r"§.")
_PROM_LINE_RE = re.compile(r"^([A-Za-z_:][\w:]*)(?:\{.*\})?\s+(\S+)")


def parse_bool(value: Any) -> bool | None:
    """Parse a Crafty boolean that may arrive as bool, int or string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    return None


def parse_number(value: Any) -> float | None:
    """Parse a number. Crafty uses ``-1`` and ``False`` for unknown values."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().rstrip("%").strip()
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if number < 0 or math.isnan(number):  # negative sentinel or NaN
        return None
    return number


def parse_int(value: Any) -> int | None:
    """Parse a non-negative integer."""
    number = parse_number(value)
    return None if number is None else int(number)


def parse_size(value: Any) -> float | None:
    """Parse a data size into bytes.

    Current Crafty reports ``mem`` as raw bytes (a number). Older builds and
    ``world_size`` use a human readable string such as ``"42.2MB"``, built with
    1024 multipliers, so ``MB`` is treated as MiB.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return parse_number(value)
    if not isinstance(value, str):
        return None
    match = _SIZE_RE.match(value)
    if match is None:
        return None
    number = float(match.group(1))
    if number < 0:
        return None
    return number * float(1024 ** _SIZE_POWERS[match.group(2).upper()])


def parse_players(value: Any) -> list[str] | None:
    """Parse the player list.

    Crafty stores ``str(list)`` so the value is usually a Python repr such as
    ``"['Steve', 'Alex']"``; JSON and plain lists are accepted too.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return []
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(text)
        except ValueError, SyntaxError, TypeError, MemoryError, RecursionError:
            continue
        if isinstance(parsed, (list, tuple)):
            return [str(item) for item in parsed if str(item)]
        if isinstance(parsed, dict) and isinstance(parsed.get("players"), list):
            return [str(item) for item in parsed["players"] if str(item)]
        return None
    stripped = text.strip("[]")
    return [name.strip(" '\"") for name in stripped.split(",") if name.strip(" '\"")]


def parse_text(value: Any) -> str | None:
    """Parse a string field, dropping Crafty's ``False`` placeholders."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def strip_minecraft_formatting(text: str | None) -> str | None:
    """Remove Minecraft section-sign colour codes from a MOTD."""
    if text is None:
        return None
    cleaned = " ".join(_MC_FORMAT_RE.sub("", text).split())
    return cleaned or None


def parse_prometheus(text: str) -> dict[str, float]:
    """Parse Prometheus text exposition into ``{metric_name: value}``.

    Labels are ignored; the last sample of a metric wins.
    """
    metrics: dict[str, float] = {}
    for raw_line in text.splitlines():
        match = _PROM_LINE_RE.match(raw_line.strip())
        if match is None:
            continue
        try:
            metrics[match.group(1)] = float(match.group(2))
        except ValueError:
            continue
    return metrics


@dataclass(slots=True)
class ServerInfo:
    """A server from ``GET /api/v2/servers``."""

    server_id: str
    name: str
    type: str | None = None
    server_ip: str | None = None
    server_port: int | None = None
    executable: str | None = None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> ServerInfo:
        """Build from an API item."""
        server_id = str(item.get("server_id") or item.get("server_uuid") or "")
        return cls(
            server_id=server_id,
            name=parse_text(item.get("server_name")) or server_id,
            type=parse_text(item.get("type")),
            server_ip=parse_text(item.get("server_ip")),
            server_port=parse_int(item.get("server_port")),
            executable=parse_text(item.get("executable")),
        )


@dataclass(slots=True)
class ServerStats:
    """Normalized ``/servers/{id}/stats`` payload."""

    running: bool | None = None
    crashed: bool | None = None
    updating: bool | None = None
    waiting_start: bool | None = None
    cpu: float | None = None
    mem_bytes: float | None = None
    mem_percent: float | None = None
    world_name: str | None = None
    world_size_bytes: float | None = None
    online: int | None = None
    max_players: int | None = None
    players: list[str] | None = None
    motd: str | None = None
    version: str | None = None
    started: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _stats_field(payload: dict[str, Any], key: str) -> Any:
    """Look under ``data`` first, then fall back to the top level."""
    data = payload.get("data")
    if isinstance(data, dict) and key in data:
        return data[key]
    return payload.get(key)


def parse_server_stats(payload: Any) -> ServerStats:
    """Parse a stats response in either documented shape. Never raises."""
    if not isinstance(payload, dict):
        return ServerStats()

    def get(key: str) -> Any:
        return _stats_field(payload, key)

    flat: dict[str, Any] = {}
    data = payload.get("data")
    if isinstance(data, dict):
        flat.update({k: v for k, v in payload.items() if k != "data"})
        flat.update(data)
    else:
        flat.update(payload)

    return ServerStats(
        running=parse_bool(get("running")),
        crashed=parse_bool(get("crashed")),
        updating=parse_bool(get("updating")),
        waiting_start=parse_bool(get("waiting_start")),
        cpu=parse_number(get("cpu")),
        mem_bytes=parse_size(get("mem")),
        mem_percent=parse_number(get("mem_percent")),
        world_name=parse_text(get("world_name")),
        world_size_bytes=parse_size(get("world_size")),
        online=parse_int(get("online")),
        max_players=parse_int(get("max")),
        players=parse_players(get("players")),
        motd=strip_minecraft_formatting(parse_text(get("desc"))),
        version=parse_text(get("version")),
        started=parse_text(get("started")),
        raw=flat,
    )


class CraftyClient:
    """Minimal async client for the Crafty v2 API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        *,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 10,
        relogin_on_403: bool = True,
    ) -> None:
        """Initialize the client. Either ``token`` or credentials are required."""
        self._session = session
        self._host = host.strip().rstrip("/")
        self._port = port
        self._token = token
        self._username = username
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._relogin_on_403 = relogin_on_403
        self._login_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Return the base URL, e.g. ``https://crafty.lan:8443``."""
        host = self._host
        for prefix in ("https://", "http://"):
            if host.startswith(prefix):
                host = host[len(prefix) :]
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"  # bare IPv6 literal
        return f"https://{host}:{self._port}"

    @property
    def has_credentials(self) -> bool:
        """Return True if the client can log in by itself."""
        return bool(self._username and self._password)

    async def async_login(self) -> str:
        """Log in with username/password and store the returned token."""
        if not self.has_credentials:
            raise CraftyAuthError("No credentials available to log in")
        status, body = await self._raw_request(
            "POST",
            "/api/v2/auth/login",
            json_body={"username": self._username, "password": self._password},
            auth=False,
        )
        if status in (400, 401, 403):
            raise CraftyAuthError(_error_text(body) or "Login rejected")
        if status == 429:
            raise CraftyAuthError("Too many login attempts")
        _raise_for_status(status, body)
        data = body.get("data") if isinstance(body, dict) else None
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise CraftyAuthError("Login response did not include a token")
        self._token = str(token)
        return self._token

    async def async_check(self) -> None:
        """Call the unauthenticated alive check."""
        status, body = await self._raw_request("GET", "/api/v2/crafty/check", auth=False)
        _raise_for_status(status, body)

    async def async_get_servers(self) -> list[ServerInfo]:
        """Return the servers the token can access."""
        body = await self._request("GET", "/api/v2/servers")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return []
        servers = [ServerInfo.from_api(item) for item in data if isinstance(item, dict)]
        return [server for server in servers if server.server_id]

    async def async_get_server_stats(self, server_id: str) -> ServerStats:
        """Return parsed stats for one server."""
        body = await self._request("GET", f"/api/v2/servers/{server_id}/stats")
        return parse_server_stats(body)

    async def async_server_action(self, server_id: str, action: str) -> None:
        """Run a server action such as ``start_server``."""
        await self._request("POST", f"/api/v2/servers/{server_id}/action/{action}")

    async def async_send_command(self, server_id: str, command: str) -> str:
        """Send a console command. A leading slash is stripped."""
        cleaned = normalize_command(command)
        if not cleaned:
            raise ValueError("Command is empty")
        await self._request(
            "POST",
            f"/api/v2/servers/{server_id}/stdin",
            text_body=cleaned,
        )
        return cleaned

    async def async_get_logs(self, server_id: str, *, from_file: bool = False) -> list[str]:
        """Return console log lines (plain text, ANSI stripped)."""
        body = await self._request(
            "GET",
            f"/api/v2/servers/{server_id}/logs",
            params={
                "file": str(from_file).lower(),
                "colors": "false",
                "raw": "false",
                "html": "false",
            },
        )
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, list):
            return [str(line) for line in data]
        if isinstance(data, str):
            return data.splitlines()
        return []

    async def async_create_task(self, server_id: str, task: dict[str, Any]) -> str | None:
        """Create a schedule and return its id."""
        body = await self._request("POST", f"/api/v2/servers/{server_id}/tasks", json_body=task)
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict) and data.get("schedule_id") is not None:
            return str(data["schedule_id"])
        return None

    async def async_update_task(
        self, server_id: str, task_id: int, changes: dict[str, Any]
    ) -> None:
        """Patch a schedule."""
        await self._request(
            "PATCH", f"/api/v2/servers/{server_id}/tasks/{task_id}", json_body=changes
        )

    async def async_delete_task(self, server_id: str, task_id: int) -> None:
        """Delete a schedule."""
        await self._request("DELETE", f"/api/v2/servers/{server_id}/tasks/{task_id}")

    async def async_list_webhooks(self, server_id: str) -> dict[str, Any]:
        """List webhooks keyed by webhook id."""
        body = await self._request("GET", f"/api/v2/servers/{server_id}/webhook")
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            return {str(key): value for key, value in data.items()}
        if isinstance(data, list):
            return {
                str(item.get("webhook_id", index)): item
                for index, item in enumerate(data)
                if isinstance(item, dict)
            }
        return {}

    async def async_create_webhook(self, server_id: str, webhook: dict[str, Any]) -> str | None:
        """Create a webhook and return its id."""
        body = await self._request(
            "POST", f"/api/v2/servers/{server_id}/webhook", json_body=webhook
        )
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict) and data.get("webhook_id") is not None:
            return str(data["webhook_id"])
        return None

    async def async_delete_webhook(self, server_id: str, webhook_id: int) -> None:
        """Delete a webhook.

        The OpenAPI spec documents ``/api/v2/server/{id}/webhook/{wid}`` (singular)
        but Crafty's router only registers the plural ``/api/v2/servers/...`` path.
        """
        await self._request("DELETE", f"/api/v2/servers/{server_id}/webhook/{webhook_id}")

    async def async_get_host_metrics(self) -> dict[str, float]:
        """Return host metrics (``CPU_Usage``, ``Mem_Usage``) from Prometheus text."""
        text = await self._request("GET", "/metrics/host", expect_json=False)
        return parse_prometheus(text if isinstance(text, str) else "")

    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        text_body: str | None = None,
        expect_json: bool = True,
    ) -> Any:
        """Authenticated request with one transparent re-login on 403."""
        if self._token is None and self.has_credentials:
            await self._async_relogin(None)
        sent_token = self._token
        status, body = await self._raw_request(
            method,
            path,
            params=params,
            json_body=json_body,
            text_body=text_body,
            expect_json=expect_json,
        )
        if status in (401, 403) and self._relogin_on_403 and self.has_credentials:
            _LOGGER.debug("Got HTTP %s from %s, logging in again", status, path)
            await self._async_relogin(sent_token)
            status, body = await self._raw_request(
                method,
                path,
                params=params,
                json_body=json_body,
                text_body=text_body,
                expect_json=expect_json,
            )
        _raise_for_status(status, body)
        if isinstance(body, dict) and body.get("status") == "error":
            raise CraftyApiError(_error_text(body) or "Crafty returned an error", body.get("error"))
        return body

    async def _async_relogin(self, failed_token: str | None) -> None:
        """Log in again unless another request already refreshed the token."""
        async with self._login_lock:
            if self._token is not None and self._token != failed_token:
                return
            await self.async_login()

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        text_body: str | None = None,
        auth: bool = True,
        expect_json: bool = True,
    ) -> tuple[int, Any]:
        """Perform one HTTP request and return ``(status, decoded body)``."""
        headers: dict[str, str] = {"Accept": "application/json"}
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        data: str | None = None
        if text_body is not None:
            headers["Content-Type"] = "text/plain"
            data = text_body
        url = f"{self.base_url}{path}"
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                data=data,
                headers=headers,
                timeout=self._timeout,
            ) as response:
                text = await response.text()
                status = response.status
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CraftyConnectionError(
                f"Error talking to Crafty: {err.__class__.__name__}"
            ) from err
        body: Any = text
        if expect_json or status >= 400:
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                body = text if not expect_json else {"raw": text}
        return status, body


def normalize_command(command: str) -> str:
    """Trim a console command and drop a leading slash."""
    cleaned = command.strip()
    while cleaned.startswith("/"):
        cleaned = cleaned[1:].lstrip()
    return cleaned


def _error_text(body: Any) -> str | None:
    if isinstance(body, dict):
        parts = [str(body[key]) for key in ("error", "error_data") if body.get(key)]
        return ": ".join(parts) or None
    return None


def _raise_for_status(status: int, body: Any) -> None:
    if status < 400:
        return
    message = _error_text(body) or f"HTTP {status}"
    if status in (401, 403):
        raise CraftyAuthError(message)
    if status in (400, 404):
        raise CraftyPermissionError(message)
    if status >= 500:
        raise CraftyConnectionError(message)
    raise CraftyApiError(message)
