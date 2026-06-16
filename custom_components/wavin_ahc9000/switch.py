"""Switch platform for Wavin AHC 9000 valve control."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_VALVE_OPEN, MAX_TEMP, MIN_TEMP, ch_key
from .coordinator import WavinCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WavinCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WavinValveSwitch(coordinator, entry, ch)
        for ch in coordinator.thermostat_channels
    )


class WavinValveSwitch(CoordinatorEntity[WavinCoordinator], SwitchEntity):
    """
    Controls a heating zone valve by writing to the temperature setpoint register.

    Turn on  → setpoint = MAX_TEMP (35 °C) → controller opens valve
    Turn off → setpoint = MIN_TEMP (5 °C)  → controller closes valve

    is_on reflects the actual physical valve/output state read from the
    controller, not just the last command sent.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WavinCoordinator,
        entry: ConfigEntry,
        channel: int,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_unique_id = f"{entry.entry_id}_valve_switch_ch{channel}"
        self._attr_name = f"Zone {channel + 1} Valve"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Wavin AHC 9000",
            manufacturer="Wavin",
            model="AHC 9000 AC-116",
            configuration_url=f"http://{entry.data['host']}",
        )

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get(ch_key(self._channel, KEY_VALVE_OPEN), False))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_temperature(self._channel, MAX_TEMP)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_temperature(self._channel, MIN_TEMP)
