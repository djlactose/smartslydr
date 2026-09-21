"""Coordinator behavior when the upstream API rate-limits the account.

Background: AWS API Gateway enforces an undocumented per-account throttle
in front of the SmartSlydr backend, and reads and writes share that
quota. A user running a 10-second scan interval saw /operation/get
rejected with HTTP 429 on 1176 consecutive polls over four hours - and,
because writes share the quota, their open/close commands were being
rejected the whole time. The integration reported healthy throughout,
because get_status failures were swallowed into a per-poll warning and
the poll still counted as a success.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartslydr import (
    ISSUE_RATE_LIMITED,
    ISSUE_UPSTREAM_UNAVAILABLE,
    ISSUE_UPSTREAM_UNEXPECTED,
    RATE_LIMIT_STRIKES,
)
from custom_components.smartslydr.api_client import SmartSlydrRateLimitError
from custom_components.smartslydr.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

ROOMS = [
    {
        "device_list": [
            {
                "device_id": "dev1",
                "devicename": "Patio Door",
                "position": 100,
                "status": "device is online",
            }
        ]
    }
]


def _entry(hass: HomeAssistant, unique_id: str, **kwargs) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        data={CONF_USERNAME: unique_id, CONF_PASSWORD: "pw"},
        version=2,
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


def _coordinator(hass: HomeAssistant, entry: MockConfigEntry):
    return hass.data[DOMAIN][entry.entry_id]["coordinator"]


def _patched(get_devices=None, get_status=None):
    """Patch the three client calls the coordinator touches."""
    return (
        patch(
            "custom_components.smartslydr.SmartSlydrApiClient.get_devices",
            new=get_devices or AsyncMock(return_value=ROOMS),
        ),
        patch(
            "custom_components.smartslydr.SmartSlydrApiClient.get_status",
            new=get_status or AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.smartslydr.SmartSlydrApiClient.authenticate",
            new=AsyncMock(return_value=None),
        ),
    )


@pytest.mark.asyncio
async def test_sustained_rate_limit_creates_repair_issue(
    hass: HomeAssistant,
) -> None:
    """A run of 429s must escalate instead of being swallowed forever."""
    entry = _entry(hass, "strikes@example.com")
    get_status = AsyncMock(return_value=[])
    devices_p, status_p, auth_p = _patched(get_status=get_status)

    with devices_p, status_p, auth_p:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        issue_reg = ir.async_get(hass)
        assert issue_reg.async_get_issue(DOMAIN, ISSUE_RATE_LIMITED) is None

        get_status.side_effect = SmartSlydrRateLimitError("HTTP 429")
        coordinator = _coordinator(hass, entry)

        # One short of the threshold: a blip is still ridden out quietly.
        for _ in range(RATE_LIMIT_STRIKES - 1):
            await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert issue_reg.async_get_issue(DOMAIN, ISSUE_RATE_LIMITED) is None

        await coordinator.async_refresh()
        await hass.async_block_till_done()

    issue = issue_reg.async_get_issue(DOMAIN, ISSUE_RATE_LIMITED)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING


@pytest.mark.asyncio
async def test_rate_limit_issue_clears_once_a_poll_gets_through(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass, "clears@example.com")
    get_status = AsyncMock(side_effect=SmartSlydrRateLimitError("HTTP 429"))
    devices_p, status_p, auth_p = _patched(get_status=get_status)

    with devices_p, status_p, auth_p:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = _coordinator(hass, entry)
        for _ in range(RATE_LIMIT_STRIKES):
            await coordinator.async_refresh()
        await hass.async_block_till_done()

        issue_reg = ir.async_get(hass)
        assert issue_reg.async_get_issue(DOMAIN, ISSUE_RATE_LIMITED) is not None

        get_status.side_effect = None
        get_status.return_value = []
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert issue_reg.async_get_issue(DOMAIN, ISSUE_RATE_LIMITED) is None
    assert coordinator.rate_limit_strikes == 0


@pytest.mark.asyncio
async def test_successful_devices_poll_does_not_clear_rate_limit_issue(
    hass: HomeAssistant,
) -> None:
    """/devices and /operation/get are throttled independently.

    Clearing the card off the back of a working /devices call is exactly
    how a sustained /operation/get throttle stayed invisible, so
    ISSUE_RATE_LIMITED is deliberately excluded from _TRANSIENT_ISSUES.
    """
    entry = _entry(hass, "independent@example.com")
    devices_p, status_p, auth_p = _patched(
        get_status=AsyncMock(side_effect=SmartSlydrRateLimitError("HTTP 429"))
    )

    with devices_p, status_p, auth_p:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = _coordinator(hass, entry)
        for _ in range(RATE_LIMIT_STRIKES + 3):
            await coordinator.async_refresh()
        await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    # /devices succeeded on every one of those polls, so the poll itself
    # still counts as a success - the card is what carries the signal.
    assert coordinator.last_update_success is True
    assert issue_reg.async_get_issue(DOMAIN, ISSUE_RATE_LIMITED) is not None


@pytest.mark.asyncio
async def test_rate_limited_devices_call_does_not_blame_the_backend(
    hass: HomeAssistant,
) -> None:
    """A 429 on /devices must not raise an "unreachable backend" card."""
    entry = _entry(hass, "devices429@example.com")
    devices_p, status_p, auth_p = _patched(
        get_devices=AsyncMock(side_effect=SmartSlydrRateLimitError("HTTP 429"))
    )

    with devices_p, status_p, auth_p:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, ISSUE_UPSTREAM_UNAVAILABLE) is None
    assert issue_reg.async_get_issue(DOMAIN, ISSUE_UPSTREAM_UNEXPECTED) is None


@pytest.mark.asyncio
async def test_sub_minimum_scan_interval_is_clamped(hass: HomeAssistant) -> None:
    """Entries stored under the old 10s-minimum schema must be clamped.

    The options flow now refuses anything below MIN_SCAN_INTERVAL, but
    entries configured before that - or edited directly in
    core.config_entries - still carry the old value, and that value is
    what gets the account throttled.
    """
    entry = _entry(hass, "low@example.com", options={CONF_SCAN_INTERVAL: 10})
    devices_p, status_p, auth_p = _patched()

    with devices_p, status_p, auth_p:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = _coordinator(hass, entry)
    assert coordinator.update_interval == timedelta(seconds=MIN_SCAN_INTERVAL)


@pytest.mark.asyncio
async def test_scan_interval_above_the_floor_is_respected(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass, "ok@example.com", options={CONF_SCAN_INTERVAL: 120})
    devices_p, status_p, auth_p = _patched()

    with devices_p, status_p, auth_p:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = _coordinator(hass, entry)
    assert coordinator.update_interval == timedelta(seconds=120)


@pytest.mark.asyncio
async def test_non_numeric_scan_interval_falls_back_to_default(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass, "junk@example.com", options={CONF_SCAN_INTERVAL: "nonsense"})
    devices_p, status_p, auth_p = _patched()

    with devices_p, status_p, auth_p:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = _coordinator(hass, entry)
    assert coordinator.update_interval == timedelta(seconds=300)
