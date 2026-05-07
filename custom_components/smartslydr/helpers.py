# config/custom_components/smartslydr/helpers.py
"""Shared utilities and types for the SmartSlydr integration."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SmartSlydrCoordinatorData:
    """Snapshot of one coordinator update.

    ``rooms`` is the raw ``room_lists`` list returned by ``/devices``;
    individual rooms may still be malformed (a non-dict slips through),
    which is why entity platforms iterate via ``iter_devices``.

    ``petpass_states`` maps device_id -> on/off, sourced from a
    ``/operation/get`` call alongside the ``/devices`` poll.
    """

    rooms: list = field(default_factory=list)
    petpass_states: dict[str, bool] = field(default_factory=dict)


_TRUTHY_STRINGS = frozenset({"true", "1", "on", "yes", "enabled"})
_FALSY_STRINGS = frozenset({"false", "0", "off", "no", "disabled", ""})


def coerce_petpass_bool(value) -> bool | None:
    """Interpret an /operation/get petpass field as a clean bool.

    The SmartSlydr backend has been observed returning the petpass
    state as either a JSON bool, an int (0/1), or a string ("on"/"off",
    "0"/"1", etc.). Python's plain bool() coercion is dangerous on
    strings because every non-empty string is truthy, so bool("off")
    and bool("0") both return True - which is exactly the bug that
    made the switch stick at "on" regardless of the actual device
    state.

    Returns None if the value is missing or in an unrecognized shape,
    so callers can decide whether to ignore it or fall back to the
    previous polled value.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _TRUTHY_STRINGS:
            return True
        if s in _FALSY_STRINGS:
            return False
        return None
    return None


def iter_devices_in_rooms(rooms: Any) -> Iterator[dict]:
    """Yield each device dict from a room_lists payload, defensively.

    Skips any room or device that isn't a dict, and any room missing a
    list-typed device_list. The defensive shape checks live here so call
    sites don't each have to repeat them.
    """
    if not isinstance(rooms, list):
        return
    for room in rooms:
        if not isinstance(room, dict):
            continue
        device_list = room.get("device_list")
        if not isinstance(device_list, list):
            continue
        for dev in device_list:
            if isinstance(dev, dict):
                yield dev


def iter_devices(data: Any) -> Iterator[dict]:
    """Yield each device dict from coordinator data, defensively.

    Accepts either a ``SmartSlydrCoordinatorData`` instance (the modern
    shape) or ``None`` (during the brief window before the first
    successful refresh). Internal call sites that already have the
    rooms list directly should use ``iter_devices_in_rooms``.
    """
    if data is None:
        return
    rooms = getattr(data, "rooms", None)
    yield from iter_devices_in_rooms(rooms)
