# config/custom_components/smartslydr/sensor.py

import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .api_client import SmartSlydrApiClient

_LOGGER = logging.getLogger(__name__)

# Configuration for numeric sensors
_SENSOR_CONFIG = {
    "temperature": {"device_class": SensorDeviceClass.TEMPERATURE,    "unit": "°C"},
    "humidity":    {"device_class": SensorDeviceClass.HUMIDITY,       "unit": "%"},
    "wlansignal":  {"device_class": SensorDeviceClass.SIGNAL_STRENGTH, "unit": "dBm"},
    "sound":       {"device_class": SensorDeviceClass.SOUND_PRESSURE,  "unit": "dB"},
    "wlanmac":     {"device_class": None,                             "unit": None},
}

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SmartSlydr sensors from config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client: SmartSlydrApiClient = data["client"]
    coordinator = data["coordinator"]

    entities = []
    for room in coordinator.data or []:
        for dev in room.get("device_list", []) or []:
            # Numeric sensors
            for cmd in _SENSOR_CONFIG:
                if cmd in dev:
                    entities.append(
                        SmartSlydrSensor(dev, client, coordinator, cmd)
                    )
            # Status sensor
            if "status" in dev:
                entities.append(
                    SmartSlydrStatusSensor(dev, coordinator)
                )
    async_add_entities(entities)

class SmartSlydrSensor(CoordinatorEntity, SensorEntity):
    """Representation of a SmartSlydr numeric sensor."""

    def __init__(self, device, client, coordinator, sensor_type):
        super().__init__(coordinator)
        self._device = device
        self._client = client
        self._sensor_type = sensor_type

        cfg = _SENSOR_CONFIG[sensor_type]
        if cfg["device_class"]:
            self._attr_device_class = cfg["device_class"]
        if cfg["unit"]:
            self._attr_native_unit_of_measurement = cfg["unit"]

        self._attr_name = f"{device['devicename']} {sensor_type.capitalize()}"
        self._attr_unique_id = f"{device['device_id']}_{sensor_type}"
        self._state = device.get(sensor_type)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device["device_id"])},
            "name": self._device.get("devicename"),
            "manufacturer": "SmartSlydr",
        }

    @property
    def native_value(self):
        return self._state

    async def async_update(self):
        response = await self._client.get_status([
            {"device_id": self._device["device_id"], "command": self._sensor_type}
        ])
        for res in response:
            if self._sensor_type in res:
                self._state = res[self._sensor_type]
                break

class SmartSlydrStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a SmartSlydr device status sensor."""

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self._device = device
        # Initial state from device list
        self._state = device.get("status")

        self._attr_name = f"{device['devicename']} Status"
        self._attr_unique_id = f"{device['device_id']}_status"
        self._attr_device_class = None
        self._attr_native_unit_of_measurement = None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device["device_id"])},
            "name": self._device.get("devicename"),
            "manufacturer": "SmartSlydr",
        }

    @property
    def native_value(self):
        return self._state

    async def async_update(self):
        # Coordinator data refresh includes the 'status' field
        for room in self.coordinator.data or []:
            for dev in room.get("device_list", []) or []:
                if dev.get("device_id") == self._device.get("device_id"):
                    self._state = dev.get("status")
                    return
