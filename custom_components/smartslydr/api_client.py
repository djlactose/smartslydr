# config/custom_components/smartslydr/api_client.py

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from .const import DEFAULT_BASE_URL

_LOGGER = logging.getLogger(__name__)

# Subtract a safety margin from the documented 30-minute lifetime so a token
# that's about to expire isn't used for a request that takes a few seconds to
# reach the server.
TOKEN_LIFETIME = timedelta(minutes=29)

# Keys whose values we replace with "***" before logging a response body.
# Users routinely paste debug logs into bug reports; raw bearer tokens must
# not leak that way.
_REDACT_KEYS = frozenset({"access_token", "refresh_token"})


def _redact(body):
    if isinstance(body, dict):
        return {k: ("***" if k in _REDACT_KEYS else _redact(v)) for k, v in body.items()}
    if isinstance(body, list):
        return [_redact(v) for v in body]
    return body


def _raise_if_upstream_error(label: str, data) -> None:
    """Raise SmartSlydrApiError if the body looks like an upstream Lambda error.

    AWS Lambda sometimes returns 200 with a body shaped like
    {errorType, errorMessage, trace, ...} when the function threw. The
    documented success shape never contains those keys.
    """
    if isinstance(data, dict) and ("errorType" in data or "errorMessage" in data):
        _LOGGER.error(
            "[%s] upstream error: %s / %s",
            label,
            data.get("errorType"),
            data.get("errorMessage"),
        )
        raise SmartSlydrApiError(f"SmartSlydr {label} upstream error")


class SmartSlydrApiClient:
    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        base_url: str = DEFAULT_BASE_URL,
    ):
        self._username = username
        self._password = password
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._access_token: str | None = None
        self._refresh_token_value: str | None = None
        self._token_expires: datetime | None = None
        # Serializes _ensure_token across concurrent callers (coordinator
        # poll firing while a user-initiated cover command is in flight).
        # Without it, both paths can trigger /token at the same time.
        self._token_lock = asyncio.Lock()

    def _log_response(self, label: str, status: int, body) -> None:
        # _LOGGER.debug only emits when the user clicks "Enable debug
        # logging" on the integration page (or sets logger: ... debug
        # in YAML). _redact() strips bearer tokens before they hit logs.
        _LOGGER.debug("[%s] HTTP %s response: %s", label, status, _redact(body))

    async def authenticate(self) -> None:
        url = f"{self._base_url}/auth"
        payload = {"username": self._username, "password": self._password}
        async with self._session.post(url, json=payload) as resp:
            body = await resp.json(content_type=None)
            self._log_response("AUTH", resp.status, body)
            resp.raise_for_status()
        self._access_token = body["access_token"]
        self._refresh_token_value = body.get("refresh_token")
        self._token_expires = datetime.now(timezone.utc) + TOKEN_LIFETIME

    async def refresh_token(self) -> None:
        url = f"{self._base_url}/token"
        payload = {"refresh_token": self._refresh_token_value}
        async with self._session.post(url, json=payload) as resp:
            body = await resp.json(content_type=None)
            self._log_response("REFRESH_TOKEN", resp.status, body)
            resp.raise_for_status()
        self._access_token = body["access_token"]
        self._token_expires = datetime.now(timezone.utc) + TOKEN_LIFETIME

    async def _request_with_retry(self, label: str, perform):
        """Retry transient 5xx and connection errors for idempotent calls.

        ``perform`` is a zero-arg callable returning a fresh coroutine each
        invocation (a coroutine object can only be awaited once). Only
        called for read-only operations - state-changing calls like
        set_command must not retry, since an ambiguous failure could
        actuate the device twice.
        """
        delays = (0.5, 1.5)
        for attempt, delay in enumerate((*delays, None)):
            try:
                return await perform()
            except aiohttp.ClientResponseError as err:
                if err.status < 500 or delay is None:
                    raise
                _LOGGER.debug(
                    "[%s] HTTP %s on attempt %d, retrying in %ss",
                    label, err.status, attempt + 1, delay,
                )
            except (aiohttp.ClientConnectorError, asyncio.TimeoutError) as err:
                if delay is None:
                    raise
                _LOGGER.debug(
                    "[%s] %s on attempt %d, retrying in %ss",
                    label, type(err).__name__, attempt + 1, delay,
                )
            await asyncio.sleep(delay)
        # Unreachable - the loop either returns or raises.
        raise RuntimeError("retry loop exhausted")

    async def _ensure_token(self) -> None:
        async with self._token_lock:
            now = datetime.now(timezone.utc)
            # Re-check inside the lock - another waiter may have just
            # refreshed; if it did, the staleness check is now false.
            if not self._access_token or self._token_expires is None or now >= self._token_expires:
                if self._refresh_token_value:
                    try:
                        await self.refresh_token()
                        return
                    except aiohttp.ClientResponseError as err:
                        _LOGGER.debug("Refresh token rejected (%s); re-authenticating", err.status)
                        self._refresh_token_value = None
                await self.authenticate()

    async def get_devices(self):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}

        async def _do_request():
            async with self._session.get(
                f"{self._base_url}/devices", headers=headers
            ) as resp:
                body = await resp.json(content_type=None)
                self._log_response("GET_DEVICES", resp.status, body)
                resp.raise_for_status()
            return body

        data = await self._request_with_retry("GET_DEVICES", _do_request)

        _raise_if_upstream_error("GET_DEVICES", data)

        rooms = data.get("room_lists") if isinstance(data, dict) else None
        if not isinstance(rooms, list):
            # Don't log the full body - it can be large and may contain
            # account-scoped identifiers; the type alone is enough to debug.
            _LOGGER.error(
                "Unexpected /devices response: room_lists is %s",
                type(rooms).__name__,
            )
            raise SmartSlydrApiError("SmartSlydr devices API returned unexpected data")

        return rooms

    async def get_status(self, commands):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}
        payload = {"commands": commands}

        async def _do_request():
            async with self._session.post(
                f"{self._base_url}/operation/get", json=payload, headers=headers
            ) as resp:
                body = await resp.json(content_type=None)
                self._log_response("GET_STATUS", resp.status, body)
                resp.raise_for_status()
            return body

        data = await self._request_with_retry("GET_STATUS", _do_request)
        _raise_if_upstream_error("GET_STATUS", data)
        if not isinstance(data, dict):
            return []
        return data.get("response", [])

    async def set_command(self, setcommands):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}
        payload = {"setcommands": setcommands}
        async with self._session.post(
            f"{self._base_url}/operation", json=payload, headers=headers
        ) as resp:
            data = await resp.json(content_type=None)
            self._log_response("SET_COMMAND", resp.status, data)
            resp.raise_for_status()
        _raise_if_upstream_error("SET_COMMAND", data)
        if not isinstance(data, dict):
            return []
        return data.get("response", [])


class SmartSlydrApiError(Exception):
    """Raised when the SmartSlydr API returns an unexpected payload."""
