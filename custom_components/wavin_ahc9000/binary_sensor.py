"""Binary sensor platform for Wavin AHC 9000 valve/output status."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_VALVE_OPEN, ch_key
from .coordinator import WavinCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WavinCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        WavinValveSensor(coordinator, entry, ch)
        for ch in coordinator.active_channels
    )


class WavinValveSensor(CoordinatorEntity[WavinCoordinator], BinarySensorEntity):
    """True when the zone's actuator/valve output is active (heating)."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(
        self,
        coordinator: WavinCoordinator,
        entry: ConfigEntry,
        channel: int,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_unique_id = f"{entry.entry_id}_valve_ch{channel}"
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
