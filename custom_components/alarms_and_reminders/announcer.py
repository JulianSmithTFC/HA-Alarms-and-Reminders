"""Handle announcements and sounds on satellites with duration tracking."""
import logging
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from pydub import AudioSegment
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util
from homeassistant.helpers.network import get_url

_LOGGER = logging.getLogger(__name__)


class AudioDurationDetector:
    """Detect audio file duration using pydub."""
    
    @staticmethod
    def get_duration(audio_path: str) -> float:
        """Get audio duration in seconds using pydub.
        
        Supports: MP3, WAV, OGG, FLAC, M4A, and more.
        Returns duration in seconds, or 5.0 as fallback if unable to detect.
        """
        try:
            if not os.path.exists(audio_path):
                _LOGGER.warning("Audio file not found: %s", audio_path)
                return 5.0
            
            # Load audio file
            audio = AudioSegment.from_file(audio_path)
            
            # Get duration in milliseconds and convert to seconds
            duration = len(audio) / 1000.0
            
            _LOGGER.debug("pydub detected duration: %.2f seconds for %s", duration, audio_path)
            return float(duration)
            
        except Exception as err:
            _LOGGER.error("Error detecting audio duration for %s: %s", audio_path, err)
            return 5.0


class SatelliteStateMonitor:
    """Monitor satellite state and detect transitions."""
    
    def __init__(self, hass: HomeAssistant, satellite_entity_id: str):
        self.hass = hass
        self.satellite_entity_id = satellite_entity_id
        self._state_change_callbacks = []
        self._unsub_state_changed = None
        self._last_state = None
        
    async def async_start(self) -> None:
        """Start monitoring satellite state."""
        @callback
        def _on_state_changed(event):
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")
            
            if new_state and new_state.entity_id == self.satellite_entity_id:
                old_status = old_state.state if old_state else "unknown"
                new_status = new_state.state
                self._last_state = new_status
                
                _LOGGER.debug(
                    "Satellite %s state changed: %s -> %s",
                    self.satellite_entity_id,
                    old_status,
                    new_status
                )
                
                # Notify callbacks
                for cb in self._state_change_callbacks:
                    try:
                        self.hass.async_create_task(
                            cb(old_status, new_status)
                        )
                    except Exception as e:
                        _LOGGER.error("Error in state change callback: %s", e)
        
        # Initialize last state
        state = self.hass.states.get(self.satellite_entity_id)
        self._last_state = state.state if state else "unknown"
        
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
        1. Announce TTS message (alarm/reminder name + time + optional message)
        2. Play ringtone audio file while monitoring its duration
        3. Track satellite state transitions during playback
        4. If satellite becomes idle during playback (sudden idle):
           - Stop ringing immediately
           - Mark as completed
           - Don't restart
        5. If ringtone finishes naturally:
           - Announce again (shorter message with just name + time)
           - Play ringtone again
           - Repeat until stop_event is triggered
        6. If satellite transitions to listening (voice command detected):
           - Stop playback but keep ringing active
           - Monitor for snooze/stop function calls
           - If no snooze/stop within 2 seconds, restart announcement
        """
        satellite_entity_id = (
            satellite if satellite.startswith("assist_satellite.")
            else f"assist_satellite.{satellite}"
        )
        
        item_id = satellite.split(".")[-1] if "." in satellite else satellite
        
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
            ring_state = {
                "active": True,
                "audio_duration": audio_duration,
                "last_announcement_time": None,
                "announcement_count": 0,
                "started_at": dt_util.now(),
            }
            self._active_rings[item_id] = ring_state
            
            # Create and start satellite state monitor
            monitor = SatelliteStateMonitor(self.hass, satellite_entity_id)
            await monitor.async_start()
            
            # Track state transitions during media playback
            sudden_idle_detected = False
            voice_command_detected = False
            
            async def _on_satellite_state_changed(old_state: str, new_state: str) -> None:
                """Handle satellite state changes during ringing."""
                nonlocal sudden_idle_detected, voice_command_detected
                
                _LOGGER.debug(
                    "Satellite state change during ring: %s -> %s",
                    old_state,
                    new_state
                )
                
                # Sudden idle while playing media = user pressed stop button or stop word detected
                # This is different from idle after announcement completes
                if old_state == "responding" and new_state == "idle":
                    _LOGGER.warning(
                        "Satellite %s suddenly became idle from responding state. "
                        "Treating as stop signal (button press or stop word).",
                        satellite_entity_id
                    )
                    sudden_idle_detected = True
                
                # Voice command detected (user is responding to the alarm)
                elif old_state == "responding" and new_state == "listening":
                    _LOGGER.info(
                        "Satellite %s detected voice command (listening state). "
                        "Monitoring for snooze/stop function calls.",
                        satellite_entity_id
                    )
                    voice_command_detected = True
            
            monitor.add_state_change_callback(_on_satellite_state_changed)
            
            # Main ringing cycle
            while ring_state["active"]:
                # Check if stop event was set
                if stop_event and stop_event.is_set():
                    _LOGGER.info("Stop event triggered for %s", item_id)
                    break
                
                # Check if we detected a sudden idle (stop signal)
                if sudden_idle_detected:
                    _LOGGER.info(
                        "Sudden idle detected for %s. Marking as completed.",
                        item_id
                    )
                    break
                
                # Wait for satellite to be ready
                current_state = monitor.get_current_state()
                if current_state not in ["idle", "responding", "listening"]:
                    _LOGGER.debug(
                        "Satellite %s in unexpected state: %s. Waiting...",
                        satellite_entity_id,
                        current_state
                    )
                    await asyncio.sleep(2)
                    continue
                
                try:
                    # Step 1: Announce TTS message with alarm/reminder name and time
                    announcement = self._format_announcement(
                        name=name,
                        is_alarm=is_alarm,
                        message=message,
                        is_full=True  # Full announcement on first call
                    )
                    
                    _LOGGER.debug("Announcing (full): %s", announcement)
                    
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
                    
                    # Step 2: Play ringtone audio file with duration-based timing
                    # Reset state flags for this cycle
                    sudden_idle_detected = False
                    voice_command_detected = False
                    
                    media_start_time = time.time()
                    
                    _LOGGER.debug(
                        "Playing ringtone: %s (duration: %.2f seconds)",
                        sound_file,
                        audio_duration
                    )
                    
                    # Start playing audio (non-blocking)
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
                    
                    # Wait for audio duration to elapse while monitoring state
                    elapsed = 0.0
                    check_interval = 0.3  # Check every 300ms for precise tracking
                    
                    while elapsed < audio_duration:
                        # Check if ringing should stop
                        if stop_event and stop_event.is_set():
                            _LOGGER.info(
                                "Stop event triggered during audio (elapsed: %.2f/%.2f)",
                                elapsed,
                                audio_duration
                            )
                            sudden_idle_detected = True
                            break
                        
                        # Check for sudden idle (user pressed stop button)
                        if sudden_idle_detected:
                            elapsed_when_detected = elapsed
                            _LOGGER.info(
                                "Sudden idle detected during audio playback at %.2f/%.2f seconds",
                                elapsed_when_detected,
                                audio_duration
                            )
                            break
                        
                        # Check for voice command (listening state) - pause but don't stop
                        if voice_command_detected:
                            _LOGGER.debug(
                                "Voice command detected. Will restart if no snooze/stop action within 2 seconds."
                            )
                            # Wait 2 seconds to see if a snooze/stop action is called
                            await asyncio.sleep(2)
                            
                            # If stop wasn't called, restart announcement
                            if not stop_event or not stop_event.is_set():
                                _LOGGER.info("No snooze/stop action detected. Restarting announcement.")
                                voice_command_detected = False
                                break
                            else:
                                _LOGGER.info("Stop event detected after voice command.")
                                break
                        
                        await asyncio.sleep(check_interval)
                        elapsed += check_interval
                    
                    _LOGGER.debug(
                        "Audio playback completed or interrupted (elapsed: %.2f/%.2f)",
                        elapsed,
                        audio_duration
                    )
                    
                    # Step 3: After audio finishes, check if we should continue
                    if sudden_idle_detected or (stop_event and stop_event.is_set()):
                        _LOGGER.info("Ring stopped for %s due to user action or stop event", item_id)
                        break
                    
                    # If we're here, audio finished naturally - announce and loop
                    # Short announcement (just name + time, no extra message)
                    short_announcement = self._format_announcement(
                        name=name,
                        is_alarm=is_alarm,
                        message=None,  # No message for short announcement
                        is_full=False  # Short announcement
                    )
                    
                    _LOGGER.debug("Announcing (short): %s", short_announcement)
                    
                    await self.hass.services.async_call(
                        "assist_satellite",
                        "announce",
                        {
                            "entity_id": satellite_entity_id,
                            "message": short_announcement,
                            "preannounce": False,
                        },
                        blocking=True
                    )
                    
                    ring_state["announcement_count"] += 1
                    ring_state["last_announcement_time"] = dt_util.now()
                    
                    # Loop back to play audio again
                    _LOGGER.debug("Restarting ringtone cycle for %s", item_id)
                    
                except Exception as err:
                    _LOGGER.error(
                        "Error during announcement/playback on %s: %s",
                        satellite_entity_id,
                        err,
                        exc_info=True
                    )
                    await asyncio.sleep(2)
            
            _LOGGER.info(
                "Ring completed for %s. Total full announcements: %d, duration: %s",
                item_id,
                ring_state["announcement_count"],
                dt_util.now() - ring_state["started_at"]
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
        message: str = None,
        is_full: bool = True
    ) -> str:
        """Format announcement message based on type and context.
        
        Args:
            name: Alarm/reminder name
            is_alarm: Whether this is an alarm (True) or reminder (False)
            message: Optional custom message (only for full announcements)
            is_full: If True, use full announcement with message; if False, just name + time
        """
        now = dt_util.now()
        current_time = now.strftime("%I:%M %p").lstrip("0")
        
        if is_alarm:
            # For alarms, only include name if it's not auto-generated
            if name and not name.startswith("alarm_"):
                announcement = f"{name} alarm"
            else:
                announcement = "Alarm"
            
            announcement += f". It's {current_time}"
            
            # Add custom message only for full announcements
            if is_full and message:
                announcement += f". {message}"
        else:
            # For reminders, always include the name
            announcement = f"Time to {name}"
            announcement += f". It's {current_time}"
            
            # Add custom message only for full announcements
            if is_full and message:
                announcement += f". {message}"
        
        return announcement
    
    async def stop_satellite_ring(self, item_id: str) -> None:
        """Stop a satellite ring by updating ring state."""
        if item_id in self._active_rings:
            self._active_rings[item_id]["active"] = False
            _LOGGER.info("Marked ring as inactive for %s", item_id)
