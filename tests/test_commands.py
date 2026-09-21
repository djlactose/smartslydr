"""Cover and switch behavior when a command is rejected.

Regression coverage for a gap that let a rate-limited door look like a
working one: both entity platforms caught only SmartSlydrApiError around
set_command, so an HTTP-level rejection (a 429 from the upstream
throttle) escaped as a raw aiohttp exception with no rollback of the
optimistic state that had already been written to the UI.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartslydr.api_client import SmartSlydrRateLimitError
from custom_components.smartslydr.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

ROOMS = [
    {
        "device_list": [
            {
                "device_id": "dev1",
                "devicename": "Patio Door",
                # Starts open so a close is a real state change.
                "position": 100,
                "status": "device is online",
            }
        ]
    }
]

PETPASS_OFF = [{"device_id": "dev1", "petpass": "off"}]


@asynccontextmanager
async def _setup(hass: HomeAssistant, set_command: AsyncMock):
    """Set up the entry with every client call mocked.

    The patches stay active for the body so the service call under test
    hits the mocked set_command rather than the network.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="cmd@example.com",
        data={CONF_USERNAME: "cmd@example.com", CONF_PASSWORD: "pw"},
        version=2,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_devices",
        new=AsyncMock(return_value=ROOMS),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.get_status",
        new=AsyncMock(return_value=PETPASS_OFF),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.authenticate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.smartslydr.SmartSlydrApiClient.set_command",
        new=set_command,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        try:
            yield entry
        finally:
            # Unloading cancels the fast-poll restore timer and any
            # in-flight position animation, so a successful command
            # doesn't leave a lingering timer for the next test.
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, f"{unique_id} was not registered"
    return entity_id


@pytest.mark.asyncio
async def test_cover_rate_limited_command_raises_home_assistant_error(
    hass: HomeAssistant,
) -> None:
    set_command = AsyncMock(side_effect=SmartSlydrRateLimitError("HTTP 429"))
    async with _setup(hass, set_command):
        entity_id = _entity_id(hass, "cover", "dev1_cover")
        with pytest.raises(HomeAssistantError) as excinfo:
            await hass.services.async_call(
                "cover", "close_cover", {"entity_id": entity_id}, blocking=True
            )
        # The message has to point at the scan interval - this is the one
        # command failure the user can actually fix themselves.
        assert "rate-limiting" in str(excinfo.value)


@pytest.mark.asyncio
async def test_cover_transport_error_raises_home_assistant_error(
    hass: HomeAssistant,
) -> None:
    """A connection error used to escape the catch entirely."""
    set_command = AsyncMock(side_effect=aiohttp.ClientConnectionError("reset"))
    async with _setup(hass, set_command):
        entity_id = _entity_id(hass, "cover", "dev1_cover")
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "cover", "close_cover", {"entity_id": entity_id}, blocking=True
            )


@pytest.mark.asyncio
async def test_failed_cover_command_rolls_back_optimistic_state(
    hass: HomeAssistant,
) -> None:
    """The card must not keep animating a move that never started.

    Without the rollback the entity sat at is_closing=True until a later
    poll happened to clear it - and when the cause is an API throttle,
    that poll may itself be rejected, stretching the lie across several
    scan intervals.
    """
    set_command = AsyncMock(side_effect=SmartSlydrRateLimitError("HTTP 429"))
    async with _setup(hass, set_command):
        entity_id = _entity_id(hass, "cover", "dev1_cover")
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "cover", "close_cover", {"entity_id": entity_id}, blocking=True
            )
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state is not None
        # Back to the last polled truth, not a phantom close.
        assert state.state == "open"
        assert state.attributes["current_position"] == 100


@pytest.mark.asyncio
async def test_failed_petpass_command_rolls_back_optimistic_state(
    hass: HomeAssistant,
) -> None:
    """The switch's rollback was skipped for non-SmartSlydrApiError failures.

    A transport-level rejection left the toggle showing the requested
    state for the full optimistic safety timeout (120s).
    """
    set_command = AsyncMock(side_effect=aiohttp.ClientConnectionError("reset"))
    async with _setup(hass, set_command):
        entity_id = _entity_id(hass, "switch", "dev1_petpass")
        assert hass.states.get(entity_id).state == "off"

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": entity_id}, blocking=True
            )
        await hass.async_block_till_done()

        assert hass.states.get(entity_id).state == "off"


@pytest.mark.asyncio
async def test_successful_cover_command_is_unaffected(
    hass: HomeAssistant,
) -> None:
    """The happy path still dispatches the command."""
    set_command = AsyncMock(return_value=[])
    async with _setup(hass, set_command):
        entity_id = _entity_id(hass, "cover", "dev1_cover")
        await hass.services.async_call(
            "cover", "close_cover", {"entity_id": entity_id}, blocking=True
        )
        assert set_command.await_count == 1
