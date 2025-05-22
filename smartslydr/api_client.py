# config/custom_components/smartslydr/api_client.py

import logging
import aiohttp
from datetime import datetime, timedelta

_LOGGER = logging.getLogger(__name__)

class SmartSlydrApiClient:
    BASE_URL = "https://34yl6ald82.execute-api.us-east-2.amazonaws.com/prod"

    def __init__(self, username: str, password: str, session: aiohttp.ClientSession):
        self._username = username
        self._password = password
        self._session = session
        self._access_token: str = None
        self._refresh_token: str = None
        self._token_expires: datetime = None

    async def authenticate(self):
        url = f"{self.BASE_URL}/auth"
        payload = {"username": self._username, "password": self._password}
        async with self._session.post(url, json=payload) as resp:
            resp.raise_for_status()
            result = await resp.json()
        self._access_token = result["access_token"]
        self._refresh_token = result.get("refresh_token")
        self._token_expires = datetime.utcnow() + timedelta(minutes=30)

    async def _ensure_token(self):
        if not self._access_token or datetime.utcnow() >= self._token_expires:
            if self._refresh_token:
                await self.refresh_token()
            else:
                await self.authenticate()

    async def refresh_token(self):
        url = f"{self.BASE_URL}/token"
        payload = {"refresh_token": self._refresh_token}
        async with self._session.post(url, json=payload) as resp:
            resp.raise_for_status()
            result = await resp.json()
        self._access_token = result["access_token"]
        self._token_expires = datetime.utcnow() + timedelta(minutes=30)

    async def get_devices(self):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}
        async with self._session.get(f"{self.BASE_URL}/devices", headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if "room_lists" not in data:
            _LOGGER.error("Unexpected /devices response: %s", data)
            raise Exception("SmartSlydr devices API returned unexpected data")

        return data["room_lists"]

    async def get_status(self, commands):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}
        payload = {"commands": commands}
        async with self._session.post(f"{self.BASE_URL}/operation/get", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("response", [])

    async def set_command(self, setcommands):
        await self._ensure_token()
        headers = {"Authorization": self._access_token}
        payload = {"setcommands": setcommands}
        async with self._session.post(f"{self.BASE_URL}/operation", json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("response", [])
