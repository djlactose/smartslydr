# config/custom_components/smartslydr/switch.py

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmartSlydrApiClient
from .const import DOMAIN
from .helpers import iter_devices

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SmartSlydr pet pass switches from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client: SmartSlydrApiClient = data["client"]
    coordinator = data["coordinator"]

    entities = [
        SmartSlydrPetpassSwitch(dev, client, coordinator)
        for dev in iter_devices(coordinator.data)
    ]
    async_add_entities(entities)


class SmartSlydrPetpassSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of the SmartSlydr pet pass (door) as a toggle switch."""

    def __init__(self, device, client, coordinator):
        super().__init__(coordinator)
        self._device_id = device.get("device_id", "")
        self._device_name = device.get("devicename", self._device_id)
        self._client = client

        self._attr_unique_id = f"{self._device_id}_petpass"
        self._attr_name = f"{self._device_name} Petpass"

    def _device_data(self) -> dict:
        for dev in iter_devices(self.coordinator.data):
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
    def is_on(self) -> bool:
        data = self.coordinator.data
        states = data.petpass_states if data is not None else {}
        return bool(states.get(self._device_id, False))

    @property
    def extra_state_attributes(self):
        slots = self._device_data().get("petpass") or []
        allowed = [slot.get("name") for slot in slots if isinstance(slot, dict)]
        return {"allowed_pets": allowed}

    async def async_turn_on(self, **kwargs):
        await self._client.set_command([
            {"device_id": self._device_id, "commands": [{"key": "petpass", "value": 1}]}
        ])
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        await self._client.set_command([
            {"device_id": self._device_id, "commands": [{"key": "petpass", "value": 0}]}
        ])
        await self.coordinator.async_request_refresh()
