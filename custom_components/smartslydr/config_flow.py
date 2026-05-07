# config/custom_components/smartslydr/config_flow.py

import asyncio
import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import SmartSlydrApiClient, SmartSlydrAuthError
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SmartSlydrConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartSlydr."""

    VERSION = 1

    def __init__(self):
        self._reauth_username: str | None = None

    async def _validate_credentials(self, username: str, password: str):
        """Run /auth against the SmartSlydr API. Returns an error key or None."""
        session = async_get_clientsession(self.hass)
        client = SmartSlydrApiClient(username, password, session)
        try:
            await client.authenticate()
        except SmartSlydrAuthError:
            return "auth_failed"
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("SmartSlydr auth HTTP error: %s", err)
            return "cannot_connect"
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("SmartSlydr auth network error: %s", err)
            return "cannot_connect"
        except Exception as err:  # noqa: BLE001 - last-ditch diagnostic
            _LOGGER.exception("SmartSlydr auth unexpected error: %s", err)
            return "unknown"
        return None

    async def async_step_user(self, user_input=None):
        """Handle the initial step where the user provides credentials."""
        errors = {}
        if user_input:
            err = await self._validate_credentials(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if err:
                errors["base"] = err
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=user_input,
                )

        data_schema = vol.Schema({
            vol.Required(CONF_USERNAME): vol.All(str, vol.Length(min=1, max=512)),
            vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1, max=512)),
        })
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data):
        """Triggered by ConfigEntryAuthFailed; HA hands us the entry data."""
        self._reauth_username = entry_data.get(CONF_USERNAME)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None and self._reauth_username:
            err = await self._validate_credentials(
                self._reauth_username, user_input[CONF_PASSWORD]
            )
            if err:
                errors["base"] = err
            else:
                entry = self._get_reauth_entry()
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1, max=512)),
            }),
            description_placeholders={"username": self._reauth_username or ""},
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
            ): vol.All(int, vol.Range(min=10, max=3600)),
            vol.Optional(
                CONF_BASE_URL,
                default=self._config_entry.options.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            ): vol.All(str, vol.Length(min=1, max=512)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
