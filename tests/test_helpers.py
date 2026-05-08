"""Unit tests for custom_components.smartslydr.helpers.

These touch only the helpers module (no HA fixtures required).
"""

from __future__ import annotations

from custom_components.smartslydr.helpers import (
    SmartSlydrCoordinatorData,
    iter_devices,
    iter_devices_in_rooms,
)


def test_iter_devices_in_rooms_happy_path() -> None:
    rooms = [
        {"room_name": "Den", "device_list": [{"device_id": "d1"}, {"device_id": "d2"}]},
        {"room_name": "Garage", "device_list": [{"device_id": "d3"}]},
    ]
    assert [d["device_id"] for d in iter_devices_in_rooms(rooms)] == ["d1", "d2", "d3"]


def test_iter_devices_in_rooms_skips_non_dict_rooms() -> None:
    rooms = [
        None,
        "garbage",
        {"device_list": [{"device_id": "good"}]},
        42,
    ]
    assert [d["device_id"] for d in iter_devices_in_rooms(rooms)] == ["good"]


def test_iter_devices_in_rooms_skips_non_list_device_list() -> None:
    rooms = [
        {"device_list": None},
        {"device_list": "oops"},
        {"device_list": [{"device_id": "good"}]},
    ]
    assert [d["device_id"] for d in iter_devices_in_rooms(rooms)] == ["good"]


def test_iter_devices_in_rooms_skips_non_dict_devices() -> None:
    rooms = [
        {"device_list": [None, "x", {"device_id": "good"}, 5]},
    ]
    assert [d["device_id"] for d in iter_devices_in_rooms(rooms)] == ["good"]


def test_iter_devices_in_rooms_handles_non_list_input() -> None:
    assert list(iter_devices_in_rooms(None)) == []
    assert list(iter_devices_in_rooms("oops")) == []
    assert list(iter_devices_in_rooms({"not": "a list"})) == []


def test_iter_devices_accepts_coordinator_data_instance() -> None:
    data = SmartSlydrCoordinatorData(
        rooms=[{"device_list": [{"device_id": "x"}]}],
        petpass_states={},
    )
    assert [d["device_id"] for d in iter_devices(data)] == ["x"]


def test_iter_devices_handles_none() -> None:
    assert list(iter_devices(None)) == []


def test_coordinator_data_is_frozen() -> None:
    data = SmartSlydrCoordinatorData(rooms=[], petpass_states={})
    import dataclasses

    try:
        data.rooms = [{"device_id": "x"}]  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("Expected FrozenInstanceError")
