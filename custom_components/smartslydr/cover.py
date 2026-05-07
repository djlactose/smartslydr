# config/custom_components/smartslydr/cover.py

import logging
import time

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import SmartSlydrApiClient, SmartSlydrApiError
from .const import DOMAIN
from .helpers import iter_devices

_LOGGER = logging.getLogger(__name__)

COMMAND_POSITION = "position"
# Sentinel for the position command meaning "stop wherever you are" -
# 0..100 are real position percentages, 200 is documented as the stop op.
STOP_VALUE = 200

# Debounce window for set-position commands. Without this, an upstream bridge
# (e.g. Home Bridge mirroring HA state) can fan out a single user action into
# multiple rapid set_command calls that confuse the device.
SET_POSITION_DEBOUNCE_S = 2.0


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SmartSlydr covers (doors/blinds) from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client: SmartSlydrApiClient = data["client"]
    coordinator = data["coordinator"]

    entities = [
        SmartSlydrCover(dev, client, coordinator)
        for dev in iter_devices(coordinator.data)
        if "position" in dev
    ]
    async_add_entities(entities)


class SmartSlydrCover(CoordinatorEntity, CoverEntity):
    """Representation of a SmartSlydr cover (e.g. door or shade)."""

    # SmartSlydr's flagship product is a sliding door. v0.4 of the
    # public REST API doesn't surface a per-device type field; if a
    # future schema does, this can be flipped to detection-based.
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_has_entity_name = True
    _attr_name = None  # primary entity inherits the device name
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

        # Suffix the unique_id so it doesn't collide with the device-
        # registry identifier and leaves room for future per-device
        # entities (e.g. a future tilt accessory). Pre-v2 covers used
        # the bare device_id; async_migrate_entry rewrites them.
        self._attr_unique_id = f"{self._device_id}_cover"

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
    def current_cover_position(self) -> int:
        # _attr_current_cover_position takes precedence when set as an
        # instance attribute (optimistic write); fall through to the
        # last polled value otherwise.
        if "_attr_current_cover_position" in self.__dict__:
            return self.__dict__["_attr_current_cover_position"]
        return int(self._device_data().get("position", 0) or 0)

    @property
    def is_closed(self) -> bool:
        return self.current_cover_position == 0

    def _clear_optimistic_state(self) -> None:
        """Drop optimistic overrides so the coordinator value takes over."""
        for attr in (
            "_attr_current_cover_position",
            "_attr_is_opening",
            "_attr_is_closing",
        ):
            self.__dict__.pop(attr, None)

    def _handle_coordinator_update(self) -> None:
        # Coordinator just polled - real state is now authoritative.
        self._clear_optimistic_state()
        super()._handle_coordinator_update()

    async def async_open_cover(self, **kwargs) -> None:
        await self.async_set_cover_position(position=100)

    async def async_close_cover(self, **kwargs) -> None:
        await self.async_set_cover_position(position=0)

    async def async_stop_cover(self, **kwargs) -> None:
        # Stop intentionally bypasses SET_POSITION_DEBOUNCE_S - the
        # debounce was added to suppress duplicate set-position fan-out
        # from upstream bridges, not to swallow user-initiated stops.
        self._attr_is_opening = False
        self._attr_is_closing = False
        self.async_write_ha_state()
        await self._send_command(
            [{"key": COMMAND_POSITION, "value": STOP_VALUE}]
        )
        await self.coordinator.async_request_refresh()

    async def async_set_cover_position(self, **kwargs) -> None:
        pos = kwargs.get("position")
        if pos is None:
            return
        now = time.monotonic()
        if now - self._last_set_position_at < SET_POSITION_DEBOUNCE_S:
            # Debounce hit: silently drop. Do NOT raise or write
            # optimistic state - this is deliberate suppression, not
            # a failure, and the API call never went out.
            return
        self._last_set_position_at = now

        # Optimistic write so Lovelace responds immediately. Real state
        # lands on the next (fast-poll) coordinator refresh, which
        # _handle_coordinator_update clears optimistic overrides for.
        start = self.current_cover_position
        self._attr_current_cover_position = pos
        self._attr_is_opening = pos > start
        self._attr_is_closing = pos < start
        self.async_write_ha_state()

        await self._send_command(
            [{"key": COMMAND_POSITION, "value": pos}]
        )
        await self.coordinator.async_request_refresh()

    async def _send_command(self, commands: list[dict]) -> None:
        """Send a set_command for this device, surfacing failures to HA."""
        try:
            await self._client.set_command(
                [{"device_id": self._device_id, "commands": commands}]
            )
        except SmartSlydrApiError as err:
            _LOGGER.warning(
                "SmartSlydr set_command failed for %s: %s",
                self._device_id,
                err,
            )
            raise HomeAssistantError(
                f"SmartSlydr command failed: {err}"
            ) from err
