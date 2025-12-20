"""Intent handling for Alarms and Reminders."""
import logging
from datetime import datetime
from typing import List
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.translation import async_get_translations

from .const import (
    DOMAIN,
    SERVICE_SET_ALARM,
    SERVICE_SET_REMINDER,
    SERVICE_STOP_ALARM,
    SERVICE_STOP_REMINDER,
    SERVICE_SNOOZE_ALARM,
    SERVICE_SNOOZE_REMINDER,
    DEFAULT_SNOOZE_MINUTES,
)
from .datetime_parser import parse_datetime_string

_LOGGER = logging.getLogger(__name__)

async def async_setup_intents(hass: HomeAssistant) -> None:
    """Set up the Alarms and Reminders intents."""
    if hasattr(hass.data, f"{DOMAIN}_intents_registered"):
        _LOGGER.debug("Intents already registered, skipping setup")
        return

    intent.async_register(hass, SetAlarmIntentHandler())
    intent.async_register(hass, SetReminderIntentHandler())
    intent.async_register(hass, StopAlarmIntentHandler())
    intent.async_register(hass, StopReminderIntentHandler())
    intent.async_register(hass, SnoozeAlarmIntentHandler())
    intent.async_register(hass, SnoozeReminderIntentHandler())

    # Mark intents as registered
    hass.data[f"{DOMAIN}_intents_registered"] = True

class SetAlarmIntentHandler(intent.IntentHandler):
    """Handle SetAlarm intents."""

    intent_type = "SetAlarm"
    slot_schema = {
        vol.Required("datetime"): str,
        vol.Optional("message"): str,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        datetime_str = slots["datetime"]["value"]
        message = slots.get("message", {}).get("value", "")
        satellite = intent_obj.context.id  # Get the satellite that received the command

        _LOGGER.info(f"Received SetAlarm intent: datetime='{datetime_str}', satellite={satellite}, slots={slots}")

        # Parse the datetime string
        try:
            parsed = parse_datetime_string(datetime_str)
            time_obj = parsed["time"]
            date_obj = parsed["date"]
            _LOGGER.info(f"Successfully parsed alarm: date={date_obj}, time={time_obj}")
        except ValueError as e:
            _LOGGER.error(f"Failed to parse datetime '{datetime_str}': {e}")
            response = intent_obj.create_response()
            response.async_set_speech(f"Sorry, I couldn't understand the time '{datetime_str}'")
            return response

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ALARM,
            {
                "time": time_obj,
                "date": date_obj,
                "satellite": satellite,
                "message": message
            },
        )

        response = intent_obj.create_response()
        response.async_set_speech(f"Alarm set for {time_obj.strftime('%I:%M %p')} on {date_obj.strftime('%A, %B %d')}")
        return response

class SetReminderIntentHandler(intent.IntentHandler):
    """Handle SetReminder intents."""

    intent_type = "SetReminder"
    slot_schema = {
        vol.Required("task"): str,
        vol.Required("datetime"): str,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        task = slots["task"]["value"]
        datetime_str = slots["datetime"]["value"]
        satellite = intent_obj.context.id

        _LOGGER.debug(f"Received SetReminder intent: datetime='{datetime_str}', task='{task}', satellite={satellite}")

        # Parse the datetime string
        try:
            parsed = parse_datetime_string(datetime_str)
            time_obj = parsed["time"]
            date_obj = parsed["date"]
            _LOGGER.info(f"Successfully parsed reminder: date={date_obj}, time={time_obj}, task='{task}'")
        except ValueError as e:
            _LOGGER.error(f"Failed to parse datetime '{datetime_str}': {e}")
            response = intent_obj.create_response()
            response.async_set_speech(f"Sorry, I couldn't understand the time '{datetime_str}'")
            return response

        # Combine date and time into datetime string for the service call
        from datetime import datetime
        combined_datetime = datetime.combine(date_obj, time_obj)
        datetime_str_formatted = combined_datetime.strftime("%Y-%m-%d %H:%M:%S")

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_REMINDER,
            {
                "datetime": datetime_str_formatted,
                "satellite": satellite,
                "message": task
            },
        )

        response = intent_obj.create_response()
        response.async_set_speech(f"Reminder set for {time_obj.strftime('%I:%M %p')} on {date_obj.strftime('%A, %B %d')}: {task}")
        return response

class StopAlarmIntentHandler(intent.IntentHandler):
    """Handle StopAlarm intents."""

    intent_type = "StopAlarm"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        satellite = intent_obj.context.id
        
        # Get coordinator from data
        coordinator = next(iter(hass.data[DOMAIN].values()))
        await coordinator.stop_current_alarm()

        response = intent_obj.create_response()
        response.async_set_speech("Alarm stopped")
        return response

class StopReminderIntentHandler(intent.IntentHandler):
    """Handle StopReminder intents."""

    intent_type = "StopReminder"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        satellite = intent_obj.context.id
        
        coordinator = next(iter(hass.data[DOMAIN].values()))
        await coordinator.stop_current_reminder()

        response = intent_obj.create_response()
        response.async_set_speech("Reminder stopped")
        return response

class SnoozeAlarmIntentHandler(intent.IntentHandler):
    """Handle SnoozeAlarm intents."""

    intent_type = "SnoozeAlarm"
    slot_schema = {
        vol.Optional("minutes"): vol.Coerce(int),
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        
        minutes = slots.get("minutes", {}).get("value", DEFAULT_SNOOZE_MINUTES)
        satellite = intent_obj.context.id
        
        coordinator = next(iter(hass.data[DOMAIN].values()))
        await coordinator.snooze_current_alarm(minutes)

        response = intent_obj.create_response()
        response.async_set_speech(f"Alarm snoozed for {minutes} minutes")
        return response

class SnoozeReminderIntentHandler(intent.IntentHandler):
    """Handle SnoozeReminder intents."""

    intent_type = "SnoozeReminder"
    slot_schema = {
        vol.Optional("minutes"): vol.Coerce(int),
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        
        minutes = slots.get("minutes", {}).get("value", DEFAULT_SNOOZE_MINUTES)
        satellite = intent_obj.context.id
        
        coordinator = next(iter(hass.data[DOMAIN].values()))
        await coordinator.snooze_current_reminder(minutes)

        response = intent_obj.create_response()
        response.async_set_speech(f"Reminder snoozed for {minutes} minutes")
        return response
