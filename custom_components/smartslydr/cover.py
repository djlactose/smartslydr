# config/custom_components/smartslydr/cover.py

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .api_client import SmartSlydrApiClient

COMMAND_POSITION = "position"

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SmartSlydr covers (doors/blinds) from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client: SmartSlydrApiClient = data["client"]
    coordinator = data["coordinator"]

    entities = []
    for room in coordinator.data:
        for dev in room.get("device_list", []):
            if "position" in dev:
                entities.append(SmartSlydrCover(dev, client, coordinator))

    async_add_entities(entities)


class SmartSlydrCover(CoordinatorEntity, CoverEntity):
    """Representation of a SmartSlydr cover (e.g. door or shade)."""

    def __init__(self, device, client, coordinator):
        super().__init__(coordinator)
        self._device = device
        self._client = client

        self._attr_name = f"SmartSlydr {device['devicename']}"
        self._attr_unique_id = device["device_id"]
        self._position = device.get("position", 0)
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

    @property
    def device_info(self):
        """Group the cover under the same physical device registry entry."""
        return {
            "identifiers": {(DOMAIN, self._device["device_id"])},
            "name": self._device["devicename"],
            "manufacturer": "SmartSlydr",
        }

    @property
    def current_cover_position(self) -> int:
        return self._position

    @property
    def is_closed(self) -> bool:
        return self._position == 0

    async def async_open_cover(self, **kwargs) -> None:
        await self.async_set_cover_position(position=100)

    async def async_close_cover(self, **kwargs) -> None:
        await self.async_set_cover_position(position=0)

    async def async_stop_cover(self, **kwargs) -> None:
        await self._client.set_command([{
            "device_id": self._device["device_id"],
            "commands": [{"key": COMMAND_POSITION, "value": 200}],
        }])

    async def async_set_cover_position(self, **kwargs) -> None:
        pos = kwargs.get("position")
        await self._client.set_command([{
            "device_id": self._device["device_id"],
            "commands": [{"key": COMMAND_POSITION, "value": pos}],
        }])
        self._position = pos
        self.async_write_ha_state()
