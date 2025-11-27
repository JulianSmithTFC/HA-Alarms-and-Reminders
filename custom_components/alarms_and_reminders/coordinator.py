"""Coordinator for scheduling alarms and reminders."""
import logging
import re
from typing import Dict, Any
from datetime import datetime, timedelta
import asyncio

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.util import dt as dt_util
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, DEFAULT_SNOOZE_MINUTES, DEFAULT_NAME

from .storage import AlarmReminderStorage

_LOGGER = logging.getLogger(__name__)

__all__ = ["AlarmAndReminderCoordinator"]

# Dispatcher event names (used by switch platform to add/remove/update entities)
ITEM_CREATED = f"{DOMAIN}_item_created"
ITEM_UPDATED = f"{DOMAIN}_item_updated"
ITEM_DELETED = f"{DOMAIN}_item_deleted"
DASHBOARD_UPDATED = f"{DOMAIN}_dashboard_updated"


class AlarmAndReminderCoordinator:
    """Coordinates scheduling of alarms and reminders."""
    
    def __init__(self, hass: HomeAssistant, media_handler, announcer):
        """Initialize coordinator."""
        self.hass = hass
        self.media_handler = media_handler
        self.announcer = announcer
        self._active_items: Dict[str, Dict[str, Any]] = {}
        self._stop_events: Dict[str, asyncio.Event] = {}
        self.async_add_entities = None
        self._alarm_counter = 0
        self._reminder_counter = 0
        self.storage = AlarmReminderStorage(hass)
        
        # Load existing items from states with better logging
        _LOGGER.debug("Starting to load existing items")
        try:
            for state in hass.states.async_all():
                if not state.entity_id.startswith(f"{DOMAIN}."):
                    continue

                item_id = state.entity_id.split(".")[-1]
                _LOGGER.debug(
                    "Found entity: %s with state: %s and attributes: %s",
                    state.entity_id,
                    state.state,
                    state.attributes,
                )

                # Convert the scheduled_time back to datetime if it exists
                attributes = dict(state.attributes)
                if "scheduled_time" in attributes:
                    try:
                        if isinstance(attributes["scheduled_time"], str):
                            attributes["scheduled_time"] = dt_util.parse_datetime(
                                attributes["scheduled_time"]
                            )
                    except Exception as err:
                        _LOGGER.error("Error parsing scheduled_time: %s", err)

                self._active_items[item_id] = attributes
                if state.state == "active":
                    self._stop_events[item_id] = asyncio.Event()

                # Update counters for alarm ids like alarm_1, alarm_2...
                if attributes.get("is_alarm"):
                    try:
                        counter_num = int(item_id.split("_")[-1]) if item_id.startswith("alarm_") else 0
                    except Exception:
                        counter_num = 0
                    self._alarm_counter = max(self._alarm_counter, counter_num)

                _LOGGER.debug(
                    "Loaded item: %s with attributes: %s",
                    item_id,
                    self._active_items[item_id],
                )

            _LOGGER.debug("Finished loading items. Active items: %s", self._active_items)

        except Exception as err:
            _LOGGER.error("Error loading existing items: %s", err, exc_info=True)
        
        # Ensure domain data structure exists
        if DOMAIN not in self.hass.data:
            self.hass.data[DOMAIN] = {}
        
        # Initialize entities list if not exists
        for config_entry in self.hass.config_entries.async_entries(DOMAIN):
            if config_entry.entry_id not in self.hass.data[DOMAIN]:
                self.hass.data[DOMAIN][config_entry.entry_id] = {}
            if "entities" not in self.hass.data[DOMAIN][config_entry.entry_id]:
                self.hass.data[DOMAIN][config_entry.entry_id]["entities"] = []

        # Add these new methods
        self._used_alarm_ids = set()  # Track used alarm IDs
        self._used_reminder_ids = set()  # Track used reminder IDs

        # Notification action mapping: listen once globally and dispatch by tag
        self._notification_listener = hass.bus.async_listen(
            "mobile_app_notification_action", self._on_mobile_notification_action
        )
        self._notification_tag_map: Dict[str, str] = {}  # tag -> item_id

    def _get_next_available_id(self, prefix: str) -> str:
        """Get next available ID for alarms."""
        counter = 1
        while True:
            potential_id = f"{prefix}_{counter}"
            if potential_id not in self._active_items:
                return potential_id
            counter += 1

    async def async_load_items(self) -> None:
        """Load items from storage and restore internal state (called at startup)."""
        try:
            # Flattened mapping: item_id -> item dict
            self._active_items = await self.storage.async_load()
            _LOGGER.debug("Loaded items from storage: %s", self._active_items)

            # Rebuild used id sets
            self._used_alarm_ids = {iid for iid, it in self._active_items.items() if it.get("is_alarm")}
            self._used_reminder_ids = {iid for iid, it in self._active_items.items() if not it.get("is_alarm")}

            now = dt_util.now()

            for item_id, item in list(self._active_items.items()):
                # Normalize scheduled_time if string
                if "scheduled_time" in item and isinstance(item["scheduled_time"], str):
                    item["scheduled_time"] = dt_util.parse_datetime(item["scheduled_time"])

                status = item.get("status", "scheduled")

                # Restore entity state in HA
                state_data = dict(item)
                if "scheduled_time" in state_data and isinstance(state_data["scheduled_time"], datetime):
                    state_data["scheduled_time"] = state_data["scheduled_time"].isoformat()

                # We no longer create an entity per item. Instead update a central dashboard entity.
                # Mark overall state 'active' if any active items exist, otherwise 'idle'
                # and include full items lists as attributes.
                # schedule playback/resume as before per item
                if status == "active":
                    self._stop_events[item_id] = asyncio.Event()
                    self.hass.async_create_task(self._start_playback(item_id), name=f"playback_{item_id}")

                # Schedule future triggers for scheduled items
                elif status == "scheduled" and item.get("scheduled_time"):
                    sched = item["scheduled_time"]
                    if isinstance(sched, str):
                        sched = dt_util.parse_datetime(sched)
                        item["scheduled_time"] = sched

                    if isinstance(sched, datetime):
                        if sched <= now:
                            self.hass.async_create_task(self._trigger_item(item_id))
                        else:
                            # schedule using async_track_point_in_time
                            async_track_point_in_time(self.hass, lambda now_dt, iid=item_id: self.hass.async_create_task(self._trigger_item(iid)), sched)

            # update central dashboard entity
            self._update_dashboard_state()
            # notify listeners that the dashboard has been updated
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

        except Exception as err:
            _LOGGER.error("Error loading items in coordinator: %s", err, exc_info=True)
        
    async def schedule_item(self, call: ServiceCall, is_alarm: bool, target: dict) -> None:
        """Schedule an alarm or reminder (moved from sensor)."""
        try:
            now = dt_util.now()

            # parse inputs (time/date/message/repeat etc.) - adapt to your service schema keys
            time_input = call.data.get("time")  # expected as time object or "HH:MM" or ISO
            date_input = call.data.get("date")  # optional date object
            message = call.data.get("message", "")

            # If user supplied a name use it; otherwise allocate numeric id (alarm_1, alarm_2, ...)
            supplied_name = call.data.get("name")
            if is_alarm:
                if supplied_name:
                    # sanitize to entity-safe id
                    item_name = supplied_name.replace(" ", "_")
                    display_name = supplied_name
                    # if this id already exists, fall back to numeric ID allocation
                    if item_name in self._active_items:
                        item_name = self._get_next_available_id("alarm")
                        display_name = item_name
                else:
                    item_name = self._get_next_available_id("alarm")
                    display_name = item_name
            else:
                # Reminders MUST have names as requested
                if not supplied_name:
                    raise ValueError("Reminders require a name")
                item_name = supplied_name.replace(" ", "_")
                display_name = supplied_name
                if item_name in self._active_items:
                    # Do not allow duplicate reminder names
                    raise ValueError(f"Reminder name already exists: {supplied_name}")

            repeat = call.data.get("repeat", "once")
            repeat_days = call.data.get("repeat_days", [])
            item_id = item_name

            # compute time object from input
            if isinstance(time_input, str):
                # Accept "HH:MM", "HH:MM:SS", or ISO datetime "YYYY-MM-DDTHH:MM:SS"
                time_str = time_input.split("T")[-1]
                parsed = dt_util.parse_time(time_str)
                if parsed is None:
                    _LOGGER.error("Invalid time format provided: %s", time_input)
                    raise ValueError(f"Invalid time format: {time_input}")
                time_obj = parsed
            elif isinstance(time_input, datetime):
                time_obj = time_input.time()
            else:
                # assume it's already a time object (or None -> use now)
                time_obj = time_input or now.time()

            # combine date/time and make timezone-aware
            if date_input:
                scheduled_time = datetime.combine(date_input, time_obj)
            else:
                scheduled_time = datetime.combine(now.date(), time_obj)

            # Make scheduled_time timezone-aware in Home Assistant's local timezone
            scheduled_time = dt_util.as_local(scheduled_time)

            # If the computed time is already in the past, push to next day
            if scheduled_time <= now:
                scheduled_time = scheduled_time + timedelta(days=1)

            # Build item dict
            item = {
                "scheduled_time": scheduled_time,
                "satellite": target.get("satellite"),
                "message": message,
                "is_alarm": is_alarm,
                "repeat": repeat,
                "repeat_days": repeat_days,
                "status": "scheduled",
                "name": display_name,
                "entity_id": item_id,
                "unique_id": item_id,
                "enabled": True,
                "sound_file": call.data.get("sound_file"),
                "notify_device": call.data.get("notify_device"),
            }

            # Save and put into memory
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)

            # Update central dashboard entity (single switch-like view)
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

            # notify switch platform (and any other listeners) a new item was created
            async_dispatcher_send(self.hass, ITEM_CREATED, item_id, item)

            # Schedule the trigger with async_track_point_in_time (avoids late-binding)
            async_track_point_in_time(
                self.hass,
                lambda now_dt, iid=item_id: self.hass.async_create_task(self._trigger_item(iid)),
                scheduled_time,
            )

            _LOGGER.info("Scheduled %s %s for %s", "alarm" if is_alarm else "reminder", item_id, scheduled_time)

        except Exception as err:
            _LOGGER.error("Error scheduling: %s", err, exc_info=True)
            raise

    async def _trigger_item(self, item_id: str) -> None:
        """Trigger the scheduled item."""
        if item_id not in self._active_items:
            return

        try:
            item = self._active_items[item_id]

            # Set status to active and persist
            item["status"] = "active"
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)

            # Update central dashboard entity
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

            # notify listeners item updated (became active)
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)

            # Create stop event and start playback in background task
            stop_event = asyncio.Event()
            self._stop_events[item_id] = stop_event

            # Send notification if configured (do not block playback start)
            if item.get("notify_device"):
                # map tag to item_id so global listener can route actions
                self._notification_tag_map[item_id] = item_id
                self.hass.async_create_task(self._send_notification(item_id, item))

            # Start playback non-blocking so stop_item can set stop_event
            self.hass.async_create_task(self._start_playback(item_id), name=f"playback_{item_id}")

        except Exception as err:
            _LOGGER.error("Error triggering item %s: %s", item_id, err, exc_info=True)
            item["status"] = "error"
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, self._active_items[item_id])

    async def _start_playback(self, item_id: str) -> None:
        """Start playback loops for an active item (background task)."""
        try:
            item = self._active_items.get(item_id)
            if not item:
                _LOGGER.debug("Playback start: item %s not found", item_id)
                return

            stop_event = self._stop_events.get(item_id)
            if not stop_event:
                stop_event = asyncio.Event()
                self._stop_events[item_id] = stop_event

            if item.get("satellite"):
                await self._satellite_playback_loop(item, stop_event)
            else:
                _LOGGER.debug("No playback target for %s", item_id)

            # After playback ends, update status if still present and not manually stopped earlier
            if item_id in self._active_items:
                if self._active_items[item_id].get("status") == "active":
                    self._active_items[item_id]["status"] = "stopped"
                    self._active_items[item_id]["last_stopped"] = dt_util.now().isoformat()
                    await self.storage.async_save(self._active_items)
                    # keep dashboard in sync
                    self._update_dashboard_state()
                    async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, self._active_items[item_id])
                    self.hass.states.async_set(f"{DOMAIN}.{item_id}", "stopped", self._active_items[item_id])
                    self.hass.bus.async_fire(f"{DOMAIN}_state_changed")

            # cleanup notification tag mapping and stop_event
            self._notification_tag_map.pop(item_id, None)
            self._stop_events.pop(item_id, None)

        except Exception as err:
            _LOGGER.error("Error in playback task for %s: %s", item_id, err, exc_info=True)
            if item_id in self._active_items:
                self._active_items[item_id]["status"] = "error"
                await self.storage.async_save(self._active_items)
                self.hass.states.async_set(f"{DOMAIN}.{item_id}", "error", self._active_items[item_id])
                async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, self._active_items[item_id])

    async def _send_notification(self, item_id: str, item: dict) -> None:
        """Send notification with action buttons."""
        try:
            device_id = item.get("notify_device")
            if not device_id:
                return

            # Accept either "mobile_app_xxx" or "notify.mobile_app_xxx" or just device id
            # Normalize to notify service target (service = mobile_app_xxx)
            if device_id.startswith("notify."):
                service_target = device_id.split(".", 1)[1]
            elif device_id.startswith("mobile_app_"):
                service_target = device_id
            else:
                # user provided raw id (e.g. mobile_app_sm_a528b) or 'sm_a528b' - assume mobile_app_ prefix if missing 'mobile_app_'
                service_target = device_id if device_id.startswith("mobile_app_") else f"mobile_app_{device_id}"

            message = item.get("message") or f"It's {dt_util.now().strftime('%I:%M %p')}"
            payload = {
                "message": message,
                "title": f"{item.get('name', 'Alarm & Reminder')}",
                "data": {
                    "tag": item_id,
                    "actions": [
                        {"action": "stop", "title": "Stop"},
                        {"action": "snooze", "title": "Snooze"}
                    ]
                }
            }

            _LOGGER.debug("Notify %s -> %s", service_target, payload)
            await self.hass.services.async_call("notify", service_target, payload, blocking=True)

        except Exception as err:
            _LOGGER.error("Error sending notification for item %s: %s", item_id, err, exc_info=True)

    @callback
    def _on_mobile_notification_action(self, event) -> None:
        """Global handler for mobile_app_notification_action events."""
        try:
            tag = event.data.get("tag")
            action = event.data.get("action")
            if not tag:
                return

            # Map tag to item id (we stored item_id as tag earlier)
            item_id = tag if tag in self._active_items else self._notification_tag_map.get(tag)
            if not item_id:
                _LOGGER.debug("Notification action for unknown tag: %s", tag)
                return

            _LOGGER.debug("Notification action '%s' for item %s", action, item_id)
            if action == "stop":
                self.hass.async_create_task(self.stop_item(item_id, self._active_items[item_id]["is_alarm"]))
            elif action == "snooze":
                # default snooze minutes
                self.hass.async_create_task(self.snooze_item(item_id, DEFAULT_SNOOZE_MINUTES, self._active_items[item_id]["is_alarm"]))

        except Exception as err:
            _LOGGER.error("Error handling mobile notification action: %s", err, exc_info=True)

    async def stop_item(self, item_id: str, is_alarm: bool) -> None:
        """Stop an active or scheduled item."""
        try:
            # Remove domain prefix if present
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]

            _LOGGER.debug("Stop request for %s. Current active items: %s", item_id, {k: {'name': v.get('name'), 'status': v.get('status')} for k, v in self._active_items.items()})

            # Try to find the item in active items or storage
            item = None
            if item_id in self._active_items:
                item = self._active_items[item_id]
            else:
                stored = await self.storage.async_load()
                if item_id in stored:
                    item = stored[item_id]
                    self._active_items[item_id] = item
                    _LOGGER.debug("Restored item %s from storage", item_id)

            if not item:
                _LOGGER.warning("Item %s not found in active items", item_id)
                return

            if item.get("is_alarm") != is_alarm:
                _LOGGER.warning("Attempted to stop %s with wrong service: %s", "alarm" if item.get("is_alarm") else "reminder", item_id)
                return

            # Set stop event if exists (playback loops check this)
            if item_id in self._stop_events:
                self._stop_events[item_id].set()

            # Cancel playback task if running
            for task in asyncio.all_tasks():
                if task.get_name() == f"playback_{item_id}":
                    task.cancel()
                    _LOGGER.debug("Cancelled playback task for %s", item_id)

            # Cancel scheduled trigger task if exists
            for task in asyncio.all_tasks():
                if task.get_name() == f"trigger_{item_id}":
                    task.cancel()
                    _LOGGER.debug("Cancelled scheduled trigger for %s", item_id)

            # Update item status to stopped and persist
            item["status"] = "stopped"
            item["last_stopped"] = dt_util.now().isoformat()
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)

            # Update central dashboard entity
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

            # notify listeners that item state changed (stopped)
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)

            self.hass.bus.async_fire(f"{DOMAIN}_state_changed")
            _LOGGER.info("Successfully stopped %s: %s", "alarm" if is_alarm else "reminder", item_id)

        except Exception as err:
            _LOGGER.error("Error stopping item %s: %s", item_id, err, exc_info=True)

    async def snooze_item(self, item_id: str, minutes: int, is_alarm: bool) -> None:
        """Snooze an active item by stopping and rescheduling it."""
        try:
            # Remove domain prefix if present
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]
                
            _LOGGER.debug("Attempting to snooze item %s for %d minutes", item_id, minutes)
                
            if item_id not in self._active_items:
                _LOGGER.warning("Item %s not found in active items: %s", item_id, self._active_items.keys())
                return
                    
            item = self._active_items[item_id]
            
            # Verify item type matches
            if item["is_alarm"] != is_alarm:
                _LOGGER.error(
                    "Cannot snooze %s as %s",
                    "alarm" if is_alarm else "reminder",
                    "reminder" if is_alarm else "alarm"
                )
                return

            # Step 1: Stop the item using stop_item method
            await self.stop_item(item_id, is_alarm)
            
            # Wait for stop to complete and verify status
            await asyncio.sleep(1)  # Give time for stop to complete
            
            # Verify item is stopped
            if item_id in self._active_items and self._active_items[item_id]["status"] != "stopped":
                _LOGGER.error("Failed to stop item %s before snoozing", item_id)
                return

            # Step 2: Calculate new time rounded to start of next minute
            now = dt_util.now()
            new_time = now + timedelta(minutes=minutes)
            new_time = new_time.replace(second=0, microsecond=0)
            
            # Step 3: Update item data for rescheduling
            item = self._active_items[item_id]  # Get fresh item data
            item["scheduled_time"] = new_time
            item["status"] = "scheduled"
            if "last_stopped" in item:
                item["last_rescheduled_from"] = item["last_stopped"]
            item["last_stopped"] = now.isoformat()
            
            # Step 4: Save to storage
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)
            
            # Step 5: Update central dashboard
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

            # notify listeners of update
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)

            # Step 6: Schedule new trigger
            delay = (new_time - now).total_seconds()
            self.hass.loop.call_later(
                delay,
                lambda: self.hass.async_create_task(
                    self._trigger_item(item_id),
                    name=f"trigger_{item_id}"
                )
            )

            self.hass.bus.async_fire(f"{DOMAIN}_state_changed")
            _LOGGER.info(
                "Successfully snoozed %s %s for %d minutes. Will ring at %s",
                "alarm" if is_alarm else "reminder",
                item_id,
                minutes,
                new_time.strftime("%H:%M:%S")
            )

        except Exception as err:
            _LOGGER.error("Error snoozing item %s: %s", item_id, err, exc_info=True)

    async def stop_all_items(self, is_alarm: bool = None) -> None:
        """Stop all active items. If is_alarm is None, stops both alarms and reminders."""
        try:
            stopped_count = 0
            for item_id, item in list(self._active_items.items()):  # Use list to avoid modification during iteration
                if is_alarm is None or item["is_alarm"] == is_alarm:
                    if item["status"] in ["active", "scheduled"]:
                        # Stop the item
                        if item_id in self._stop_events:
                            self._stop_events[item_id].set()
                            await asyncio.sleep(0.1)
                            self._stop_events.pop(item_id)
                        
                        # Update item status
                        item["status"] = "stopped"
                        self._active_items[item_id] = item
                        
                        # Update entity state
                        self.hass.states.async_set(
                            f"{DOMAIN}.{item_id}",
                            "stopped",
                            item
                        )
                        stopped_count += 1

            if stopped_count > 0:
                # Force update of sensors
                self._update_dashboard_state()
                async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
                self.hass.bus.async_fire(f"{DOMAIN}_state_changed")
                _LOGGER.info(
                    "Successfully stopped %d %s", 
                    stopped_count,
                    "alarms" if is_alarm else "reminders" if is_alarm is not None else "items"
                )
            else:
                _LOGGER.info("No active items to stop")

        except Exception as err:
            _LOGGER.error("Error stopping all items: %s", err, exc_info=True)

    async def edit_item(self, item_id: str, changes: dict, is_alarm: bool) -> None:
        """Edit an existing alarm or reminder."""
        try:
            _LOGGER.debug("Starting edit request for %s", item_id)
            _LOGGER.debug("Changes requested: %s", changes)
            _LOGGER.debug("Current active items: %s", 
                         {k: {'name': v.get('name'), 'status': v.get('status')} 
                          for k, v in self._active_items.items()})

            # Remove domain prefix if present
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]

            # Try to find the item by ID or name
            found_id = None
            if item_id in self._active_items:
                found_id = item_id
            else:
                # Try by name
                name_to_find = item_id.replace("_", " ").lower()
                for aid, item in self._active_items.items():
                    if (item.get('name', '').lower() == name_to_find or 
                        aid.lower() == name_to_find):
                        found_id = aid
                        break

            if not found_id:
                _LOGGER.error("Item %s not found in active items: %s", 
                             item_id,
                             [f"{k} ({v.get('name', '')}, {v.get('status', '')})" 
                              for k, v in self._active_items.items()])
                return

            item = self._active_items[found_id]
            
            # Verify item type matches
            if item.get("is_alarm") != is_alarm:
                _LOGGER.error(
                    "Cannot edit %s as %s", 
                    "alarm" if is_alarm else "reminder",
                    "reminder" if is_alarm else "alarm"
                )
                return

            # Process changes
            if "time" in changes:
                time_input = changes["time"]
                if isinstance(time_input, str):
                    hour, minute = map(int, time_input.split(':'))
                    time_input = datetime.time(hour, minute)
                
                # Get current date or new date if provided
                current_date = (
                    changes["date"] if "date" in changes 
                    else item["scheduled_time"].date()
                )
                
                # Create new scheduled time
                new_time = datetime.combine(current_date, time_input)
                new_time = dt_util.as_local(new_time)
                
                # Check if new time is in the past
                if new_time < dt_util.now():
                    if "date" not in changes:  # Only adjust if date wasn't explicitly set
                        new_time = new_time + timedelta(days=1)
                
                item["scheduled_time"] = new_time

            # Update other fields if provided
            for field in ["name", "message", "satellite"]:
                if field in changes:
                    item[field] = changes[field]

            # Store updated item
            self._active_items[found_id] = item
            
            # Save to storage
            await self.storage.async_save(self._active_items)

            # Update entity state
            self.hass.states.async_set(
                f"{DOMAIN}.{found_id}",
                "scheduled",
                item
            )

            # Force update of sensors / dashboard
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

            # notify listeners that this item changed
            async_dispatcher_send(self.hass, ITEM_UPDATED, found_id, item)

            _LOGGER.info(
                "Successfully edited %s: %s", 
                "alarm" if is_alarm else "reminder",
                found_id
            )

        except Exception as err:
            _LOGGER.error("Error editing item %s: %s", item_id, err, exc_info=True)

    async def delete_item(self, item_id: str, is_alarm: bool) -> None:
        """Delete a specific item."""
        try:
            # Remove domain prefix if present
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]

            if item_id not in self._active_items:
                _LOGGER.warning("Item %s not found for deletion", item_id)
                return

            item = self._active_items[item_id]
            
            # Verify item type matches
            if item["is_alarm"] != is_alarm:
                _LOGGER.error(
                    "Cannot delete %s as %s", 
                    "alarm" if is_alarm else "reminder",
                    "reminder" if is_alarm else "alarm"
                )
                return

            # Stop if active
            if item_id in self._stop_events:
                self._stop_events[item_id].set()
                await asyncio.sleep(0.1)
                self._stop_events.pop(item_id)

            # Remove from storage and active items
            await self.storage.async_delete(item_id)
            self._active_items.pop(item_id)

            # Remove entity (if entity exists)
            self.hass.states.async_remove(f"{DOMAIN}.{item_id}")

            # notify listeners to remove entity objects too
            async_dispatcher_send(self.hass, ITEM_DELETED, item_id)

            # Force update of sensors / dashboard
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

            self.hass.bus.async_fire(f"{DOMAIN}_state_changed")

            _LOGGER.info(
                "Successfully deleted %s: %s",
                "alarm" if is_alarm else "reminder",
                item_id
            )

        except Exception as err:
            _LOGGER.error("Error deleting item %s: %s", item_id, err, exc_info=True)

    async def delete_all_items(self, is_alarm: bool = None) -> None:
        """Delete all items. If is_alarm is None, deletes both alarms and reminders."""
        try:
            deleted_count = 0
            for item_id in list(self._active_items.keys()):
                item = self._active_items[item_id]
                if is_alarm is None or item["is_alarm"] == is_alarm:
                    # Stop if active
                    if item_id in self._stop_events:
                        self._stop_events[item_id].set()
                        await asyncio.sleep(0.1)
                        self._stop_events.pop(item_id)

                    # Remove from storage and active items
                    await self.storage.async_delete(item_id)
                    self._active_items.pop(item_id)

                    # Remove entity
                    self.hass.states.async_remove(f"{DOMAIN}.{item_id}")

                    # notify removal
                    async_dispatcher_send(self.hass, ITEM_DELETED, item_id)
                    
                    deleted_count += 1

            if deleted_count > 0:
                # Force update of sensors
                self._update_dashboard_state()
                async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
                self.hass.bus.async_fire(f"{DOMAIN}_state_changed")
                _LOGGER.info(
                    "Successfully deleted %d %s",
                    deleted_count,
                    "alarms" if is_alarm else "reminders" if is_alarm is not None else "items"
                )
            else:
                _LOGGER.info("No items to delete")

        except Exception as err:
            _LOGGER.error("Error deleting all items: %s", err, exc_info=True)

    async def reschedule_item(self, item_id: str, changes: dict, is_alarm: bool) -> None:
        """Reschedule a stopped or completed item."""
        try:
            # Remove domain prefix if present
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]
            
            _LOGGER.debug("Attempting to reschedule item %s with changes: %s", item_id, changes)
            _LOGGER.debug("Current active items: %s", self._active_items)
            
            if item_id not in self._active_items:
                # Try to find item in storage
                stored_items = await self.storage.async_load()
                if item_id in stored_items:
                    self._active_items[item_id] = stored_items[item_id]
                    _LOGGER.debug("Restored item %s from storage", item_id)
                else:
                    _LOGGER.error("Item %s not found in storage or active items", item_id)
                    return
                
            item = self._active_items[item_id]
            
            # Verify item type matches
            if item["is_alarm"] != is_alarm:
                _LOGGER.error(
                    "Cannot reschedule %s as %s",
                    "alarm" if is_alarm else "reminder",
                    "reminder" if is_alarm else "alarm"
                )
                return

            # Calculate new scheduled time
            now = dt_util.now()
            if "time" in changes or "date" in changes:
                time_input = changes.get("time", item["scheduled_time"].time())
                date_input = changes.get("date", now.date())
                new_time = datetime.combine(date_input, time_input)
                new_time = dt_util.as_local(new_time)
                
                # Validate future time
                if new_time < now:
                    if "date" not in changes:  # Only adjust if date wasn't explicitly set
                        new_time = new_time + timedelta(days=1)
                
                item["scheduled_time"] = new_time

            # Update other fields if provided
            for field in ["message", "satellite"]:
                if field in changes:
                    item[field] = changes[field]

            # Update status
            item["status"] = "scheduled"
            if "last_stopped" in item:
                item["last_rescheduled_from"] = item["last_stopped"]
            
            # Create stop event if needed
            if item_id not in self._stop_events:
                self._stop_events[item_id] = asyncio.Event()
            
            # Save changes
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)
            
            # Update entity state
            state_data = dict(item)
            state_data["scheduled_time"] = item["scheduled_time"].isoformat()
            self.hass.states.async_set(
                f"{DOMAIN}.{item_id}",
                "scheduled",
                state_data
            )

            # notify listeners
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

            # Schedule new trigger with task name
            delay = (item["scheduled_time"] - now).total_seconds()
            self.hass.loop.call_later(
                delay,
                lambda: self.hass.async_create_task(
                    self._trigger_item(item_id),
                    name=f"trigger_{item_id}"
                )
            )

            _LOGGER.info(
                "Successfully rescheduled %s %s for %s",
                "alarm" if is_alarm else "reminder",
                item_id,
                item["scheduled_time"].strftime("%Y-%m-%d %H:%M:%S")
            )

        except Exception as err:
            _LOGGER.error("Error rescheduling item %s: %s", item_id, err, exc_info=True)

    async def _satellite_playback_loop(self, item: dict, stop_event: asyncio.Event) -> None:
        """Playback loop for announce-on-satellite targets."""
        try:
            # announcer.announce_on_satellite handles its own loop and stop_event
            satellite = item.get("satellite")
            if not satellite:
                _LOGGER.debug("No satellite configured for item")
                return

            await self.announcer.announce_on_satellite(
                satellite=satellite,
                message=item.get("message", ""),
                sound_file=item.get("sound_file"),
                stop_event=stop_event,
                name=item.get("name"),
                is_alarm=item.get("is_alarm", False)
            )

        except Exception as err:
            _LOGGER.error("Satellite playback error for %s: %s", item.get("entity_id", "<unknown>"), err, exc_info=True)
            # mark item as error so storage doesn't crash and persist
            try:
                item_id = item.get("entity_id") or item.get("unique_id")
                if item_id:
                    item["status"] = "error"
                    self._active_items[item_id] = item
                    await self.storage.async_save(self._active_items)
                    self.hass.states.async_set(f"{DOMAIN}.{item_id}", "error", item)
                    async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)
            except Exception:
                _LOGGER.exception("Failed to persist error state")

    def _update_dashboard_state(self) -> None:
        """Update a single dashboard entity with full lists of alarms and reminders.

        This creates/updates entity alarms_and_reminders.items with attributes:
         - alarms: mapping id -> attributes
         - reminders: mapping id -> attributes
         - counts and overall state ('active' if any active items else 'idle')
        """
        try:
            alarms = {}
            reminders = {}
            overall_state = "idle"
            for iid, item in self._active_items.items():
                summary = {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "scheduled_time": item.get("scheduled_time").isoformat() if isinstance(item.get("scheduled_time"), datetime) else item.get("scheduled_time"),
                    "message": item.get("message"),
                    "is_alarm": bool(item.get("is_alarm")),
                    "sound_file": item.get("sound_file"),
                }
                if item.get("status") == "active":
                    overall_state = "active"
                if item.get("is_alarm"):
                    alarms[iid] = summary
                else:
                    reminders[iid] = summary

            attrs = {
                "alarms": alarms,
                "reminders": reminders,
                "alarm_count": len(alarms),
                "reminder_count": len(reminders),
                "last_updated": dt_util.now().isoformat(),
            }
            # single switch-like entity
            self.hass.states.async_set(f"{DOMAIN}.items", overall_state, attrs)
            # also notify listeners that dashboard changed
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
        except Exception as err:
            _LOGGER.error("Failed to update dashboard state: %s", err, exc_info=True)