# config/custom_components/smartslydr/api_client.py

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from .const import DOMAIN

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


class SmartSlydrApiClient:
    BASE_URL = "https://34yl6ald82.execute-api.us-east-2.amazonaws.com/prod"

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession, hass=None):
        self._username = username
        self._password = password
        self._session = session
        self._hass = hass
        self._access_token: str | None = None
        self._refresh_token_value: str | None = None
        self._token_expires: datetime | None = None

    def _debug_enabled(self) -> bool:
        if not self._hass:
            return False
        return bool(self._hass.data.get(DOMAIN, {}).get("debug", False))

    def _log_response(self, label: str, status: int, body) -> None:
        if self._debug_enabled():
            _LOGGER.debug("[%s] HTTP %s response: %s", label, status, _redact(body))

    async def authenticate(self) -> None:
        url = f"{self.BASE_URL}/auth"
        payload = {"username": self._username, "password": self._password}
        async with self._session.post(url, json=payload) as resp:
            body = await resp.json(content_type=None)
            self._log_response("AUTH", resp.status, body)
            resp.raise_for_status()
        self._access_token = body["access_token"]
        self._refresh_token_value = body.get("refresh_token")
        self._token_expires = datetime.now(timezone.utc) + TOKEN_LIFETIME

    async def refresh_token(self) -> None:
        url = f"{self.BASE_URL}/token"
        payload = {"refresh_token": self._refresh_token_value}
        async with self._session.post(url, json=payload) as resp:
            body = await resp.json(content_type=None)
            self._log_response("REFRESH_TOKEN", resp.status, body)
            resp.raise_for_status()
        self._access_token = body["access_token"]
        self._token_expires = datetime.now(timezone.utc) + TOKEN_LIFETIME

    async def _ensure_token(self) -> None:
        now = datetime.now(timezone.utc)
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
        async with self._session.get(f"{self.BASE_URL}/devices", headers=headers) as resp:
            data = await resp.json(content_type=None)
            self._log_response("GET_DEVICES", resp.status, data)
            resp.raise_for_status()

        if not isinstance(data, dict) or "room_lists" not in data:
            _LOGGER.error("Unexpected /devices response: %s", data)
            raise SmartSlydrApiError("SmartSlydr devices API returned unexpected data")

        return data["room_lists"]

    async def get_status(self, commands):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}
        payload = {"commands": commands}
        async with self._session.post(
            f"{self.BASE_URL}/operation/get", json=payload, headers=headers
        ) as resp:
            data = await resp.json(content_type=None)
            self._log_response("GET_STATUS", resp.status, data)
            resp.raise_for_status()
        if not isinstance(data, dict):
            return []
        return data.get("response", [])

    async def set_command(self, setcommands):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}
        payload = {"setcommands": setcommands}
        async with self._session.post(
            f"{self.BASE_URL}/operation", json=payload, headers=headers
        ) as resp:
            data = await resp.json(content_type=None)
            self._log_response("SET_COMMAND", resp.status, data)
            resp.raise_for_status()
        if not isinstance(data, dict):
            return []
        return data.get("response", [])


class SmartSlydrApiError(Exception):
    """Raised when the SmartSlydr API returns an unexpected payload."""
