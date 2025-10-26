"""The Alarms and Reminders integration."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.const import ATTR_NAME
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    SERVICE_SET_ALARM,
    SERVICE_SET_REMINDER,
    SERVICE_STOP_ALARM,
    SERVICE_SNOOZE_ALARM,
    SERVICE_STOP_REMINDER,
    SERVICE_SNOOZE_REMINDER,
    SERVICE_STOP_ALL_ALARMS,
    SERVICE_STOP_ALL_REMINDERS,
    SERVICE_STOP_ALL,
    SERVICE_EDIT_ALARM,
    SERVICE_EDIT_REMINDER,
    SERVICE_DELETE_ALARM,
    SERVICE_DELETE_REMINDER,
    SERVICE_DELETE_ALL_ALARMS,
    SERVICE_DELETE_ALL_REMINDERS,
    SERVICE_DELETE_ALL,
    ATTR_DATETIME,
    ATTR_SATELLITE,
    ATTR_MESSAGE,
    ATTR_ALARM_ID,
    ATTR_REMINDER_ID,
    ATTR_SNOOZE_MINUTES,
    ATTR_MEDIA_PLAYER,
    ATTR_NOTIFY_DEVICE,
    DEFAULT_SNOOZE_MINUTES,
    CONF_MEDIA_PLAYER,
    CONF_ENABLE_LLM,
    DEFAULT_ENABLE_LLM,
)

from .coordinator import AlarmAndReminderCoordinator
from .media_player import MediaHandler
from .announcer import Announcer
from .intents import async_setup_intents
from .llm_functions import async_setup_llm_api, async_cleanup_llm_api

_LOGGER = logging.getLogger(__name__)

REPEAT_OPTIONS = [
    "once",
    "daily",
    "weekdays",
    "weekends",
    "weekly",
    "custom",
]

DEFAULT_ALARM_SOUND = "/custom_components/alarms_and_reminders/sounds/alarms/birds.mp3"
DEFAULT_REMINDER_SOUND = "/custom_components/alarms_and_reminders/sounds/reminders/ringtone.mp3"

PLATFORMS = ["switch"]


def _find_coordinator(hass: HomeAssistant) -> Optional[AlarmAndReminderCoordinator]:
    """Return a coordinator instance if available.

    Looks first for a top-level coordinator (single-entry case) then per-entry.
    """
    data = hass.data.get(DOMAIN) or {}
    # top-level coordinator
    coord = data.get("coordinator")
    if isinstance(coord, AlarmAndReminderCoordinator):
        return coord

    # fall back to first config entry's coordinator
    for entry_id, entry_data in data.items():
        if entry_id == "coordinator":
            continue
        if isinstance(entry_data, dict) and "coordinator" in entry_data:
            c = entry_data.get("coordinator")
            if isinstance(c, AlarmAndReminderCoordinator):
                return c
    return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration: register services and create base hass.data container."""
    hass.data.setdefault(DOMAIN, {})

    # We'll register the core public services once here. Service handlers locate
    # the appropriate coordinator at call time using _find_coordinator.
    async def _service_schedule_alarm(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass)
        if not coordinator:
            _LOGGER.error("Service %s called but no coordinator available", SERVICE_SET_ALARM)
            return
        # Build a normalized target dict expected by coordinator.schedule_item
        target = {
            "satellite": call.data.get("satellite"),
            "media_players": call.data.get("media_player") or call.data.get("media_players") or [],
        }
        await coordinator.schedule_item(call, is_alarm=True, target=target)

    async def _service_schedule_reminder(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass)
        if not coordinator:
            _LOGGER.error("Service %s called but no coordinator available", SERVICE_SET_REMINDER)
            return
        target = {
            "satellite": call.data.get("satellite"),
            "media_players": call.data.get("media_player") or call.data.get("media_players") or [],
        }
        await coordinator.schedule_item(call, is_alarm=False, target=target)

    async def _service_stop(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass)
        if not coordinator:
            _LOGGER.error("Stop service called but no coordinator available")
            return
        # support passing alarm/reminder id via named fields or target entity
        target = call.data.get(ATTR_ALARM_ID) or call.data.get(ATTR_REMINDER_ID)
        if not target and call.target:
            # call.target may include entity_id list
            ent = call.target.get("entity_id")
            if isinstance(ent, list):
                target = ent[0] if ent else None
            else:
                target = ent
        if not target:
            _LOGGER.error("Stop service called without target")
            return
        # determine whether it's an alarm or reminder by looking up item if possible
        if isinstance(target, str) and target.startswith(f"{DOMAIN}."):
            target_id = target.split(".")[-1]
        else:
            target_id = target
        # coordinator.stop_item handles type verification through parameter is_alarm; best-effort: try both
        await coordinator.stop_item(target_id, is_alarm=True)
        await coordinator.stop_item(target_id, is_alarm=False)

    async def _service_snooze(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass)
        if not coordinator:
            _LOGGER.error("Snooze service called but no coordinator available")
            return
        minutes = call.data.get("minutes", DEFAULT_SNOOZE_MINUTES)
        target = call.data.get(ATTR_ALARM_ID) or call.data.get(ATTR_REMINDER_ID)
        if not target and call.target:
            ent = call.target.get("entity_id")
            target = ent[0] if isinstance(ent, list) else ent
        if not target:
            _LOGGER.error("Snooze service called without target")
            return
        if isinstance(target, str) and target.startswith(f"{DOMAIN}."):
            target_id = target.split(".")[-1]
        else:
            target_id = target
        # Try both types; snooze_item will verify type match
        await coordinator.snooze_item(target_id, minutes, is_alarm=True)
        await coordinator.snooze_item(target_id, minutes, is_alarm=False)

    async def _service_delete(call: ServiceCall) -> None:
        coordinator = _find_coordinator(hass)
        if not coordinator:
            _LOGGER.error("Delete service called but no coordinator available")
            return
        target = call.data.get(ATTR_ALARM_ID) or call.data.get(ATTR_REMINDER_ID)
        if not target and call.target:
            ent = call.target.get("entity_id")
            target = ent[0] if isinstance(ent, list) else ent
        if not target:
            _LOGGER.error("Delete service called without target")
            return
        if isinstance(target, str) and target.startswith(f"{DOMAIN}."):
            target_id = target.split(".")[-1]
        else:
            target_id = target
        # Attempt delete for both types; coordinator will validate
        await coordinator.delete_item(target_id, is_alarm=True)
        await coordinator.delete_item(target_id, is_alarm=False)

    # Register a minimal set of services. The services.yaml file in the integration
    # still provides the UI metadata for the Home Assistant frontend.
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ALARM,
        _service_schedule_alarm,
        # schema is described in services.yaml (frontend uses that). We leave runtime validation to coordinator.
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_REMINDER,
        _service_schedule_reminder,
    )

    hass.services.async_register(
        DOMAIN,
        "stop",
        _service_stop,
    )

    hass.services.async_register(
        DOMAIN,
        "snooze",
        _service_snooze,
    )

    hass.services.async_register(
        DOMAIN,
        "delete",
        _service_delete,
    )

    # Core setup completed
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry: create/store coordinator and forward platforms."""
    try:
        hass.data.setdefault(DOMAIN, {})
        # Ensure per-entry container
        hass.data[DOMAIN].setdefault(entry.entry_id, {})
        entry_store: Dict = hass.data[DOMAIN][entry.entry_id]

        # Create media/announcer and coordinator
        sounds_dir = Path(__file__).parent / "sounds"
        media_handler = MediaHandler(
            hass,
            str(sounds_dir / "alarms" / "birds.mp3"),
            str(sounds_dir / "reminders" / "ringtone.mp3"),
        )
        announcer = Announcer(hass)

        coordinator = AlarmAndReminderCoordinator(hass, media_handler, announcer)

        # Attach stable id and create device so switches group under one device
        coordinator.id = "alarms_and_reminders"  # stable identifier shared across entries
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, coordinator.id)},
            name="Alarms and Reminders",
            model="Alarms and Reminders",
            sw_version="0.0.0",
            manufacturer="@omaramin-2000",
        )

        # Store coordinator for this entry
        entry_store["coordinator"] = coordinator
        entry_store.setdefault("entities", [])

        # Keep a top-level coordinator reference for single-entry setups
        # The global coordinator pointer is handy for service handlers that don't
        # supply an entry_id; overwrite only if not present to avoid stomping a
        # previously set top-level coordinator.
        if "coordinator" not in hass.data[DOMAIN]:
            hass.data[DOMAIN]["coordinator"] = coordinator

        # Allow coordinator to restore saved items
        if hasattr(coordinator, "async_load_items"):
            await coordinator.async_load_items()

        # Setup LLM API for voice assistant integration if enabled in options
        enable_llm = entry.options.get(CONF_ENABLE_LLM, DEFAULT_ENABLE_LLM)
        if enable_llm:
            try:
                await async_setup_llm_api(hass)
                _LOGGER.info("LLM API setup completed for alarms and reminders")
            except Exception as llm_err:
                _LOGGER.warning("Failed to setup LLM API (non-critical): %s", llm_err)
        else:
            _LOGGER.debug("LLM API disabled for this entry")

        # Forward platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        return True

    except Exception as err:
        _LOGGER.error("Error setting up config entry: %s", err, exc_info=True)
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Clean up LLM API (best-effort)
        try:
            await async_cleanup_llm_api(hass)
            _LOGGER.info("LLM API cleanup completed")
        except Exception as llm_err:
            _LOGGER.debug("Error cleaning up LLM API: %s", llm_err)
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update listener for config entry changes."""
    await hass.config_entries.async_reload(entry.entry_id)


# Convenience wrappers for stop-all style services can be implemented in coordinator.
async def async_stop_all_alarms(call: ServiceCall):
    try:
        coordinator = _find_coordinator(call.hass)
        if coordinator:
            await coordinator.stop_all_items(is_alarm=True)
    except Exception as err:
        _LOGGER.error("Error stopping all alarms: %s", err)


async def async_stop_all_reminders(call: ServiceCall):
    try:
        coordinator = _find_coordinator(call.hass)
        if coordinator:
            await coordinator.stop_all_items(is_alarm=False)
    except Exception as err:
        _LOGGER.error("Error stopping all reminders: %s", err)


async def async_stop_all(call: ServiceCall):
    try:
        coordinator = _find_coordinator(call.hass)
        if coordinator:
            await coordinator.stop_all_items()
    except Exception as err:
        _LOGGER.error("Error stopping all items: %s", err)