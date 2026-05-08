"""Coordinator + repair-issue lifecycle tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartslydr.api_client import SmartSlydrApiError
from custom_components.smartslydr.const import CONF_PASSWORD, CONF_USERNAME, DOMAIN

ISSUE_UPSTREAM_UNEXPECTED = "upstream_unexpected_response"
ISSUE_UPSTREAM_UNAVAILABLE = "upstream_unavailable"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "pw"},
        version=2,
    )
    e.add_to_hass(hass)
    return e


@pytest.mark.asyncio
async def test_unexpected_response_creates_repair_issue(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    with patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_devices",
        new=AsyncMock(side_effect=SmartSlydrApiError("unexpected")),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_status",
        new=AsyncMock(return_value=[]),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.authenticate",
        new=AsyncMock(return_value=None),
    ):
        # Setup is expected to fail (ConfigEntryNotReady from first refresh
        # raising UpdateFailed). What we care about is the issue.
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ISSUE_UPSTREAM_UNEXPECTED)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING


@pytest.mark.asyncio
async def test_successful_poll_clears_repair_issue(hass: HomeAssistant) -> None:
    entry = _entry(hass)

    # Pre-create a stale repair issue, then verify it disappears after a
    # successful coordinator update.
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_UPSTREAM_UNEXPECTED,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UPSTREAM_UNEXPECTED,
    )

    with patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_devices",
        new=AsyncMock(return_value=[]),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_status",
        new=AsyncMock(return_value=[]),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.authenticate",
        new=AsyncMock(return_value=None),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, ISSUE_UPSTREAM_UNEXPECTED) is None


@pytest.mark.asyncio
async def test_invalid_url_creates_unavailable_issue(hass: HomeAssistant) -> None:
    """A bad/unreachable base URL must surface a repair card.

    Regression: the coordinator's exception catch was originally narrow
    (ClientResponseError, ClientConnectorError, TimeoutError), so an
    aiohttp.InvalidURL raised when the user pasted a malformed base URL
    fell through to a bare-Exception branch that logged but did NOT
    create the repair card. The user was left with no in-UI way to
    reset the URL via the fix flow. The catch was widened to
    aiohttp.ClientError + OSError + bare Exception, all of which now
    create ISSUE_UPSTREAM_UNAVAILABLE.
    """
    entry = _entry(hass)
    with patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_devices",
        new=AsyncMock(side_effect=aiohttp.InvalidURL("not a real url")),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_status",
        new=AsyncMock(return_value=[]),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.authenticate",
        new=AsyncMock(return_value=None),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ISSUE_UPSTREAM_UNAVAILABLE)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING


@pytest.mark.asyncio
async def test_oserror_creates_unavailable_issue(hass: HomeAssistant) -> None:
    """A raw OSError (DNS / socket failure) must also surface the repair card."""
    entry = _entry(hass)
    with patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_devices",
        new=AsyncMock(side_effect=OSError("nodename nor servname provided")),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_status",
        new=AsyncMock(return_value=[]),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.authenticate",
        new=AsyncMock(return_value=None),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, ISSUE_UPSTREAM_UNAVAILABLE)
    assert issue is not None
