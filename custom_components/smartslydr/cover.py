# config/custom_components/smartslydr/cover.py

import time

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmartSlydrApiClient
from .const import DOMAIN

COMMAND_POSITION = "position"
STOP_VALUE = 200

# Debounce window for set-position commands. Without this, an upstream bridge
# (e.g. Home Bridge mirroring HA state) can fan out a single user action into
# multiple rapid set_command calls that confuse the device.
SET_POSITION_DEBOUNCE_S = 2.0


def _iter_devices(coordinator_data):
    for room in (coordinator_data or {}).get("rooms") or []:
        for dev in room.get("device_list") or []:
            yield dev


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SmartSlydr covers (doors/blinds) from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client: SmartSlydrApiClient = data["client"]
    coordinator = data["coordinator"]

    entities = [
        SmartSlydrCover(dev, client, coordinator)
        for dev in _iter_devices(coordinator.data)
        if "position" in dev
    ]
    async_add_entities(entities)


class SmartSlydrCover(CoordinatorEntity, CoverEntity):
    """Representation of a SmartSlydr cover (e.g. door or shade)."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, device, client, coordinator):
        super().__init__(coordinator)
        self._device_id = device["device_id"]
        self._device_name = device.get("devicename", self._device_id)
        self._client = client
        self._last_set_position_at: float = 0.0

        self._attr_name = f"SmartSlydr {self._device_name}"
        self._attr_unique_id = self._device_id

    def _device_data(self) -> dict:
        for dev in _iter_devices(self.coordinator.data):
            if dev.get("device_id") == self._device_id:
                return dev
        return {}

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "name": self._device_name,
            "manufacturer": "SmartSlydr",
        }

    @property
    def current_cover_position(self) -> int:
        return int(self._device_data().get("position", 0) or 0)

    @property
    def is_closed(self) -> bool:
        return self.current_cover_position == 0

    async def async_open_cover(self, **kwargs) -> None:
        await self.async_set_cover_position(position=100)

    async def async_close_cover(self, **kwargs) -> None:
        await self.async_set_cover_position(position=0)

    async def async_stop_cover(self, **kwargs) -> None:
        await self._client.set_command([{
            "device_id": self._device_id,
            "commands": [{"key": COMMAND_POSITION, "value": STOP_VALUE}],
        }])
        await self.coordinator.async_request_refresh()

    async def async_set_cover_position(self, **kwargs) -> None:
        pos = kwargs.get("position")
        if pos is None:
            return
        now = time.monotonic()
        if now - self._last_set_position_at < SET_POSITION_DEBOUNCE_S:
            return
        self._last_set_position_at = now
        await self._client.set_command([{
            "device_id": self._device_id,
            "commands": [{"key": COMMAND_POSITION, "value": pos}],
        }])
        await self.coordinator.async_request_refresh()
