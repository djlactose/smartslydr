# config/custom_components/smartslydr/__init__.py

import asyncio
import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import SmartSlydrApiClient, SmartSlydrApiError, SmartSlydrAuthError
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .helpers import SmartSlydrCoordinatorData, iter_devices_in_rooms

_LOGGER = logging.getLogger(__name__)

ISSUE_UPSTREAM_UNEXPECTED = "upstream_unexpected_response"
ISSUE_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
_TRANSIENT_ISSUES = (ISSUE_UPSTREAM_UNEXPECTED, ISSUE_UPSTREAM_UNAVAILABLE)


def _create_issue(hass: HomeAssistant, key: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        key,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
    )


def _clear_transient_issues(hass: HomeAssistant) -> None:
    for key in _TRANSIENT_ISSUES:
        ir.async_delete_issue(hass, DOMAIN, key)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    session = async_get_clientsession(hass)
    base_url = entry.options.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    client = SmartSlydrApiClient(username, password, session, base_url=base_url)

    hass.data.setdefault(DOMAIN, {})

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    async def _async_update_data():
        try:
            rooms = await client.get_devices()
        except SmartSlydrAuthError as err:
            # Triggers HA's reauth flow (a "Repair credentials" card on
            # the integration page). User re-enters the password without
            # losing entity history.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmartSlydrApiError as err:
            # SmartSlydrApiError messages are sanitized at construction
            # (no upstream payload echo), safe to surface. Also signal a
            # repair issue so the user sees a clear "this is server-side"
            # explanation on the integration page.
            _create_issue(hass, ISSUE_UPSTREAM_UNEXPECTED)
            raise UpdateFailed(str(err)) from err
        except (
            aiohttp.ClientResponseError,
            aiohttp.ClientConnectorError,
            asyncio.TimeoutError,
        ) as err:
            # Retries already exhausted in the api_client. A persistent
            # network/5xx warrants a repair card too.
            _create_issue(hass, ISSUE_UPSTREAM_UNAVAILABLE)
            _LOGGER.warning("SmartSlydr backend unreachable: %s", err)
            raise UpdateFailed("Error fetching devices") from err
        except Exception as err:
            # Generic exceptions can carry whatever the underlying lib
            # decided to put in the message - keep it out of HA's UI and
            # let logs carry the detail via "from err".
            _LOGGER.exception("Unexpected error fetching devices")
            raise UpdateFailed("Error fetching devices") from err

        # Successful poll - clear any stale repair cards.
        _clear_transient_issues(hass)

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
