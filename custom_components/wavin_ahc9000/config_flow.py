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
    CAT_CHANNELS,
    CONF_ACTIVE_CHANNELS,
    CONF_CHANNEL_NAMES,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE_ID,
    DOMAIN,
    IDX_CH_PRIMARY_ELEMENT,
    MAX_CHANNELS,
    PRIMARY_ELEMENT_IDX_MASK,
    channel_display_name,
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
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=300)
        ),
    }
)


def _scan_device(host: str, port: int, slave_id: int) -> dict:
    """
    Blocking: connect, read device info, and scan all 16 channels for
    wired thermostats (element_idx > 0 in IDX_CH_PRIMARY_ELEMENT).

    Returns:
        {
            "device_info": {...},
            "active_channels": [0, 2, 5, ...]   # channel indices in use
        }

    Raises CannotConnect on any failure.
    Must be called via hass.async_add_executor_job.
    """
    client = WavinClient(host, port, slave_id)
    try:
        client.connect()
        device_info = client.read_device_info()
        active: list[int] = []
        for ch in range(MAX_CHANNELS):
            prim = client.read_registers(
                CAT_CHANNELS, IDX_CH_PRIMARY_ELEMENT, page=ch, qty=1
            )
            if prim is not None:
                element_idx = prim[0] & PRIMARY_ELEMENT_IDX_MASK
                if element_idx > 0:
                    active.append(ch)
        return {"device_info": device_info, "active_channels": active}
    finally:
        client.disconnect()


class WavinConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Config flow for setting up the Wavin AHC 9000 integration via the UI.

    Step 1 (user):    Collect host, port, slave ID, poll interval.
                      On submit: connect and scan all 16 channels.
    Step 2 (confirm): Show the auto-detected active zones — user just confirms.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}
        self._active_channels: list[int] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1 — connection details and channel scan."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                result = await self.hass.async_add_executor_job(
                    _scan_device,
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_SLAVE_ID],
                )
                _LOGGER.debug(
                    "Wavin config flow: device_info=%s active_channels=%s",
                    result["device_info"],
                    result["active_channels"],
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during Wavin AHC 9000 config flow")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()
                self._connection_data = user_input
                self._active_channels = result["active_channels"]
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2 — display detected zones and let the user confirm."""
        if user_input is not None:
            title = f"Wavin AHC 9000 ({self._connection_data[CONF_HOST]})"
            return self.async_create_entry(
                title=title,
                data={
                    **self._connection_data,
                    CONF_ACTIVE_CHANNELS: self._active_channels,
                },
            )

        if self._active_channels:
            zone_list = ", ".join(
                f"Zone {ch + 1}" for ch in self._active_channels
            )
            summary = f"{len(self._active_channels)} zone(s) found: {zone_list}"
        else:
            summary = "No active zones detected. Check wiring and try again."

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"zones_summary": summary},
            data_schema=vol.Schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return WavinOptionsFlow(config_entry)


class WavinOptionsFlow(config_entries.OptionsFlow):
    """
    Two-step options flow.

    Step 1 (init):          Poll interval.
    Step 2 (channel_names): User-assigned name for each active heating zone.
    """

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._pending: dict[str, Any] = {}

    # ── Step 1: poll interval ─────────────────────────────────────────────────

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._pending = user_input
            return await self.async_step_channel_names()

        current = self._entry.options.get(
            CONF_SCAN_INTERVAL,
            self._entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=10, max=300)
                    ),
                }
            ),
        )

    # ── Step 2: zone names ────────────────────────────────────────────────────

    async def async_step_channel_names(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        active_channels: list[int] = self._entry.data.get(CONF_ACTIVE_CHANNELS, [])

        if user_input is not None:
            names = {
                str(ch): user_input.get(f"zone_{ch + 1}_name", f"Zone {ch + 1}")
                for ch in active_channels
            }
            return self.async_create_entry(
                title="",
                data={**self._pending, CONF_CHANNEL_NAMES: names},
            )

        schema_fields: dict = {}
        for ch in active_channels:
            default = channel_display_name(self._entry.options, ch)
            schema_fields[vol.Optional(f"zone_{ch + 1}_name", default=default)] = str

        return self.async_show_form(
            step_id="channel_names",
            data_schema=vol.Schema(schema_fields),
        )
