# config/custom_components/smartslydr/__init__.py

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import SmartSlydrApiClient, SmartSlydrApiError
from .const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .helpers import SmartSlydrCoordinatorData, iter_devices_in_rooms

_LOGGER = logging.getLogger(__name__)

DEBUG_BOOLEAN = "input_boolean.smartslydr_debug_mode"


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})

    def _refresh_debug_state() -> None:
        state = hass.states.get(DEBUG_BOOLEAN)
        hass.data.setdefault(DOMAIN, {})["debug"] = bool(state and state.state == "on")

    @callback
    def _on_debug_change(event) -> None:
        _refresh_debug_state()
        _LOGGER.debug("SmartSlydr debug logging set to %s", hass.data[DOMAIN].get("debug"))

    _refresh_debug_state()
    async_track_state_change_event(hass, [DEBUG_BOOLEAN], _on_debug_change)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    session = async_get_clientsession(hass)
    client = SmartSlydrApiClient(username, password, session, hass=hass)

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    async def _async_update_data():
        try:
            rooms = await client.get_devices()
        except SmartSlydrApiError as err:
            # SmartSlydrApiError messages are sanitized at construction
            # (no upstream payload echo), safe to surface.
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            # Generic exceptions can carry whatever the underlying lib
            # decided to put in the message - keep it out of HA's UI and
            # let logs carry the detail via "from err".
            _LOGGER.exception("Unexpected error fetching devices")
            raise UpdateFailed("Error fetching devices") from err

        device_ids = [
            dev["device_id"]
            for dev in iter_devices_in_rooms(rooms)
            if dev.get("device_id")
        ]

        petpass_states: dict[str, bool] = {}
        if device_ids:
            commands = [{"device_id": did, "command": "petpass"} for did in device_ids]
            try:
                statuses = await client.get_status(commands)
                for st in statuses or []:
                    did = st.get("device_id")
                    if did is not None and "petpass" in st:
                        petpass_states[did] = bool(st.get("petpass"))
            except Exception as err:
                _LOGGER.warning("Failed to fetch petpass states: %s", err)

        return SmartSlydrCoordinatorData(rooms=rooms, petpass_states=petpass_states)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=_async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    # Raises ConfigEntryNotReady on failure so HA retries with backoff
    # instead of marking the entry permanently failed.
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
