"""Diagnostics for the Wavin AHC 9000 integration.

Provides the "Download diagnostics" button in
Settings → Devices & Services → Wavin AHC 9000.

The exported JSON contains the thermostat-to-circuit mapping together with
the most recent live readings, so installers and users can document or
troubleshoot the floor-heating layout without opening the HA developer tools.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHANNEL_NAMES,
    CONF_THERMOSTAT_GROUPS,
    DOMAIN,
    KEY_AIR_TEMP,
    KEY_DESIRED_TEMP,
    KEY_FLOOR_TEMP,
    KEY_TP_LOST,
    KEY_VALVE_OPEN,
    ch_key,
    channel_display_name,
    channel_thermostat_type,
)
from .coordinator import WavinCoordinator

_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Shape
    -----
    {
      "config": { ... redacted connection params ... },
      "thermostats": [
        {
          "name":                "Living Room",
          "thermostat_type":     "air_only",
          "primary_circuit":     1,
          "circuits":            [1, 2, 3],
          "current_temp_c":      21.5,
          "floor_temp_c":        22.1,
          "target_temp_c":       22.0,
          "valve_open":          true,
          "thermostat_lost":     false
        },
        ...
      ]
    }
    """
    coordinator: WavinCoordinator = hass.data[DOMAIN][entry.entry_id]
    live: dict[str, Any] = coordinator.data or {}

    raw_groups: dict = entry.data.get(CONF_THERMOSTAT_GROUPS, {})

    thermostats = []
    for primary_ch_str in sorted(raw_groups, key=lambda k: int(k)):
        primary_ch = int(primary_ch_str)
        circuits_0idx: list[int] = raw_groups[primary_ch_str]

        thermostats.append(
            {
                "name": channel_display_name(entry.options, primary_ch, entry.data),
                "thermostat_type": channel_thermostat_type(
                    entry.options, primary_ch, entry.data
                ),
                "primary_circuit": primary_ch + 1,
                "circuits":        sorted(ch + 1 for ch in circuits_0idx),
                "current_temp_c":  live.get(ch_key(primary_ch, KEY_AIR_TEMP)),
                "floor_temp_c":    live.get(ch_key(primary_ch, KEY_FLOOR_TEMP)),
                "target_temp_c":   live.get(ch_key(primary_ch, KEY_DESIRED_TEMP)),
                "valve_open":      live.get(ch_key(primary_ch, KEY_VALVE_OPEN), False),
                "thermostat_lost": live.get(ch_key(primary_ch, KEY_TP_LOST), False),
            }
        )

    return {
        "config": async_redact_data(
            {**entry.data, **entry.options},
            _REDACT,
        ),
        "thermostats": thermostats,
    }
