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

from .alarm_tools import DeleteAlarmTool, ListAlarmsTool, SetAlarmTool
from .reminder_tools import DeleteReminderTool, ListRemindersTool, SetReminderTool
from .alarm_control_tools import SnoozeAlarmTool, StopAlarmTool, SnoozeReminderTool, StopReminderTool
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ALARM_REMINDER_API_NAME = "Alarm and Reminder Management"


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
                if tool_name in ["set_alarm", "set_reminder"]:
                    # For set commands, directly call the tool with parsed input
                    result = await tool.async_run(hass, parsed_input, llm_context)
                else:
                    # For other tools, use the coordinator context
                    coordinator = None
                    for entry_id, data in hass.data.get(DOMAIN, {}).items():
                        if isinstance(data, dict) and "coordinator" in data:
                            coordinator = data["coordinator"]
                            break
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

                    # Call the appropriate service based on the tool
                    if tool_name in ["list_alarms", "list_reminders"]:
                        result = await tool.async_run(hass, service_data, llm_context)
                    else:
                        result = await tool.async_run(hass, service_data, llm_context)

            except Exception as e:
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

    # create coordinator/storage/etc...

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
    # ...existing code...
