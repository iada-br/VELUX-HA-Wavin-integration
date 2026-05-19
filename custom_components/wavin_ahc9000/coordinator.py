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
    CAT_PACKED,
    CAT_CHANNELS,
    CONF_ACTIVE_CHANNELS,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IDX_CH_MANUAL_TEMP,
    IDX_CH_PRIMARY_ELEMENT,
    IDX_CH_TIMER_EVENT,
    KEY_DESIRED_TEMP,
    KEY_TP_LOST,
    KEY_VALVE_OPEN,
    MAX_TEMP,
    MIN_TEMP,
    PRIMARY_ELEMENT_TP_LOST_MASK,
    TIMER_EVENT_OUTP_ON_MASK,
    ch_key,
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
            return await self.hass.async_add_executor_job(self._fetch_all)
        except CannotConnect as exc:
            raise UpdateFailed(
                f"Cannot connect to Wavin AHC 9000 at"
                f" {self._entry.data[CONF_HOST]}:{self._entry.data[CONF_PORT]}: {exc}"
            ) from exc
        except Exception as exc:
            raise UpdateFailed(
                f"Unexpected error reading Wavin AHC 9000: {exc}"
            ) from exc

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

        for ch in self.active_channels:
            # ── Thermostat-lost flag ───────────────────────────────────────
            prim = self.client.read_registers(
                CAT_CHANNELS, IDX_CH_PRIMARY_ELEMENT, page=ch, qty=1
            )
            data[ch_key(ch, KEY_TP_LOST)] = (
                bool(prim[0] & PRIMARY_ELEMENT_TP_LOST_MASK) if prim else True
            )

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

        return data

    # ── Temperature write ─────────────────────────────────────────────────────

    async def async_set_temperature(
        self, channel: int, temp_celsius: float
    ) -> None:
        """Write a new setpoint to the given zone (0-indexed)."""
        temp_celsius = max(MIN_TEMP, min(MAX_TEMP, temp_celsius))
        raw_val = int(round(temp_celsius * 10))

        def _write() -> bool:
            self.client.ensure_connected()
            return self.client.write_register(
                CAT_PACKED, IDX_CH_MANUAL_TEMP, page=channel, val=raw_val
            )

        success = await self.hass.async_add_executor_job(_write)
        if not success:
            _LOGGER.warning(
                "Zone %d set_temperature %.1f °C: no echo received"
                " (write may still have taken effect; next poll will confirm)",
                channel + 1, temp_celsius,
            )
        await self.async_request_refresh()
