"""Intent handling for Alarms and Reminders."""
import logging
from datetime import datetime, time as time_type
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

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

_LOGGER = logging.getLogger(__name__)

async def async_setup_intents(hass: HomeAssistant) -> None:
    """Set up the Alarms and Reminders intents."""
    if hass.data.get(f"{DOMAIN}_intents_registered"):
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
        vol.Required("time"): str,
        vol.Optional("date"): str,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        time_str = slots["time"]["value"]
        date_str = slots.get("date", {}).get("value", "")
        
        # Extract satellite_id from intent context (from trigger)
        satellite_id = intent_obj.context.satellite_id if hasattr(intent_obj.context, "satellite_id") else None

        _LOGGER.info(f"Received SetAlarm intent: time='{time_str}', date='{date_str}', satellite_id={satellite_id}")

        try:
            # Parse time string (e.g., "2:22 pm") to time object
            time_obj = self._parse_time(time_str)
            
            # Parse date string (e.g., "today", "tomorrow", "Monday") to date object
            date_obj = self._parse_date(date_str) if date_str else datetime.now().date()
            
            _LOGGER.info(f"Successfully parsed alarm: date={date_obj}, time={time_obj}")
        except ValueError as e:
            _LOGGER.error(f"Failed to parse alarm time/date: {e}")
            response = intent_obj.create_response()
            response.async_set_speech(f"Sorry, I couldn't understand the time '{time_str}'")
            return response

        # Build service call data, only including satellite if it's not null
        service_data = {
            "time": time_obj,
            "date": date_obj,
        }
        if satellite_id:
            service_data["satellite"] = satellite_id

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ALARM,
            service_data,
        )

        response = intent_obj.create_response()
        response.async_set_speech(f"Alarm set for {time_obj.strftime('%I:%M %p')}")
        return response

    @staticmethod
    def _parse_time(time_str: str) -> time_type:
        """Parse time string like '2:22 pm' to time object."""
        time_str = time_str.lower().strip()
        
        # Handle format: "2:22 pm" or "2:22"
        if ' ' in time_str:
            parts = time_str.split()
            time_part = parts[0]
            period = parts[1] if len(parts) > 1 else ""
        else:
            time_part = time_str
            period = ""
        
        # Parse hour and minute
        if ':' in time_part:
            hour, minute = map(int, time_part.split(':'))
        else:
            hour = int(time_part)
            minute = 0
        
        # Convert to 24-hour format
        if period == 'pm' and hour != 12:
            hour += 12
        elif period == 'am' and hour == 12:
            hour = 0
        
        return time_type(hour, minute, 0)

    @staticmethod
    def _parse_date(date_str: str) -> 'datetime.date':
        """Parse date string like 'today', 'tomorrow', or 'Monday' to date object."""
        from datetime import timedelta
        
        date_str = date_str.lower().strip()
        today = datetime.now().date()
        
        if date_str == "today":
            return today
        elif date_str == "tomorrow":
            return today + timedelta(days=1)
        elif date_str == "after tomorrow":
            return today + timedelta(days=2)
        
        # Handle weekday names
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6
        }
        
        if date_str in weekdays:
            target_weekday = weekdays[date_str]
            current_weekday = today.weekday()
            days_ahead = target_weekday - current_weekday
            
            if days_ahead <= 0:  # Target day already happened this week
                days_ahead += 7
            
            return today + timedelta(days=days_ahead)
        
        # If parsing fails, default to today
        _LOGGER.warning(f"Could not parse date '{date_str}', defaulting to today")
        return today

class SetReminderIntentHandler(intent.IntentHandler):
    """Handle SetReminder intents."""

    intent_type = "SetReminder"
    slot_schema = {
        vol.Required("task"): str,
        vol.Required("time"): str,
        vol.Optional("date"): str,
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)

        task = slots["task"]["value"]
        time_str = slots["time"]["value"]
        date_str = slots.get("date", {}).get("value", "")
        
        # Extract satellite_id from intent context (from trigger)
        satellite_id = intent_obj.context.satellite_id if hasattr(intent_obj.context, "satellite_id") else None

        _LOGGER.info(f"Received SetReminder intent: time='{time_str}', date='{date_str}', task='{task}', satellite_id={satellite_id}")

        try:
            # Parse time and date
            time_obj = SetAlarmIntentHandler._parse_time(time_str)
            date_obj = SetAlarmIntentHandler._parse_date(date_str) if date_str else datetime.now().date()
            
            _LOGGER.info(f"Successfully parsed reminder: date={date_obj}, time={time_obj}, task='{task}'")
        except ValueError as e:
            _LOGGER.error(f"Failed to parse reminder time/date: {e}")
            response = intent_obj.create_response()
            response.async_set_speech(f"Sorry, I couldn't understand the time '{time_str}'")
            return response

        # Build service call data, only including satellite if it's not null
        service_data = {
            "time": time_obj,
            "date": date_obj,
            "name": task,
            "message": task,
        }
        if satellite_id:
            service_data["satellite"] = satellite_id

        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_REMINDER,
            service_data,
        )

        response = intent_obj.create_response()
        response.async_set_speech(f"Reminder set for {task} at {time_obj.strftime('%I:%M %p')}")
        return response

class StopAlarmIntentHandler(intent.IntentHandler):
    """Handle StopAlarm intents."""

    intent_type = "StopAlarm"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        
        # Extract satellite_id from intent context (from trigger)
        satellite_id = intent_obj.context.satellite_id if hasattr(intent_obj.context, "satellite_id") else None
        
        _LOGGER.info(f"Received StopAlarm intent from satellite_id={satellite_id}")
        
        try:
            coordinator = next(
                (data.get("coordinator") for data in hass.data.get(DOMAIN, {}).values()
                 if isinstance(data, dict) and "coordinator" in data),
                None
            )
            
            if coordinator:
                await coordinator.stop_current_alarm(satellite_id=satellite_id)
                response = intent_obj.create_response()
                response.async_set_speech("Alarm stopped")
                return response
            else:
                _LOGGER.error("Coordinator not found")
                response = intent_obj.create_response()
                response.async_set_speech("Could not stop alarm")
                return response
        except Exception as e:
            _LOGGER.error(f"Error stopping alarm: {e}")
            response = intent_obj.create_response()
            response.async_set_speech("An error occurred while stopping the alarm")
            return response

class StopReminderIntentHandler(intent.IntentHandler):
    """Handle StopReminder intents."""

    intent_type = "StopReminder"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        
        # Extract satellite_id from intent context (from trigger)
        satellite_id = intent_obj.context.satellite_id if hasattr(intent_obj.context, "satellite_id") else None
        
        _LOGGER.info(f"Received StopReminder intent from satellite_id={satellite_id}")
        
        try:
            coordinator = next(
                (data.get("coordinator") for data in hass.data.get(DOMAIN, {}).values()
                 if isinstance(data, dict) and "coordinator" in data),
                None
            )
            
            if coordinator:
                await coordinator.stop_current_reminder(satellite_id=satellite_id)
                response = intent_obj.create_response()
                response.async_set_speech("Reminder stopped")
                return response
            else:
                _LOGGER.error("Coordinator not found")
                response = intent_obj.create_response()
                response.async_set_speech("Could not stop reminder")
                return response
        except Exception as e:
            _LOGGER.error(f"Error stopping reminder: {e}")
            response = intent_obj.create_response()
            response.async_set_speech("An error occurred while stopping the reminder")
            return response

class SnoozeAlarmIntentHandler(intent.IntentHandler):
    """Handle SnoozeAlarm intents."""

    intent_type = "SnoozeAlarm"
    slot_schema = {
        vol.Optional("minutes_to_snooze"): vol.Coerce(int),
    }

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        
        minutes = slots.get("minutes_to_snooze", {}).get("value", DEFAULT_SNOOZE_MINUTES)
        
        # Extract satellite_id from intent context (from trigger)
        satellite_id = intent_obj.context.satellite_id if hasattr(intent_obj.context, "satellite_id") else None
        
        _LOGGER.info(f"Received SnoozeAlarm intent: minutes={minutes}, satellite_id={satellite_id}")
        
        try:
            coordinator = next(
                (data.get("coordinator") for data in hass.data.get(DOMAIN, {}).values()
                 if isinstance(data, dict) and "coordinator" in data),
                None
            )
            
            if coordinator:
                await coordinator.snooze_current_alarm(minutes, satellite_id=satellite_id)
                response = intent_obj.create_response()
                response.async_set_speech(f"Alarm snoozed for {minutes} minutes")
                return response
            else:
                _LOGGER.error("Coordinator not found")
                response = intent_obj.create_response()
                response.async_set_speech("Could not snooze alarm")
                return response
        except Exception as e:
            _LOGGER.error(f"Error snoozing alarm: {e}")
            response = intent_obj.create_response()
            response.async_set_speech("An error occurred while snoozing the alarm")
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
        
        # Extract satellite_id from intent context (from trigger)
        satellite_id = intent_obj.context.satellite_id if hasattr(intent_obj.context, "satellite_id") else None
        
        _LOGGER.info(f"Received SnoozeReminder intent: minutes={minutes}, satellite_id={satellite_id}")
        
        try:
            coordinator = next(
                (data.get("coordinator") for data in hass.data.get(DOMAIN, {}).values()
                 if isinstance(data, dict) and "coordinator" in data),
                None
            )
            
            if coordinator:
                await coordinator.snooze_current_reminder(minutes, satellite_id=satellite_id)
                response = intent_obj.create_response()
                response.async_set_speech(f"Reminder snoozed for {minutes} minutes")
                return response
            else:
                _LOGGER.error("Coordinator not found")
                response = intent_obj.create_response()
                response.async_set_speech("Could not snooze reminder")
                return response
        except Exception as e:
            _LOGGER.error(f"Error snoozing reminder: {e}")
            response = intent_obj.create_response()
            response.async_set_speech("An error occurred while snoozing the reminder")
            return response
