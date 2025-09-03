# Expose each alarm/reminder as a Switch so user can enable/disable items
from __future__ import annotations
from typing import List
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .coordinator import AlarmAndReminderCoordinator

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator: AlarmAndReminderCoordinator = hass.data[DOMAIN]["coordinator"]
    entities: List[AlarmItemSwitch] = []

    # Build initial switches from currently loaded items
    for item_id, item in coordinator._active_items.items():
        entities.append(AlarmItemSwitch(coordinator, item_id))

    async_add_entities(entities, True)

    # Listen for state changes / new items and add/remove entities accordingly
    @callback
    def _on_state_change(event):
        # minimal: add entity if missing
        eid = event.data.get("entity_id")
        if not eid or not eid.startswith(f"{DOMAIN}."):
            return
        item_id = eid.split(".")[-1]
        # if entity created and not represented yet, add a switch
        if item_id not in {e.item_id for e in entities} and item_id in coordinator._active_items:
            ent = AlarmItemSwitch(coordinator, item_id)
            entities.append(ent)
            hass.async_create_task(async_add_entities([ent], True))

    hass.bus.async_listen(f"{DOMAIN}_state_changed", _on_state_change)


class AlarmItemSwitch(SwitchEntity):
    def __init__(self, coordinator: AlarmAndReminderCoordinator, item_id: str):
        self.coordinator = coordinator
        self.item_id = item_id
        self._attr_name = f"{coordinator._active_items.get(item_id, {}).get('name', item_id)}"
        self._available = True

    @property
    def unique_id(self) -> str:
        return f"alarms_and_reminders_{self.item_id}"

    @property
    def is_on(self) -> bool:
        item = self.coordinator._active_items.get(self.item_id)
        if not item:
            return False
        # treat 'disabled' status or explicit enabled flag
        return item.get("enabled", item.get("status", "scheduled") != "disabled")

    async def async_turn_on(self, **kwargs):
        """Enable this scheduled item."""
        item = self.coordinator._active_items.get(self.item_id)
        if not item:
            return
        item["enabled"] = True
        item["status"] = "scheduled"
        # persist and (re)schedule
        self.coordinator._active_items[self.item_id] = item
        await self.coordinator.storage.async_save(self.coordinator._active_items)
        # ask coordinator to (re)schedule immediately
        await self.coordinator.reschedule_item(self.item_id, {}, item.get("is_alarm", False))

    async def async_turn_off(self, **kwargs):
        """Disable this scheduled item (prevent trigger)."""
        item = self.coordinator._active_items.get(self.item_id)
        if not item:
            return
        item["enabled"] = False
        item["status"] = "disabled"
        self.coordinator._active_items[self.item_id] = item
        await self.coordinator.storage.async_save(self.coordinator._active_items)
        # cancel scheduled trigger if any by removing scheduled call or letting coordinator handle on trigger
        # attempt to cancel any call_later by naming pattern (best-effort)
        for task in asyncio.all_tasks():
            if task.get_name() == f"trigger_{self.item_id}":
                task.cancel()
        # update entity state
        state_data = dict(item)
        if "scheduled_time" in state_data and isinstance(state_data["scheduled_time"], datetime):
            state_data["scheduled_time"] = state_data["scheduled_time"].isoformat()
        self.hass.states.async_set(f"{DOMAIN}.{self.item_id}", "disabled", state_data)
# ...end file...