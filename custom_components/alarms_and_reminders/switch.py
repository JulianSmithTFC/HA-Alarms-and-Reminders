"""Expose each alarm/reminder as a Switch so user can enable/disable items"""
from __future__ import annotations
from typing import Dict, List
import asyncio
import logging
from datetime import datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, DEFAULT_NAME
from .coordinator import AlarmAndReminderCoordinator, ITEM_CREATED, ITEM_UPDATED, ITEM_DELETED, DASHBOARD_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up switches for each saved alarm/reminder for this config entry."""
    # Coordinator may be stored at root hass.data[DOMAIN]["coordinator"] or per-entry
    root = hass.data.get(DOMAIN, {})
    # prefer per-entry coordinator if stored, otherwise root["coordinator"]
    coordinator = root.get(entry.entry_id, {}).get("coordinator") or root.get("coordinator")
    if not coordinator:
        _LOGGER.error("Coordinator not found for entry %s", entry.entry_id)
        return

    entities: List[AlarmItemSwitch] = []

    # Build initial switches from currently loaded items
    for item_id in list(coordinator._active_items.keys()):
        entities.append(AlarmItemSwitch(coordinator, item_id))

    async_add_entities(entities, True)

    # Track entities by item_id for quick lookup
    entity_map: Dict[str, AlarmItemSwitch] = {e.item_id: e for e in entities}

    @callback
    def _on_item_created(item_id: str):
        """Add entity when coordinator reports a new item."""
        if item_id in entity_map:
            return
        ent = AlarmItemSwitch(coordinator, item_id)
        entity_map[item_id] = ent
        hass.async_create_task(async_add_entities([ent], True))

    @callback
    def _on_item_deleted(item_id: str):
        """Remove entity when coordinator reports deletion."""
        ent = entity_map.pop(item_id, None)
        if ent:
            # Let HA remove state; entity will be removed from registry automatically if configured
            hass.async_create_task(ent.async_remove())

    @callback
    def _on_item_updated(item_id: str):
        """Refresh entity state/attributes on updates."""
        ent = entity_map.get(item_id)
        if ent:
            ent.async_schedule_update_ha_state(False)

    async_dispatcher_connect(hass, ITEM_CREATED, lambda ev: _on_item_created(ev))  # send item_id from coordinator
    async_dispatcher_connect(hass, ITEM_DELETED, lambda ev: _on_item_deleted(ev))
    async_dispatcher_connect(hass, ITEM_UPDATED, lambda ev: _on_item_updated(ev))


class AlarmItemSwitch(SwitchEntity):
    """Switch representing a single alarm/reminder item."""

    def __init__(self, coordinator: AlarmAndReminderCoordinator, item_id: str):
        self.coordinator = coordinator
        self.item_id = item_id
        self._attr_name = coordinator._active_items.get(item_id, {}).get("name", item_id)
        self._attr_unique_id = f"{item_id}"
        self._available = True

    @property
    def is_on(self) -> bool:
        item = self.coordinator._active_items.get(self.item_id, {})
        return item.get("enabled", False)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose item attributes in the switch entity for dashboard view."""
        item = self.coordinator._active_items.get(self.item_id, {}) or {}
        # Normalize scheduled_time to iso string if it's a datetime
        sched = item.get("scheduled_time")
        try:
            if hasattr(sched, "isoformat"):
                sched = sched.isoformat()
        except Exception:
            pass

        return {
            "name": item.get("name"),
            "message": item.get("message"),
            "scheduled_time": sched,
            "status": item.get("status"),
            "is_alarm": bool(item.get("is_alarm")),
            "repeat": item.get("repeat"),
            "repeat_days": item.get("repeat_days"),
            "sound_file": item.get("sound_file"),
            "notify_device": item.get("notify_device"),
            "enabled": item.get("enabled", True),
        }

    @property
    def device_info(self):
        """Return device info so all switches are grouped under one integration device."""
        # Use the coordinator id (set to the config entry id) for grouping under the device created in __init__.py
        device_id = getattr(self.coordinator, "id", "controller")
        return {
            "identifiers": {(DOMAIN, device_id)},
            "name": DEFAULT_NAME,
            "manufacturer": "Alarms and Reminders",
            "model": "alarms_and_reminders",
        }

    async def async_turn_on(self, **kwargs):
        """Enable and reschedule the item."""
        # enable the item and persist
        item = self.coordinator._active_items.get(self.item_id)
        if item:
            item["enabled"] = True
            self.coordinator._active_items[self.item_id] = item
            await self.coordinator.storage.async_save(self.coordinator._active_items)
            self.schedule_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Disable the scheduled item."""
        item = self.coordinator._active_items.get(self.item_id)
        if item:
            item["enabled"] = False
            self.coordinator._active_items[self.item_id] = item
            await self.coordinator.storage.async_save(self.coordinator._active_items)
            self.schedule_update_ha_state()

    async def async_added_to_hass(self):
        """Register listener so entity updates when coordinator publishes changes."""
        @callback
        def _handle_update(event_item_id: str, item: dict = None):
            # refresh name and state
            if event_item_id != self.item_id:
                return
            self._name = self.coordinator._active_items.get(self.item_id, {}).get("name", self._name)
            # write state so extra_state_attributes are refreshed on the UI
            self.async_write_ha_state()

        # keep reference so we can remove later if needed
        self._remove_dispatcher = async_dispatcher_connect(self.hass, ITEM_UPDATED, _handle_update)

    async def async_will_remove_from_hass(self):
        """Cleanup listeners."""
        if getattr(self, "_remove_dispatcher", None):
            self._remove_dispatcher()
            self._remove_dispatcher = None
