"""Coordinator for scheduling alarms and reminders."""
import logging
import asyncio
import re
from typing import Dict, Any
from datetime import datetime, timedelta

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
        self.storage = AlarmReminderStorage(hass)
        
        # Load existing items from states
        _LOGGER.debug("Initializing coordinator")
        try:
            for state in hass.states.async_all():
                if not state.entity_id.startswith(f"{DOMAIN}."):
                    continue

                item_id = state.entity_id.split(".")[-1]
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

                _LOGGER.debug("Loaded item: %s", item_id)

        except Exception as err:
            _LOGGER.error("Error loading existing items: %s", err, exc_info=True)
        
        # Ensure domain data structure exists
        if DOMAIN not in self.hass.data:
            self.hass.data[DOMAIN] = {}
        
        # Notification action mapping
        self._notification_listener = hass.bus.async_listen(
            "mobile_app_notification_action", self._on_mobile_notification_action
        )
        self._notification_tag_map: Dict[str, str] = {}

    def _get_next_available_id(self, prefix: str) -> str:
        """Get next available ID for alarms."""
        counter = 1
        while True:
            potential_id = f"{prefix}_{counter}"
            if potential_id not in self._active_items:
                return potential_id
            counter += 1

    async def async_load_items(self) -> None:
        """Load items from storage and restore internal state."""
        try:
            self._active_items = await self.storage.async_load()
            _LOGGER.debug("Loaded items from storage: %d items", len(self._active_items))

            now = dt_util.now()

            for item_id, item in list(self._active_items.items()):
                # Normalize scheduled_time if string
                if "scheduled_time" in item and isinstance(item["scheduled_time"], str):
                    item["scheduled_time"] = dt_util.parse_datetime(item["scheduled_time"])

                status = item.get("status", "scheduled")

                if status == "active":
                    self._stop_events[item_id] = asyncio.Event()
                    self.hass.async_create_task(
                        self._start_playback(item_id),
                        name=f"playback_{item_id}"
                    )
                elif status == "scheduled" and item.get("scheduled_time"):
                    sched = item["scheduled_time"]
                    if isinstance(sched, str):
                        sched = dt_util.parse_datetime(sched)
                        item["scheduled_time"] = sched

                    if isinstance(sched, datetime) and sched > now:
                        async_track_point_in_time(
                            self.hass,
                            lambda now_dt, iid=item_id: self.hass.async_create_task(
                                self._trigger_item(iid)
                            ),
                            sched
                        )

            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)

        except Exception as err:
            _LOGGER.error("Error loading items: %s", err, exc_info=True)
        
    async def schedule_item(self, call: ServiceCall, is_alarm: bool, target: dict) -> None:
        """Schedule an alarm or reminder."""
        try:
            now = dt_util.now()

            time_input = call.data.get("time")
            date_input = call.data.get("date")
            message = call.data.get("message", "")
            supplied_name = call.data.get("name")

            # Determine item ID and display name
            if is_alarm:
                if supplied_name:
                    item_name = supplied_name.replace(" ", "_")
                    display_name = supplied_name
                    if item_name in self._active_items:
                        item_name = self._get_next_available_id("alarm")
                        display_name = item_name
                else:
                    item_name = self._get_next_available_id("alarm")
                    display_name = item_name
            else:
                if not supplied_name:
                    raise ValueError("Reminders require a name")
                item_name = supplied_name.replace(" ", "_")
                display_name = supplied_name
                if item_name in self._active_items:
                    raise ValueError(f"Reminder already exists: {supplied_name}")

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

            # If time is in past, push to next day
            if scheduled_time <= now:
                scheduled_time = scheduled_time + timedelta(days=1)

            # Build item
            item = {
                "scheduled_time": scheduled_time,
                "satellite": target.get("satellite"),
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

            self._active_items[item_name] = item
            await self.storage.async_save(self._active_items)

            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
            async_dispatcher_send(self.hass, ITEM_CREATED, item_name, item)

            # Schedule trigger
            async_track_point_in_time(
                self.hass,
                lambda now_dt, iid=item_name: self.hass.async_create_task(
                    self._trigger_item(iid)
                ),
                scheduled_time,
            )

            _LOGGER.info(
                "Scheduled %s %s for %s",
                "alarm" if is_alarm else "reminder",
                item_name,
                scheduled_time
            )

        except Exception as err:
            _LOGGER.error("Error scheduling: %s", err, exc_info=True)
            raise

    async def _trigger_item(self, item_id: str) -> None:
        """Trigger the scheduled item."""
        if item_id not in self._active_items:
            return

        try:
            item = self._active_items[item_id]

            item["status"] = "active"
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)

            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)

            stop_event = asyncio.Event()
            self._stop_events[item_id] = stop_event

            # Send notification if configured
            if item.get("notify_device"):
                self._notification_tag_map[item_id] = item_id
                self.hass.async_create_task(self._send_notification(item_id, item))

            # Start playback
            self.hass.async_create_task(
                self._start_playback(item_id),
                name=f"playback_{item_id}"
            )

        except Exception as err:
            _LOGGER.error("Error triggering item %s: %s", item_id, err, exc_info=True)
            item["status"] = "error"
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, self._active_items[item_id])

    async def _start_playback(self, item_id: str) -> None:
        """Start playback for active item."""
        try:
            item = self._active_items.get(item_id)
            if not item:
                _LOGGER.debug("Item %s not found", item_id)
                return

            stop_event = self._stop_events.get(item_id)
            if not stop_event:
                stop_event = asyncio.Event()
                self._stop_events[item_id] = stop_event

            if item.get("satellite"):
                await self._satellite_playback_loop(item, stop_event)
            else:
                _LOGGER.debug("No satellite configured for item %s", item_id)

            # Update status when playback ends
            if item_id in self._active_items:
                if self._active_items[item_id].get("status") == "active":
                    self._active_items[item_id]["status"] = "stopped"
                    self._active_items[item_id]["last_stopped"] = dt_util.now().isoformat()
                    await self.storage.async_save(self._active_items)
                    # keep dashboard in sync
                    self._update_dashboard_state()
                    async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, self._active_items[item_id])

            self._notification_tag_map.pop(item_id, None)
            self._stop_events.pop(item_id, None)

        except Exception as err:
            _LOGGER.error("Error in playback for %s: %s", item_id, err, exc_info=True)
            if item_id in self._active_items:
                self._active_items[item_id]["status"] = "error"
                await self.storage.async_save(self._active_items)
                async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, self._active_items[item_id])

    async def _satellite_playback_loop(self, item: dict, stop_event: asyncio.Event) -> None:
        """Playback loop with duration tracking and state monitoring."""
        try:
            satellite = item.get("satellite")
            if not satellite:
                _LOGGER.debug("No satellite configured")
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
            _LOGGER.error("Satellite playback error: %s", err, exc_info=True)

    async def _send_notification(self, item_id: str, item: dict) -> None:
        """Send notification with action buttons."""
        try:
            device_id = item.get("notify_device")
            if not device_id:
                return

            if device_id.startswith("notify."):
                service_target = device_id.split(".", 1)[1]
            elif device_id.startswith("mobile_app_"):
                service_target = device_id
            else:
                service_target = f"mobile_app_{device_id}"

            message = item.get("message") or f"It's {dt_util.now().strftime('%I:%M %p')}"
            payload = {
                "message": message,
                "title": item.get("name", "Alarm & Reminder"),
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
            _LOGGER.error("Error sending notification: %s", err, exc_info=True)

    @callback
    def _on_mobile_notification_action(self, event) -> None:
        """Handle mobile app notification actions."""
        try:
            tag = event.data.get("tag")
            action = event.data.get("action")
            if not tag:
                return

            item_id = tag if tag in self._active_items else self._notification_tag_map.get(tag)
            if not item_id:
                return

            if action == "stop":
                self.hass.async_create_task(
                    self.stop_item(item_id, self._active_items[item_id]["is_alarm"])
                )
            elif action == "snooze":
                self.hass.async_create_task(
                    self.snooze_item(
                        item_id,
                        DEFAULT_SNOOZE_MINUTES,
                        self._active_items[item_id]["is_alarm"]
                    )
                )

        except Exception as err:
            _LOGGER.error("Error handling notification action: %s", err, exc_info=True)

    async def stop_item(self, item_id: str, is_alarm: bool) -> None:
        """Stop an active or scheduled item."""
        try:
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]

            if item_id not in self._active_items:
                _LOGGER.warning("Item %s not found", item_id)
                return

            item = self._active_items[item_id]
            
            if item.get("is_alarm") != is_alarm:
                _LOGGER.warning("Type mismatch for item %s", item_id)
                return

            # Set stop event
            if item_id in self._stop_events:
                self._stop_events[item_id].set()

            # Cancel tasks
            for task in asyncio.all_tasks():
                if task.get_name() in [f"playback_{item_id}", f"trigger_{item_id}"]:
                    task.cancel()

            # Update status
            item["status"] = "stopped"
            item["last_stopped"] = dt_util.now().isoformat()
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)

            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)

            _LOGGER.info("Stopped %s: %s", "alarm" if is_alarm else "reminder", item_id)

        except Exception as err:
            _LOGGER.error("Error stopping item: %s", err, exc_info=True)

    async def snooze_item(self, item_id: str, minutes: int, is_alarm: bool) -> None:
        """Snooze an active item."""
        try:
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]

            if item_id not in self._active_items:
                _LOGGER.warning("Item %s not found", item_id)
                return

            item = self._active_items[item_id]
            
            if item["is_alarm"] != is_alarm:
                _LOGGER.error("Type mismatch for item %s", item_id)
                return

            # Stop current playback
            await self.stop_item(item_id, is_alarm)
            await asyncio.sleep(1)

            # Calculate new time
            now = dt_util.now()
            new_time = now + timedelta(minutes=minutes)
            new_time = new_time.replace(second=0, microsecond=0)

            # Update item
            item = self._active_items[item_id]
            item["scheduled_time"] = new_time
            item["status"] = "scheduled"
            if "last_stopped" in item:
                item["last_rescheduled_from"] = item["last_stopped"]
            item["last_stopped"] = now.isoformat()
            
            # Step 4: Save to storage
            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)

            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)

            # Schedule new trigger
            delay = (new_time - now).total_seconds()
            self.hass.loop.call_later(
                delay,
                lambda: self.hass.async_create_task(
                    self._trigger_item(item_id),
                    name=f"trigger_{item_id}"
                )
            )

            _LOGGER.info(
                "Snoozed %s for %d minutes. Will ring at %s",
                item_id,
                minutes,
                new_time.strftime("%H:%M:%S")
            )

        except Exception as err:
            _LOGGER.error("Error snoozing item: %s", err, exc_info=True)

    async def stop_all_items(self, is_alarm: bool = None) -> None:
        """Stop all active items."""
        try:
            stopped_count = 0
            for item_id, item in list(self._active_items.items()):
                if is_alarm is None or item["is_alarm"] == is_alarm:
                    if item["status"] in ["active", "scheduled"]:
                        if item_id in self._stop_events:
                            self._stop_events[item_id].set()
                            await asyncio.sleep(0.1)
                            self._stop_events.pop(item_id)
                        
                        item["status"] = "stopped"
                        self._active_items[item_id] = item
                        self.hass.states.async_set(
                            f"{DOMAIN}.{item_id}",
                            "stopped",
                            item
                        )
                        stopped_count += 1

            if stopped_count > 0:
                self._update_dashboard_state()
                async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
                _LOGGER.info("Successfully stopped %d items", stopped_count)

        except Exception as err:
            _LOGGER.error("Error stopping all items: %s", err, exc_info=True)

    async def edit_item(self, item_id: str, changes: dict, is_alarm: bool) -> None:
        """Edit an existing item."""
        try:
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]

            if item_id not in self._active_items:
                _LOGGER.warning("Item %s not found", item_id)
                return

            item = self._active_items[item_id]
            
            if item.get("is_alarm") != is_alarm:
                _LOGGER.error("Type mismatch")
                return

            # Update time if provided
            if "time" in changes:
                time_input = changes["time"]
                if isinstance(time_input, str):
                    hour, minute = map(int, time_input.split(':'))
                    time_input = datetime.time(hour, minute)
                
                current_date = changes.get("date", item["scheduled_time"].date())
                new_time = datetime.combine(current_date, time_input)
                new_time = dt_util.as_local(new_time)
                
                if new_time < dt_util.now() and "date" not in changes:
                    new_time = new_time + timedelta(days=1)
                
                item["scheduled_time"] = new_time

            # Update other fields
            for field in ["name", "message", "satellite"]:
                if field in changes:
                    item[field] = changes[field]

            self._active_items[item_id] = item
            await self.storage.async_save(self._active_items)

            self.hass.states.async_set(f"{DOMAIN}.{item_id}", "scheduled", item)
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
            async_dispatcher_send(self.hass, ITEM_UPDATED, item_id, item)

            _LOGGER.info("Edited %s: %s", "alarm" if is_alarm else "reminder", item_id)

        except Exception as err:
            _LOGGER.error("Error editing item: %s", err, exc_info=True)

    async def delete_item(self, item_id: str, is_alarm: bool) -> None:
        """Delete a specific item."""
        try:
            # Remove domain prefix if present
            if item_id.startswith(f"{DOMAIN}."):
                item_id = item_id.split(".")[-1]

            if item_id not in self._active_items:
                _LOGGER.warning("Item %s not found", item_id)
                return

            item = self._active_items[item_id]
            
            # Verify item type matches
            if item["is_alarm"] != is_alarm:
                _LOGGER.error("Type mismatch")
                return

            # Stop if active
            if item_id in self._stop_events:
                self._stop_events[item_id].set()
                await asyncio.sleep(0.1)
                self._stop_events.pop(item_id)

            await self.storage.async_delete(item_id)
            self._active_items.pop(item_id)

            # Remove entity (if entity exists)
            self.hass.states.async_remove(f"{DOMAIN}.{item_id}")
            self._update_dashboard_state()
            async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
            async_dispatcher_send(self.hass, ITEM_DELETED, item_id)

            _LOGGER.info("Deleted %s: %s", "alarm" if is_alarm else "reminder", item_id)

        except Exception as err:
            _LOGGER.error("Error deleting item: %s", err, exc_info=True)

    async def delete_all_items(self, is_alarm: bool = None) -> None:
        """Delete all items."""
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

                    await self.storage.async_delete(item_id)
                    self._active_items.pop(item_id)
                    self.hass.states.async_remove(f"{DOMAIN}.{item_id}")
                    async_dispatcher_send(self.hass, ITEM_DELETED, item_id)
                    deleted_count += 1

            if deleted_count > 0:
                self._update_dashboard_state()
                async_dispatcher_send(self.hass, DASHBOARD_UPDATED)
                _LOGGER.info("Deleted %d items", deleted_count)

        except Exception as err:
            _LOGGER.error("Error deleting all items: %s", err, exc_info=True)

    def _update_dashboard_state(self) -> None:
        """Update central dashboard entity."""
        try:
            alarms = {}
            reminders = {}
            overall_state = "idle"
            
            for iid, item in self._active_items.items():
                summary = {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "scheduled_time": (
                        item.get("scheduled_time").isoformat()
                        if isinstance(item.get("scheduled_time"), datetime)
                        else item.get("scheduled_time")
                    ),
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
            
            self.hass.states.async_set(f"{DOMAIN}.items", overall_state, attrs)

        except Exception as err:
            _LOGGER.error("Failed to update dashboard state: %s", err, exc_info=True)