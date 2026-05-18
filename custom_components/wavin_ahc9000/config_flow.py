"""Config flow for the Wavin AHC 9000 integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .client import CannotConnect, WavinClient
from .const import (
    CONF_NUM_CHANNELS,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    DEFAULT_HOST,
    DEFAULT_NUM_CHANNELS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_SLAVE_ID, default=DEFAULT_SLAVE_ID): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
        vol.Required(CONF_NUM_CHANNELS, default=DEFAULT_NUM_CHANNELS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10)
        ),
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=300)
        ),
    }
)


def _try_connect(host: str, port: int, slave_id: int) -> dict:
    """
    Blocking: open a connection and read device info to validate the config.
    Must be called via hass.async_add_executor_job from the config flow.

    Returns the device info dict from WavinClient.read_device_info().
    Raises CannotConnect on any failure.
    """
    client = WavinClient(host, port, slave_id)
    try:
        client.connect()
        return client.read_device_info()
    finally:
        client.disconnect()


class WavinConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Config flow for setting up the Wavin AHC 9000 integration via the UI.

    Collects host, port, slave ID, zone count, and poll interval, then
    validates connectivity before creating the config entry.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user-facing configuration form."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await self.hass.async_add_executor_job(
                    _try_connect,
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_SLAVE_ID],
                )
                _LOGGER.debug("Wavin config flow: device info = %s", info)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception(
                    "Unexpected error during Wavin AHC 9000 config flow"
                )
                errors["base"] = "unknown"
            else:
                # Prevent duplicate entries for the same physical device.
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()

                title = f"Wavin AHC 9000 ({user_input[CONF_HOST]})"
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return WavinOptionsFlow(config_entry)


class WavinOptionsFlow(config_entries.OptionsFlow):
    """Options flow to adjust poll interval without re-adding the integration."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get(
            CONF_SCAN_INTERVAL,
            self._entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=10, max=300)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
