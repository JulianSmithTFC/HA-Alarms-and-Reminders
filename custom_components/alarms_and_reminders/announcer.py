"""Handle announcements and sounds on satellites."""
import logging
import asyncio
import os
import time
from datetime import datetime
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.network import get_url

_LOGGER = logging.getLogger(__name__)

class Announcer:
    """Handles announcements and sounds on satellites."""
    
    def __init__(self, hass: HomeAssistant):
        """Initialize announcer."""
        self.hass = hass
        self._stop_event = None

    async def _start_stream(self, source: str, entity_id: str, message: str, announce_every: int = 60, prefer_external: bool = True, timeout: float = 3.0):
        """
        Start the announce_stream service and wait for the started event.
        Returns (stream_id, media_url) or (None, None) on failure.
        """
        try:
            # listen for started event
            fut = asyncio.get_event_loop().create_future()

            def _on_started(event):
                try:
                    data = event.data or {}
                    if data.get("entity_id") == entity_id and not fut.done():
                        fut.set_result(data.get("stream_id"))
                except Exception:
                    pass

            unsub = self.hass.bus.async_listen("announce_stream_started", _on_started)

            # call start service (non-blocking)
            await self.hass.services.async_call(
                "announce_stream",
                "start",
                {
                    "source": source,
                    "entity_id": entity_id,
                    "message": message,
                    "announce_every": announce_every,
                    "prefer_external": prefer_external,
                },
                blocking=False,
            )

            try:
                stream_id = await asyncio.wait_for(fut, timeout=timeout)
            except Exception:
                stream_id = None
            finally:
                try:
                    unsub()
                except Exception:
                    pass

            if not stream_id:
                return None, None

            base = get_url(self.hass, prefer_external=prefer_external)
            media_url = f"{base}/api/announce_stream/{stream_id}"
            return stream_id, media_url
        except Exception as err:
            _LOGGER.debug("Failed to start announce_stream: %s", err)
            return None, None

    async def _stop_stream(self, stream_id: str):
        """Stop the announce_stream service for a given stream_id (best-effort)."""
        try:
            if not stream_id:
                return
            await self.hass.services.async_call(
                "announce_stream",
                "stop",
                {"stream_id": stream_id},
                blocking=False,
            )
        except Exception as err:
            _LOGGER.debug("Failed to stop announce_stream %s: %s", stream_id, err)

    async def announce_on_satellite(self, satellite: str, message: str, sound_file: str, 
                                    stop_event=None, name: str = None, is_alarm: bool = False) -> None:
        """Make announcement and play sound on satellite."""
        try:
            # Store stop event
            self._stop_event = stop_event

            # Ensure proper entity_id format
            satellite_entity_id = (
                satellite if satellite.startswith("assist_satellite.") 
                else f"assist_satellite.{satellite}"
            )

            # Attempt to resolve a local sound file path if provided as a short name
            source = sound_file
            try:
                # If it's a URL, keep it
                if isinstance(sound_file, str) and (sound_file.startswith("http://") or sound_file.startswith("https://")):
                    source = sound_file
                else:
                    # try absolute path as-is
                    if os.path.isabs(sound_file) and os.path.exists(sound_file):
                        source = sound_file
                    else:
                        # try relative to this component folder (sounds/...)
                        comp_dir = os.path.dirname(__file__)
                        candidate = os.path.join(comp_dir, sound_file)
                        if os.path.exists(candidate):
                            source = candidate
                        else:
                            # look in sounds folder
                            candidate2 = os.path.join(comp_dir, "sounds", sound_file)
                            if os.path.exists(candidate2):
                                source = candidate2
            except Exception:
                source = sound_file

            # Prepare streaming: try to start announce_stream service to produce an mp3 stream
            stream_id = None
            media_url = None
            try:
                # use a 1s short timeout to avoid blocking HA if announce_stream not present
                stream_id, media_url = await self._start_stream(source, satellite_entity_id, message or "", announce_every=60)
            except Exception:
                stream_id, media_url = None, None

            while True:
                if self._stop_event and self._stop_event.is_set():
                    _LOGGER.debug("Announcement loop stopped")
                    break

                try:
                    # Format announcement based on type and name
                    now = dt_util.now()  # Get local time from HA
                    current_time = now.strftime("%I:%M %p").lstrip("0")  # Remove leading zero
                    
                    if is_alarm:
                        # For alarms, only include name if it's not auto-generated
                        if name and not name.startswith("alarm_"):
                            announcement = f"{name} alarm. It's {current_time}"
                            if message:
                                announcement += f". {message}"
                        else:
                            # Auto-generated alarm name, just announce time
                            announcement = f"It's {current_time}"
                            if message:
                                announcement += f". {message}"
                    else:
                        # For reminders, always include the name
                        announcement = f"Time to {name}. It's {current_time}"
                        if message:
                            announcement += f". {message}"
                    
                    _LOGGER.debug("Making announcement: %s", announcement)

                    # If we have a working stream, tell satellite to play the stream URL as media_id.
                    # The announce_stream service mixes TTS and music periodically; we only make an initial
                    # small TTS call optionally for compatibility.
                    if media_url:
                        # send a one-off short TTS announce so satellite UI will show the message immediately,
                        # then play media (media contains ongoing TTS mixing)
                        try:
                            await self.hass.services.async_call(
                                "assist_satellite",
                                "announce",
                                {
                                    "entity_id": satellite_entity_id,
                                    "message": announcement,
                                },
                                blocking=True
                            )
                        except Exception:
                            # ignore; continue to instruct play_media
                            pass

                        # Ask satellite to play the streamed mp3 URL
                        await self.hass.services.async_call(
                            "assist_satellite",
                            "announce",
                            {
                                "entity_id": satellite_entity_id,
                                "media_id": media_url,
                            },
                            blocking=False
                        )
                    else:
                        # No stream available -> use legacy behavior: TTS then play media file via media_id
                        await self.hass.services.async_call(
                            "assist_satellite",
                            "announce",
                            {
                                "entity_id": satellite_entity_id,
                                "message": announcement
                            },
                            blocking=True
                        )

                        # Play ringtone file if provided (best-effort by media_id)
                        try:
                            await self.hass.services.async_call(
                                "assist_satellite",
                                "announce",
                                {
                                    "entity_id": satellite_entity_id,
                                    "media_id": sound_file
                                },
                                blocking=True
                            )
                        except Exception:
                            _LOGGER.debug("Failed to play media_id %s directly on satellite", sound_file)

                    # 3. Wait for satellite to be idle or until stopped
                    while not await self._is_satellite_idle(satellite_entity_id):
                        if self._stop_event and self._stop_event.is_set():
                            # stop stream if any
                            if stream_id:
                                await self._stop_stream(stream_id)
                            return
                        await asyncio.sleep(1)

                    # 5. Wait for one minute or until stopped (announce_every handled inside stream)
                    try:
                        if self._stop_event:
                            await asyncio.wait_for(self._stop_event.wait(), timeout=60)
                            break
                        else:
                            await asyncio.sleep(60)
                    except asyncio.TimeoutError:
                        continue

                except Exception as err:
                    _LOGGER.error("Error in announcement loop: %s", err)
                    await asyncio.sleep(5)

        except Exception as err:
            _LOGGER.error(
                "Error announcing on satellite %s: %s",
                satellite,
                str(err),
                exc_info=True
            )
        finally:
            # ensure stream stopped
            try:
                if 'stream_id' in locals() and stream_id:
                    await self._stop_stream(stream_id)
            except Exception:
                pass

    async def _is_satellite_idle(self, satellite_entity_id: str) -> bool:
        """Check if satellite is idle."""
        try:
            state = self.hass.states.get(satellite_entity_id)
            return state.state == "idle" if state else True
        except Exception:
            return True  # Assume idle if can't get state
