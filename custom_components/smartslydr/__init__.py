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

from .api_client import (
    SmartSlydrApiClient,
    SmartSlydrApiError,
    SmartSlydrAuthError,
    SmartSlydrRateLimitError,
)
from .const import (
    CALIBRATED_DURATION_OPTION_PREFIX,
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    PLATFORMS,
    SERVICE_RECALIBRATE_COVER,
)
from .helpers import (
    SmartSlydrCoordinatorData,
    coerce_petpass_bool,
    iter_devices_in_rooms,
)

_LOGGER = logging.getLogger(__name__)

ISSUE_UPSTREAM_UNEXPECTED = "upstream_unexpected_response"
ISSUE_UPSTREAM_UNAVAILABLE = "upstream_unavailable"
ISSUE_RATE_LIMITED = "rate_limited"

# Cleared by any successful /devices poll. ISSUE_RATE_LIMITED is
# deliberately NOT in here: /devices and /operation/get are throttled
# independently, so a working /devices poll says nothing about whether
# we're still being rate-limited - and clearing the card on that basis is
# exactly how a four-hour throttle stayed invisible.
_TRANSIENT_ISSUES = (ISSUE_UPSTREAM_UNEXPECTED, ISSUE_UPSTREAM_UNAVAILABLE)

# How many consecutive rate-limited polls before we raise the repair
# card. A single 429 is a blip worth riding out silently; a run of them
# is a configuration problem the user has to act on. Five polls is
# ~2.5 minutes at the minimum scan interval.
RATE_LIMIT_STRIKES = 5

# Once the card is up, re-log at this interval (in polls) so a long
# throttle leaves a trail without the 1176-identical-warnings problem
# that made the original report so hard to read.
RATE_LIMIT_LOG_EVERY = 50


def _create_issue(hass: HomeAssistant, key: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        key,
        # Fixable so the card has a "Submit" button that runs a retry
        # via repairs.py instead of only the dismiss-forever "Ignore"
        # button. Submit doesn't mark the issue dismissed-by-version,
        # so the card reappears if the underlying problem recurs.
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
    )


def _clear_transient_issues(hass: HomeAssistant) -> None:
    for key in _TRANSIENT_ISSUES:
        ir.async_delete_issue(hass, DOMAIN, key)


# Adaptive polling: when a user issues a command, we want to see real
# state quickly (so optimistic writes get reconciled). Drop the poll
# interval to FAST_POLL_INTERVAL_S for FAST_POLL_DURATION_S, then revert.
#
# 5s/30s was the original (12 requests in 30s, since each poll hits
# /devices and /operation/get). That tripped 429s on the upstream AWS
# API Gateway when several covers were polled concurrently. 10s/30s
# gives the local interpolation enough reconciliation samples while
# halving the request rate during the fast window.
FAST_POLL_INTERVAL_S = 10
FAST_POLL_DURATION_S = 30


class SmartSlydrCoordinator(DataUpdateCoordinator):
    """Coordinator that supports a temporary fast-poll window."""

    def __init__(self, hass: HomeAssistant, *, default_interval: timedelta, **kwargs):
        super().__init__(hass, **kwargs)
        self._default_interval = default_interval
        self._restore_handle = None
        # Consecutive polls rejected with HTTP 429, from either endpoint.
        # Drives the rate-limit repair card and its log throttling.
        self.rate_limit_strikes = 0
        # Consecutive non-429 /operation/get failures. Tracked separately
        # only so the warning that used to fire on every poll can be
        # throttled the same way.
        self.petpass_failures = 0

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


def _resolve_scan_interval(entry: ConfigEntry) -> int:
    """Return the entry's scan interval, clamped to MIN_SCAN_INTERVAL."""
    configured = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    try:
        seconds = int(configured)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "Ignoring non-numeric scan_interval %r; using %ds",
            configured,
            DEFAULT_SCAN_INTERVAL,
        )
        return DEFAULT_SCAN_INTERVAL
    if seconds < MIN_SCAN_INTERVAL:
        _LOGGER.warning(
            "Configured scan interval of %ds is below the %ds minimum and "
            "would get this account rate-limited by the SmartSlydr API "
            "(which would also start rejecting open/close commands). "
            "Polling at %ds instead - update the value in the "
            "integration's options to silence this warning",
            seconds,
            MIN_SCAN_INTERVAL,
            MIN_SCAN_INTERVAL,
        )
        return MIN_SCAN_INTERVAL
    return seconds


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    session = async_get_clientsession(hass)
    base_url = entry.options.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    client = SmartSlydrApiClient(username, password, session, base_url=base_url)

    hass.data.setdefault(DOMAIN, {})

    # Clamp rather than trust the stored value. The options flow used to
    # accept a 10s minimum, so entries configured under the old schema
    # still carry sub-floor values and would otherwise keep polling at a
    # rate that gets the account throttled. Re-validating here also
    # covers options edited directly in core.config_entries.
    scan_interval = _resolve_scan_interval(entry)

    @callback
    def _note_rate_limit(err: Exception) -> None:
        """Record a throttled poll, escalating once it stops being a blip.

        Logging is throttled deliberately. The previous code logged one
        warning per failed poll, which on a real report meant 1176
        identical lines in four hours - enough noise to bury the two
        lines that actually explained the outage.
        """
        coordinator.rate_limit_strikes += 1
        strikes = coordinator.rate_limit_strikes
        if strikes < RATE_LIMIT_STRIKES:
            _LOGGER.debug("SmartSlydr rate-limited (strike %d): %s", strikes, err)
            return
        if strikes == RATE_LIMIT_STRIKES:
            _LOGGER.warning(
                "SmartSlydr has rate-limited this account on %d consecutive "
                "polls (scan interval %ds). Open/close commands share the "
                "same quota and are likely being rejected too. Raise the "
                "scan interval in the integration's options: %s",
                strikes,
                scan_interval,
                err,
            )
            _create_issue(hass, ISSUE_RATE_LIMITED)
            return
        if strikes % RATE_LIMIT_LOG_EVERY == 0:
            _LOGGER.warning(
                "SmartSlydr is still rate-limiting this account "
                "(%d consecutive polls): %s",
                strikes,
                err,
            )

    @callback
    def _clear_rate_limit() -> None:
        if coordinator.rate_limit_strikes:
            if coordinator.rate_limit_strikes >= RATE_LIMIT_STRIKES:
                _LOGGER.info(
                    "SmartSlydr rate limit cleared after %d throttled polls",
                    coordinator.rate_limit_strikes,
                )
            coordinator.rate_limit_strikes = 0
        ir.async_delete_issue(hass, DOMAIN, ISSUE_RATE_LIMITED)

    @callback
    def _note_petpass_failure(err: Exception) -> None:
        """Log a non-throttle /operation/get failure, with the same throttling."""
        coordinator.petpass_failures += 1
        failures = coordinator.petpass_failures
        if failures == 1 or failures % RATE_LIMIT_LOG_EVERY == 0:
            _LOGGER.warning(
                "Failed to fetch petpass states on %d consecutive poll(s) "
                "(keeping last known): %s",
                failures,
                err,
            )
        else:
            _LOGGER.debug("Failed to fetch petpass states (attempt %d): %s", failures, err)

    async def _async_update_data():
        try:
            rooms = await client.get_devices()
        except SmartSlydrAuthError as err:
            # Triggers HA's reauth flow (a "Repair credentials" card on
            # the integration page). User re-enters the password without
            # losing entity history.
            raise ConfigEntryAuthFailed(str(err)) from err
        except SmartSlydrRateLimitError as err:
            # Caught ahead of SmartSlydrApiError (its parent) so the user
            # gets the rate-limit card, which tells them to raise the
            # scan interval, rather than one blaming the backend.
            _note_rate_limit(err)
            raise UpdateFailed(str(err)) from err
        except SmartSlydrApiError as err:
            # SmartSlydrApiError messages are sanitized at construction
            # (no upstream payload echo), safe to surface. Also signal a
            # repair issue so the user sees a clear "this is server-side"
            # explanation on the integration page.
            _create_issue(hass, ISSUE_UPSTREAM_UNEXPECTED)
            raise UpdateFailed(str(err)) from err
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            # aiohttp.ClientError is the parent of ClientResponseError,
            # ClientConnectorError, and aiohttp.InvalidURL. OSError covers
            # raw socket/DNS errors that aren't always wrapped. Any of
            # these warrants a repair card with the URL-reset fix flow.
            _create_issue(hass, ISSUE_UPSTREAM_UNAVAILABLE)
            _LOGGER.warning(
                "SmartSlydr backend unreachable (%s): %s",
                type(err).__name__,
                err,
            )
            raise UpdateFailed("Error fetching devices") from err
        except Exception as err:
            # Truly unexpected (likely a Python-side bug). Still surface a
            # repair card so the user has access to the URL-reset fix
            # flow if that turns out to be the underlying cause; the full
            # traceback hits the log so the bug stays diagnosable.
            _create_issue(hass, ISSUE_UPSTREAM_UNAVAILABLE)
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
        #
        # On a transient failure (429, network blip), retain the previous
        # poll's petpass_states - clearing them would flip every petpass
        # switch to OFF in the UI for one cycle, which is a worse lie
        # than showing slightly stale data.
        prev = coordinator.data
        prev_petpass = prev.petpass_states if prev is not None else {}
        petpass_states: dict[str, bool] = dict(prev_petpass)
        rate_limited = False
        if device_ids:
            commands = [{"device_id": did, "command": "petpass"} for did in device_ids]
            try:
                statuses = await client.get_status(commands)
            except SmartSlydrRateLimitError as err:
                # Keep the last-known states (see above), but unlike any
                # other failure here this one has to escalate: being
                # throttled on /operation/get means writes to /operation
                # are being rejected too, so the user's open/close
                # commands are silently failing while the integration
                # otherwise looks perfectly healthy.
                _note_rate_limit(err)
                rate_limited = True
            except Exception as err:
                _note_petpass_failure(err)
            else:
                coordinator.petpass_failures = 0
                # Success: replace with fresh values rather than merging,
                # so a removed device drops out cleanly.
                petpass_states = {}
                for st in statuses or []:
                    did = st.get("device_id")
                    if did is None or "petpass" not in st:
                        continue
                    raw = st.get("petpass")
                    parsed = coerce_petpass_bool(raw)
                    if parsed is None:
                        # Unrecognized shape - log once at warning and
                        # keep the previous value if any.
                        _LOGGER.warning(
                            "Unrecognized petpass value for %s: %r (type %s)",
                            did,
                            raw,
                            type(raw).__name__,
                        )
                        if did in prev_petpass:
                            petpass_states[did] = prev_petpass[did]
                        continue
                    petpass_states[did] = parsed

        # This poll got through without a throttle rejection, so retire
        # the strike count and the card. Done at the end of the poll
        # rather than off the back of a successful /devices call: the two
        # endpoints are throttled independently, and clearing on /devices
        # alone is what let a sustained /operation/get throttle keep
        # presenting as a healthy integration.
        if not rate_limited:
            _clear_rate_limit()

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

    # Reload when the user saves the options form so changes to
    # base_url, scan_interval, etc. take effect immediately. async_on_unload
    # ensures the listener is removed during reload so we don't accumulate
    # one per setup cycle.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _async_register_services(hass)

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so the running client picks up new options."""
    await hass.config_entries.async_reload(entry.entry_id)


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
