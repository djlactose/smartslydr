"""Migration tests for v1 -> v2 entry schema."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartslydr.const import CONF_PASSWORD, CONF_USERNAME, DOMAIN


@pytest.mark.asyncio
async def test_migrate_v1_cover_unique_id_to_v2(hass: HomeAssistant) -> None:
    """A v1 entry with a cover unique_id = device_id is rewritten to <id>_cover."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)

    # Pre-create a v1-shaped cover entity in the registry.
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        domain="cover",
        platform=DOMAIN,
        unique_id="device-abc",
        config_entry=entry,
        suggested_object_id="living_room_door",
    )

    # Stub the api_client so async_setup_entry doesn't actually hit the network
    # before async_migrate_entry runs.
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
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Entry is now version 2.
    assert entry.version == 2

    # The cover's unique_id was rewritten in place.
    rewritten = ent_reg.async_get_entity_id("cover", DOMAIN, "device-abc_cover")
    assert rewritten is not None, "expected new unique_id to be present"

    # The old unique_id is gone.
    old = ent_reg.async_get_entity_id("cover", DOMAIN, "device-abc")
    assert old is None, "expected v1 unique_id to be rewritten"


@pytest.mark.asyncio
async def test_migrate_is_idempotent_for_already_v2_unique_id(
    hass: HomeAssistant,
) -> None:
    """A cover whose unique_id already ends with _cover isn't double-suffixed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "pw"},
    )
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        domain="cover",
        platform=DOMAIN,
        unique_id="device-xyz_cover",  # already in new shape
        config_entry=entry,
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
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # No double suffix.
    survived = ent_reg.async_get_entity_id("cover", DOMAIN, "device-xyz_cover")
    assert survived is not None
    double = ent_reg.async_get_entity_id("cover", DOMAIN, "device-xyz_cover_cover")
    assert double is None
