# config/custom_components/smartslydr/__init__.py

import asyncio
import logging
from datetime import timedelta

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import SmartSlydrApiClient, SmartSlydrApiError, SmartSlydrAuthError
from .const import (
    CALIBRATED_DURATION_OPTION_PREFIX,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_RECALIBRATE_COVER,
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


# Adaptive polling: when a user issues a command, we want to see real
# state quickly (so optimistic writes get reconciled). Drop the poll
# interval to FAST_POLL_INTERVAL_S for FAST_POLL_DURATION_S, then revert.
FAST_POLL_INTERVAL_S = 5
FAST_POLL_DURATION_S = 30


class SmartSlydrCoordinator(DataUpdateCoordinator):
    """Coordinator that supports a temporary fast-poll window."""

    def __init__(self, hass: HomeAssistant, *, default_interval: timedelta, **kwargs):
        super().__init__(hass, **kwargs)
        self._default_interval = default_interval
        self._restore_handle = None

    @callback
    def trigger_fast_poll(self) -> None:
        """Drop to fast polling for FAST_POLL_DURATION_S, then restore.

        Idempotent: a second call inside the window cancels and reschedules
        the restore (the window slides) rather than nesting timers.
        """
        self.update_interval = timedelta(seconds=FAST_POLL_INTERVAL_S)
        if self._restore_handle is not None:
            self._restore_handle()
        self._restore_handle = async_call_later(
            self.hass, FAST_POLL_DURATION_S, self._restore_default_interval
        )
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _restore_default_interval(self, _now) -> None:
        self._restore_handle = None
        self.update_interval = self._default_interval


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

        # /devices.petpass and /operation/get?command=petpass look like
        # they overlap, but they're different: /devices.petpass is the
        # *configuration* (the list of allowed-pet slot entries),
        # while /operation/get returns the *current on/off* state of
        # the petpass toggle. Both are needed - the switch's is_on
        # reads this map; allowed_pets reads /devices.petpass.
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

    default_interval = timedelta(seconds=scan_interval)
    coordinator = SmartSlydrCoordinator(
        hass,
        logger=_LOGGER,
        name=DOMAIN,
        update_method=_async_update_data,
        update_interval=default_interval,
        config_entry=entry,
        default_interval=default_interval,
    )

    # Raises ConfigEntryNotReady on failure so HA retries with backoff
    # instead of marking the entry permanently failed.
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_register_services(hass)

    return True


_RECALIBRATE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_ids,
})


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration-level services. Idempotent across entries."""
    if hass.services.has_service(DOMAIN, SERVICE_RECALIBRATE_COVER):
        return

    async def _handle_recalibrate(call: ServiceCall) -> None:
        ent_reg = er.async_get(hass)
        for entity_id in call.data["entity_id"]:
            entity = ent_reg.async_get(entity_id)
            if entity is None or entity.domain != "cover":
                continue
            if not entity.unique_id.endswith("_cover"):
                continue
            device_id = entity.unique_id[: -len("_cover")]
            target_entry = hass.config_entries.async_get_entry(entity.config_entry_id)
            if target_entry is None:
                continue
            key = f"{CALIBRATED_DURATION_OPTION_PREFIX}{device_id}"
            if key not in target_entry.options:
                continue
            new_options = {k: v for k, v in target_entry.options.items() if k != key}
            hass.config_entries.async_update_entry(target_entry, options=new_options)
            _LOGGER.info(
                "Cleared calibrated move duration for %s (%s)",
                entity_id,
                device_id,
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RECALIBRATE_COVER,
        _handle_recalibrate,
        schema=_RECALIBRATE_SCHEMA,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        bucket = hass.data[DOMAIN].pop(entry.entry_id, None)
        coordinator = bucket.get("coordinator") if bucket else None
        # Cancel any in-flight fast-poll restore so we don't leak the timer.
        if isinstance(coordinator, SmartSlydrCoordinator) and coordinator._restore_handle:
            coordinator._restore_handle()
            coordinator._restore_handle = None
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a config entry to the current schema version."""
    _LOGGER.debug("Migrating SmartSlydr entry %s from v%s", entry.entry_id, entry.version)

    if entry.version == 1:
        # v1 -> v2: cover entity unique_id was the bare device_id, which
        # collides with the device-registry identifier and leaves no room
        # for future per-device entities. Rewrite to <device_id>_cover.
        ent_reg = er.async_get(hass)
        for entity in list(ent_reg.entities.values()):
            if (
                entity.config_entry_id == entry.entry_id
                and entity.domain == "cover"
                and not entity.unique_id.endswith("_cover")
            ):
                new_unique_id = f"{entity.unique_id}_cover"
                _LOGGER.info(
                    "Migrating cover %s unique_id %s -> %s",
                    entity.entity_id,
                    entity.unique_id,
                    new_unique_id,
                )
                ent_reg.async_update_entity(
                    entity.entity_id, new_unique_id=new_unique_id
                )
        hass.config_entries.async_update_entry(entry, version=2)

    return True
