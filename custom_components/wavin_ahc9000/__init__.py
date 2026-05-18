"""Wavin AHC 9000 Home Assistant integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_CHANNEL_NAMES,
    DOMAIN,
    KEY_AIR_TEMP,
    KEY_DESIRED_TEMP,
    KEY_TP_LOST,
    KEY_VALVE_OPEN,
    MAX_TEMP,
    MIN_TEMP,
    SERVICE_GET_CHANNEL_INFO,
    SERVICE_SET_TEMPERATURE,
    channel_display_name,
    ch_key,
)
from .coordinator import WavinCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]

_SET_TEMPERATURE_SCHEMA = vol.Schema(
    {
        vol.Optional("zone_name"): cv.string,
        vol.Optional("channel"): vol.All(vol.Coerce(int), vol.Range(min=0, max=9)),
        vol.Required("temperature"): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_TEMP, max=MAX_TEMP)
        ),
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
    if not hass.services.has_service(DOMAIN, SERVICE_SET_TEMPERATURE):
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
        hass.services.async_remove(DOMAIN, SERVICE_SET_TEMPERATURE)
        hass.services.async_remove(DOMAIN, SERVICE_GET_CHANNEL_INFO)

    return unload_ok


# ── Service registration ──────────────────────────────────────────────────────

def _register_services(hass: HomeAssistant) -> None:
    """Register domain-level services. Called once when the first entry loads."""

    async def _handle_set_temperature(call: ServiceCall) -> None:
        """
        Set the target temperature of a zone.

        Identification priority: zone_name (case-insensitive) > channel index.
        Searches all loaded Wavin entries so the caller does not need to know
        which config entry owns the zone.
        """
        zone_name: str | None = call.data.get("zone_name")
        channel_idx: int | None = call.data.get("channel")
        temperature: float = call.data["temperature"]

        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items():
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue

            if zone_name is not None:
                for ch in range(coordinator.num_channels):
                    if channel_display_name(entry.options, ch).lower() == zone_name.lower():
                        await coordinator.async_set_temperature(ch, temperature)
                        return
            elif channel_idx is not None and channel_idx < coordinator.num_channels:
                await coordinator.async_set_temperature(channel_idx, temperature)
                return

        _LOGGER.warning(
            "set_temperature: no matching zone found (zone_name=%r, channel=%r)",
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
            for ch in range(coordinator.num_channels):
                zones.append(
                    {
                        "channel": ch,
                        "name": channel_display_name(entry.options, ch),
                        "current_temperature": data.get(ch_key(ch, KEY_AIR_TEMP)),
                        "target_temperature": data.get(ch_key(ch, KEY_DESIRED_TEMP)),
                        "valve_open": data.get(ch_key(ch, KEY_VALVE_OPEN), False),
                        "thermostat_lost": data.get(ch_key(ch, KEY_TP_LOST), False),
                    }
                )

        return {"zones": zones}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_TEMPERATURE,
        _handle_set_temperature,
        schema=_SET_TEMPERATURE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_CHANNEL_INFO,
        _handle_get_channel_info,
        supports_response=SupportsResponse.ONLY,
    )
