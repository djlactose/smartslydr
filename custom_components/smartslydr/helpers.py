# config/custom_components/smartslydr/helpers.py
"""Shared utilities for the SmartSlydr integration."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


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


def iter_devices(coordinator_data: Any) -> Iterator[dict]:
    """Yield each device dict from a coordinator-data payload, defensively.

    Convenience wrapper for entity platforms that operate on
    ``coordinator.data`` (a dict). Internal call sites that already have
    the rooms list directly should use ``iter_devices_in_rooms``.
    """
    if not isinstance(coordinator_data, dict):
        return
    yield from iter_devices_in_rooms(coordinator_data.get("rooms"))
