"""Handle media playback for alarms and reminders."""
import asyncio
import logging
from datetime import datetime, timedelta
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

class MediaHandler:
    """Handles playing sounds and TTS on media players."""
    
    def __init__(self, hass: HomeAssistant, alarm_sound: str, reminder_sound: str):
        """Initialize media handler."""
        self.hass = hass
        self.alarm_sound = alarm_sound
        self.reminder_sound = reminder_sound
        self._active_alarms = {}  # Store active alarms/reminders

    async def play_on_media_player(self, media_player: str, message: str, is_alarm: bool,
                                    stop_event: asyncio.Event = None, name: str = None) -> None:
        """Play TTS and sound on media player in a loop with periodic announcements."""
        try:
            # Loop until stopped
            while True:
                if stop_event and stop_event.is_set():
                    _LOGGER.debug("Media player playback stopped")
                    break

                # Format announcement with current time
                now = dt_util.now()
                current_time = now.strftime("%I:%M %p").lstrip("0")

                if is_alarm:
                    if name and not name.startswith("alarm_"):
                        announcement = f"{name} alarm. It's {current_time}"
                        if message:
                            announcement += f". {message}"
                    else:
                        announcement = f"It's {current_time}"
                        if message:
                            announcement += f". {message}"
                else:
                    announcement = f"Time to {name}. It's {current_time}"
                    if message:
                        announcement += f". {message}"

                # Play TTS announcement
                try:
                    await self.hass.services.async_call(
                        "tts",
                        "speak",
                        {
                            "entity_id": media_player,
                            "message": announcement,
                            "language": "en"
                        },
                        blocking=True
                    )

                    # Wait for TTS to finish
                    await asyncio.sleep(2)
                except Exception as tts_err:
                    _LOGGER.warning("Error playing TTS on media player: %s", tts_err)

                # Check stop event before playing sound
                if stop_event and stop_event.is_set():
                    break

                # Play sound file
                try:
                    await self.hass.services.async_call(
                        "media_player",
                        "play_media",
                        {
                            "entity_id": media_player,
                            "media_content_id": sound_file,
                            "media_content_type": "music"
                        },
                        blocking=False
                    )
                except Exception as media_err:
                    _LOGGER.warning("Error playing sound on media player: %s", media_err)

                # Wait for 60 seconds or until stopped
                try:
                    if stop_event:
                        await asyncio.wait_for(stop_event.wait(), timeout=60)
                        break
                    else:
                        await asyncio.sleep(60)
                except asyncio.TimeoutError:
                    continue

        except Exception as err:
            _LOGGER.error("Error in media player playback loop %s: %s", media_player, err, exc_info=True)

    async def play_sound(self, satellite: str, media_players: list, is_alarm: bool, message: str) -> None:
        """Play the appropriate sound file."""
        try:
            if media_players:
                for media_player in media_players:
                    await self.play_on_media_player(media_player, message, is_alarm)

        except Exception as err:
            _LOGGER.error("Error playing sound: %s", err, exc_info=True)
            raise

    async def stop_alarm(self, alarm_id: str) -> None:
        """Stop a specific alarm."""
        if alarm_id in self._active_alarms:
            self._active_alarms[alarm_id]["stop_event"].set()
            del self._active_alarms[alarm_id]

    async def snooze_alarm(self, alarm_id: str, snooze_minutes: int = 5) -> None:
        """Snooze a specific alarm."""
        if alarm_id in self._active_alarms:
            # Stop current ringing
            await self.stop_alarm(alarm_id)
            
            # Schedule to ring again after snooze period
            alarm_info = self._active_alarms[alarm_id]
            snooze_delay = timedelta(minutes=snooze_minutes)
            
            await asyncio.sleep(snooze_delay.total_seconds())
            await self.play_sound(
                alarm_info["target"],
                alarm_info["is_alarm"],
                is_satellite="satellite" in alarm_info["target"],
                alarm_id=alarm_id
            )