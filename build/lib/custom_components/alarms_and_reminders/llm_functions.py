"""LLM function implementations for alarm and reminder services."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util.json import JsonObjectType  

# Make llm import optional so tests / older HA installs don't fail at import time.
try:
    from homeassistant.helpers import llm  # type: ignore
except Exception:
    llm = None  # type: ignore

from .const import DOMAIN
from .llm_helpers import get_coordinator

_LOGGER = logging.getLogger(__name__)

ALARM_REMINDER_API_NAME = "Alarm and Reminder Management"

ALARM_REMINDER_SERVICES_PROMPT = """
You have access to alarm and reminder management tools to help users manage their alarms and reminders.

For Alarms:
- When a user asks to set an alarm, use the set_alarm tool
- When a user asks what alarms are set or scheduled, use the list_alarms tool
- When a user asks to delete or cancel an alarm, use the delete_alarm tool
- When a user asks to stop or dismiss a ringing alarm, use the stop_alarm tool
- When a user asks to snooze a ringing alarm, use the snooze_alarm tool

For Reminders:
- When a user asks to set a reminder, use the set_reminder tool
- When a user asks what reminders are set or scheduled, use the list_reminders tool
- When a user asks to delete or cancel a reminder, use the delete_reminder tool
- When a user asks to stop or dismiss a ringing reminder, use the stop_reminder tool
- When a user asks to snooze a ringing reminder, use the snooze_reminder tool

Be helpful and conversational when confirming actions or listing items.
""".strip()

# Only define the real LLM API class if llm helper is available.
if llm is not None:

    class AlarmReminderAPI(llm.API):
        """Alarm and Reminder management API for LLM integration."""

        def __init__(self, hass: HomeAssistant, name: str) -> None:
            """Initialize the API."""
            super().__init__(hass=hass, id=DOMAIN, name=name)

        async def async_get_api_instance(
            self, llm_context: "llm.LLMContext"
        ) -> "llm.APIInstance":
            """Get API instance."""
            # Import tools here to avoid circular imports
            from .alarm_tools import DeleteAlarmTool, ListAlarmsTool, SetAlarmTool
            from .reminder_tools import DeleteReminderTool, ListRemindersTool, SetReminderTool
            from .alarm_control_tools import SnoozeAlarmTool, StopAlarmTool, SnoozeReminderTool, StopReminderTool
            
            tools = [
                SetAlarmTool(),
                ListAlarmsTool(),
                DeleteAlarmTool(),
                StopAlarmTool(),
                SnoozeAlarmTool(),
                SetReminderTool(),
                ListRemindersTool(),
                DeleteReminderTool(),
                StopReminderTool(),
                SnoozeReminderTool(),
            ]

            return llm.APIInstance(
                api=self,
                api_prompt=ALARM_REMINDER_SERVICES_PROMPT,
                llm_context=llm_context,
                tools=tools,
            )

        async def async_call(
            self,
            hass: HomeAssistant,
            tool_input: llm.ToolInput,
            llm_context: llm.LLMContext,
        ) -> JsonObjectType:
            """Call the appropriate tool based on the input."""
            tool_name = tool_input.tool
            tool = next((t for t in self.tools if t.name == tool_name), None)
            if tool is None:
                return {"error": f"Tool {tool_name} not available"}

            # Convert tool input to the expected type
            try:
                parsed_input = tool.parse_input(tool_input.input)
            except Exception:
                return {"error": f"Invalid input for {tool_name}"}

            # Call the tool's async function
            try:
                coordinator = get_coordinator(hass)
                if not coordinator:
                    return {"error": "Coordinator not available"}

                # Derive satellite from llm_context.device_id if available
                satellite = None
                if hasattr(llm_context, "device_id") and llm_context.device_id:
                    satellite = f"assist_satellite.{llm_context.device_id}"

                # Prepare service data
                service_data = {
                    "coordinator": coordinator,
                    "satellite": satellite,
                    **parsed_input,
                }

                # Call the tool
                result = await tool.async_call(hass, tool_input, llm_context)

            except Exception as e:
                _LOGGER.exception("Error calling tool %s", tool_name)
                return {"error": str(e)}

            return result


    async def async_setup_llm_api(hass: HomeAssistant) -> None:
        """Set up LLM API for alarm and reminder services."""
        # Check if already set up
        if DOMAIN in hass.data and "llm_api" in hass.data[DOMAIN]:
            _LOGGER.debug("LLM API already registered")
            return

        hass.data.setdefault(DOMAIN, {})

        # Create and register the API
        alarm_reminder_api = AlarmReminderAPI(hass, ALARM_REMINDER_API_NAME)
        hass.data[DOMAIN]["llm_api"] = alarm_reminder_api

        try:
            unregister_func = llm.async_register_api(hass, alarm_reminder_api)
            hass.data[DOMAIN]["llm_api_unregister"] = unregister_func
            _LOGGER.info("Alarms and Reminders LLM API registered successfully")
        except Exception as e:
            _LOGGER.error("Failed to register LLM API: %s", e, exc_info=True)
            raise


    async def async_cleanup_llm_api(hass: HomeAssistant) -> None:
        """Clean up LLM API."""
        if DOMAIN not in hass.data:
            return

        # Unregister API if we have the unregister function
        unreg_func = hass.data[DOMAIN].get("llm_api_unregister")
        if unreg_func:
            try:
                unreg_func()
                _LOGGER.info("Alarms and Reminders LLM API unregistered")
            except Exception as e:
                _LOGGER.debug("Error unregistering LLM API: %s", e)

        # Clean up stored data
        hass.data[DOMAIN].pop("llm_api", None)
        hass.data[DOMAIN].pop("llm_api_unregister", None)

else:
    # llm helper not available - provide no-op stubs so importing this module is safe.
    class AlarmReminderAPI:
        def __init__(self, hass: HomeAssistant, name: str) -> None:
            self.hass = hass
            self.id = DOMAIN
            self.name = name

        async def async_get_api_instance(self, llm_context=None):
            return None

    async def async_setup_llm_api(hass: HomeAssistant) -> None:
        """No-op when HA does not provide llm helpers."""
        _LOGGER.debug("LLM helper not available; skipping LLM API setup")
        return False

    async def async_cleanup_llm_api(hass: HomeAssistant) -> None:
        """No-op cleanup when llm helpers are absent."""
        return


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    # LLM: optionally register API if enabled in entry options (or default True)
    enable_llm = entry.options.get("enable_llm", entry.data.get("enable_llm", True))
    if enable_llm:
        try:
            await async_setup_llm_api(hass)
            hass.data[DOMAIN][entry.entry_id]["llm_enabled"] = True
        except Exception:
            _LOGGER.exception("Failed to enable LLM API")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an entry."""
    # teardown LLM if we enabled it
    if hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("llm_enabled"):
        try:
            await async_cleanup_llm_api(hass)
        except Exception:
            _LOGGER.exception("Failed to cleanup LLM API")
    return True

