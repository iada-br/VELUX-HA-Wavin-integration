"""DataUpdateCoordinator for the Wavin AHC 9000 integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .client import CannotConnect, WavinClient, raw_to_temp
from .const import (
    CAT_ELEMENTS,
    CAT_PACKED,
    CAT_CHANNELS,
    CONF_ACTIVE_CHANNELS,
    CONF_CHANNEL_NAMES,
    CONF_CHANNEL_THERMOSTAT_TYPES,
    CONF_ELEMENT_MAP,
    CONF_THERMOSTAT_GROUPS,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IDX_CH_MANUAL_TEMP,
    IDX_CH_PRIMARY_ELEMENT,
    IDX_CH_TIMER_EVENT,
    IDX_ELEM_AIR_TEMP,
    KEY_AIR_TEMP,
    KEY_DESIRED_TEMP,
    KEY_FLOOR_TEMP,
    KEY_TP_LOST,
    KEY_VALVE_OPEN,
    MAX_TEMP,
    MIN_TEMP,
    PRIMARY_ELEMENT_IDX_MASK,
    PRIMARY_ELEMENT_TP_LOST_MASK,
    THERMOSTAT_AIR_FLOOR,
    THERMOSTAT_AIR_ONLY,
    TIMER_EVENT_OUTP_ON_MASK,
    ch_key,
    channel_thermostat_type,
)

_LOGGER = logging.getLogger(__name__)


class WavinCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Polls all Wavin AHC 9000 registers on a fixed schedule and caches
    results in self.data for climate and sensor entities to consume.

    Data layout after a successful update
    --------------------------------------
    {
        "ch0_air_temp":     float | None,  # Zone 1 air temperature (°C)
        "ch0_floor_temp":   float | None,  # Zone 1 floor temperature (°C)
        "ch0_desired_temp": float | None,  # Zone 1 setpoint (°C)
        "ch0_valve_open":   bool,          # Zone 1 valve/output active
        "ch0_tp_lost":      bool,          # Zone 1 thermostat lost
        "ch1_air_temp":     float | None,
        ...
    }

    None temperatures mean the sensor is absent (0x7FFF) or the thermostat
    is not communicating. A single failing register does NOT fail the whole
    update — only a complete connection failure does that.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self.active_channels: list[int] = entry.data[CONF_ACTIVE_CHANNELS]

        # element_map is a setup-time snapshot only — element indices are
        # reassigned by the device each TCP session. Kept for UI display only.
        raw_map = entry.data.get(CONF_ELEMENT_MAP, {})
        self.element_map: dict[int, list[int]] = {int(k): v for k, v in raw_map.items()}

        # thermostat_groups: primary_ch → all channel indices sharing that thermostat.
        # Determines which channels share one climate entity and receive the same
        # setpoint when the user changes the target temperature.
        # Fallback: each channel is its own group (old config entries without this key).
        raw_groups = entry.data.get(CONF_THERMOSTAT_GROUPS, {})
        self.thermostat_groups: dict[int, list[int]] = (
            {int(k): v for k, v in raw_groups.items()}
            if raw_groups
            else {ch: [ch] for ch in self.active_channels}
        )
        self.thermostat_channels: list[int] = sorted(self.thermostat_groups.keys())
        # Flag: need a live scan to populate missing thermostat_groups (old config entry).
        self._group_scan_pending: bool = not bool(raw_groups)
        self.client = WavinClient(
            host=entry.data[CONF_HOST],
            port=entry.data[CONF_PORT],
            slave_id=entry.data[CONF_SLAVE_ID],
        )
        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    # ── DataUpdateCoordinator interface ──────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        """Called every update_interval. Offloads blocking I/O to executor."""
        try:
            result = await self.hass.async_add_executor_job(self._fetch_all)
        except CannotConnect as exc:
            raise UpdateFailed(
                f"Cannot connect to Wavin AHC 9000 at"
                f" {self._entry.data[CONF_HOST]}:{self._entry.data[CONF_PORT]}: {exc}"
            ) from exc
        except Exception as exc:
            raise UpdateFailed(
                f"Unexpected error reading Wavin AHC 9000: {exc}"
            ) from exc

        live_groups: dict[int, list[int]] = result.pop("__live_groups__", {})

        # Auto-discover thermostat groups when config entry pre-dates this feature.
        # On first successful poll we have live element_idx data; use it to build
        # groups, persist them to the config entry, then reload so the correct
        # (grouped) entity count takes effect without any user action.
        if self._group_scan_pending and live_groups:
            self._group_scan_pending = False
            self.thermostat_groups = live_groups
            self.thermostat_channels = sorted(live_groups.keys())
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    CONF_THERMOSTAT_GROUPS: {str(k): v for k, v in live_groups.items()},
                },
            )
            _LOGGER.info(
                "Wavin AHC 9000: auto-discovered %d thermostat group(s) — reloading to apply.",
                len(live_groups),
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._entry.entry_id)
            )

        return result

    # ── Blocking fetch (runs in executor thread) ──────────────────────────────

    def _fetch_all(self) -> dict[str, Any]:
        """
        Fetch all register values from the device.

        Query order per zone
        --------------------
        1. Primary element pointer (CAT=0x03 IDX=0x02) — yields element_index
           and all_tp_lost flag.
        2. Valve/output status (CAT=0x03 IDX=0x00).
        3. Setpoint (CAT=0x02 IDX=0x00).
        4. Air + floor temp (CAT=0x01 IDX=0x04, qty=2) using element_index as
           page — only when element_index > 0 and thermostat is not lost.

        Total queries = up to 4 × num_channels.
        """
        self.client.ensure_connected()
        data: dict[str, Any] = {}

        # Always read qty=2 (air + floor) on first encounter of an element_idx.
        # Element indices are reassigned each TCP session so the stored element_map
        # cannot be trusted for qty decisions. Reading 2 registers costs one extra
        # word per unique thermostat but guarantees floor data is available for any
        # zone that needs it, regardless of which zone is seen first in the loop.
        temps_cache: dict[int, list | None] = {}
        # Collect live grouping (element_idx → channels) for auto-discovery.
        live_elem_ch: dict[int, list[int]] = {}

        for ch in self.active_channels:
            # ── Thermostat-lost flag + element index ───────────────────────
            prim = self.client.read_registers(
                CAT_CHANNELS, IDX_CH_PRIMARY_ELEMENT, page=ch, qty=1
            )
            element_idx = (prim[0] & PRIMARY_ELEMENT_IDX_MASK) if prim else 0
            data[ch_key(ch, KEY_TP_LOST)] = (
                bool(prim[0] & PRIMARY_ELEMENT_TP_LOST_MASK) if prim else True
            )
            if element_idx > 0:
                live_elem_ch.setdefault(element_idx, []).append(ch)

            # ── Valve / output status ──────────────────────────────────────
            timer = self.client.read_registers(
                CAT_CHANNELS, IDX_CH_TIMER_EVENT, page=ch, qty=1
            )
            data[ch_key(ch, KEY_VALVE_OPEN)] = (
                bool(timer[0] & TIMER_EVENT_OUTP_ON_MASK) if timer else False
            )

            # ── Setpoint ───────────────────────────────────────────────────
            setp = self.client.read_registers(
                CAT_PACKED, IDX_CH_MANUAL_TEMP, page=ch, qty=1
            )
            data[ch_key(ch, KEY_DESIRED_TEMP)] = (
                raw_to_temp(setp[0]) if setp else None
            )

            # ── Air (+ floor) temperature ──────────────────────────────────
            # element_idx is 1-based; register pages for CAT_ELEMENTS are
            # 0-based, so page = element_idx - 1.
            # temps_cache avoids re-reading the same physical thermostat when
            # it is wired to multiple zones (same element_idx).
            has_floor = (
                channel_thermostat_type(self._entry.options, ch, self._entry.data)
                == THERMOSTAT_AIR_FLOOR
            )
            if element_idx > 0:
                if element_idx not in temps_cache:
                    temps_cache[element_idx] = self.client.read_registers(
                        CAT_ELEMENTS, IDX_ELEM_AIR_TEMP,
                        page=element_idx - 1, qty=2,
                    )
                temps = temps_cache[element_idx]
                data[ch_key(ch, KEY_AIR_TEMP)] = (
                    raw_to_temp(temps[0]) if temps else None
                )
                data[ch_key(ch, KEY_FLOOR_TEMP)] = (
                    raw_to_temp(temps[1]) if (has_floor and temps and len(temps) > 1) else None
                )
            else:
                data[ch_key(ch, KEY_AIR_TEMP)] = None
                data[ch_key(ch, KEY_FLOOR_TEMP)] = None

        # Aggregate per-group data.
        # Valve: heating if ANY channel in the zone is open.
        # TP lost: lost if ANY channel is lost.
        # Desired temp: all channels in a zone share one setpoint — use the
        # primary channel's value as the authoritative zone setpoint, so all
        # channels reflect the same target temperature.
        for primary_ch, group_channels in self.thermostat_groups.items():
            if len(group_channels) > 1:
                data[ch_key(primary_ch, KEY_VALVE_OPEN)] = any(
                    data.get(ch_key(ch, KEY_VALVE_OPEN), False) for ch in group_channels
                )
                data[ch_key(primary_ch, KEY_TP_LOST)] = any(
                    data.get(ch_key(ch, KEY_TP_LOST), False) for ch in group_channels
                )
                zone_setpoint = data.get(ch_key(primary_ch, KEY_DESIRED_TEMP))
                if zone_setpoint is not None:
                    for ch in group_channels:
                        data[ch_key(ch, KEY_DESIRED_TEMP)] = zone_setpoint

        # Pass live grouping back for auto-discovery in _async_update_data.
        data["__live_groups__"] = {
            min(chs): sorted(chs) for chs in live_elem_ch.values()
        }

        return data

    # ── Temperature write ─────────────────────────────────────────────────────

    async def async_set_temperature(
        self, channel: int, temp_celsius: float
    ) -> None:
        """Write a new setpoint to all channels in the thermostat group.

        Accepts either a primary channel (key in thermostat_groups) or any
        member channel — both resolve to the full group so the setpoint is
        always written uniformly across every circuit sharing that thermostat.
        """
        temp_celsius = max(MIN_TEMP, min(MAX_TEMP, temp_celsius))
        raw_val = int(round(temp_celsius * 10))
        # Resolve to the primary channel that owns this channel's group.
        if channel not in self.thermostat_groups:
            channel = next(
                (p for p, chs in self.thermostat_groups.items() if channel in chs),
                channel,
            )
        group = self.thermostat_groups.get(channel, [channel])

        def _write() -> None:
            self.client.ensure_connected()
            for ch in group:
                ok = self.client.write_register(CAT_PACKED, IDX_CH_MANUAL_TEMP, page=ch, val=raw_val)
                if not ok:
                    _LOGGER.warning(
                        "Zone %d set_temperature %.1f °C: no echo (write may still have taken effect)",
                        ch + 1, temp_celsius,
                    )

        await self.hass.async_add_executor_job(_write)
        await self.async_request_refresh()

