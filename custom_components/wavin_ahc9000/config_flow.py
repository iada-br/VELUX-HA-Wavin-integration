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

from .client import CannotConnect, WavinClient, raw_to_temp
from .const import (
    CAT_CHANNELS,
    CAT_ELEMENTS,
    CAT_PACKED,
    CONF_ACTIVE_CHANNELS,
    CONF_CHANNEL_COMFORT_TEMPS,
    CONF_CHANNEL_ECO_TEMPS,
    CONF_CHANNEL_NAMES,
    CONF_CHANNEL_THERMOSTAT_TYPES,
    CONF_ELEMENT_MAP,
    CONF_THERMOSTAT_GROUPS,
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
    IDX_ELEM_AIR_TEMP,
    MAX_CHANNELS,
    PRIMARY_ELEMENT_IDX_MASK,
    PRIMARY_ELEMENT_TP_LOST_MASK,
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
    """Blocking: connect, read device info, scan channels for wired thermostats.

    Returns the active channel list and an element_map that groups channels by
    their shared physical thermostat (element_idx).  Two channels with the same
    element_idx share a single thermostat wired to multiple zones.
    """
    client = WavinClient(host, port, slave_id)
    try:
        client.connect()
        device_info = client.read_device_info()
        active: list[int] = []
        element_map: dict[int, list[int]] = {}
        for ch in range(MAX_CHANNELS):
            prim = client.read_registers(
                CAT_CHANNELS, IDX_CH_PRIMARY_ELEMENT, page=ch, qty=1
            )
            if prim is not None:
                element_idx = prim[0] & PRIMARY_ELEMENT_IDX_MASK
                if element_idx > 0:
                    active.append(ch)
                    element_map.setdefault(element_idx, []).append(ch)
        # Group channels by thermostat: primary_ch (lowest in group) → all channels.
        # This is stored as the stable thermostat identity even though element_idx
        # is reassigned each TCP session.
        thermostat_groups: dict[int, list[int]] = {
            min(channels): sorted(channels)
            for channels in element_map.values()
        }
        return {
            "device_info": device_info,
            "active_channels": active,
            "element_map": element_map,
            "thermostat_groups": thermostat_groups,
        }
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


def _read_live_thermostat_summary(
    client: WavinClient,
    channels: list[int],
    channel_names: dict[int, str] | None = None,
) -> str:
    """Blocking: read element indices + temperatures from the device.

    Groups channels by their shared element_idx (physical thermostat) and
    returns a human-readable summary that includes the HA entity slug, e.g.:

        - **Zone 1** (entity: `zone_1`) — 6 loops — Air: 23.1 °C
        - **Zone 7** (entity: `zone_7`) — 5 loops — Air: 21.3 °C
        - **Zone 13** (entity: `zone_13`) — 4 loops — Air: 19.8 °C — TP LOST
    """
    client.ensure_connected()

    element_channels: dict[int, list[int]] = {}
    tp_lost_elements: set[int] = set()
    for ch in channels:
        prim = client.read_registers(CAT_CHANNELS, IDX_CH_PRIMARY_ELEMENT, page=ch, qty=1)
        if prim:
            element_idx = prim[0] & PRIMARY_ELEMENT_IDX_MASK
            if element_idx > 0:
                element_channels.setdefault(element_idx, []).append(ch)
                if prim[0] & PRIMARY_ELEMENT_TP_LOST_MASK:
                    tp_lost_elements.add(element_idx)

    lines = []
    for elem_idx, elem_channels in sorted(element_channels.items()):
        primary_ch = min(elem_channels)
        name = (channel_names or {}).get(primary_ch, f"Zone {primary_ch + 1}")
        slug = f"zone_{primary_ch + 1}"
        n = len(elem_channels)
        loops = f" — {n} loop{'s' if n != 1 else ''}" if n > 1 else ""

        temps = client.read_registers(CAT_ELEMENTS, IDX_ELEM_AIR_TEMP, page=elem_idx - 1, qty=2)
        air   = raw_to_temp(temps[0]) if temps else None
        floor = raw_to_temp(temps[1]) if (temps and len(temps) > 1) else None

        temp_parts = []
        if air is not None and -20.0 <= air <= 80.0:
            temp_parts.append(f"Air: {air:.1f} °C")
        if floor is not None and -20.0 <= floor <= 80.0:
            temp_parts.append(f"Floor: {floor:.1f} °C")
        temp_str = " — " + " / ".join(temp_parts) if temp_parts else ""

        lost = " — TP LOST" if elem_idx in tp_lost_elements else ""
        lines.append(f"**{name}** (entity: `{slug}`){loops}{temp_str}{lost}")

    return "\n".join(f"- {l}" for l in lines) if lines else "No thermostats detected."


# ── Config flow ───────────────────────────────────────────────────────────────

class WavinConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Single-step config flow for the Wavin AHC 9000 integration.

    Step 1 (user): Enter connection details. The controller is scanned
    automatically and an entry is created with default zone names and types.
    All zone customisation (names, thermostat types, temp ranges) is done
    via the options flow after setup (press Configure on the integration card).
    """

    VERSION = 1

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

                active_channels: list[int] = result["active_channels"]
                element_map: dict[int, list[int]] = result["element_map"]
                thermostat_groups: dict[int, list[int]] = result["thermostat_groups"]
                primary_channels: list[int] = sorted(thermostat_groups.keys())

                _LOGGER.info(
                    "Wavin AHC 9000 scan complete: %d zone(s), %d thermostat(s). Groups: %s",
                    len(active_channels),
                    len(thermostat_groups),
                    {f"Th#{p}": [f"Zone {ch+1}" for ch in chs]
                     for p, chs in sorted(thermostat_groups.items())},
                )

                return self.async_create_entry(
                    title=f"Wavin AHC 9000 ({user_input[CONF_HOST]})",
                    data={
                        **user_input,
                        CONF_ACTIVE_CHANNELS: active_channels,
                        CONF_ELEMENT_MAP: {str(k): v for k, v in element_map.items()},
                        CONF_THERMOSTAT_GROUPS: {str(k): v for k, v in thermostat_groups.items()},
                        # Names and types keyed by primary_ch — one entry per thermostat.
                        CONF_CHANNEL_NAMES: {str(p): f"Zone {p + 1}" for p in primary_channels},
                        CONF_CHANNEL_THERMOSTAT_TYPES: {str(p): THERMOSTAT_AIR_ONLY for p in primary_channels},
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
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
        """Thermostat names and sensor types — one row per physical thermostat."""
        raw_groups = self._entry.data.get(CONF_THERMOSTAT_GROUPS, {})
        # Primary channels = one per physical thermostat (fallback: all active channels).
        if raw_groups:
            primary_channels: list[int] = sorted(int(k) for k in raw_groups)
        else:
            primary_channels = self._entry.data.get(CONF_ACTIVE_CHANNELS, [])

        if user_input is not None:
            names = {
                str(p): user_input.get(f"zone_{p + 1}_name", f"Zone {p + 1}")
                for p in primary_channels
            }
            types = {
                str(p): user_input.get(f"zone_{p + 1}_type", THERMOSTAT_AIR_ONLY)
                for p in primary_channels
            }
            self._pending[CONF_CHANNEL_NAMES] = names
            self._pending[CONF_CHANNEL_THERMOSTAT_TYPES] = types
            return await self.async_step_temp_ranges()

        schema_fields: dict = {}
        for p in primary_channels:
            default_name = channel_display_name(self._entry.options, p, self._entry.data)
            default_type = channel_thermostat_type(self._entry.options, p, self._entry.data)
            schema_fields[
                vol.Optional(f"zone_{p + 1}_name", default=default_name)
            ] = str
            schema_fields[
                vol.Optional(f"zone_{p + 1}_type", default=default_type)
            ] = _THERMOSTAT_TYPE_SELECTOR

        # Read live thermostat grouping + temperatures directly from the device.
        # Merge stored names so entity slugs in the summary match what HA will show.
        stored_names: dict[int, str] = {
            int(k): v for k, v in {
                **self._entry.data.get(CONF_CHANNEL_NAMES, {}),
                **self._entry.options.get(CONF_CHANNEL_NAMES, {}),
            }.items()
        }
        thermostats_summary = "Could not read live data from the device."
        try:
            from .coordinator import WavinCoordinator
            coordinator: WavinCoordinator = self.hass.data[DOMAIN][self._entry.entry_id]
            thermostats_summary = await self.hass.async_add_executor_job(
                _read_live_thermostat_summary,
                coordinator.client,
                self._entry.data.get(CONF_ACTIVE_CHANNELS, []),
                stored_names,
            )
        except Exception:
            _LOGGER.warning("Could not read live thermostat data for options flow zones step.")

        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema(schema_fields),
            description_placeholders={"thermostats_summary": thermostats_summary},
        )

    async def async_step_temp_ranges(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Comfort / eco temperature limits — one row per thermostat, not per channel.

        Reads live values from the primary channel of each thermostat group.
        On save, the same values are written to every channel in the group
        and stored in options keyed by primary channel.
        """
        raw_groups = self._entry.data.get(CONF_THERMOSTAT_GROUPS, {})
        thermostat_groups: dict[int, list[int]] = (
            {int(k): v for k, v in raw_groups.items()}
            if raw_groups
            else {ch: [ch] for ch in self._entry.data.get(CONF_ACTIVE_CHANNELS, [])}
        )
        primary_channels: list[int] = sorted(thermostat_groups.keys())
        active_channels: list[int] = self._entry.data.get(CONF_ACTIVE_CHANNELS, [])
        errors: dict[str, str] = {}

        if user_input is not None:
            valid = True
            for p in primary_channels:
                comfort = user_input.get(f"zone_{p + 1}_comfort", DEFAULT_COMFORT_TEMP)
                eco     = user_input.get(f"zone_{p + 1}_eco",     DEFAULT_ECO_TEMP)
                if eco >= comfort:
                    errors[f"zone_{p + 1}_eco"] = "eco_above_comfort"
                    valid = False

            if valid:
                # Expand primary values to every channel in each group for device write.
                expanded: dict[str, Any] = {}
                for p in primary_channels:
                    for ch in thermostat_groups.get(p, [p]):
                        expanded[f"zone_{ch + 1}_comfort"] = user_input.get(f"zone_{p + 1}_comfort", DEFAULT_COMFORT_TEMP)
                        expanded[f"zone_{ch + 1}_eco"]     = user_input.get(f"zone_{p + 1}_eco",     DEFAULT_ECO_TEMP)

                try:
                    from .coordinator import WavinCoordinator
                    coordinator: WavinCoordinator = self.hass.data[DOMAIN][self._entry.entry_id]
                    await self.hass.async_add_executor_job(
                        _write_device_ranges,
                        coordinator.client,
                        active_channels,
                        expanded,
                    )
                except Exception:
                    _LOGGER.warning(
                        "Could not write temp ranges to device; values saved to options only."
                    )

                return self.async_create_entry(
                    title="",
                    data={
                        **self._pending,
                        CONF_CHANNEL_COMFORT_TEMPS: {
                            str(p): user_input.get(f"zone_{p + 1}_comfort", DEFAULT_COMFORT_TEMP)
                            for p in primary_channels
                        },
                        CONF_CHANNEL_ECO_TEMPS: {
                            str(p): user_input.get(f"zone_{p + 1}_eco", DEFAULT_ECO_TEMP)
                            for p in primary_channels
                        },
                    },
                )

        # Read current values from device (primary channel per group is sufficient).
        live: dict[str, float] = {}
        try:
            from .coordinator import WavinCoordinator
            coordinator: WavinCoordinator = self.hass.data[DOMAIN][self._entry.entry_id]
            live = await self.hass.async_add_executor_job(
                _read_device_ranges, coordinator.client, primary_channels
            )
        except Exception:
            _LOGGER.warning("Could not read temp ranges from device; using stored values.")

        stored_comfort = self._entry.options.get(CONF_CHANNEL_COMFORT_TEMPS, {})
        stored_eco     = self._entry.options.get(CONF_CHANNEL_ECO_TEMPS, {})

        schema_fields: dict = {}
        for p in primary_channels:
            name    = channel_display_name(self._entry.options, p, self._entry.data)
            comfort = live.get(f"{p}_comfort") or stored_comfort.get(str(p), DEFAULT_COMFORT_TEMP)
            eco     = live.get(f"{p}_eco")     or stored_eco.get(str(p),     DEFAULT_ECO_TEMP)

            schema_fields[
                vol.Optional(f"zone_{p + 1}_comfort", default=float(comfort))
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=60, step=0.5, mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°C",
                )
            )
            schema_fields[
                vol.Optional(f"zone_{p + 1}_eco", default=float(eco))
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=60, step=0.5, mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="°C",
                )
            )

        return self.async_show_form(
            step_id="temp_ranges",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
            description_placeholders={"zone_count": str(len(primary_channels))},
        )
