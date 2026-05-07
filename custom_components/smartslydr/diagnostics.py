# config/custom_components/smartslydr/diagnostics.py
"""Diagnostics download for the SmartSlydr integration."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Keys whose values get replaced with REDACTED before the JSON is shown
# to the user. Account email and credentials are obvious; tokens are
# defense-in-depth (api_client._log_response already redacts them in
# the normal log path, but coordinator_data could still surface them
# if a future change widens what's stored there).
TO_REDACT = {
    "username",
    "password",
    "access_token",
    "refresh_token",
    "email",
    "wlanmac",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return a redacted snapshot of entry + coordinator state."""
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = bucket.get("coordinator")

    snapshot: Any = None
    if coordinator is not None and coordinator.data is not None:
        if is_dataclass(coordinator.data):
            snapshot = asdict(coordinator.data)
        else:
            snapshot = coordinator.data

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "options": async_redact_data(dict(entry.options), TO_REDACT),
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "coordinator_data": async_redact_data(snapshot or {}, TO_REDACT),
    }
