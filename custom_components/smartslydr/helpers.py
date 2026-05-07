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
