"""Sensor platform for the Wavin AHC 9000 integration."""
from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    KEY_AIR_TEMP,
    KEY_FLOOR_TEMP,
    KEY_DESIRED_TEMP,
    THERMOSTAT_AIR_FLOOR,
    ch_key,
    channel_display_name,
    channel_thermostat_type,
)
from .coordinator import WavinCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register temperature sensors for all zones."""
    coordinator: WavinCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for ch in coordinator.active_channels:
        room = channel_display_name(entry.options, ch, entry.data)
        entities.append(
            WavinTemperatureSensor(
                coordinator, entry, ch, KEY_AIR_TEMP,
                f"{room} Air Temperature", enabled=True,
            )
        )
        # Floor sensor only created when the channel is configured as air+floor type.
        if channel_thermostat_type(entry.options, ch, entry.data) == THERMOSTAT_AIR_FLOOR:
            entities.append(
                WavinTemperatureSensor(
                    coordinator, entry, ch, KEY_FLOOR_TEMP,
                    f"{room} Floor Temperature", enabled=True,
                )
            )

    async_add_entities(entities)


class WavinTemperatureSensor(CoordinatorEntity[WavinCoordinator], SensorEntity):
    """
    Air or floor temperature sensor for one zone.

    Air temperature mirrors the climate entity's current_temperature but is
    useful for dashboards, history graphs, and automations.

    Floor temperature is disabled by default — it requires a physical floor
    sensor to be wired to the thermostat (enable it in the entity registry if
    your installation has floor sensors).
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: WavinCoordinator,
        entry: ConfigEntry,
        channel: int,
        data_key: str,
        label: str,
        enabled: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._data_key = data_key
        self._attr_entity_registry_enabled_default = enabled
        self._attr_unique_id = (
            f"{entry.entry_id}_sensor_ch{channel}_{data_key}"
        )
        self._attr_name = f"Zone {channel + 1} {label}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Wavin AHC 9000",
            manufacturer="Wavin",
            model="AHC 9000 AC-116",
            configuration_url=f"http://{entry.data['host']}",
        )

    @property
    def native_value(self) -> Optional[float]:
        return self.coordinator.data.get(ch_key(self._channel, self._data_key))
