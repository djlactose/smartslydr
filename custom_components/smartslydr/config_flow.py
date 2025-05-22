# config/custom_components/smartslydr/config_flow.py

import logging
import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import CONF_SCAN_INTERVAL

from .const import DOMAIN, CONF_USERNAME, CONF_PASSWORD, DEFAULT_SCAN_INTERVAL
from .api_client import SmartSlydrApiClient

_LOGGER = logging.getLogger(__name__)

class SmartSlydrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartSlydr."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step where the user provides credentials."""
        errors = {}
        if user_input:
            try:
                # Verify credentials by authenticating
                async with aiohttp.ClientSession() as session:
                    client = SmartSlydrApiClient(
                        user_input[CONF_USERNAME],
                        user_input[CONF_PASSWORD],
                        session
                    )
                    await client.authenticate()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=user_input
                )
            except Exception as err:
                _LOGGER.error("Authentication failed: %s", err)
                errors["base"] = "auth_failed"

        data_schema = vol.Schema({
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for an existing entry."""
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Handle the options for SmartSlydr integration."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        # Avoid setting self.config_entry (deprecated)
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the SmartSlydr integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema({
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self._config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ): int
        })
        return self.async_show_form(
            step_id="init",
            data_schema=schema
        )
