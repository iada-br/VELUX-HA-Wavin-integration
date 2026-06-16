"""Climate platform for Wavin AHC 9000 zones."""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    KEY_AIR_TEMP,
    KEY_DESIRED_TEMP,
    KEY_TP_LOST,
    MAX_TEMP,
    MIN_TEMP,
    TEMP_STEP,
    ch_key,
    channel_display_name,
)
from .coordinator import WavinCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register one WavinClimate entity per configured zone."""
    coordinator: WavinCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WavinClimate(coordinator, entry, ch)
        for ch in coordinator.thermostat_channels
    )


class WavinClimate(CoordinatorEntity[WavinCoordinator], ClimateEntity):
    """
    Thermostat entity for one underfloor heating zone.

    current_temperature  → element air sensor    (CAT_ELEMENTS, IDX_ELEM_AIR_TEMP)
    target_temperature   → desired setpoint       (CAT_CHANNELS, IDX_CH_DESIRED_TEMP)
    async_set_temperature → FC 0x44 write        (CAT_CHANNELS, IDX_CH_DESIRED_TEMP)

    HVAC mode is HEAT-only.  The AHC 9000 does not expose on/off control
    through the available register interface.
    """

    _attr_has_entity_name = True
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TEMP_STEP

    def __init__(
        self,
        coordinator: WavinCoordinator,
        entry: ConfigEntry,
        channel: int,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_unique_id = f"{entry.entry_id}_climate_ch{channel}"
        self._attr_name = channel_display_name(entry.options, channel, entry.data)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Wavin AHC 9000",
            manufacturer="Wavin",
            model="AHC 9000 AC-116",
            configuration_url=f"http://{entry.data['host']}",
        )

    @property
    def max_temp(self) -> float:
        return MAX_TEMP

    @property
    def min_temp(self) -> float:
        return MIN_TEMP

    @property
    def available(self) -> bool:
        """Mark unavailable when the coordinator itself has no data."""
        return super().available

    @property
    def current_temperature(self) -> Optional[float]:
        """Current room temperature from the zone's air sensor (°C)."""
        return self.coordinator.data.get(ch_key(self._channel, KEY_AIR_TEMP))

    @property
    def target_temperature(self) -> Optional[float]:
        """Desired setpoint currently stored in the controller (°C)."""
        return self.coordinator.data.get(ch_key(self._channel, KEY_DESIRED_TEMP))

    @property
    def extra_state_attributes(self) -> dict:
        tp_lost = self.coordinator.data.get(ch_key(self._channel, KEY_TP_LOST))
        return {"thermostat_lost": tp_lost}

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Handle a temperature change requested from the HA UI or an automation."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.coordinator.async_set_temperature(
            self._channel, float(temperature)
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """No-op: HEAT is the only supported mode."""
