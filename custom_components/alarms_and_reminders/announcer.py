"""Handle announcements and sounds on satellites with duration tracking."""
import logging
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.network import get_url

_LOGGER = logging.getLogger(__name__)

try:
    import librosa
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False
    _LOGGER.warning("librosa not installed; using fallback duration detection")


class AudioDurationDetector:
    """Detect audio file duration using librosa or fallback methods."""
    
    @staticmethod
    def get_duration(audio_path: str) -> float:
        """Get audio duration in seconds.
        
        Returns duration in seconds, or 5.0 as fallback if unable to detect.
        """
        try:
            if not os.path.exists(audio_path):
                _LOGGER.warning("Audio file not found: %s", audio_path)
                return 5.0
            
            if HAS_LIBROSA:
                try:
                    duration = librosa.get_duration(filename=audio_path)
                    _LOGGER.debug("librosa detected duration: %.2f seconds for %s", duration, audio_path)
                    return float(duration)
                except Exception as e:
                    _LOGGER.debug("librosa failed: %s, trying ffprobe", e)
            
            # Fallback: try ffprobe
            try:
                import subprocess
                result = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
                     "-of", "default=noprint_wrappers=1:nokey=1:noprint_wrappers=1", 
                     audio_path],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    duration = float(result.stdout.strip())
                    _LOGGER.debug("ffprobe detected duration: %.2f seconds for %s", duration, audio_path)
                    return duration
            except Exception as e:
                _LOGGER.debug("ffprobe failed: %s", e)
            
            # Final fallback: estimate based on file size (very rough)
            try:
                file_size = os.path.getsize(audio_path)
                # Assume ~128 kbps = ~16000 bytes/sec (very rough estimate)
                estimated_duration = file_size / 16000
                _LOGGER.debug("Estimated duration from file size: %.2f seconds for %s", estimated_duration, audio_path)
                return max(3.0, min(estimated_duration, 30.0))  # Clamp between 3-30 seconds
            except Exception as e:
                _LOGGER.debug("File size estimation failed: %s", e)
            
            return 5.0  # Default fallback
            
        except Exception as err:
            _LOGGER.error("Error detecting audio duration: %s", err)
            return 5.0


class SatelliteStateMonitor:
    """Monitor satellite state and detect transitions."""
    
    def __init__(self, hass: HomeAssistant, satellite_entity_id: str):
        self.hass = hass
        self.satellite_entity_id = satellite_entity_id
        self._state_change_callbacks = []
        self._unsub_state_changed = None
        
    async def async_start(self) -> None:
        """Start monitoring satellite state."""
        @callback
        def _on_state_changed(event):
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")
            
            if new_state and new_state.entity_id == self.satellite_entity_id:
                old_status = old_state.state if old_state else "unknown"
                new_status = new_state.state
                _LOGGER.debug(
                    "Satellite %s state changed: %s -> %s",
                    self.satellite_entity_id,
                    old_status,
                    new_status
                )
                
                # Notify callbacks
                for callback in self._state_change_callbacks:
                    try:
                        self.hass.async_create_task(
                            callback(old_status, new_status)
                        )
                    except Exception as e:
                        _LOGGER.error("Error in state change callback: %s", e)
        
        from homeassistant.core import callback
        self._unsub_state_changed = self.hass.bus.async_listen(
            "state_changed",
            _on_state_changed
        )
    
    async def async_stop(self) -> None:
        """Stop monitoring satellite state."""
        if self._unsub_state_changed:
            self._unsub_state_changed()
            self._unsub_state_changed = None
    
    def add_state_change_callback(self, callback):
        """Register a callback for state changes (old_state, new_state)."""
        self._state_change_callbacks.append(callback)
    
    def get_current_state(self) -> str:
        """Get current satellite state."""
        state = self.hass.states.get(self.satellite_entity_id)
        return state.state if state else "unknown"


class Announcer:
    """Handles announcements and sounds on satellites with advanced state tracking."""
    
    def __init__(self, hass: HomeAssistant):
        """Initialize announcer."""
        self.hass = hass
        self.duration_detector = AudioDurationDetector()
        self._active_rings: dict = {}  # item_id -> ring state
    
    async def announce_on_satellite(
        self,
        satellite: str,
        message: str,
        sound_file: str,
        stop_event: asyncio.Event = None,
        name: str = None,
        is_alarm: bool = False
    ) -> None:
        """
        Ring alarm/reminder on satellite with intelligent state tracking.
        
        Flow:
        1. Start monitoring satellite state
        2. Announce TTS message
        3. Play audio file (while monitoring duration and state)
        4. When audio ends OR satellite becomes idle, loop back to step 2
        5. If stop_event is set, stop immediately
        6. If snooze/stop command detected, handle accordingly
        """
        satellite_entity_id = (
            satellite if satellite.startswith("assist_satellite.")
            else f"assist_satellite.{satellite}"
        )
        
        try:
            # Get audio duration for tracking
            audio_duration = self.duration_detector.get_duration(sound_file)
            _LOGGER.info(
                "Starting ring on %s (duration: %.2f seconds). Name: %s, is_alarm: %s",
                satellite_entity_id,
                audio_duration,
                name,
                is_alarm
            )
            
            # Initialize ring state
            item_id = satellite.split(".")[-1] if "." in satellite else satellite
            ring_state = {
                "active": True,
                "audio_duration": audio_duration,
                "last_announcement_time": None,
                "announcement_count": 0,
            }
            self._active_rings[item_id] = ring_state
            
            # Create and start satellite state monitor
            monitor = SatelliteStateMonitor(self.hass, satellite_entity_id)
            await monitor.async_start()
            
            # Flag to track if we should exit the loop
            should_exit = False
            media_playing = False
            media_start_time = None
            
            async def _on_satellite_state_changed(old_state: str, new_state: str) -> None:
                """Handle satellite state changes during ringing."""
                nonlocal should_exit, media_playing
                
                _LOGGER.debug(
                    "Satellite state change during ring: %s -> %s",
                    old_state,
                    new_state
                )
                
                # If satellite becomes idle while media is playing, restart announcement
                if (
                    old_state == "responding"
                    and new_state == "idle"
                    and media_playing
                ):
                    _LOGGER.info(
                        "Satellite %s became idle during playback. "
                        "Will restart announcement when alarm/reminder still active.",
                        satellite_entity_id
                    )
                    media_playing = False
                    # Don't exit; let the loop detect idle state and restart
                
                # If satellite becomes listening, it may handle voice commands
                elif old_state == "responding" and new_state == "listening":
                    _LOGGER.debug(
                        "Satellite %s changed to listening. "
                        "Monitoring for snooze/stop voice commands.",
                        satellite_entity_id
                    )
                    # Stop media but keep ringing active for voice command handling
                    media_playing = False
            
            monitor.add_state_change_callback(_on_satellite_state_changed)
            
            # Main ringing loop
            loop_count = 0
            while ring_state["active"] and not should_exit:
                loop_count += 1
                
                # Check if stop event was set
                if stop_event and stop_event.is_set():
                    _LOGGER.info("Stop event triggered for %s", item_id)
                    should_exit = True
                    break
                
                # Get current satellite state
                current_state = monitor.get_current_state()
                
                # Only announce if satellite is idle or ready
                if current_state not in ["idle", "responding", "listening"]:
                    _LOGGER.debug(
                        "Satellite %s in unexpected state: %s. Waiting...",
                        satellite_entity_id,
                        current_state
                    )
                    await asyncio.sleep(2)
                    continue
                
                try:
                    # Step 1: Announce TTS message
                    announcement = self._format_announcement(
                        name=name,
                        is_alarm=is_alarm,
                        message=message,
                        loop_count=loop_count
                    )
                    
                    _LOGGER.debug("Announcing: %s", announcement)
                    
                    await self.hass.services.async_call(
                        "assist_satellite",
                        "announce",
                        {
                            "entity_id": satellite_entity_id,
                            "message": announcement,
                            "preannounce": False,
                        },
                        blocking=True
                    )
                    
                    ring_state["last_announcement_time"] = dt_util.now()
                    ring_state["announcement_count"] += 1
                    
                    # Step 2: Play audio file with duration tracking
                    media_start_time = time.time()
                    media_playing = True
                    
                    _LOGGER.debug(
                        "Playing audio file: %s (duration: %.2f s)",
                        sound_file,
                        audio_duration
                    )
                    
                    await self.hass.services.async_call(
                        "assist_satellite",
                        "announce",
                        {
                            "entity_id": satellite_entity_id,
                            "media_id": sound_file,
                            "preannounce": False,
                        },
                        blocking=False
                    )
                    
                    # Wait for audio to finish or for stop event
                    elapsed = 0.0
                    check_interval = 0.5  # Check state every 500ms
                    
                    while elapsed < audio_duration:
                        # Check stop event
                        if stop_event and stop_event.is_set():
                            _LOGGER.info(
                                "Stop event triggered during audio playback (elapsed: %.2f/%.2f)",
                                elapsed,
                                audio_duration
                            )
                            should_exit = True
                            break
                        
                        # Check if satellite became idle (interrupted by user action)
                        if monitor.get_current_state() == "idle":
                            elapsed_since_start = time.time() - media_start_time
                            _LOGGER.debug(
                                "Satellite became idle during playback (elapsed: %.2f/%.2f)",
                                elapsed_since_start,
                                audio_duration
                            )
                            # Will restart announcement in next loop iteration
                            media_playing = False
                            break
                        
                        await asyncio.sleep(check_interval)
                        elapsed += check_interval
                    
                    media_playing = False
                    
                    if should_exit:
                        break
                    
                    # Step 3: Wait before next announcement cycle (or repeat immediately)
                    # If satellite is idle, restart immediately
                    if monitor.get_current_state() == "idle":
                        _LOGGER.debug(
                            "Satellite idle after audio. Restarting announcement immediately."
                        )
                        continue
                    
                    # If satellite is still responding, wait a bit before next cycle
                    await asyncio.sleep(1)
                    
                except Exception as err:
                    _LOGGER.error(
                        "Error during announcement/playback on %s: %s",
                        satellite_entity_id,
                        err,
                        exc_info=True
                    )
                    await asyncio.sleep(2)
            
            _LOGGER.info(
                "Ring completed for %s. Total announcements: %d",
                item_id,
                ring_state["announcement_count"]
            )
            
        except Exception as err:
            _LOGGER.error(
                "Error in announce_on_satellite for %s: %s",
                satellite_entity_id,
                err,
                exc_info=True
            )
        finally:
            # Cleanup
            self._active_rings.pop(item_id, None)
            try:
                await monitor.async_stop()
            except Exception:
                pass
    
    def _format_announcement(
        self,
        name: str,
        is_alarm: bool,
        message: str,
        loop_count: int = 1
    ) -> str:
        """Format announcement message based on type and context."""
        now = dt_util.now()
        current_time = now.strftime("%I:%M %p").lstrip("0")
        
        if is_alarm:
            # For alarms, only include name if it's not auto-generated (doesn't start with "alarm_")
            if name and not name.startswith("alarm_"):
                announcement = f"{name} alarm"
            else:
                announcement = "Alarm"
            
            announcement += f". It's {current_time}"
            
            if message:
                announcement += f". {message}"
        else:
            # For reminders, always include the name
            announcement = f"Time to {name}"
            announcement += f". It's {current_time}"
            
            if message:
                announcement += f". {message}"
        
        # Add loop indication if ringing multiple times
        if loop_count > 1:
            announcement += f" (attempt {loop_count})"
        
        return announcement
    
    async def stop_satellite_ring(self, item_id: str) -> None:
        """Stop a satellite ring by updating ring state."""
        if item_id in self._active_rings:
            self._active_rings[item_id]["active"] = False
            _LOGGER.info("Marked ring as inactive for %s", item_id)
