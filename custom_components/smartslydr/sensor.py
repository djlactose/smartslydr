# config/custom_components/smartslydr/sensor.py

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .helpers import iter_devices

_LOGGER = logging.getLogger(__name__)

# Configuration for numeric/string sensors keyed off /devices fields.
# `name` is what HA renders after the device name (because
# _attr_has_entity_name is True on the entity classes).
_SENSOR_CONFIG = {
    "temperature": {"device_class": SensorDeviceClass.TEMPERATURE,     "unit": "°C",  "name": "Temperature"},
    "humidity":    {"device_class": SensorDeviceClass.HUMIDITY,        "unit": "%",   "name": "Humidity"},
    "wlansignal":  {"device_class": SensorDeviceClass.SIGNAL_STRENGTH, "unit": "dBm", "name": "WLAN signal"},
    "sound":       {"device_class": SensorDeviceClass.SOUND_PRESSURE,  "unit": "dB",  "name": "Sound"},
    "wlanmac":     {"device_class": None,                              "unit": None, "name": "WLAN MAC"},
}


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SmartSlydr sensors from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = []
    for dev in iter_devices(coordinator.data):
        for cmd in _SENSOR_CONFIG:
            if cmd in dev:
                entities.append(SmartSlydrSensor(dev, coordinator, cmd))
        if "status" in dev:
            entities.append(SmartSlydrStatusSensor(dev, coordinator))
    async_add_entities(entities)


class _SmartSlydrSensorBase(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self._device_id = device["device_id"]
        self._device_name = device.get("devicename", self._device_id)

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

        self._attr_name = cfg["name"]
        self._attr_unique_id = f"{self._device_id}_{sensor_type}"

    @property
    def native_value(self):
        return self._device_data().get(self._sensor_type)


class SmartSlydrStatusSensor(_SmartSlydrSensorBase):
    """Representation of a SmartSlydr device status sensor."""

    def __init__(self, device, coordinator):
        super().__init__(device, coordinator)
        self._attr_name = "Status"
        self._attr_unique_id = f"{self._device_id}_status"

    @property
    def native_value(self):
        return self._device_data().get("status")
