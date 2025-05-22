# config/custom_components/smartslydr/switch.py

import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .api_client import SmartSlydrApiClient

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SmartSlydr pet pass switches from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client: SmartSlydrApiClient = data["client"]
    coordinator = data["coordinator"]

    entities = []
    for room in coordinator.data or []:
        for dev in room.get("device_list", []) or []:
            entities.append(
                SmartSlydrPetpassSwitch(dev, client, coordinator)
            )

    async_add_entities(entities)

class SmartSlydrPetpassSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of the SmartSlydr pet pass (door) as a toggle switch."""

    def __init__(self, device, client, coordinator):
        super().__init__(coordinator)
        self._device = device
        self._client = client
        self._state = False

        dev_id = device.get("device_id", "")
        name = device.get("devicename", dev_id)
        self._attr_unique_id = f"{dev_id}_petpass"
        self._attr_name = f"{name} Petpass"

        slots = device.get("petpass", [])
        self._allowed = [slot.get("name") for slot in slots if isinstance(slot, dict)]

    async def async_added_to_hass(self):
        """Fetch initial petpass state when entity is added."""
        await super().async_added_to_hass()
        await self.async_update()
        self.async_write_ha_state()

    @property
    def device_info(self):
        """Return device registry info for this switch."""
        return {
            "identifiers": {(DOMAIN, self._device.get("device_id"))},
            "name": self._device.get("devicename"),
            "manufacturer": "SmartSlydr",
        }

    @property
    def is_on(self) -> bool:
        """Return True if petpass is currently allowed/open."""
        return bool(self._state)

    @property
    def extra_state_attributes(self):
        """Return additional attributes like allowed pet names."""
        return {"allowed_pets": self._allowed}

    async def async_update(self):
        """Fetch current petpass state from the API."""
        try:
            response = await self._client.get_status([
                {"device_id": self._device.get("device_id"), "command": "petpass"}
            ])
            for res in response:
                if "petpass" in res:
                    self._state = bool(res.get("petpass"))
                    return
        except Exception as err:
            _LOGGER.error("Failed to fetch petpass state for %s: %s", self._device.get("device_id"), err)

    async def async_turn_on(self, **kwargs):
        """Enable (open) the petpass door."""
        await self._client.set_command([
            {"device_id": self._device.get("device_id"), "commands": [{"key": "petpass", "value": 1}]}  # 1 = open
        ])
        self._state = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Disable (close) the petpass door."""
        await self._client.set_command([
            {"device_id": self._device.get("device_id"), "commands": [{"key": "petpass", "value": 0}]}  # 0 = close
        ])
        self._state = False
        self.async_write_ha_state()