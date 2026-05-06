# config/custom_components/smartslydr/config_flow.py

import asyncio
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import SmartSlydrApiClient
from .const import CONF_PASSWORD, CONF_USERNAME, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SmartSlydrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartSlydr."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step where the user provides credentials."""
        errors = {}
        if user_input:
            session = async_get_clientsession(self.hass)
            client = SmartSlydrApiClient(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                session,
            )
            try:
                await client.authenticate()
            except aiohttp.ClientResponseError as err:
                if err.status in (400, 401, 403):
                    _LOGGER.warning("SmartSlydr auth rejected (%s)", err.status)
                    errors["base"] = "auth_failed"
                else:
                    _LOGGER.error("SmartSlydr auth HTTP error: %s", err)
                    errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.error("SmartSlydr auth network error: %s", err)
                errors["base"] = "cannot_connect"
            except KeyError as err:
                _LOGGER.error("SmartSlydr auth response missing field: %s", err)
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=user_input,
                )

        data_schema = vol.Schema({
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for an existing entry."""
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Handle the options for SmartSlydr integration."""

    def __init__(self, config_entry):
        # Avoid setting self.config_entry (deprecated)
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema({
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=self._config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): int,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
