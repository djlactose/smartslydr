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


async def _read_json(label: str, resp: aiohttp.ClientResponse):
    """Parse a response body, tolerating non-JSON error pages.

    API Gateway and CloudFront serve HTML (or an empty body) for some
    throttle and gateway errors. ``resp.json()`` raises ValueError on
    those, and because the body was read before the status check, that
    ValueError used to escape every call path as a bare JSONDecodeError
    instead of the HTTP error the caller could actually act on.

    Returns None when the body isn't JSON; the caller checks the status
    immediately afterwards, so a None body on an error response is
    expected and harmless.
    """
    try:
        return await resp.json(content_type=None)
    except (aiohttp.ClientResponseError, ValueError):
        # aiohttp has already cached the body, so .text() won't re-read
        # the socket. Truncated because an HTML error page is long and
        # the first line is the only informative part.
        text = await resp.text()
        _LOGGER.debug(
            "[%s] HTTP %s body was not JSON: %.200s", label, resp.status, text
        )
        return None


def _raise_for_status(label: str, resp: aiohttp.ClientResponse) -> None:
    """``resp.raise_for_status()``, but map HTTP 429 to its own type.

    A 429 is neither a backend fault nor a transport fault - it means we
    are asking for data more often than the account's quota allows. It
    needs a distinct type so that:

    - the coordinator can raise a rate-limit repair card telling the user
      to raise their scan interval, instead of a "backend unreachable"
      card that points the blame upstream; and
    - cover/switch can turn it into a readable HomeAssistantError instead
      of leaking a raw aiohttp exception out of the service call.
    """
    try:
        resp.raise_for_status()
    except aiohttp.ClientResponseError as err:
        if err.status == 429:
            retry_after = resp.headers.get("Retry-After")
            suffix = f"; upstream asked us to retry after {retry_after}s" if retry_after else ""
            raise SmartSlydrRateLimitError(
                f"SmartSlydr {label} was rate-limited by the API (HTTP 429){suffix}"
            ) from err
        raise


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
            body = await _read_json("AUTH", resp)
            self._log_response("AUTH", resp.status, body)
            try:
                _raise_for_status("AUTH", resp)
            except aiohttp.ClientResponseError as err:
                if err.status in (400, 401, 403):
                    raise SmartSlydrAuthError(
                        "SmartSlydr rejected the stored credentials"
                    ) from err
                raise
        if not isinstance(body, dict) or "access_token" not in body:
            raise SmartSlydrApiError(
                "SmartSlydr auth response missing access_token"
            )
        self._access_token = body["access_token"]
        self._refresh_token_value = body.get("refresh_token")
        self._token_expires = datetime.now(timezone.utc) + TOKEN_LIFETIME

    async def refresh_token(self) -> None:
        url = f"{self._base_url}/token"
        payload = {"refresh_token": self._refresh_token_value}
        async with self._session.post(url, json=payload) as resp:
            body = await _read_json("REFRESH_TOKEN", resp)
            self._log_response("REFRESH_TOKEN", resp.status, body)
            _raise_for_status("REFRESH_TOKEN", resp)
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
                    # A SmartSlydrRateLimitError deliberately propagates
                    # here rather than falling through to authenticate().
                    # Being throttled is not a sign the refresh token is
                    # bad, and answering a 429 with a second request
                    # against the same quota only deepens the hole. The
                    # refresh token is left intact so the next poll can
                    # retry it once the throttle clears.
                await self.authenticate()

    async def get_devices(self):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}

        async def _do_request():
            async with self._session.get(
                f"{self._base_url}/devices", headers=headers
            ) as resp:
                body = await _read_json("GET_DEVICES", resp)
                self._log_response("GET_DEVICES", resp.status, body)
                _raise_for_status("GET_DEVICES", resp)
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
                body = await _read_json("GET_STATUS", resp)
                self._log_response("GET_STATUS", resp.status, body)
                _raise_for_status("GET_STATUS", resp)
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
            data = await _read_json("SET_COMMAND", resp)
            self._log_response("SET_COMMAND", resp.status, data)
            _raise_for_status("SET_COMMAND", resp)
        _raise_if_upstream_error("SET_COMMAND", data)
        if not isinstance(data, dict):
            return []
        return data.get("response", [])


class SmartSlydrApiError(Exception):
    """Raised when the SmartSlydr API returns an unexpected payload."""


class SmartSlydrAuthError(SmartSlydrApiError):
    """Raised when SmartSlydr rejects the stored credentials.

    Distinguished from a generic API error so the coordinator can map it
    to ConfigEntryAuthFailed and trigger HA's reauth flow.
    """


class SmartSlydrRateLimitError(SmartSlydrApiError):
    """Raised when the upstream API rejects a request with HTTP 429.

    Distinguished from a generic API error because the cause, the blame
    and the remedy are all different: nothing upstream is broken, the
    integration is simply polling faster than the account's quota
    allows, and the fix is a larger scan interval rather than waiting
    for a backend recovery.

    Subclasses SmartSlydrApiError so existing ``except
    SmartSlydrApiError`` handlers keep working; handlers that want the
    rate-limit-specific message catch this first.
    """
