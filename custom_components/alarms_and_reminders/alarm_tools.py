"""LLM Tools for alarm management."""
import logging
import re
from datetime import datetime, time, timedelta
from typing import Dict, Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
try:
    from homeassistant.helpers import llm
except Exception:
    llm = None

if llm is None:
    # LLM helper missing — provide minimal no-op tool classes so imports succeed in tests.
    class SetAlarmTool:
        async def async_call(self, *args, **kwargs):
            return {"error": "LLM not available"}

    class ListAlarmsTool:
        async def async_call(self, *args, **kwargs):
            return {"error": "LLM not available"}

    # class DeleteAlarmTool:
    #     async def async_call(self, *args, **kwargs):
    #         return {"error": "LLM not available"}
else:
    from homeassistant.util.json import JsonObjectType
    from homeassistant.util import dt as dt_util

    from .const import DOMAIN

    from .llm_functions import get_coordinator

    _LOGGER = logging.getLogger(__name__)


    class SetAlarmTool(llm.Tool):
        """Tool for setting an alarm."""

        name = "set_alarm"
        description = "Set a new alarm at a specific time. Use this when the user wants to create an alarm or be woken up at a specific time."
        response_instruction = """
        Confirm to the user that the alarm has been set with the time.
        Keep your response concise and friendly, in plain text without formatting.
        """

        parameters = vol.Schema(
            {
                vol.Required(
                    "time",
                    description="The time for the alarm in HH:MM format (24-hour). Example: 07:30 for 7:30 AM, 14:00 for 2:00 PM",
                ): str,
                vol.Optional(
                    "name",
                    description="An optional descriptive name or label for the alarm. Example: 'Morning alarm', 'Meeting reminder', 'Wake up'",
                ): str,
                vol.Optional(
                    "repeat_days",
                    description="Optional list of days when alarm should repeat. Use lowercase 3-letter abbreviations: mon, tue, wed, thu, fri, sat, sun. Leave empty for one-time alarm.",
                ): [str],
                vol.Optional(
                    "message",
                    description="Optional message to announce when the alarm rings",
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
            """Call the tool to set an alarm."""
            time_str = tool_input.tool_args["time"]
            name = tool_input.tool_args.get("name")
            repeat_days = tool_input.tool_args.get("repeat_days")
            message = tool_input.tool_args.get("message", "")

            _LOGGER.info("Setting alarm at %s with name: %s", time_str, name)

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

                coordinator = get_coordinator(hass)
                if not coordinator:
                    return {"error": "Coordinator not available"}

                # Create service call data
                service_data = {
                    "time": time_obj,
                    "message": message,
                }

                if name:
                    service_data["name"] = name

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
                target = {"satellite": satellite}

                # Schedule the alarm using the coordinator
                await coordinator.schedule_item(call, is_alarm=True, target=target)

                response_msg = f"Alarm set for {time_str}"
                if name:
                    response_msg = f"Alarm '{name}' set for {time_str}"
                if repeat_days:
                    response_msg += f" on {', '.join(repeat_days)}"

                return self.wrap_response({
                    "success": True,
                    "message": response_msg,
                    "time": time_str,
                })

            except Exception as e:
                _LOGGER.error("Error setting alarm: %s", e, exc_info=True)
                return {"error": f"Failed to set alarm: {str(e)}"}


    class ListAlarmsTool(llm.Tool):
        """Tool for listing all alarms."""

        name = "list_alarms"
        description = "List all currently set alarms. Use this when the user asks what alarms are set or wants to see their alarms."
        response_instruction = """
        Present the list of alarms to the user in a clear, conversational way.
        Include the time and name for each alarm.
        If there are no alarms, let the user know in a friendly way.
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
            """Call the tool to list alarms."""
            _LOGGER.info("Listing all alarms")

            try:
                # Get coordinator
                coordinator = None
                for entry_id, data in hass.data.get(DOMAIN, {}).items():
                    if isinstance(data, dict) and "coordinator" in data:
                        coordinator = data["coordinator"]
                        break

                if not coordinator:
                    return {"error": "Alarm system coordinator not found"}

                # Get all alarms from active items
                alarms = []
                for item_id, item in coordinator._active_items.items():
                    if item.get("is_alarm") and item.get("status") in ["scheduled", "active"]:
                        alarm_info = {
                            "id": item_id,
                            "name": item.get("name", item_id),
                            "status": item.get("status"),
                        }

                        # Format scheduled time
                        sched_time = item.get("scheduled_time")
                        if isinstance(sched_time, datetime):
                            alarm_info["time"] = sched_time.strftime("%H:%M")
                            alarm_info["date"] = sched_time.strftime("%Y-%m-%d")
                        elif isinstance(sched_time, str):
                            parsed = dt_util.parse_datetime(sched_time)
                            if parsed:
                                alarm_info["time"] = parsed.strftime("%H:%M")
                                alarm_info["date"] = parsed.strftime("%Y-%m-%d")

                        if item.get("repeat_days"):
                            alarm_info["repeat_days"] = item["repeat_days"]

                        if item.get("message"):
                            alarm_info["message"] = item["message"]

                        alarms.append(alarm_info)

                if not alarms:
                    return self.wrap_response(
                        {"alarms": [], "message": "No alarms are currently set"}
                    )

                return self.wrap_response(
                    {
                        "alarms": alarms,
                        "count": len(alarms),
                        "message": f"You have {len(alarms)} alarm{'s' if len(alarms) != 1 else ''} set",
                    }
                )

            except Exception as e:
                _LOGGER.error("Error listing alarms: %s", e, exc_info=True)
                return {"error": f"Failed to list alarms: {str(e)}"}


    # class DeleteAlarmTool(llm.Tool):
    #     """Tool for deleting alarms."""
    #
    #     name = "delete_alarm"
    #     description = "Delete one or more alarms. Use this when the user wants to cancel, remove, or delete an alarm. Can delete by alarm name or all alarms."
    #     response_instruction = """
    #     Confirm to the user which alarm(s) were deleted.
    #     Keep your response concise and friendly, in plain text without formatting.
    #     """
    #
    #     parameters = vol.Schema(
    #         {
    #             vol.Optional(
    #                 "name",
    #                 description="Delete alarm(s) by name or partial name match. Example: 'morning' will delete alarms with 'morning' in the name.",
    #             ): str,
    #             vol.Optional(
    #                 "delete_all",
    #                 description="Set to true to delete all alarms. Use when user says 'delete all alarms' or 'clear all alarms'.",
    #             ): bool,
    #         }
    #     )
    #
    #     def wrap_response(self, response: dict) -> dict:
    #         response["instruction"] = self.response_instruction
    #         return response
    #
    #     async def async_call(
    #         self,
    #         hass: HomeAssistant,
    #         tool_input: llm.ToolInput,
    #         llm_context: llm.LLMContext,
    #     ) -> JsonObjectType:
    #         """Call the tool to delete alarm(s)."""
    #         name = tool_input.tool_args.get("name")
    #         delete_all = tool_input.tool_args.get("delete_all", False)
    #
    #         _LOGGER.info("Deleting alarm: name=%s, delete_all=%s", name, delete_all)
    #
    #         try:
    #             # Get coordinator
    #             coordinator = None
    #             for entry_id, data in hass.data.get(DOMAIN, {}).items():
    #                 if isinstance(data, dict) and "coordinator" in data:
    #                     coordinator = data["coordinator"]
    #                     break
    #
    #             if not coordinator:
    #                 return {"error": "Alarm system coordinator not found"}
    #
    #             if delete_all:
    #                 # Delete all alarms
    #                 await coordinator.delete_all_items(is_alarm=True)
    #                 # Count deleted alarms (need to get count before deletion)
    #                 alarm_count = sum(1 for item in coordinator._active_items.values() if item.get("is_alarm"))
    #                 return self.wrap_response(
    #                     {
    #                         "success": True,
    #                         "deleted_count": alarm_count,
    #                         "message": f"Deleted all {alarm_count} alarm{'s' if alarm_count != 1 else ''}",
    #                     }
    #                 )
    #
    #             if name:
    #                 # Find alarms matching name
    #                 deleted_count = 0
    #                 name_lower = name.lower()
    #                 items_to_delete = []
    #
    #                 for item_id, item in coordinator._active_items.items():
    #                     if not item.get("is_alarm"):
    #                         continue
    #                     item_name = item.get("name", "").lower()
    #                     if name_lower in item_name or name_lower in item_id.lower():
    #                         items_to_delete.append(item_id)
    #
    #                 for item_id in items_to_delete:
    #                     await coordinator.delete_item(item_id, is_alarm=True)
    #                     deleted_count += 1
    #
    #                 if deleted_count > 0:
    #                     return self.wrap_response(
    #                         {
    #                             "success": True,
    #                             "deleted_count": deleted_count,
    #                             "message": f"Deleted {deleted_count} alarm{'s' if deleted_count != 1 else ''} matching '{name}'",
    #                         }
    #                     )
    #                 return {"error": f"No alarms found matching '{name}'"}
    #
    #             return {
    #                 "error": "Please specify a name or set delete_all to true to delete alarms"
    #             }
    #
    #         except Exception as e:
    #             _LOGGER.error("Error deleting alarm: %s", e, exc_info=True)
    #             return {"error": f"Failed to delete alarm: {str(e)}"}
