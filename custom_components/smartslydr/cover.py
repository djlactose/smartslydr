# config/custom_components/smartslydr/cover.py

import asyncio
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
from .const import (
    CALIBRATED_DURATION_OPTION_PREFIX,
    DEFAULT_MOVE_DURATION,
    DOMAIN,
    MOVE_DURATION_OPTION_PREFIX,
)
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

# How often the local interpolation timer ticks while a move is in flight.
# Fast enough that Lovelace's cover-card animation is smooth; slow enough
# not to flood HA's event bus.
_TICK_INTERVAL = 0.5

# When the polled position drifts from the interpolated estimate by more
# than this many percentage points, we cancel the animation and snap to
# truth. Catches motor jams, manual overrides, and bad calibration.
_RECONCILE_DRIFT = 10


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

    # SmartSlydr is a sliding door with a 0..100 position. CoverDeviceClass
    # .DOOR uses a binary swing-door icon that doesn't reflect the lateral
    # motion or percentage position; CURTAIN matches the actual UX better
    # (lateral, position-aware, animates with the slider).
    _attr_device_class = CoverDeviceClass.CURTAIN
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
        self._move_task: asyncio.Task | None = None
        # Tracks an in-flight calibration attempt. Populated only on
        # full-range moves (0->100 or 100->0); cleared on success,
        # interruption, or timeout.
        self._calibration_pending: dict | None = None

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
        # instance attribute (optimistic write or live interpolation);
        # fall through to the last polled value otherwise.
        if "_attr_current_cover_position" in self.__dict__:
            return self.__dict__["_attr_current_cover_position"]
        return int(self._device_data().get("position", 0) or 0)

    @property
    def is_closed(self) -> bool:
        return self.current_cover_position == 0

    def _polled_position(self) -> int:
        """Return the position from the last coordinator poll, ignoring overrides."""
        return int(self._device_data().get("position", 0) or 0)

    def _move_duration_seconds(self) -> float:
        """Resolve the move duration: manual override > calibrated > default."""
        entry = self.coordinator.config_entry
        options = entry.options if entry is not None else {}
        override = options.get(f"{MOVE_DURATION_OPTION_PREFIX}{self._device_id}")
        if override:
            try:
                return max(1.0, float(override))
            except (TypeError, ValueError):
                pass
        calibrated = options.get(
            f"{CALIBRATED_DURATION_OPTION_PREFIX}{self._device_id}"
        )
        if calibrated:
            try:
                return max(1.0, float(calibrated))
            except (TypeError, ValueError):
                pass
        return DEFAULT_MOVE_DURATION

    def _clear_optimistic_state(self) -> None:
        """Drop optimistic overrides so the coordinator value takes over."""
        for attr in (
            "_attr_current_cover_position",
            "_attr_is_opening",
            "_attr_is_closing",
        ):
            self.__dict__.pop(attr, None)

    def _cancel_move_task(self) -> None:
        if self._move_task and not self._move_task.done():
            self._move_task.cancel()

    async def _animate_to(
        self, start: int, target: int, duration: float
    ) -> None:
        """Locally interpolate position over `duration` seconds.

        Lovelace's cover card animates smoothly when current_cover_position
        ticks across intermediate values. Real state lands on the next
        coordinator poll (fast-poll mode for ~30s after the command); if
        it diverges, _handle_coordinator_update cancels this task.
        """
        loop_time = self.hass.loop.time
        t0 = loop_time()
        try:
            while True:
                elapsed = loop_time() - t0
                if elapsed >= duration:
                    break
                progress = elapsed / duration
                current = round(start + (target - start) * progress)
                self._attr_current_cover_position = current
                self.async_write_ha_state()
                await asyncio.sleep(_TICK_INTERVAL)
            # Normal completion - snap to target and stop signaling motion.
            self._attr_current_cover_position = target
            self._attr_is_opening = False
            self._attr_is_closing = False
            self.async_write_ha_state()
        except asyncio.CancelledError:
            # Stop, superseding command, or entity teardown. Leave the
            # last interpolated value in place; the next poll reconciles.
            self._attr_is_opening = False
            self._attr_is_closing = False
            self.async_write_ha_state()
            raise

    def _start_calibration_if_full_traversal(
        self, start: int, target: int
    ) -> None:
        """Begin tracking a calibration attempt for a 0->100 / 100->0 move."""
        if (start, target) not in ((0, 100), (100, 0)):
            self._calibration_pending = None
            return
        duration = self._move_duration_seconds()
        self._calibration_pending = {
            "start": start,
            "target": target,
            "t_start": time.monotonic(),
            "expires_at": time.monotonic() + duration * 3.0,
        }

    def _check_calibration(self) -> None:
        """Settle a pending calibration if the polled position has reached the target."""
        cal = self._calibration_pending
        if not cal:
            return
        now = time.monotonic()
        if now > cal["expires_at"]:
            # Move took >3x the expected duration - likely jammed or never
            # reached target. Discard the sample.
            _LOGGER.debug(
                "Calibration aborted for %s: target %d not reached within %.1fs",
                self._device_id,
                cal["target"],
                cal["expires_at"] - cal["t_start"],
            )
            self._calibration_pending = None
            return
        polled = self._polled_position()
        if abs(polled - cal["target"]) <= 2:
            observed = now - cal["t_start"]
            self._calibration_pending = None
            self._persist_calibration(observed)

    def _persist_calibration(self, duration: float) -> None:
        entry = self.coordinator.config_entry
        if entry is None:
            return
        key = f"{CALIBRATED_DURATION_OPTION_PREFIX}{self._device_id}"
        new_options = {**entry.options, key: round(duration, 2)}
        self.hass.config_entries.async_update_entry(entry, options=new_options)
        _LOGGER.info(
            "Calibrated move duration for %s: %.2fs",
            self._device_id,
            duration,
        )

    def _handle_coordinator_update(self) -> None:
        # Settle calibration first - uses the last-polled position.
        self._check_calibration()

        polled = self._polled_position()

        if self._move_task and not self._move_task.done():
            # Animation is running. Reconcile if it has drifted from
            # truth; otherwise let it keep ticking.
            estimated = self.__dict__.get("_attr_current_cover_position", polled)
            if abs(polled - estimated) > _RECONCILE_DRIFT:
                _LOGGER.debug(
                    "Cover %s interpolation drift %d -> %d, snapping",
                    self._device_id,
                    estimated,
                    polled,
                )
                self._cancel_move_task()
                self._attr_current_cover_position = polled
        else:
            # No animation - drop optimistic overrides; coordinator wins.
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
        self._cancel_move_task()
        self._calibration_pending = None
        self._attr_is_opening = False
        self._attr_is_closing = False
        self.async_write_ha_state()
        await self._send_command(
            [{"key": COMMAND_POSITION, "value": STOP_VALUE}]
        )
        self.coordinator.trigger_fast_poll()

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

        # Cancel any in-flight animation - the new command supersedes it.
        self._cancel_move_task()

        # Optimistic write so Lovelace responds immediately. _animate_to
        # then ticks the position toward `pos`; real state lands on the
        # next (fast-poll) coordinator refresh.
        start = self.current_cover_position
        self._attr_current_cover_position = start  # baseline
        self._attr_is_opening = pos > start
        self._attr_is_closing = pos < start
        self.async_write_ha_state()

        # Track calibration if this is a full-range move.
        self._start_calibration_if_full_traversal(start, pos)

        await self._send_command(
            [{"key": COMMAND_POSITION, "value": pos}]
        )

        duration = self._move_duration_seconds()
        self._move_task = self.hass.async_create_task(
            self._animate_to(start, pos, duration)
        )
        self.coordinator.trigger_fast_poll()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any in-flight animation before HA tears the entity down."""
        self._cancel_move_task()
        if self._move_task is not None:
            try:
                await self._move_task
            except (asyncio.CancelledError, Exception):
                pass
        await super().async_will_remove_from_hass()

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
