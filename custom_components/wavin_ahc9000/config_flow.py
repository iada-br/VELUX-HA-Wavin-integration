"""Config flow for the Wavin AHC 9000 integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .client import CannotConnect, WavinClient
from .const import (
    CAT_CHANNELS,
    CAT_PACKED,
    CONF_ACTIVE_CHANNELS,
    CONF_CHANNEL_COMFORT_TEMPS,
    CONF_CHANNEL_ECO_TEMPS,
    CONF_CHANNEL_NAMES,
    CONF_CHANNEL_THERMOSTAT_TYPES,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    DEFAULT_COMFORT_TEMP,
    DEFAULT_ECO_TEMP,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE_ID,
    DOMAIN,
    IDX_CH_COMFORT_TEMP,
    IDX_CH_ECO_TEMP,
    IDX_CH_PRIMARY_ELEMENT,
    MAX_CHANNELS,
    PRIMARY_ELEMENT_IDX_MASK,
    THERMOSTAT_AIR_FLOOR,
    THERMOSTAT_AIR_ONLY,
    channel_display_name,
    channel_thermostat_type,
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

_THERMOSTAT_TYPE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            {"value": THERMOSTAT_AIR_ONLY,  "label": "Air temperature only"},
            {"value": THERMOSTAT_AIR_FLOOR, "label": "Air + Floor temperature"},
        ],
        mode=selector.SelectSelectorMode.LIST,
    )
)


def _scan_device(host: str, port: int, slave_id: int) -> dict:
    """Blocking: connect, read device info, scan channels for wired thermostats."""
    client = WavinClient(host, port, slave_id)
    try:
        client.connect()
        device_info = client.read_device_info()
        active: list[int] = []
        for ch in range(MAX_CHANNELS):
            prim = client.read_registers(
                CAT_CHANNELS, IDX_CH_PRIMARY_ELEMENT, page=ch, qty=1
            )
            if prim is not None and (prim[0] & PRIMARY_ELEMENT_IDX_MASK) > 0:
                active.append(ch)
        return {"device_info": device_info, "active_channels": active}
    finally:
        client.disconnect()


def _read_device_ranges(client: WavinClient, channels: list[int]) -> dict:
    """Blocking: read comfort + eco temps from the device for given channels."""
    client.ensure_connected()
    result: dict[str, float] = {}
    for ch in channels:
        for key, idx in (("comfort", IDX_CH_COMFORT_TEMP), ("eco", IDX_CH_ECO_TEMP)):
            reg = client.read_registers(CAT_PACKED, idx, page=ch, qty=1)
            if reg:
                raw = reg[0]
                signed = raw if raw < 0x8000 else raw - 0x10000
                result[f"{ch}_{key}"] = round(signed / 10.0, 1)
    return result


def _write_device_ranges(
    client: WavinClient, channels: list[int], user_input: dict
) -> None:
    """Blocking: write comfort + eco temps to the device."""
    client.ensure_connected()
    for ch in channels:
        for key, idx in (("comfort", IDX_CH_COMFORT_TEMP), ("eco", IDX_CH_ECO_TEMP)):
            val = user_input.get(f"zone_{ch + 1}_{key}")
            if val is not None:
                client.write_register(CAT_PACKED, idx, page=ch, val=int(round(val * 10)))


# ── Config flow ───────────────────────────────────────────────────────────────

class WavinConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Three-step config flow for the Wavin AHC 9000 integration.

    Step 1 (user):    Connection details + auto-scan.
    Step 2 (confirm): Show detected zones, user confirms.
    Step 3 (zones):   Assign room names and thermostat types.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}
        self._active_channels: list[int] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                result = await self.hass.async_add_executor_job(
                    _scan_device,
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_SLAVE_ID],
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
        if user_input is not None:
            return await self.async_step_zones()

        if self._active_channels:
            zone_list = ", ".join(f"Zone {ch + 1}" for ch in self._active_channels)
            summary = f"{len(self._active_channels)} zone(s) found: {zone_list}"
        else:
            summary = "No active zones detected. Check wiring and try again."

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"zones_summary": summary},
            data_schema=vol.Schema({}),
        )

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3 — room names and thermostat types."""
        if user_input is not None:
            names = {
                str(ch): user_input.get(f"zone_{ch + 1}_name", f"Zone {ch + 1}")
                for ch in self._active_channels
            }
            types = {
                str(ch): user_input.get(f"zone_{ch + 1}_type", THERMOSTAT_AIR_ONLY)
                for ch in self._active_channels
            }
            return self.async_create_entry(
                title=f"Wavin AHC 9000 ({self._connection_data[CONF_HOST]})",
                data={
                    **self._connection_data,
                    CONF_ACTIVE_CHANNELS: self._active_channels,
                    CONF_CHANNEL_NAMES: names,
                    CONF_CHANNEL_THERMOSTAT_TYPES: types,
                },
            )

        schema_fields: dict = {}
        for ch in self._active_channels:
            schema_fields[
                vol.Optional(f"zone_{ch + 1}_name", default=f"Zone {ch + 1}")
            ] = str
            schema_fields[
                vol.Optional(f"zone_{ch + 1}_type", default=THERMOSTAT_AIR_ONLY)
            ] = _THERMOSTAT_TYPE_SELECTOR

        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema(schema_fields),
            description_placeholders={"zone_count": str(len(self._active_channels))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return WavinOptionsFlow(config_entry)


# ── Options flow ──────────────────────────────────────────────────────────────

class WavinOptionsFlow(config_entries.OptionsFlow):
    """
    Three-step options flow.

    Step 1 (init):        Poll interval.
    Step 2 (zones):       Room names and thermostat types.
    Step 3 (temp_ranges): Comfort / eco temperature limits per zone.
                          Reads live values from the device before showing
                          the form; writes confirmed values back to the device.
    """

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._pending: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._pending.update(user_input)
            return await self.async_step_zones()

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

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Room names and thermostat types."""
        active_channels: list[int] = self._entry.data.get(CONF_ACTIVE_CHANNELS, [])

        if user_input is not None:
            names = {
                str(ch): user_input.get(f"zone_{ch + 1}_name", f"Zone {ch + 1}")
                for ch in active_channels
            }
            types = {
                str(ch): user_input.get(f"zone_{ch + 1}_type", THERMOSTAT_AIR_ONLY)
                for ch in active_channels
            }
            self._pending[CONF_CHANNEL_NAMES] = names
            self._pending[CONF_CHANNEL_THERMOSTAT_TYPES] = types
            return await self.async_step_temp_ranges()

        schema_fields: dict = {}
        for ch in active_channels:
            default_name = channel_display_name(self._entry.options, ch, self._entry.data)
            default_type = channel_thermostat_type(self._entry.options, ch, self._entry.data)
            schema_fields[
                vol.Optional(f"zone_{ch + 1}_name", default=default_name)
            ] = str
            schema_fields[
                vol.Optional(f"zone_{ch + 1}_type", default=default_type)
            ] = _THERMOSTAT_TYPE_SELECTOR

        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema(schema_fields),
        )

    async def async_step_temp_ranges(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Comfort / eco temperature limits — reads from device, writes on submit."""
        active_channels: list[int] = self._entry.data.get(CONF_ACTIVE_CHANNELS, [])
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate eco < comfort for every zone
            valid = True
            for ch in active_channels:
                comfort = user_input.get(f"zone_{ch + 1}_comfort", DEFAULT_COMFORT_TEMP)
                eco     = user_input.get(f"zone_{ch + 1}_eco",     DEFAULT_ECO_TEMP)
                if eco >= comfort:
                    errors[f"zone_{ch + 1}_eco"] = "eco_above_comfort"
                    valid = False

            if valid:
                # Write to device (best-effort; save regardless)
                try:
                    from .coordinator import WavinCoordinator
                    coordinator: WavinCoordinator = self.hass.data[DOMAIN][self._entry.entry_id]
                    await self.hass.async_add_executor_job(
                        _write_device_ranges,
                        coordinator.client,
                        active_channels,
                        user_input,
                    )
                except Exception:
                    _LOGGER.warning(
                        "Could not write temp ranges to device; values saved to options only."
                    )

                comfort_map = {
                    str(ch): user_input.get(f"zone_{ch + 1}_comfort", DEFAULT_COMFORT_TEMP)
                    for ch in active_channels
                }
                eco_map = {
                    str(ch): user_input.get(f"zone_{ch + 1}_eco", DEFAULT_ECO_TEMP)
                    for ch in active_channels
                }
                return self.async_create_entry(
                    title="",
                    data={
                        **self._pending,
                        CONF_CHANNEL_COMFORT_TEMPS: comfort_map,
                        CONF_CHANNEL_ECO_TEMPS:     eco_map,
                    },
                )

        # Read current values from device to pre-fill the form
        live: dict[str, float] = {}
        try:
            from .coordinator import WavinCoordinator
            coordinator: WavinCoordinator = self.hass.data[DOMAIN][self._entry.entry_id]
            live = await self.hass.async_add_executor_job(
                _read_device_ranges, coordinator.client, active_channels
            )
        except Exception:
            _LOGGER.warning("Could not read temp ranges from device; using stored values.")

        stored_comfort = self._entry.options.get(CONF_CHANNEL_COMFORT_TEMPS, {})
        stored_eco     = self._entry.options.get(CONF_CHANNEL_ECO_TEMPS, {})

        schema_fields: dict = {}
        for ch in active_channels:
            name    = channel_display_name(self._entry.options, ch, self._entry.data)
            comfort = live.get(f"{ch}_comfort") or stored_comfort.get(str(ch), DEFAULT_COMFORT_TEMP)
            eco     = live.get(f"{ch}_eco")     or stored_eco.get(str(ch),     DEFAULT_ECO_TEMP)

            schema_fields[
                vol.Optional(f"zone_{ch + 1}_comfort", default=float(comfort))
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=35, step=0.5, mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°C",
                )
            )
            schema_fields[
                vol.Optional(f"zone_{ch + 1}_eco", default=float(eco))
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=35, step=0.5, mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°C",
                )
            )

        return self.async_show_form(
            step_id="temp_ranges",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
            description_placeholders={"zone_count": str(len(active_channels))},
        )
