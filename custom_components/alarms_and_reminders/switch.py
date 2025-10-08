# Expose each alarm/reminder as a Switch so user can enable/disable items
from __future__ import annotations
from typing import Dict, List
import asyncio
import logging
from datetime import datetime

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, DEFAULT_NAME
from .coordinator import AlarmAndReminderCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up switches for each saved alarm/reminder for this config entry."""
    # Coordinator may be stored at root hass.data[DOMAIN]["coordinator"] or per-entry
    root = hass.data.get(DOMAIN, {})
    coordinator = root.get("coordinator") or root.get(entry.entry_id, {}).get("coordinator")
    if not coordinator:
        _LOGGER.error("Coordinator not found for entry %s", entry.entry_id)
        return

    entities: List[AlarmItemSwitch] = []

    # Build initial switches from currently loaded items
    for item_id in coordinator._active_items.keys():
        entities.append(AlarmItemSwitch(coordinator, item_id))

    async_add_entities(entities, True)

    # Track entities by item_id for quick lookup
    entity_map: Dict[str, AlarmItemSwitch] = {e.item_id: e for e in entities}

    @callback
    def _on_state_change(event):
        """Handle coordinator state change events: add/update/remove switches."""
        eid = event.data.get("entity_id")
        if not eid or not eid.startswith(f"{DOMAIN}."):
            return
        item_id = eid.split(".")[-1]

        # If item exists but switch not created, add it
        if item_id in coordinator._active_items and item_id not in entity_map:
            ent = AlarmItemSwitch(coordinator, item_id)
            entity_map[item_id] = ent
            hass.async_create_task(async_add_entities([ent], True))
            return

        # If entity exists, ask it to refresh its state
        if item_id in entity_map:
            entity_map[item_id].async_schedule_update_ha_state(False)

    hass.bus.async_listen(f"{DOMAIN}_state_changed", _on_state_change)


class AlarmItemSwitch(SwitchEntity):
    """Switch representing a single alarm/reminder item."""

    def __init__(self, coordinator: AlarmAndReminderCoordinator, item_id: str):
        self.coordinator = coordinator
        self.item_id = item_id
        self._available = True
        # default name; will be kept in sync on update
        self._name = coordinator._active_items.get(item_id, {}).get("name", item_id)

    @property
    def unique_id(self) -> str:
        return f"{DOMAIN}_{self.item_id}"

    @property
    def name(self) -> str:
        # Use the stored name, fallback to id
        return self.coordinator._active_items.get(self.item_id, {}).get("name", self._name)

    @property
    def is_on(self) -> bool:
        item = self.coordinator._active_items.get(self.item_id)
        if not item:
            return False
        # Enabled flag preferred, otherwise treat 'disabled' status as off
        return item.get("enabled", item.get("status", "scheduled") != "disabled")

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
        item = self.coordinator._active_items.get(self.item_id)
        if not item:
            _LOGGER.debug("Turn on: item %s not found", self.item_id)
            return
        item["enabled"] = True
        # set back to scheduled so coordinator will schedule it
        item["status"] = "scheduled"
        self.coordinator._active_items[self.item_id] = item
        await self.coordinator.storage.async_save(self.coordinator._active_items)
        # ask coordinator to reschedule/resume
        await self.coordinator.reschedule_item(self.item_id, {}, item.get("is_alarm", False))
        # Update HA entity attributes/state
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Disable the scheduled item."""
        item = self.coordinator._active_items.get(self.item_id)
        if not item:
            _LOGGER.debug("Turn off: item %s not found", self.item_id)
            return
        item["enabled"] = False
        item["status"] = "disabled"
        self.coordinator._active_items[self.item_id] = item
        await self.coordinator.storage.async_save(self.coordinator._active_items)

        # Best-effort: cancel scheduled triggers named trigger_<item_id>
        for task in asyncio.all_tasks():
            try:
                if task.get_name() == f"trigger_{self.item_id}":
                    task.cancel()
            except Exception:
                continue

        # Update HA entity attributes/state
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Register listener so entity updates when coordinator publishes changes."""
        @callback
        def _handle_update(event=None):
            # refresh name and state
            self._name = self.coordinator._active_items.get(self.item_id, {}).get("name", self._name)
            # write state so extra_state_attributes are refreshed on the UI
            self.async_write_ha_state()

        # keep reference so we can remove later if needed
        self._remove_listener = self.hass.bus.async_listen(f"{DOMAIN}_state_changed", _handle_update)

    async def async_will_remove_from_hass(self):
        """Cleanup listeners."""
        if getattr(self, "_remove_listener", None):
            self._remove_listener()
            self._remove_listener = None
