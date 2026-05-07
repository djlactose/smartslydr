# config/custom_components/smartslydr/api_client.py

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Subtract a safety margin from the documented 30-minute lifetime so a token
# that's about to expire isn't used for a request that takes a few seconds to
# reach the server.
TOKEN_LIFETIME = timedelta(minutes=29)

# Schema discovery: anything OUTSIDE these sets is logged at WARNING the first
# time we see it on a given device, so undocumented fields (e.g. door-button
# accessory data added after API v0.4) become visible without enabling debug
# logging or dumping full payloads. Remove once the schema is understood.
_KNOWN_ROOM_FIELDS = frozenset({"room_name", "room_id", "device_list"})
_KNOWN_DEVICE_FIELDS = frozenset({
    "device_id", "devicename", "petpass", "room_name", "room_id",
    "wlansignal", "temperature", "humidity", "position", "error",
    "status", "sound", "wlanmac",
})
_KNOWN_TOPLEVEL_FIELDS = frozenset({"statusCode", "room_lists"})

# Endpoint discovery: /devices doesn't return the door-button accessory for
# at least one real account, which suggests it's served by a different path.
# Probed once per HA process; any 200 response is logged at WARNING with a
# body preview. Remove when the schema is fully understood.
_PROBE_PATHS = (
    "/buttons",
    "/button",
    "/accessories",
    "/accessory",
    "/door_buttons",
    "/doorbuttons",
    "/extras",
)


def _truncate(value, limit: int = 400):
    """Stringify and truncate so a stray giant value doesn't flood logs."""
    s = repr(value)
    return s if len(s) <= limit else s[:limit] + "...<truncated>"


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
        # Tracks which (scope, key) tuples we've already warned about so each
        # unfamiliar field is reported once per HA process, not every refresh.
        self._reported_unknown_fields: set[tuple[str, str]] = set()
        self._endpoints_probed: bool = False

    def _debug_enabled(self) -> bool:
        if not self._hass:
            return False
        return bool(self._hass.data.get(DOMAIN, {}).get("debug", False))

    def _log_response(self, label: str, status: int, body) -> None:
        if self._debug_enabled():
            _LOGGER.debug("[%s] HTTP %s response: %s", label, status, body)

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

        self._report_unknown_devices_fields(data)
        if not self._endpoints_probed:
            self._endpoints_probed = True
            try:
                await self._probe_extra_endpoints()
            except Exception as err:  # noqa: BLE001 - diagnostic, never fatal
                _LOGGER.debug("Endpoint probe failed (non-fatal): %s", err)
        return data["room_lists"]

    async def _probe_extra_endpoints(self) -> None:
        """One-time GET against speculative paths; log anything that returns 200."""
        headers = {"Authorization": self._access_token}
        timeout = aiohttp.ClientTimeout(total=5)
        summary: list[str] = []
        for path in _PROBE_PATHS:
            url = f"{self.BASE_URL}{path}"
            try:
                async with self._session.get(url, headers=headers, timeout=timeout) as resp:
                    body = await resp.text()
                    summary.append(f"{path}={resp.status}")
                    if resp.status == 200:
                        _LOGGER.warning(
                            "[ENDPOINT_PROBE] %s returned 200, body preview: %s",
                            path, _truncate(body),
                        )
            except asyncio.TimeoutError:
                summary.append(f"{path}=timeout")
            except aiohttp.ClientError as err:
                summary.append(f"{path}=err({type(err).__name__})")
        _LOGGER.warning("[ENDPOINT_PROBE summary] %s", " ".join(summary))

    def _report_unknown_devices_fields(self, data: dict) -> None:
        """Warn once per process for each schema field not in API v0.4."""
        for key in set(data.keys()) - _KNOWN_TOPLEVEL_FIELDS:
            if ("toplevel", key) in self._reported_unknown_fields:
                continue
            self._reported_unknown_fields.add(("toplevel", key))
            _LOGGER.warning(
                "[GET_DEVICES schema] unrecognized top-level key %r; value type=%s, sample=%r",
                key, type(data[key]).__name__, _truncate(data[key]),
            )

        for room in data.get("room_lists") or []:
            if not isinstance(room, dict):
                continue
            for key in set(room.keys()) - _KNOWN_ROOM_FIELDS:
                if ("room", key) in self._reported_unknown_fields:
                    continue
                self._reported_unknown_fields.add(("room", key))
                _LOGGER.warning(
                    "[GET_DEVICES schema] room %r has unrecognized key %r = %r",
                    room.get("room_name"), key, _truncate(room[key]),
                )

            for dev in room.get("device_list") or []:
                if not isinstance(dev, dict):
                    continue
                for key in set(dev.keys()) - _KNOWN_DEVICE_FIELDS:
                    scope_key = ("device", key)
                    if scope_key in self._reported_unknown_fields:
                        continue
                    self._reported_unknown_fields.add(scope_key)
                    _LOGGER.warning(
                        "[GET_DEVICES schema] device %s (%s) has unrecognized key %r = %r",
                        dev.get("device_id"),
                        dev.get("devicename"),
                        key,
                        _truncate(dev[key]),
                    )

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
