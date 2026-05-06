# config/custom_components/smartslydr/sensor.py

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Configuration for numeric sensors
_SENSOR_CONFIG = {
    "temperature": {"device_class": SensorDeviceClass.TEMPERATURE,    "unit": "°C"},
    "humidity":    {"device_class": SensorDeviceClass.HUMIDITY,       "unit": "%"},
    "wlansignal":  {"device_class": SensorDeviceClass.SIGNAL_STRENGTH, "unit": "dBm"},
    "sound":       {"device_class": SensorDeviceClass.SOUND_PRESSURE,  "unit": "dB"},
    "wlanmac":     {"device_class": None,                              "unit": None},
}


def _iter_devices(coordinator_data):
    for room in (coordinator_data or {}).get("rooms") or []:
        for dev in room.get("device_list") or []:
            yield dev


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SmartSlydr sensors from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for dev in _iter_devices(coordinator.data):
        for cmd in _SENSOR_CONFIG:
            if cmd in dev:
                entities.append(SmartSlydrSensor(dev, coordinator, cmd))
        if "status" in dev:
            entities.append(SmartSlydrStatusSensor(dev, coordinator))
    async_add_entities(entities)


class _SmartSlydrSensorBase(CoordinatorEntity, SensorEntity):
    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self._device_id = device["device_id"]
        self._device_name = device.get("devicename", self._device_id)

    def _device_data(self) -> dict:
        for dev in _iter_devices(self.coordinator.data):
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


class SmartSlydrSensor(_SmartSlydrSensorBase):
    """Representation of a SmartSlydr numeric sensor."""

    def __init__(self, device, coordinator, sensor_type):
        super().__init__(device, coordinator)
        self._sensor_type = sensor_type

        cfg = _SENSOR_CONFIG[sensor_type]
        if cfg["device_class"]:
            self._attr_device_class = cfg["device_class"]
        if cfg["unit"]:
            self._attr_native_unit_of_measurement = cfg["unit"]

        self._attr_name = f"{self._device_name} {sensor_type.capitalize()}"
        self._attr_unique_id = f"{self._device_id}_{sensor_type}"

    @property
    def native_value(self):
        return self._device_data().get(self._sensor_type)


class SmartSlydrStatusSensor(_SmartSlydrSensorBase):
    """Representation of a SmartSlydr device status sensor."""

    def __init__(self, device, coordinator):
        super().__init__(device, coordinator)
        self._attr_name = f"{self._device_name} Status"
        self._attr_unique_id = f"{self._device_id}_status"

    @property
    def native_value(self):
        return self._device_data().get("status")
