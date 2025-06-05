# config/custom_components/smartslydr/__init__.py
import logging
import aiohttp
import asyncio
from datetime import timedelta
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, DEFAULT_SCAN_INTERVAL, PLATFORMS
from .api_client import SmartSlydrApiClient

_LOGGER = logging.getLogger(__name__)

# Because this integration uses config entries only (no YAML), declare:
CONFIG_SCHEMA = cv.config_entry_only_config_schema

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the SmartSlydr integration (nothing to do here)."""
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up a SmartSlydr config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    # Create a ClientSession for API calls
    session = aiohttp.ClientSession()
    client = SmartSlydrApiClient(username, password, session)

    # Set up a DataUpdateCoordinator to poll /devices periodically
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=client.get_devices,
        update_interval=timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        ),
    )

    # Perform an initial data fetch; if it fails, let HA retry later
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        raise UpdateFailed("Failed to fetch initial device list from SmartSlydr API")

    # Store the client and coordinator for use in platforms
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    # Forward setup to all platforms (cover, sensor, switch), and wait for them all
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a SmartSlydr config entry."""
    # Ask HA to unload all platforms at once
    unload_ok = await hass.config_entries.async_forward_entry_unloads(entry, PLATFORMS)

    if unload_ok:
        # Clean up our saved data and close the aiohttp session
        data = hass.data[DOMAIN].pop(entry.entry_id)
        client: SmartSlydrApiClient = data["client"]

        # The `SmartSlydrApiClient` holds the ClientSession as `._session`
        # We need to close it so it doesn't keep running
        try:
            await client._session.close()
        except Exception as err:
            _LOGGER.warning("Error closing HTTP session: %s", err)

    return unload_ok
