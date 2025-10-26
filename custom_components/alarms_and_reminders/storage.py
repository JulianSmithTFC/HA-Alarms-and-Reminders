"""Storage handling for Alarms and Reminders.

Refinements inspired by nielsfaber/scheduler store:
- grouped "Alarms"/"Reminders" persistence format
- migration helper for legacy formats
- debounced async_schedule_save with asyncio.Lock protection
- listener registration so coordinator can react to storage changes
- convenience list/get/create/update/delete APIs
- existence checks and clear (useful for tests)
"""
from __future__ import annotations
from typing import Dict, Any, MutableMapping, Optional, Callable, Awaitable, List, cast
import logging
import asyncio

from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import bind_hass
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

DATA_REGISTRY = "alarms_and_reminders_storage"
STORAGE_KEY = "alarms_and_reminders.storage"
STORAGE_VERSION = 1
SAVE_DELAY = 1  # debounce seconds


Listener = Callable[[], Awaitable[None]]


class AlarmReminderStorage:
    """Simple storage for alarms & reminders (id -> item dict)."""
    
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        # flattened in-memory mapping id -> item
        self._items: MutableMapping[str, Dict[str, Any]] = {}
        # holds the cancel function returned by async_call_later
        self._save_handle: Optional[Callable[[], None]] = None
        self._lock = asyncio.Lock()
        self._listeners: List[Listener] = []

    #
    # Public API used by coordinator / switch code
    #
    async def async_load(self) -> Dict[str, Dict[str, Any]]:
        """Load items from storage into flattened in-memory mapping.

        Returns the flattened mapping item_id -> item dict.
        """
        data = await self._store.async_load()
        if not data:
            self._items = {}
            return {}

        # New grouped format: read from top-level "data" key
        if isinstance(data, dict) and "data" in data:
            grouped = data.get("data", {}) or {}
            alarms = grouped.get("Alarms", {}) or {}
            reminders = grouped.get("Reminders", {}) or {}
            merged: Dict[str, Dict[str, Any]] = {}
            merged.update(alarms)
            merged.update(reminders)
            # Keep in-memory flattened mapping for runtime operations
            self._items = dict(merged)
            _LOGGER.debug(
                "AlarmReminderStorage loaded grouped format: %d alarms + %d reminders",
                len(alarms),
                len(reminders),
            )
            return dict(self._items)

        # Legacy compatibility: try old "items" key or flat mapping
        if isinstance(data, dict) and "items" in data and isinstance(data.get("items"), dict):
            raw = data.get("items")
            self._items = dict(raw)
            _LOGGER.debug("AlarmReminderStorage loaded legacy 'items' format: %d items", len(self._items))
            return dict(self._items)

        # If store contained a flat mapping
        if isinstance(data, dict):
            self._items = dict(data)
            _LOGGER.debug("AlarmReminderStorage loaded flat dict: %d keys", len(self._items))
            return dict(self._items)

        # Unknown format -> empty
        self._items = {}
        return {}

    async def async_list_items(self) -> Dict[str, Dict[str, Any]]:
        """Return a copy of all items (flattened)."""
        async with self._lock:
            return dict(self._items)

    async def async_list_alarms(self) -> Dict[str, Dict[str, Any]]:
        """Return only alarm items."""
        async with self._lock:
            return {k: dict(v) for k, v in self._items.items() if v.get("is_alarm")}

    async def async_list_reminders(self) -> Dict[str, Dict[str, Any]]:
        """Return only reminder items."""
        async with self._lock:
            return {k: dict(v) for k, v in self._items.items() if not v.get("is_alarm")}

    async def async_get(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Return a single item copy or None."""
        async with self._lock:
            v = self._items.get(item_id)
            return dict(v) if v is not None else None

    async def async_exists(self, item_id: str) -> bool:
        """Return True if item exists in storage (in-memory)."""
        async with self._lock:
            return item_id in self._items

    async def async_create(self, item_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create and persist a new item (overwrites if exists)."""
        async with self._lock:
            self._items[item_id] = dict(data)
            # schedule a debounced save (don't block callers)
            self.async_schedule_save()
            return dict(self._items[item_id])

    async def async_update(self, item_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing item and persist; returns updated item or None."""
        async with self._lock:
            if item_id not in self._items:
                return None
            self._items[item_id].update(changes)
            # schedule a debounced save
            self.async_schedule_save()
            return dict(self._items[item_id])

    async def async_delete(self, item_id: str) -> bool:
        """Delete an item and persist. Returns True if removed."""
        async with self._lock:
            if item_id in self._items:
                del self._items[item_id]
                # schedule a debounced save
                self.async_schedule_save()
                return True
            return False

    async def async_clear(self) -> None:
        """Remove all items (clears both buckets)."""
        async with self._lock:
            self._items = {}
            # schedule a debounced save
            self.async_schedule_save()

    #
    # Save / schedule save / listener management
    #
    @callback
    def async_schedule_save(self) -> None:
        """Schedule a debounced save to disk after SAVE_DELAY seconds."""
        # cancel previous scheduled save (async_call_later returns a cancel function)
        if self._save_handle:
            try:
                self._save_handle()
            except Exception:
                pass
            self._save_handle = None

        def _do_save(now):
            # create task to perform actual save
            self.hass.async_create_task(self.async_save())

        # schedule save using HA helper; it returns a cancel function we store
        self._save_handle = async_call_later(self.hass, SAVE_DELAY, _do_save)

    async def async_save(self, items: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        """Persist flattened items mapping to storage using grouped structure.

        After saving, notify registered listeners (coordinator) by awaiting each listener callback.
        """
        try:
            async with self._lock:
                if items is None:
                    items = dict(self._items)
                # Build grouped buckets and ensure datetimes serialized
                alarms: Dict[str, Dict[str, Any]] = {}
                reminders: Dict[str, Dict[str, Any]] = {}
                for item_id, data in items.items():
                    stored = dict(data)
                    sched = stored.get("scheduled_time")
                    try:
                        from datetime import datetime
                        if isinstance(sched, datetime):
                            stored["scheduled_time"] = sched.isoformat()
                    except Exception:
                        pass
                    # Determine bucket by is_alarm flag (default False -> Reminders)
                    if stored.get("is_alarm"):
                        alarms[item_id] = stored
                    else:
                        reminders[item_id] = stored

                payload = {
                    # include user-requested metadata shape
                    "version": STORAGE_VERSION,
                    "minor_version": 1,
                    "key": STORAGE_KEY,
                    "data": {"Alarms": alarms, "Reminders": reminders},
                }

                await self._store.async_save(payload)

                # keep in-memory copy flattened (ensure it matches what we saved)
                merged = {}
                merged.update(alarms)
                merged.update(reminders)
                self._items = dict(merged)

            # Notify listeners outside the lock
            if self._listeners:
                for lst in list(self._listeners):
                    try:
                        # schedule listener, don't block saving for long-running listeners
                        self.hass.async_create_task(lst())
                    except Exception:
                        _LOGGER.exception("Error scheduling storage listener")
            _LOGGER.debug("AlarmReminderStorage saved: %d alarms + %d reminders", len(alarms), len(reminders))

        except Exception as err:
            _LOGGER.exception("Error saving to storage: %s", err)

    def async_listen(self, listener: Listener) -> Callable[[], None]:
        """Register an async listener called after a successful save.

        Returns a function that removes the listener when called.
        """
        self._listeners.append(listener)

        def _remove() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _remove

    #
    # Migration helper (very small; expand if you need more complex migrations)
    #
    async def async_migrate_if_needed(self) -> None:
        """Placeholder for migrations between storage versions.

        Currently kept for future compatibility: you can implement steps
        here to upgrade older payloads to the new grouped layout.
        """
        # Example: if you detect old format keys you can transform and call async_save.
        pass


@bind_hass
async def async_get_storage(hass: HomeAssistant) -> AlarmReminderStorage:
    """Return (and initialize) AlarmReminderStorage for hass."""
    task = hass.data.get(DATA_REGISTRY)
    if task is None:

        async def _load_reg() -> AlarmReminderStorage:
            reg = AlarmReminderStorage(hass)
            # load existing items into memory
            await reg.async_load()
            return reg

        task = hass.data[DATA_REGISTRY] = hass.async_create_task(_load_reg())

    return cast(AlarmReminderStorage, await task)
