"""Helper utilities for LLM integration - avoids circular imports."""
import logging
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

def get_coordinator(hass: HomeAssistant):
    """Get the coordinator from hass.data - shared helper function.
    
    This is in a separate module to avoid circular imports.
    """
    for entry_id, data in hass.data.get(DOMAIN, {}).items():
        if isinstance(data, dict) and "coordinator" in data:
            return data["coordinator"]
    return None