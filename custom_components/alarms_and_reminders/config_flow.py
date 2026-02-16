from __future__ import annotations
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import DOMAIN, DEFAULT_SATELLITE


class AlarmsAndRemindersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Alarms and Reminders."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is None:
            return self.async_show_form(step_id="user")
        return self.async_create_entry(title="Alarms and Reminders", data={})

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow handler."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options.get(DEFAULT_SATELLITE)

        schema = vol.Schema(
            {
                vol.Optional(DEFAULT_SATELLITE, default=current): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="assist_satellite")
                )
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
