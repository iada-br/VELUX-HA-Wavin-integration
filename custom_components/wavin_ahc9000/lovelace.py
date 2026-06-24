"""Automatic Lovelace dashboard for the Wavin AHC 9000 integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import (
    CONF_THERMOSTAT_GROUPS,
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
)

_LOGGER = logging.getLogger(__name__)

_DASHBOARD_URL_PATH = "wavin-heating"
_DASHBOARD_TITLE    = "Underfloor Heating"
_DASHBOARD_ICON     = "mdi:radiator"
_LOVELACE_DOMAIN    = "lovelace"
_STORE_VERSION      = 1


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def build_dashboard_config(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Build the Lovelace dashboard dict from the config entry.

    Entity IDs are resolved from the entity registry so user renames are
    respected. Called after async_forward_entry_setups so all entities exist.
    """
    raw_groups: dict = entry.data.get(CONF_THERMOSTAT_GROUPS, {})
    entry_id = entry.entry_id

    thermostat_cards: list[dict] = []
    for primary_ch_str in sorted(raw_groups, key=lambda k: int(k)):
        primary_ch = int(primary_ch_str)

        climate_id = _entity_id(hass, "climate", f"{entry_id}_climate_ch{primary_ch}")
        valve_id   = _entity_id(hass, "switch",  f"{entry_id}_valve_switch_ch{primary_ch}")
        air_id     = _entity_id(hass, "sensor",  f"{entry_id}_sensor_ch{primary_ch}_air_temp")

        if not climate_id:
            _LOGGER.warning(
                "Wavin AHC 9000: no climate entity for channel %d — skipping card.",
                primary_ch,
            )
            continue

        entity_rows: list[dict] = []
        if valve_id:
            entity_rows.append({"entity": valve_id, "name": "Heating active", "icon": "mdi:radiator"})
        if air_id:
            entity_rows.append({"entity": air_id, "name": "Air temperature"})
        entity_rows.append({
            "entity":    climate_id,
            "attribute": "linked_circuits",
            "name":      "Linked circuits",
            "icon":      "mdi:pipe-disconnected",
        })

        thermostat_cards.append({
            "type": "vertical-stack",
            "cards": [
                {
                    "type":     "thermostat",
                    "entity":   climate_id,
                    "min_temp": MIN_TEMP,
                    "max_temp": MAX_TEMP,
                },
                {"type": "entities", "entities": entity_rows},
            ],
        })

    return {
        "title": _DASHBOARD_TITLE,
        "views": [
            {
                "title": "Heating",
                "path":  "heating",
                "icon":  _DASHBOARD_ICON,
                "cards": thermostat_cards,
            }
        ],
    }


async def async_setup_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create or update the Wavin Lovelace dashboard.

    Uses the live DashboardsCollection so the entry persists in HA's in-memory
    state and survives any subsequent saves from the UI — unlike writing directly
    to the storage file.
    """
    config = build_dashboard_config(hass, entry)
    _LOGGER.debug(
        "Wavin AHC 9000: dashboard build produced %d cards.",
        len(config["views"][0]["cards"]),
    )

    lovelace: dict = hass.data.get(_LOVELACE_DOMAIN, {})
    _LOGGER.debug(
        "Wavin AHC 9000: hass.data['lovelace'] keys = %s",
        list(lovelace.keys()) if isinstance(lovelace, dict) else type(lovelace).__name__,
    )
    collection = lovelace.get("dashboards_collection")

    if collection is not None:
        _LOGGER.debug(
            "Wavin AHC 9000: using live DashboardsCollection (%d item(s) currently registered).",
            len(list(collection.async_items())),
        )
        await _upsert_via_collection(hass, lovelace, collection, config)
    else:
        _LOGGER.warning(
            "Wavin AHC 9000: lovelace dashboards_collection not found — "
            "dashboard will appear after HA restart."
        )
        await _write_fallback_storage(hass, config)

    _LOGGER.warning(
        "Wavin AHC 9000: dashboard '%s' configured (%d thermostat card(s)).",
        _DASHBOARD_URL_PATH,
        len(config["views"][0]["cards"]),
    )


async def _upsert_via_collection(
    hass: HomeAssistant,
    lovelace: dict,
    collection: Any,
    config: dict[str, Any],
) -> None:
    """Register or refresh the dashboard via the live DashboardsCollection."""
    import voluptuous as vol  # noqa: PLC0415

    existing = next(
        (item for item in collection.async_items()
         if item.get("url_path") == _DASHBOARD_URL_PATH),
        None,
    )

    if existing is None:
        # First install — async_create_item fires storage_dashboard_changed(CHANGE_ADDED)
        # which immediately creates a LovelaceStorage object in hass.data["lovelace"]
        # ["dashboards"] and registers the sidebar panel.
        try:
            await collection.async_create_item({
                "url_path":        _DASHBOARD_URL_PATH,
                "title":           _DASHBOARD_TITLE,
                "icon":            _DASHBOARD_ICON,
                "require_admin":   False,
                "show_in_sidebar": True,
            })
            _LOGGER.debug("Wavin AHC 9000: dashboard created in collection.")
        except vol.Invalid as exc:
            # The frontend panel is already registered (e.g. loaded from storage on
            # startup) but not in the collection's in-memory items. The sidebar entry
            # is already there — just update the content below.
            _LOGGER.warning(
                "Wavin AHC 9000: async_create_item rejected (%s) — "
                "panel already registered, updating content only.",
                exc,
            )
    else:
        _LOGGER.debug(
            "Wavin AHC 9000: dashboard already in collection (id=%s) — updating content.",
            existing["id"],
        )

    # Write content via the live LovelaceStorage object so HA's in-memory cache is
    # updated and connected browsers receive an EVENT_LOVELACE_UPDATED notification.
    live_dash = lovelace.get("dashboards", {}).get(_DASHBOARD_URL_PATH)
    if live_dash is not None and hasattr(live_dash, "async_save"):
        await live_dash.async_save(config)
        _LOGGER.debug("Wavin AHC 9000: dashboard content saved via LovelaceStorage.")
    else:
        # Fallback: write directly to the storage file (dashboard appears after restart).
        dashboard_id = existing["id"] if existing else _DASHBOARD_URL_PATH
        content_store: Store[dict[str, Any]] = Store(
            hass, _STORE_VERSION, f"lovelace.{dashboard_id}"
        )
        await content_store.async_save({"config": config})
        _LOGGER.warning(
            "Wavin AHC 9000: LovelaceStorage not found for '%s' — "
            "wrote to storage file; dashboard will appear after HA restart.",
            _DASHBOARD_URL_PATH,
        )


async def _write_fallback_storage(hass: HomeAssistant, config: dict[str, Any]) -> None:
    """Write dashboard registration + content directly to storage files.

    Last resort: takes effect after HA restarts. Entries written this way can
    be overwritten if the user later modifies dashboards via the UI (because HA
    saves its in-memory collection state, which won't include our entry).
    """
    content_store: Store[dict[str, Any]] = Store(
        hass, _STORE_VERSION, f"lovelace.{_DASHBOARD_URL_PATH}"
    )
    await content_store.async_save({"config": config})

    reg_store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, "lovelace_dashboards")
    registry = await reg_store.async_load() or {"items": []}
    items: list[dict] = registry.get("items", [])
    if not any(item.get("url_path") == _DASHBOARD_URL_PATH for item in items):
        items.append({
            "id":              _DASHBOARD_URL_PATH,
            "url_path":        _DASHBOARD_URL_PATH,
            "title":           _DASHBOARD_TITLE,
            "icon":            _DASHBOARD_ICON,
            "require_admin":   False,
            "mode":            "storage",
            "show_in_sidebar": True,
        })
        registry["items"] = items
        await reg_store.async_save(registry)


async def async_remove_dashboard(hass: HomeAssistant) -> None:
    """Remove the Wavin dashboard from HA when the integration is removed."""
    lovelace: dict = hass.data.get(_LOVELACE_DOMAIN, {})
    collection = lovelace.get("dashboards_collection")

    if collection is not None:
        existing = next(
            (item for item in collection.async_items()
             if item.get("url_path") == _DASHBOARD_URL_PATH),
            None,
        )
        if existing is not None:
            try:
                await collection.async_delete_item(existing["id"])
                _LOGGER.debug("Wavin AHC 9000: dashboard removed from collection.")
                return
            except Exception:
                _LOGGER.debug(
                    "Wavin AHC 9000: collection removal failed, cleaning up storage.",
                    exc_info=True,
                )

    # Fallback: clean up storage files.
    content_store: Store[dict[str, Any]] = Store(
        hass, _STORE_VERSION, f"lovelace.{_DASHBOARD_URL_PATH}"
    )
    await content_store.async_remove()

    reg_store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, "lovelace_dashboards")
    registry = await reg_store.async_load() or {"items": []}
    registry["items"] = [
        item for item in registry.get("items", [])
        if item.get("url_path") != _DASHBOARD_URL_PATH
    ]
    await reg_store.async_save(registry)
    _LOGGER.debug("Wavin AHC 9000: dashboard removed from storage.")
