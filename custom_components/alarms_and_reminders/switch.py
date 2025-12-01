"""Expose each alarm/reminder as a Switch and handle all item management."""
from __future__ import annotations

import logging
import asyncio
from typing import Any, Optional
from datetime import datetime, timedelta

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
    DOMAIN,
    DEFAULT_SNOOZE_MINUTES,
    DEFAULT_ALARM_SOUND,
    DEFAULT_REMINDER_SOUND,
    EVENT_ITEM_CREATED,
    EVENT_ITEM_UPDATED,
    EVENT_ITEM_DELETED,
    EVENT_DASHBOARD_UPDATED,
)

_LOGGER = logging.getLogger(__name__)

REPEAT_OPTIONS = ["once", "daily", "weekdays", "weekends", "weekly", "custom"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch platform and register services."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    storage = coordinator.storage

    entities: dict[str, AlarmReminderSwitch] = {}

    @callback
    def _on_item_created(item_id: str, item: dict) -> None:
        """Create a new switch entity for item."""
        if item_id not in entities:
            entity = AlarmReminderSwitch(coordinator, hass, item_id)
            entities[item_id] = entity
            async_add_entities([entity])
            _LOGGER.debug("Created switch entity for %s", item_id)

    # Listen for new items
    async_dispatcher_connect(hass, EVENT_ITEM_CREATED, _on_item_created)

    # Create switches for existing items
    try:
        existing_items = await storage.async_load()
        for item_id, item in (existing_items or {}).items():
            _on_item_created(item_id, item)
    except Exception as err:
        _LOGGER.error("Error loading existing items for switches: %s", err, exc_info=True)

    # Store entities reference
    hass.data[DOMAIN][entry.entry_id]["entities"] = entities

    # Register services (only once per config entry)
    # platform = EntityPlatform(hass, "switch", entry, _LOGGER)

    # Get mobile app devices
    async def _get_mobile_devices() -> list[dict]:
        """Get list of mobile_app devices."""
        device_registry = dr.async_get(hass)
        mobile_devices = []
        for device in device_registry.devices.values():
            for domain, _ in device.identifiers:
                if domain == "mobile_app":
                    mobile_devices.append({"label": device.name, "value": device.id})
        return mobile_devices

    # Service schemas
    ALARM_SCHEMA = vol.Schema({
        vol.Required("time"): cv.time,
        vol.Optional("date"): cv.date,
        vol.Optional("name"): cv.string,
        vol.Optional("message"): cv.string,
        vol.Optional("satellite"): cv.entity_id,
        vol.Optional("repeat", default="once"): vol.In(REPEAT_OPTIONS),
        vol.Optional("repeat_days"): cv.ensure_list,
        vol.Optional("sound_file", default=DEFAULT_ALARM_SOUND): cv.string,
        vol.Optional("notify_device"): cv.string,
    })

    REMINDER_SCHEMA = vol.Schema({
        vol.Required("time"): cv.time,
        vol.Required("name"): cv.string,
        vol.Optional("date"): cv.date,
        vol.Optional("message"): cv.string,
        vol.Optional("satellite"): cv.entity_id,
        vol.Optional("repeat", default="once"): vol.In(REPEAT_OPTIONS),
        vol.Optional("repeat_days"): cv.ensure_list,
        vol.Optional("sound_file", default=DEFAULT_REMINDER_SOUND): cv.string,
        vol.Optional("notify_device"): cv.string,
    })

    EDIT_SCHEMA = vol.Schema({
        vol.Required("item_id"): cv.string,
        vol.Optional("time"): cv.time,
        vol.Optional("date"): cv.date,
        vol.Optional("name"): cv.string,
        vol.Optional("message"): cv.string,
        vol.Optional("satellite"): cv.entity_id,
        vol.Optional("repeat"): vol.In(REPEAT_OPTIONS),
        vol.Optional("repeat_days"): cv.ensure_list,
        vol.Optional("sound_file"): cv.string,
        vol.Optional("notify_device"): cv.string,
    })

    DELETE_SCHEMA = vol.Schema({
        vol.Required("item_id"): cv.string,
    })

    CONTROL_SCHEMA = vol.Schema({
        vol.Required("item_id"): cv.string,
    })

    SNOOZE_SCHEMA = vol.Schema({
        vol.Required("item_id"): cv.string,
        vol.Optional("minutes", default=DEFAULT_SNOOZE_MINUTES): cv.positive_int,
    })

    # Helper function to schedule items
    async def _schedule_item(call: ServiceCall, is_alarm: bool) -> None:
        """Schedule new item (alarm or reminder)."""
        try:
            now = dt_util.now()

            # Parse inputs
            time_input = call.data.get("time")
            date_input = call.data.get("date")
            message = call.data.get("message", "")
            supplied_name = call.data.get("name")
            satellite = call.data.get("satellite")

            if not satellite:
                raise ValueError("satellite is required")

            # Handle name and ID generation
            if is_alarm:
                if supplied_name:
                    item_name = supplied_name.replace(" ", "_").lower()
                    display_name = supplied_name
                    # Check if exists
                    if await storage.async_exists(item_name):
                        # Generate numeric ID
                        i = 1
                        while await storage.async_exists(f"alarm_{i}"):
                            i += 1
                        item_name = f"alarm_{i}"
                        display_name = supplied_name
                else:
                    # Generate numeric ID
                    i = 1
                    while await storage.async_exists(f"alarm_{i}"):
                        i += 1
                    item_name = f"alarm_{i}"
                    display_name = item_name
            else:
                if not supplied_name:
                    raise ValueError("Reminders require a name")
                item_name = supplied_name.replace(" ", "_").lower()
                display_name = supplied_name
                if await storage.async_exists(item_name):
                    raise ValueError(f"Reminder name already exists: {supplied_name}")

            # Parse time
            if isinstance(time_input, str):
                time_str = time_input.split("T")[-1]
                parsed = dt_util.parse_time(time_str)
                if parsed is None:
                    raise ValueError(f"Invalid time format: {time_input}")
                time_obj = parsed
            elif isinstance(time_input, datetime):
                time_obj = time_input.time()
            else:
                time_obj = time_input or now.time()

            # Combine date and time
            if date_input:
                scheduled_time = datetime.combine(date_input, time_obj)
            else:
                scheduled_time = datetime.combine(now.date(), time_obj)

            scheduled_time = dt_util.as_local(scheduled_time)

            # Push to next day if in past
            if scheduled_time <= now:
                scheduled_time = scheduled_time + timedelta(days=1)

            # Build item
            item = {
                "scheduled_time": scheduled_time,
                "satellite": satellite,
                "message": message,
                "is_alarm": is_alarm,
                "repeat": call.data.get("repeat", "once"),
                "repeat_days": call.data.get("repeat_days", []),
                "status": "scheduled",
                "name": display_name,
                "entity_id": item_name,
                "unique_id": item_name,
                "enabled": True,
                "sound_file": call.data.get("sound_file"),
                "notify_device": call.data.get("notify_device"),
            }

            # Save and notify
            await storage.async_create(item_name, item)
            coordinator._active_items[item_name] = item
            coordinator._schedule_item(item_name, scheduled_time)

            _LOGGER.info(
                "Scheduled %s %s for %s",
                "alarm" if is_alarm else "reminder",
                item_name,
                scheduled_time,
            )

        except Exception as err:
            _LOGGER.error("Error scheduling item: %s", err, exc_info=True)

    # Service handlers
    async def _handle_set_alarm(call: ServiceCall) -> None:
        await _schedule_item(call, is_alarm=True)

    async def _handle_set_reminder(call: ServiceCall) -> None:
        await _schedule_item(call, is_alarm=False)

    async def _handle_edit(call: ServiceCall) -> None:
        """Edit an existing item."""
        try:
            item_id = call.data.get("item_id")
            item = await storage.async_get(item_id)
            if not item:
                raise ValueError(f"Item {item_id} not found")

            changes = {k: v for k, v in call.data.items() if k != "item_id" and v is not None}

            # Parse time if provided
            if "time" in changes:
                time_input = changes["time"]
                if isinstance(time_input, str):
                    time_str = time_input.split("T")[-1]
                    parsed = dt_util.parse_time(time_str)
                    if parsed is None:
                        raise ValueError(f"Invalid time format: {time_input}")
                    time_obj = parsed
                else:
                    time_obj = time_input

                date_input = changes.get("date") or item.get("scheduled_time").date()
                new_time = datetime.combine(date_input, time_obj)
                new_time = dt_util.as_local(new_time)

                now = dt_util.now()
                if new_time <= now:
                    new_time = new_time + timedelta(days=1)

                changes["scheduled_time"] = new_time

            # Parse date if provided without time
            if "date" in changes and "time" not in changes:
                date_input = changes["date"]
                time_obj = item.get("scheduled_time").time()
                new_time = datetime.combine(date_input, time_obj)
                new_time = dt_util.as_local(new_time)
                changes["scheduled_time"] = new_time

            # Update storage
            updated = await storage.async_update(item_id, changes)
            if updated:
                coordinator._active_items[item_id] = updated

                # Reschedule if time changed
                if "scheduled_time" in changes:
                    # Cancel old trigger
                    if item_id in coordinator._trigger_cancel_funcs:
                        try:
                            coordinator._trigger_cancel_funcs[item_id]()
                        except Exception:
                            pass
                    # Schedule new trigger
                    coordinator._schedule_item(item_id, changes["scheduled_time"])

            _LOGGER.info("Edited item %s", item_id)

        except Exception as err:
            _LOGGER.error("Error editing item: %s", err, exc_info=True)

    async def _handle_delete(call: ServiceCall) -> None:
        """Delete a single item."""
        try:
            item_id = call.data.get("item_id")
            item = await storage.async_get(item_id)
            if not item:
                raise ValueError(f"Item {item_id} not found")

            # Cancel trigger
            if item_id in coordinator._trigger_cancel_funcs:
                try:
                    coordinator._trigger_cancel_funcs[item_id]()
                except Exception:
                    pass
                del coordinator._trigger_cancel_funcs[item_id]

            # Stop if active
            if item_id in coordinator._stop_events:
                coordinator._stop_events[item_id].set()
                await asyncio.sleep(0.1)
                coordinator._stop_events.pop(item_id, None)

            # Stop satellite ring if it was ringing
            await coordinator.announcer.stop_satellite_ring(item_id)

            # Delete from memory and storage
            coordinator._active_items.pop(item_id, None)
            await storage.async_delete(item_id)

            _LOGGER.info("Deleted item %s", item_id)

        except Exception as err:
            _LOGGER.error("Error deleting item: %s", err, exc_info=True)

    async def _handle_delete_all(call: ServiceCall) -> None:
        """Delete all items of a specific type or all items."""
        try:
            is_alarm = call.data.get("is_alarm")  # None = all, True = alarms only, False = reminders only

            items_to_delete = []
            if is_alarm is None:
                items_to_delete = list(coordinator._active_items.keys())
            else:
                items_to_delete = [
                    item_id for item_id, item in coordinator._active_items.items()
                    if item.get("is_alarm") == is_alarm
                ]

            for item_id in items_to_delete:
                # Cancel trigger
                if item_id in coordinator._trigger_cancel_funcs:
                    try:
                        coordinator._trigger_cancel_funcs[item_id]()
                    except Exception:
                        pass
                    del coordinator._trigger_cancel_funcs[item_id]

                # Stop if active
                if item_id in coordinator._stop_events:
                    coordinator._stop_events[item_id].set()
                    await asyncio.sleep(0.1)
                    coordinator._stop_events.pop(item_id, None)

                # Stop satellite ring if it was ringing
                await coordinator.announcer.stop_satellite_ring(item_id)

                # Delete from memory and storage
                coordinator._active_items.pop(item_id, None)
                await storage.async_delete(item_id)

            _LOGGER.info("Deleted %d items", len(items_to_delete))

        except Exception as err:
            _LOGGER.error("Error deleting items: %s", err, exc_info=True)

    async def _handle_stop(call: ServiceCall) -> None:
        """Stop an active item."""
        try:
            item_id = call.data.get("item_id")
            await coordinator.stop_item(item_id)
        except Exception as err:
            _LOGGER.error("Error stopping item: %s", err, exc_info=True)

    async def _handle_snooze(call: ServiceCall) -> None:
        """Snooze an active item."""
        try:
            item_id = call.data.get("item_id")
            minutes = call.data.get("minutes", DEFAULT_SNOOZE_MINUTES)
            await coordinator.snooze_item(item_id, minutes)
        except Exception as err:
            _LOGGER.error("Error snoozing item: %s", err, exc_info=True)

    async def _handle_stop_all(call: ServiceCall) -> None:
        """Stop all active items (optionally by type)."""
        try:
            is_alarm = call.data.get("is_alarm")  # None = all, True = alarms only, False = reminders only

            items_to_stop = []
            if is_alarm is None:
                items_to_stop = list(coordinator._active_items.keys())
            else:
                items_to_stop = [
                    item_id for item_id, item in coordinator._active_items.items()
                    if item.get("is_alarm") == is_alarm
                ]

            for item_id in items_to_stop:
                item = coordinator._active_items.get(item_id)
                if item and item.get("status") == "active":
                    await coordinator.stop_item(item_id)

            _LOGGER.info("Stopped %d items", len(items_to_stop))

        except Exception as err:
            _LOGGER.error("Error stopping items: %s", err, exc_info=True)

    # Register all services
    hass.services.async_register(DOMAIN, "set_alarm", _handle_set_alarm, schema=ALARM_SCHEMA)
    hass.services.async_register(DOMAIN, "set_reminder", _handle_set_reminder, schema=REMINDER_SCHEMA)
    hass.services.async_register(DOMAIN, "edit", _handle_edit, schema=EDIT_SCHEMA)
    hass.services.async_register(DOMAIN, "delete", _handle_delete, schema=DELETE_SCHEMA)
    hass.services.async_register(DOMAIN, "delete_all", _handle_delete_all, schema=vol.Schema({
        vol.Optional("is_alarm"): cv.boolean,  # None = all, True = alarms, False = reminders
    }))
    hass.services.async_register(DOMAIN, "stop", _handle_stop, schema=CONTROL_SCHEMA)
    hass.services.async_register(DOMAIN, "stop_all", _handle_stop_all, schema=vol.Schema({
        vol.Optional("is_alarm"): cv.boolean,
    }))
    hass.services.async_register(DOMAIN, "snooze", _handle_snooze, schema=SNOOZE_SCHEMA)


class AlarmReminderSwitch(SwitchEntity):
    """Switch entity for each alarm/reminder."""

    def __init__(self, coordinator, hass: HomeAssistant, item_id: str) -> None:
        """Initialize the switch entity."""
        self.coordinator = coordinator
        self.hass = hass
        self.item_id = item_id
        self._item: dict = {}
        self._attr_unique_id = item_id
        self._attr_has_entity_name = True

    @property
    def name(self) -> str:
        """Return name."""
        return self._item.get("name", self.item_id.replace("_", " ").title())

    @property
    def is_on(self) -> bool:
        """Return True if enabled."""
        return self._item.get("enabled", True) and self._item.get("status") != "deleted"

    @property
    def icon(self) -> str:
        """Return icon."""
        return "mdi:alarm" if self._item.get("is_alarm") else "mdi:bell-ring"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, "alarms_and_reminders")},
            name="Alarms & Reminders",
            manufacturer="@omaramin-2000",
            model="alarms_and_reminders",
            entry_type="service",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        next_trigger = None
        sched = self._item.get("scheduled_time")
        if sched and isinstance(sched, datetime):
            next_trigger = sched.isoformat()

        return {
            "next_trigger": next_trigger,
            "message": self._item.get("message"),
            "repeat": self._item.get("repeat"),
            "repeat_days": self._item.get("repeat_days"),
            "is_alarm": self._item.get("is_alarm"),
            "sound_file": self._item.get("sound_file"),
            "satellite": self._item.get("satellite"),
            "status": self._item.get("status", "scheduled"),
        }

    async def async_added_to_hass(self) -> None:
        """Entity added to hass."""
        # Load item data
        item = await self.coordinator.storage.async_get(self.item_id)
        if item:
            self._item = item

        # Listen for updates
        async_dispatcher_connect(
            self.hass,
            EVENT_ITEM_UPDATED,
            self._on_item_updated,
        )

    @callback
    def _on_item_updated(self, item_id: str, item: dict) -> None:
        """Update when item changes."""
        if item_id == self.item_id:
            self._item = item
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        """Turn on (enable) the item."""
        if not self._item.get("enabled"):
            await self.coordinator.storage.async_update(
                self.item_id,
                {"enabled": True, "status": "scheduled"},
            )
            self.coordinator._active_items[self.item_id]["enabled"] = True
            # Reschedule
            sched_time = self.coordinator._active_items[self.item_id].get("scheduled_time")
            if sched_time and isinstance(sched_time, datetime):
                self.coordinator._schedule_item(self.item_id, sched_time)

    async def async_turn_off(self) -> None:
        """Turn off (disable) the item."""
        if self._item.get("enabled"):
            await self.coordinator.storage.async_update(
                self.item_id,
                {"enabled": False},
            )
            self.coordinator._active_items[self.item_id]["enabled"] = False
            # Cancel trigger
            if self.item_id in self.coordinator._trigger_cancel_funcs:
                try:
                    self.coordinator._trigger_cancel_funcs[self.item_id]()
                except Exception:
                    pass
