# config/custom_components/smartslydr/switch.py

import logging
import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmartSlydrApiClient, SmartSlydrApiError
from .const import DOMAIN
from .helpers import iter_devices

_LOGGER = logging.getLogger(__name__)

# Long safety timeout for the optimistic override - only hits if the
# polled state never moves off the pre-write value (which would mean
# the write silently failed and the UI would otherwise lie forever).
# Real backend propagation has been observed to take longer than 30s,
# so we don't time out against propagation lag - we wait for the polled
# value to actually change instead.
_OPTIMISTIC_SAFETY_TIMEOUT_S = 120.0


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

    _attr_has_entity_name = True
    _attr_name = "Petpass"

    def __init__(self, device, client, coordinator):
        super().__init__(coordinator)
        self._device_id = device.get("device_id", "")
        self._device_name = device.get("devicename", self._device_id)
        self._client = client
        # Pre-write polled value, captured at the moment of an optimistic
        # write. We hold the optimistic override until the polled value
        # moves OFF this baseline (propagation confirmed) or the safety
        # timeout fires (silent write failure).
        self._optimistic_baseline: bool | None = None
        self._optimistic_until: float | None = None

        self._attr_unique_id = f"{self._device_id}_petpass"

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
        if "_attr_is_on" in self.__dict__:
            return bool(self.__dict__["_attr_is_on"])
        return self._polled_is_on()

    def _polled_is_on(self) -> bool:
        data = self.coordinator.data
        states = data.petpass_states if data is not None else {}
        return bool(states.get(self._device_id, False))

    def _handle_coordinator_update(self) -> None:
        # Hold the optimistic override until either:
        # 1. The polled value moves OFF the pre-write baseline. That
        #    means the backend has propagated *something* - whether
        #    that's our written value or some other change. Either way,
        #    polled is now ground truth and the override should drop.
        # 2. The safety timeout fires. Only relevant if the polled
        #    value never changes (silent write failure); without this
        #    the override would lie forever.
        #
        # We deliberately do NOT drop the override on a wall-clock
        # timeout against an unchanged polled value - that's exactly
        # the case where the write succeeded but the backend hasn't
        # propagated yet, and dropping the override flips the toggle
        # back to its pre-write state.
        if "_attr_is_on" in self.__dict__ and self._optimistic_baseline is not None:
            polled = self._polled_is_on()
            timed_out = (
                self._optimistic_until is not None
                and time.monotonic() >= self._optimistic_until
            )
            if polled != self._optimistic_baseline or timed_out:
                self.__dict__.pop("_attr_is_on", None)
                self._optimistic_baseline = None
                self._optimistic_until = None
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self):
        slots = self._device_data().get("petpass") or []
        allowed = [slot.get("name") for slot in slots if isinstance(slot, dict)]
        return {"allowed_pets": allowed}

    async def async_turn_on(self, **kwargs):
        await self._send_petpass(1)

    async def async_turn_off(self, **kwargs):
        await self._send_petpass(0)

    async def _send_petpass(self, value: int) -> None:
        # Capture the polled value as it stood BEFORE this write so the
        # update handler can detect when the backend has propagated.
        self._optimistic_baseline = self._polled_is_on()
        self._attr_is_on = bool(value)
        self._optimistic_until = time.monotonic() + _OPTIMISTIC_SAFETY_TIMEOUT_S
        self.async_write_ha_state()
        try:
            await self._client.set_command(
                [{"device_id": self._device_id,
                  "commands": [{"key": "petpass", "value": value}]}]
            )
        except SmartSlydrApiError as err:
            # Roll back optimistic state since the command didn't take.
            self.__dict__.pop("_attr_is_on", None)
            self._optimistic_baseline = None
            self._optimistic_until = None
            self.async_write_ha_state()
            _LOGGER.warning(
                "SmartSlydr petpass set failed for %s: %s",
                self._device_id,
                err,
            )
            raise HomeAssistantError(
                f"SmartSlydr petpass command failed: {err}"
            ) from err
        self.coordinator.trigger_fast_poll()
