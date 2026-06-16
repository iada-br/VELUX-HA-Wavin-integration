"""Wavin AHC 9000 Home Assistant integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_ACTIVE_CHANNELS,
    CONF_CHANNEL_NAMES,
    DOMAIN,
    KEY_DESIRED_TEMP,
    KEY_VALVE_OPEN,
    MAX_TEMP,
    MIN_TEMP,
    SERVICE_GET_CHANNEL_INFO,
    SERVICE_SET_VALVE,
    channel_display_name,
    ch_key,
)
from .coordinator import WavinCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH]

_SET_VALVE_SCHEMA = vol.Schema(
    {
        vol.Optional("zone_name"): cv.string,
        vol.Optional("channel"): vol.All(vol.Coerce(int), vol.Range(min=0, max=15)),
        vol.Required("open"): cv.boolean,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Set up Wavin AHC 9000 from a config entry.

    Creates the coordinator, performs the first data fetch (raises
    ConfigEntryNotReady on failure so HA retries with back-off), stores
    the coordinator in hass.data, then forwards setup to each platform.
    """
    coordinator = WavinCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register services once for the whole domain (guard against multiple entries).
    if not hass.services.has_service(DOMAIN, SERVICE_SET_VALVE):
        _register_services(hass)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and close the persistent socket."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: WavinCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(coordinator.client.disconnect)

    # Remove services only when the last entry is gone.
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_SET_VALVE)
        hass.services.async_remove(DOMAIN, SERVICE_GET_CHANNEL_INFO)

    return unload_ok


# ── Service registration ──────────────────────────────────────────────────────

def _register_services(hass: HomeAssistant) -> None:
    """Register domain-level services. Called once when the first entry loads."""

    async def _handle_set_valve(call: ServiceCall) -> None:
        """
        Open or close a zone valve.

        Identification priority: zone_name (case-insensitive) > channel index.
        open=true  → setpoint MAX (forces valve open)
        open=false → setpoint MIN (forces valve closed)
        """
        zone_name: str | None = call.data.get("zone_name")
        channel_idx: int | None = call.data.get("channel")
        temp = MAX_TEMP if call.data["open"] else MIN_TEMP

        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue

            if zone_name is not None:
                for ch in coordinator.active_channels:
                    if channel_display_name(entry.options, ch).lower() == zone_name.lower():
                        await coordinator.async_set_temperature(ch, temp)
                        return
            elif channel_idx is not None and channel_idx in coordinator.active_channels:
                await coordinator.async_set_temperature(channel_idx, temp)
                return

        _LOGGER.warning(
            "set_valve: no matching zone found (zone_name=%r, channel=%r)",
            zone_name,
            channel_idx,
        )

    async def _handle_get_channel_info(call: ServiceCall) -> ServiceResponse:
        """
        Return current state for all zones across all loaded Wavin entries.

        Response shape
        --------------
        {
            "zones": [
                {
                    "channel": 0,
                    "name": "Living Room",
                    "current_temperature": 21.5,
                    "target_temperature": 22.0,
                    "valve_open": true,
                    "thermostat_lost": false
                },
                ...
            ]
        }
        """
        zones: list[dict] = []

        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            data = coordinator.data or {}
            for ch in coordinator.active_channels:
                zones.append(
                    {
                        "channel": ch,
                        "name": channel_display_name(entry.options, ch),
                        "valve_open": data.get(ch_key(ch, KEY_VALVE_OPEN), False),
                        "setpoint": data.get(ch_key(ch, KEY_DESIRED_TEMP)),
                    }
                )

        return {"zones": zones}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_VALVE,
        _handle_set_valve,
        schema=_SET_VALVE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_CHANNEL_INFO,
        _handle_get_channel_info,
        supports_response=SupportsResponse.ONLY,
    )
