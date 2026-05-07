"""Coordinator + repair-issue lifecycle tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartslydr.api_client import SmartSlydrApiError
from custom_components.smartslydr.const import CONF_PASSWORD, CONF_USERNAME, DOMAIN

ISSUE_UPSTREAM_UNEXPECTED = "upstream_unexpected_response"


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
