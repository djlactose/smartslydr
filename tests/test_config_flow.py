"""Config flow + reauth tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartslydr.api_client import SmartSlydrAuthError
from custom_components.smartslydr.const import CONF_PASSWORD, CONF_USERNAME, DOMAIN


@pytest.mark.asyncio
async def test_user_step_happy_path(hass: HomeAssistant) -> None:
    """A valid email + password creates a config entry."""
    with patch(
        "custom_components.smartslydr.config_flow.SmartSlydrApiClient.authenticate",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "pw"},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "user@example.com"
    assert result2["data"][CONF_USERNAME] == "user@example.com"


@pytest.mark.asyncio
async def test_user_step_auth_failed_shows_error(hass: HomeAssistant) -> None:
    """A 401-equivalent surfaces as auth_failed in the form errors."""
    with patch(
        "custom_components.smartslydr.config_flow.SmartSlydrApiClient.authenticate",
        new=AsyncMock(side_effect=SmartSlydrAuthError("nope")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "wrong"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "auth_failed"}


@pytest.mark.asyncio
async def test_duplicate_username_aborts(hass: HomeAssistant) -> None:
    """Adding the same email twice aborts via unique_id."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "pw"},
    )
    existing.add_to_hass(hass)

    with patch(
        "custom_components.smartslydr.config_flow.SmartSlydrApiClient.authenticate",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "pw"},
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_reauth_confirm_updates_password(hass: HomeAssistant) -> None:
    """Reauth flow updates the entry's password without changing identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "old"},
        version=2,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.smartslydr.config_flow.SmartSlydrApiClient.authenticate",
        new=AsyncMock(return_value=None),
    ):
        # HA invokes reauth via entry.async_start_reauth in real life, but
        # the flow's entry point is async_step_reauth(entry_data).
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
            data=entry.data,
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: "new"},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert hass.config_entries.async_get_entry(entry.entry_id).data[CONF_PASSWORD] == "new"
