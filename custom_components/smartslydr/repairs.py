# config/custom_components/smartslydr/repairs.py
"""Repair flows for the SmartSlydr integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN


class _UpstreamFixFlow(RepairsFlow):
    """Confirm-and-retry repair flow with optional base-URL reset.

    Two paths depending on the entry state:

    - If no entry has a customized base_url, the flow is a simple
      confirm-and-retry. Clicking Submit triggers a coordinator
      refresh on each entry; if the next poll succeeds, the
      _async_update_data success path calls async_delete_issue and
      the card clears.

    - If at least one entry has a non-default base_url (often the
      cause of "unreachable" repairs after a misconfiguration), the
      form additionally offers a "Reset to default URL" toggle. With
      that selected, the flow strips CONF_BASE_URL from each entry's
      options; the options-update listener registered in
      ``async_setup_entry`` then reloads the entry against the
      default URL.

    Submit (vs the always-available Ignore button) does NOT mark the
    issue dismissed-by-version, so if the underlying problem comes
    back the card comes back too.
    """

    def __init__(self, issue_id: str) -> None:
        self._issue_id = issue_id

    def _non_default_entries(self):
        return [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.options.get(CONF_BASE_URL, DEFAULT_BASE_URL) != DEFAULT_BASE_URL
        ]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._non_default_entries():
            return await self.async_step_confirm_with_reset()
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            await self._refresh_all()
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="confirm")

    async def async_step_confirm_with_reset(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            if user_input.get("reset_base_url"):
                for entry in self._non_default_entries():
                    new_options = {
                        k: v for k, v in entry.options.items() if k != CONF_BASE_URL
                    }
                    self.hass.config_entries.async_update_entry(
                        entry, options=new_options
                    )
                    # The options-update listener triggers a reload,
                    # which re-runs first-refresh against the default URL.
            else:
                await self._refresh_all()
            return self.async_create_entry(title="", data={})

        non_default = self._non_default_entries()
        current_url = (
            non_default[0].options[CONF_BASE_URL]
            if non_default
            else DEFAULT_BASE_URL
        )
        return self.async_show_form(
            step_id="confirm_with_reset",
            data_schema=vol.Schema(
                {vol.Optional("reset_base_url", default=False): bool}
            ),
            description_placeholders={
                "current_url": current_url,
                "default_url": DEFAULT_BASE_URL,
            },
        )

    async def _refresh_all(self) -> None:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            bucket = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if not bucket:
                continue
            coordinator = bucket.get("coordinator")
            if coordinator is not None:
                # async_refresh blocks until the poll completes, so the
                # auto-clear path runs before this flow closes.
                await coordinator.async_refresh()


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    return _UpstreamFixFlow(issue_id)
