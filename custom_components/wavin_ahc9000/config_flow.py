"""Config flow for the Wavin AHC 9000 integration."""
from __future__ import annotations

import logging
from collections import Counter
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
    CONF_ACTIVE_CHANNELS,
    CONF_CHANNEL_NAMES,
    CONF_CHANNEL_THERMOSTAT_TYPES,
    CONF_ELEMENT_MAP,
    CONF_THERMOSTAT_GROUPS,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE_ID,
    DOMAIN,
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

# Local key — only used inside config_flow.py.
_CONF_NUM_THERMOSTATS = "num_thermostats"
# Number of element_idx reads per channel used to build stable thermostat groups.
_NUM_SCAN_SAMPLES = 20

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
        vol.Required(_CONF_NUM_THERMOSTATS, default=3): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=16)
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

    Takes _NUM_SCAN_SAMPLES passes and majority-votes on the element_idx for
    each channel. A channel is considered active only when its dominant
    element_idx appears in at least 25% of the passes. This filters out the
    per-TCP-session dynamic reassignment of element indices by the AHC 9000
    firmware, which would otherwise cause different groupings on each scan.
    """
    client = WavinClient(host, port, slave_id)
    try:
        client.connect()
        device_info = client.read_device_info()

        # Accumulate element_idx votes per channel across all samples.
        elem_votes: dict[int, Counter] = {ch: Counter() for ch in range(MAX_CHANNELS)}
        for _ in range(_NUM_SCAN_SAMPLES):
            for ch in range(MAX_CHANNELS):
                prim = client.read_registers(
                    CAT_CHANNELS, IDX_CH_PRIMARY_ELEMENT, page=ch, qty=1
                )
                if prim is not None:
                    element_idx = prim[0] & PRIMARY_ELEMENT_IDX_MASK
                    if element_idx > 0:
                        elem_votes[ch][element_idx] += 1

        # Accept a channel only when its dominant element_idx appeared in ≥25% of samples.
        min_votes = max(1, _NUM_SCAN_SAMPLES // 4)
        active: list[int] = []
        element_map: dict[int, list[int]] = {}
        for ch in range(MAX_CHANNELS):
            if not elem_votes[ch]:
                continue
            dominant_elem, count = elem_votes[ch].most_common(1)[0]
            if count >= min_votes:
                active.append(ch)
                element_map.setdefault(dominant_elem, []).append(ch)

        # Group channels by thermostat: primary_ch (lowest in group) → all channels.
        thermostat_groups: dict[int, list[int]] = {
            min(channels): sorted(channels)
            for channels in element_map.values()
        }
        return {
            "device_info": device_info,
            "active_channels": sorted(active),
            "element_map": element_map,
            "thermostat_groups": thermostat_groups,
        }
    finally:
        client.disconnect()


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
        name = (channel_names or {}).get(primary_ch, f"Channel {primary_ch + 1}")
        slug = f"channel_{primary_ch + 1}"
        n = len(elem_channels)
        loops = f" — {n} channel{'s' if n != 1 else ''}" if n > 1 else ""

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
            result = None
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
                active_channels: list[int] = result["active_channels"]
                element_map: dict[int, list[int]] = result["element_map"]
                thermostat_groups: dict[int, list[int]] = result["thermostat_groups"]
                primary_channels: list[int] = sorted(thermostat_groups.keys())

                expected_count = user_input[_CONF_NUM_THERMOSTATS]
                if len(thermostat_groups) != expected_count:
                    _LOGGER.warning(
                        "Wavin AHC 9000 scan found %d thermostat(s) but %d expected.",
                        len(thermostat_groups),
                        expected_count,
                    )
                    errors[_CONF_NUM_THERMOSTATS] = "thermostat_count_mismatch"

            if not errors and result is not None:
                await self.async_set_unique_id(
                    f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()

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
                        CONF_CHANNEL_NAMES: {str(p): f"Channel {p + 1}" for p in primary_channels},
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
    Two-step options flow.

    Step 1 (init):   Poll interval + optional device re-scan.
    Step 2 (zones):  Channel names and thermostat types.
    """

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._pending: dict[str, Any] = {}
        self._num_thermostats: int = 3

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            rescan = user_input.get("rescan_channels", False)
            manual_groups = user_input.get("manual_groups", False)
            self._num_thermostats = user_input.get(_CONF_NUM_THERMOSTATS, self._num_thermostats)
            self._pending.update({
                k: v for k, v in user_input.items()
                if k not in ("rescan_channels", "manual_groups", _CONF_NUM_THERMOSTATS)
            })

            if rescan:
                try:
                    result = await self.hass.async_add_executor_job(
                        _scan_device,
                        self._entry.data[CONF_HOST],
                        self._entry.data[CONF_PORT],
                        self._entry.data[CONF_SLAVE_ID],
                    )
                    thermostat_groups: dict[int, list[int]] = result["thermostat_groups"]
                    expected_count = user_input[_CONF_NUM_THERMOSTATS]
                    if len(thermostat_groups) != expected_count:
                        _LOGGER.warning(
                            "Wavin AHC 9000 re-scan found %d thermostat(s) but %d expected.",
                            len(thermostat_groups),
                            expected_count,
                        )
                        errors[_CONF_NUM_THERMOSTATS] = "thermostat_count_mismatch"
                    else:
                        new_primaries = sorted(thermostat_groups.keys())
                        self.hass.config_entries.async_update_entry(
                            self._entry,
                            data={
                                **self._entry.data,
                                CONF_ACTIVE_CHANNELS: result["active_channels"],
                                CONF_ELEMENT_MAP: {str(k): v for k, v in result["element_map"].items()},
                                CONF_THERMOSTAT_GROUPS: {str(k): v for k, v in thermostat_groups.items()},
                                CONF_CHANNEL_NAMES: {str(p): f"Channel {p + 1}" for p in new_primaries},
                                CONF_CHANNEL_THERMOSTAT_TYPES: {
                                    str(p): THERMOSTAT_AIR_ONLY for p in new_primaries
                                },
                            },
                        )
                        _LOGGER.info(
                            "Wavin AHC 9000 re-scan: found %d thermostat(s) with groups: %s",
                            len(thermostat_groups),
                            {f"Ch#{p}": chs for p, chs in sorted(thermostat_groups.items())},
                        )
                except CannotConnect:
                    errors["base"] = "cannot_connect"

            if not errors:
                if manual_groups and not rescan:
                    return await self.async_step_groups()
                return await self.async_step_zones()

        current_interval = self._entry.options.get(
            CONF_SCAN_INTERVAL,
            self._entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        current_groups = self._entry.data.get(CONF_THERMOSTAT_GROUPS, {})
        default_num_thermostats = len(current_groups) if current_groups else 3
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=10, max=300)
                    ),
                    vol.Optional("rescan_channels", default=False): selector.BooleanSelector(),
                    vol.Optional("manual_groups",   default=False): selector.BooleanSelector(),
                    vol.Required(_CONF_NUM_THERMOSTATS, default=default_num_thermostats): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=16)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_groups(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manually assign circuits (1-indexed) to thermostat groups."""
        raw_groups = self._entry.data.get(CONF_THERMOSTAT_GROUPS, {})
        current_groups: dict[int, list[int]] = (
            {int(k): v for k, v in raw_groups.items()} if raw_groups else {}
        )
        sorted_primaries = sorted(current_groups.keys())

        errors: dict[str, str] = {}

        if user_input is not None:
            new_groups: dict[int, list[int]] = {}
            seen: set[int] = set()
            valid = True

            for i in range(1, self._num_thermostats + 1):
                raw = user_input.get(f"group_{i}_channels", "").strip()
                if not raw:
                    errors[f"group_{i}_channels"] = "empty_group"
                    valid = False
                    continue
                try:
                    chs_1idx = [int(c.strip()) for c in raw.split(",") if c.strip()]
                except ValueError:
                    errors[f"group_{i}_channels"] = "invalid_format"
                    valid = False
                    continue
                # Convert to 0-indexed and validate range.
                bad = [c for c in chs_1idx if not (1 <= c <= 16)]
                if bad:
                    errors[f"group_{i}_channels"] = "channel_out_of_range"
                    valid = False
                    continue
                channels = [c - 1 for c in chs_1idx]
                dupes = set(channels) & seen
                if dupes:
                    errors[f"group_{i}_channels"] = "duplicate_channel"
                    valid = False
                    continue
                seen.update(channels)
                primary_ch = min(channels)
                new_groups[primary_ch] = sorted(channels)

            if valid:
                all_channels = sorted(seen)
                new_primaries = sorted(new_groups.keys())
                # Carry over existing names / types for unchanged primary channels.
                existing_names: dict[str, str] = {
                    **self._entry.data.get(CONF_CHANNEL_NAMES, {}),
                    **self._entry.options.get(CONF_CHANNEL_NAMES, {}),
                }
                existing_types: dict[str, str] = {
                    **self._entry.data.get(CONF_CHANNEL_THERMOSTAT_TYPES, {}),
                    **self._entry.options.get(CONF_CHANNEL_THERMOSTAT_TYPES, {}),
                }
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={
                        **self._entry.data,
                        CONF_ACTIVE_CHANNELS: all_channels,
                        CONF_THERMOSTAT_GROUPS: {str(k): v for k, v in new_groups.items()},
                        CONF_CHANNEL_NAMES: {
                            str(p): existing_names.get(str(p), f"Channel {p + 1}")
                            for p in new_primaries
                        },
                        CONF_CHANNEL_THERMOSTAT_TYPES: {
                            str(p): existing_types.get(str(p), THERMOSTAT_AIR_ONLY)
                            for p in new_primaries
                        },
                    },
                )
                _LOGGER.info(
                    "Wavin AHC 9000 manual groups: %d thermostat(s): %s",
                    len(new_groups),
                    {f"Ch#{p}": [c + 1 for c in chs] for p, chs in sorted(new_groups.items())},
                )
                return await self.async_step_zones()

        # Build form pre-filled with current circuit assignments (1-indexed).
        schema_fields: dict = {}
        for i in range(1, self._num_thermostats + 1):
            if i - 1 < len(sorted_primaries):
                primary = sorted_primaries[i - 1]
                chs = current_groups.get(primary, [primary])
                default_val = ", ".join(str(ch + 1) for ch in sorted(chs))
            else:
                default_val = ""
            schema_fields[vol.Required(f"group_{i}_channels", default=default_val)] = str

        return self.async_show_form(
            step_id="groups",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
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
                str(p): user_input.get(f"channel_{p + 1}_name", f"Channel {p + 1}")
                for p in primary_channels
            }
            types = {
                str(p): user_input.get(f"channel_{p + 1}_type", THERMOSTAT_AIR_ONLY)
                for p in primary_channels
            }
            self._pending[CONF_CHANNEL_NAMES] = names
            self._pending[CONF_CHANNEL_THERMOSTAT_TYPES] = types
            return self.async_create_entry(title="", data=self._pending)

        schema_fields: dict = {}
        for p in primary_channels:
            default_name = channel_display_name(self._entry.options, p, self._entry.data)
            default_type = channel_thermostat_type(self._entry.options, p, self._entry.data)
            schema_fields[
                vol.Optional(f"channel_{p + 1}_name", default=default_name)
            ] = str
            schema_fields[
                vol.Optional(f"channel_{p + 1}_type", default=default_type)
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

