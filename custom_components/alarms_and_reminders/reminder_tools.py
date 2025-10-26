"""LLM Tools for reminder management."""
import logging
import re
from datetime import datetime, time, timedelta

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.util.json import JsonObjectType
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

try:
    from homeassistant.helpers import llm  # type: ignore
except Exception:
    llm = None  # type: ignore

if llm is None:
    class SetReminderTool:
        def __init__(self, *args, **kwargs):
            pass

    class ListRemindersTool:
        def __init__(self, *args, **kwargs):
            pass

    class DeleteReminderTool:
        def __init__(self, *args, **kwargs):
            pass

else:
    class SetReminderTool(llm.Tool):
        """Tool for setting a reminder."""

        name = "set_reminder"
        description = "Set a new reminder at a specific time with a task description. Use this when the user wants to be reminded about something."
        response_instruction = """
        Confirm to the user that the reminder has been set with the time and task.
        Keep your response concise and friendly, in plain text without formatting.
        """

        parameters = vol.Schema(
            {
                vol.Required(
                    "time",
                    description="The time for the reminder in HH:MM format (24-hour). Example: 07:30 for 7:30 AM, 14:00 for 2:00 PM",
                ): str,
                vol.Required(
                    "name",
                    description="A descriptive name for the reminder describing the task. Example: 'take medicine', 'meeting with John', 'water plants'",
                ): str,
                vol.Optional(
                    "repeat_days",
                    description="Optional list of days when reminder should repeat. Use lowercase 3-letter abbreviations: mon, tue, wed, thu, fri, sat, sun. Leave empty for one-time reminder.",
                ): [str],
                vol.Optional(
                    "message",
                    description="Optional additional message to announce when the reminder rings",
                ): str,
            }
        )

        def wrap_response(self, response: dict) -> dict:
            response["instruction"] = self.response_instruction
            return response

        def _validate_time(self, time_str: str) -> tuple[bool, str]:
            """Validate time format and return (is_valid, error_message)."""
            pattern = r"^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$"
            if not re.match(pattern, time_str):
                return False, "Time must be in HH:MM format (24-hour). Example: 07:30 or 14:00"
            return True, ""

        def _validate_repeat_days(self, days: list[str] | None) -> tuple[bool, str]:
            """Validate repeat days."""
            if not days:
                return True, ""
            valid_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            for day in days:
                if day.lower() not in valid_days:
                    return (
                        False,
                        f"Invalid day: {day}. Use: mon, tue, wed, thu, fri, sat, sun",
                    )
            return True, ""

        async def async_call(
            self,
            hass: HomeAssistant,
            tool_input: llm.ToolInput,
            llm_context: llm.LLMContext,
        ) -> JsonObjectType:
            """Call the tool to set a reminder."""
            time_str = tool_input.tool_args["time"]
            name = tool_input.tool_args["name"]
            repeat_days = tool_input.tool_args.get("repeat_days")
            message = tool_input.tool_args.get("message", "")

            _LOGGER.info("Setting reminder '%s' at %s", name, time_str)

            # Validate time
            is_valid, error_msg = self._validate_time(time_str)
            if not is_valid:
                return {"error": error_msg}

            # Validate repeat days
            is_valid, error_msg = self._validate_repeat_days(repeat_days)
            if not is_valid:
                return {"error": error_msg}

            try:
                # Parse time
                hour, minute = map(int, time_str.split(':'))
                time_obj = time(hour, minute)

                # Get coordinator
                coordinator = None
                for entry_id, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and "coordinator" in data:
                        coordinator = data["coordinator"]
                        break

                if not coordinator:
                    return {"error": "Reminder system coordinator not found"}

                # Determine satellite from LLM context if available
                satellite = None
                if hasattr(llm_context, "device_id") and llm_context.device_id:
                    satellite = f"assist_satellite.{llm_context.device_id}"

                # Create service call data
                service_data = {
                    "time": time_obj,
                    "name": name,
                    "message": message,
                }

                if repeat_days:
                    service_data["repeat_days"] = repeat_days
                    service_data["repeat"] = "custom"

                if satellite:
                    service_data["satellite"] = satellite

                # Create a mock ServiceCall-like object
                class MockServiceCall:
                    def __init__(self, data):
                        self.data = data

                call = MockServiceCall(service_data)
                target = {"satellite": satellite, "media_players": []}

                # Schedule the reminder using the coordinator
                await coordinator.schedule_item(call, is_alarm=False, target=target)

                response_msg = f"Reminder '{name}' set for {time_str}"
                if repeat_days:
                    response_msg += f" on {', '.join(repeat_days)}"

                return self.wrap_response({
                    "success": True,
                    "message": response_msg,
                    "time": time_str,
                    "name": name,
                })

            except Exception as e:
                _LOGGER.error("Error setting reminder: %s", e, exc_info=True)
                return {"error": f"Failed to set reminder: {str(e)}"}


    class ListRemindersTool(llm.Tool):
        """Tool for listing all reminders."""

        name = "list_reminders"
        description = "List all currently set reminders. Use this when the user asks what reminders are set or wants to see their reminders."
        response_instruction = """
        Present the list of reminders to the user in a clear, conversational way.
        Include the time and task name for each reminder.
        If there are no reminders, let the user know in a friendly way.
        Keep your response concise and in plain text without formatting.
        """

        parameters = vol.Schema({})

        def wrap_response(self, response: dict) -> dict:
            response["instruction"] = self.response_instruction
            return response

        async def async_call(
            self,
            hass: HomeAssistant,
            tool_input: llm.ToolInput,
            llm_context: llm.LLMContext,
        ) -> JsonObjectType:
            """Call the tool to list reminders."""
            _LOGGER.info("Listing all reminders")

            try:
                # Get coordinator
                coordinator = None
                for entry_id, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and "coordinator" in data:
                        coordinator = data["coordinator"]
                        break

                if not coordinator:
                    return {"error": "Reminder system coordinator not found"}

                # Get all reminders from active items
                reminders = []
                for item_id, item in coordinator._active_items.items():
                    if not item.get("is_alarm") and item.get("status") in ["scheduled", "active"]:
                        reminder_info = {
                            "id": item_id,
                            "name": item.get("name", item_id),
                            "status": item.get("status"),
                        }

                        # Format scheduled time
                        sched_time = item.get("scheduled_time")
                        if isinstance(sched_time, datetime):
                            reminder_info["time"] = sched_time.strftime("%H:%M")
                            reminder_info["date"] = sched_time.strftime("%Y-%m-%d")
                        elif isinstance(sched_time, str):
                            parsed = dt_util.parse_datetime(sched_time)
                            if parsed:
                                reminder_info["time"] = parsed.strftime("%H:%M")
                                reminder_info["date"] = parsed.strftime("%Y-%m-%d")

                        if item.get("repeat_days"):
                            reminder_info["repeat_days"] = item["repeat_days"]

                        if item.get("message"):
                            reminder_info["message"] = item["message"]

                        reminders.append(reminder_info)

                if not reminders:
                    return self.wrap_response(
                        {"reminders": [], "message": "No reminders are currently set"}
                    )

                return self.wrap_response(
                    {
                        "reminders": reminders,
                        "count": len(reminders),
                        "message": f"You have {len(reminders)} reminder{'s' if len(reminders) != 1 else ''} set",
                    }
                )

            except Exception as e:
                _LOGGER.error("Error listing reminders: %s", e, exc_info=True)
                return {"error": f"Failed to list reminders: {str(e)}"}


    class DeleteReminderTool(llm.Tool):
        """Tool for deleting reminders."""

        name = "delete_reminder"
        description = "Delete one or more reminders. Use this when the user wants to cancel, remove, or delete a reminder. Can delete by reminder name or all reminders."
        response_instruction = """
        Confirm to the user which reminder(s) were deleted.
        Keep your response concise and friendly, in plain text without formatting.
        """

        parameters = vol.Schema(
            {
                vol.Optional(
                    "name",
                    description="Delete reminder(s) by name or partial name match. Example: 'medicine' will delete reminders with 'medicine' in the name.",
                ): str,
                vol.Optional(
                    "delete_all",
                    description="Set to true to delete all reminders. Use when user says 'delete all reminders' or 'clear all reminders'.",
                ): bool,
            }
        )

        def wrap_response(self, response: dict) -> dict:
            response["instruction"] = self.response_instruction
            return response

        async def async_call(
            self,
            hass: HomeAssistant,
            tool_input: llm.ToolInput,
            llm_context: llm.LLMContext,
        ) -> JsonObjectType:
            """Call the tool to delete reminder(s)."""
            name = tool_input.tool_args.get("name")
            delete_all = tool_input.tool_args.get("delete_all", False)

            _LOGGER.info("Deleting reminder: name=%s, delete_all=%s", name, delete_all)

            try:
                # Get coordinator
                coordinator = None
                for entry_id, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and "coordinator" in data:
                        coordinator = data["coordinator"]
                        break

                if not coordinator:
                    return {"error": "Reminder system coordinator not found"}

                if delete_all:
                    # Delete all reminders
                    await coordinator.delete_all_items(is_alarm=False)
                    # Count deleted reminders
                    reminder_count = sum(1 for item in coordinator._active_items.values() if not item.get("is_alarm"))
                    return self.wrap_response(
                        {
                            "success": True,
                            "deleted_count": reminder_count,
                            "message": f"Deleted all {reminder_count} reminder{'s' if reminder_count != 1 else ''}",
                        }
                    )

                if name:
                    # Find reminders matching name
                    deleted_count = 0
                    name_lower = name.lower()
                    items_to_delete = []

                    for item_id, item in coordinator._active_items.items():
                        if item.get("is_alarm"):
                            continue
                        item_name = item.get("name", "").lower()
                        if name_lower in item_name or name_lower in item_id.lower():
                            items_to_delete.append(item_id)

                    for item_id in items_to_delete:
                        await coordinator.delete_item(item_id, is_alarm=False)
                        deleted_count += 1

                    if deleted_count > 0:
                        return self.wrap_response(
                            {
                                "success": True,
                                "deleted_count": deleted_count,
                                "message": f"Deleted {deleted_count} reminder{'s' if deleted_count != 1 else ''} matching '{name}'",
                            }
                        )
                    return {"error": f"No reminders found matching '{name}'"}

                return {
                    "error": "Please specify a name or set delete_all to true to delete reminders"
                }

            except Exception as e:
                _LOGGER.error("Error deleting reminder: %s", e, exc_info=True)
                return {"error": f"Failed to delete reminder: {str(e)}"}
