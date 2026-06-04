"""Number platform for Wavin AHC 9000 — comfort and eco temperature limits."""
from __future__ import annotations

from typing import Optional

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    KEY_COMFORT_TEMP,
    KEY_ECO_TEMP,
    MAX_TEMP,
    MIN_TEMP,
    TEMP_STEP,
    ch_key,
    channel_display_name,
)
from .coordinator import WavinCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register comfort and eco temperature number entities for every active zone."""
    coordinator: WavinCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    for ch in coordinator.active_channels:
        room = channel_display_name(entry.options, ch, entry.data)
        entities.append(
            WavinRangeNumber(
                coordinator, entry, ch,
                data_key=KEY_COMFORT_TEMP,
                label=f"{room} Comfort Temperature",
                setter="async_set_comfort_temp",
            )
        )
        entities.append(
            WavinRangeNumber(
                coordinator, entry, ch,
                data_key=KEY_ECO_TEMP,
                label=f"{room} Eco Temperature",
                setter="async_set_eco_temp",
            )
        )

    async_add_entities(entities)


class WavinRangeNumber(CoordinatorEntity[WavinCoordinator], NumberEntity):
    """
    Editable number entity for one temperature range limit (comfort or eco).

    Displayed as a box input on HA dashboards. Writing a new value sends
    FC 0x44 to the controller and triggers an immediate poll to confirm.
    """

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_step = TEMP_STEP
    _attr_mode = NumberMode.BOX
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WavinCoordinator,
        entry: ConfigEntry,
        channel: int,
        data_key: str,
        label: str,
        setter: str,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._data_key = data_key
        self._setter = setter
        self._attr_unique_id = f"{entry.entry_id}_number_ch{channel}_{data_key}"
        self._attr_name = label
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

    async def async_set_native_value(self, value: float) -> None:
        await getattr(self.coordinator, self._setter)(self._channel, value)
