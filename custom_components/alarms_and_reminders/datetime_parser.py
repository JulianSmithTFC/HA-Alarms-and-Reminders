"""Multi-language datetime parsing for alarms and reminders."""
import logging
import re
from datetime import datetime, date, time, timedelta
from typing import Dict, Optional, Tuple

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Language configuration dictionaries
LANGUAGE_CONFIGS = {
    "en": {
        "relative_dates": {
            "today": 0,
            "tomorrow": 1,
            "after tomorrow": 2,
        },
        "weekdays": {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        },
        "am_indicators": ["am", "morning"],
        "pm_indicators": ["pm", "afternoon", "evening"],
        "time_patterns": [
            r"(\d{1,2}):(\d{2})\s*(am|pm|morning|afternoon|evening)",
            r"(\d{1,2})\s+(\d{2})\s*(am|pm|morning|afternoon|evening)",
            r"(\d{1,2})\s*(am|pm|morning|afternoon|evening)",
            r"(\d{1,2}):(\d{2})",
            r"(\d{1,2})",
        ],
    },
    "de": {
        "relative_dates": {
            "heute": 0,
            "morgen": 1,
            "übermorgen": 2,
        },
        "weekdays": {
            "montag": 0,
            "dienstag": 1,
            "mittwoch": 2,
            "donnerstag": 3,
            "freitag": 4,
            "samstag": 5,
            "sonntag": 6,
        },
        "am_indicators": ["morgens", "vormittags", "am vormittag"],
        "pm_indicators": ["nachmittags", "abends", "am nachmittag", "am abend"],
        "time_patterns": [
            r"(\d{1,2}):(\d{2})\s*(morgens|vormittags|am vormittag|nachmittags|abends|am nachmittag|am abend)",
            r"(\d{1,2})\s+(\d{2})\s*(morgens|vormittags|am vormittag|nachmittags|abends|am nachmittag|am abend)",
            r"(\d{1,2})\s*(morgens|vormittags|am vormittags|nachmittags|abends|am nachmittag|am abend)",
            r"(\d{1,2}):(\d{2})",
            r"(\d{1,2})",
        ],
    },
    "fr": {
        "relative_dates": {
            "aujourd'hui": 0,
            "aujourdhui": 0,  # Without apostrophe
            "demain": 1,
            "après-demain": 2,
            "apres-demain": 2,  # Without special characters
        },
        "weekdays": {
            "lundi": 0,
            "mardi": 1,
            "mercredi": 2,
            "jeudi": 3,
            "vendredi": 4,
            "samedi": 5,
            "dimanche": 6,
        },
        "am_indicators": ["du matin"],
        "pm_indicators": ["de l'après-midi", "de laprès-midi", "de l'apres-midi", "du soir"],
        "time_patterns": [
            r"(\d{1,2})\s*heures?\s*(\d{2})?\s*(du matin|de l'après-midi|de laprès-midi|de l'apres-midi|du soir)?",
            r"(\d{1,2}):(\d{2})",
            r"(\d{1,2})",
        ],
    },
    "ar": {
        "relative_dates": {
            "اليوم": 0,
            "غدا": 1,
            "غداً": 1,  # With diacritic
            "بعد غد": 2,
        },
        "weekdays": {
            "الاثنين": 0,
            "الإثنين": 0,  # Variation
            "الثلاثاء": 1,
            "الاربعاء": 2,
            "الأربعاء": 2,  # Variation
            "الخميس": 3,
            "الجمعة": 4,
            "السبت": 5,
            "الاحد": 6,
            "الأحد": 6,  # Variation
        },
        "am_indicators": ["صباحا", "صباحاً"],
        "pm_indicators": ["مساء", "مساءً"],
        "time_patterns": [
            r"(الساعة\s*)?(\d{1,2}):(\d{2})\s*(صباحا|صباحاً|مساء|مساءً)",
            r"(الساعة\s*)?(\d{1,2})\s*و\s*(\d{1,2})\s*(دقيقة\s*)?(صباحا|صباحاً|مساء|مساءً)",
            r"(الساعة\s*)?(\d{1,2})\s*(صباحا|صباحاً|مساء|مساءً)",
            r"(\d{1,2}):(\d{2})",
            r"(\d{1,2})",
        ],
    },
}


def detect_language(text: str) -> str:
    """
    Detect language from text content.

    Args:
        text: The datetime string to analyze

    Returns:
        Language code (en, de, fr, ar)
    """
    text_lower = text.lower()

    # Check for Arabic characters
    if any('\u0600' <= char <= '\u06FF' for char in text):
        return "ar"

    # Check for German-specific words
    german_keywords = ["übermorgen", "morgens", "vormittags", "nachmittags", "montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"]
    if any(keyword in text_lower for keyword in german_keywords):
        return "de"

    # Check for French-specific words
    french_keywords = ["aujourd'hui", "aujourdhui", "demain", "après-demain", "apres-demain", "heures", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    if any(keyword in text_lower for keyword in french_keywords):
        return "fr"

    # Default to English
    return "en"


def extract_relative_date(text: str, lang: str) -> Optional[int]:
    """
    Extract relative date offset from text (today=0, tomorrow=1, after tomorrow=2).

    Args:
        text: The datetime string
        lang: Language code

    Returns:
        Date offset in days, or None if not found
    """
    text_lower = text.lower()
    config = LANGUAGE_CONFIGS.get(lang, LANGUAGE_CONFIGS["en"])

    for relative_date, offset in config["relative_dates"].items():
        if relative_date in text_lower:
            _LOGGER.debug(f"Found relative date '{relative_date}' in text, offset={offset}")
            return offset

    return None


def extract_weekday(text: str, lang: str, now: datetime) -> Optional[date]:
    """
    Extract weekday name and calculate next occurrence.

    Args:
        text: The datetime string
        lang: Language code
        now: Current datetime

    Returns:
        Date of next occurrence, or None if not found
    """
    text_lower = text.lower()
    config = LANGUAGE_CONFIGS.get(lang, LANGUAGE_CONFIGS["en"])

    # Check for "next [weekday]" patterns
    is_next_week = False
    if lang == "en" and "next " in text_lower:
        is_next_week = True
    elif lang == "de" and ("nächsten" in text_lower or "nächste" in text_lower):
        is_next_week = True
    elif lang == "fr" and "prochain" in text_lower:
        is_next_week = True
    elif lang == "ar" and ("الجاي" in text_lower or "القادم" in text_lower or "الجاية" in text_lower or "القادمة" in text_lower):
        is_next_week = True

    # Find weekday name
    for weekday_name, weekday_num in config["weekdays"].items():
        if weekday_name in text_lower:
            current_weekday = now.weekday()

            if is_next_week:
                # Next occurrence of this weekday (at least 7 days from now)
                days_ahead = (weekday_num - current_weekday + 7) % 7
                if days_ahead == 0:
                    days_ahead = 7
                days_ahead += 7  # Force next week
            else:
                # Next occurrence (could be today if same weekday)
                days_ahead = (weekday_num - current_weekday) % 7
                if days_ahead == 0:
                    days_ahead = 7  # If today is the target day, assume next week

            target_date = now.date() + timedelta(days=days_ahead)
            _LOGGER.debug(f"Found weekday '{weekday_name}', target_date={target_date}, is_next_week={is_next_week}")
            return target_date

    return None


def extract_time_components(text: str, lang: str) -> Optional[Tuple[int, int, Optional[str]]]:
    """
    Extract hour, minute, and AM/PM indicator from text.

    Args:
        text: The datetime string
        lang: Language code

    Returns:
        Tuple of (hour, minute, am_pm_indicator) or None if not found
    """
    text_lower = text.lower()
    config = LANGUAGE_CONFIGS.get(lang, LANGUAGE_CONFIGS["en"])

    for pattern in config["time_patterns"]:
        match = re.search(pattern, text_lower)
        if match:
            groups = match.groups()

            # Handle different pattern match structures
            if lang == "ar":
                # Arabic patterns may have الساعة prefix
                if len(groups) >= 3:
                    # Pattern with hour:minute and am/pm
                    if groups[1] and groups[2]:
                        # Format: الساعة HH:MM صباحاً/مساءً
                        hour = int(groups[1])
                        minute = int(groups[2])
                        am_pm = groups[3] if len(groups) > 3 else None
                    elif groups[1] and groups[3]:
                        # Format: الساعة H و M دقيقة صباحاً/مساءً
                        hour = int(groups[1])
                        minute = int(groups[2]) if groups[2].isdigit() else 0
                        am_pm = groups[4] if len(groups) > 4 else None
                    else:
                        # Format: الساعة H صباحاً/مساءً
                        hour = int(groups[1] if groups[1] and groups[1].isdigit() else groups[2])
                        minute = 0
                        am_pm = groups[3] if len(groups) > 3 else groups[2]
                else:
                    hour = int(groups[0]) if groups[0].isdigit() else int(groups[1])
                    minute = int(groups[1]) if len(groups) > 1 and groups[1] and groups[1].isdigit() else 0
                    am_pm = groups[-1] if len(groups) > 0 and groups[-1] and not groups[-1].isdigit() else None

            elif lang == "fr":
                # French patterns: "15 heures 30" or "15 heures du matin"
                hour = int(groups[0])
                minute = int(groups[1]) if len(groups) > 1 and groups[1] and groups[1].isdigit() else 0
                am_pm = groups[2] if len(groups) > 2 and groups[2] else None

            else:
                # English and German patterns
                hour = int(groups[0])
                minute = int(groups[1]) if len(groups) > 1 and groups[1] and groups[1].isdigit() else 0
                am_pm = groups[2] if len(groups) > 2 and groups[2] else None

            _LOGGER.debug(f"Extracted time: hour={hour}, minute={minute}, am_pm={am_pm}")
            return (hour, minute, am_pm)

    return None


def convert_to_24hour(hour: int, minute: int, am_pm: Optional[str], lang: str) -> Tuple[int, int]:
    """
    Convert 12-hour format to 24-hour format.

    Args:
        hour: Hour in 12-hour or 24-hour format
        minute: Minute
        am_pm: AM/PM indicator (language-specific)
        lang: Language code

    Returns:
        Tuple of (hour_24, minute)
    """
    if am_pm is None:
        # No AM/PM indicator
        # If hour is 1-12 and we have no indicator, we cannot reliably convert
        # French uses 24-hour format by default, so no conversion needed
        if lang == "fr":
            return (hour, minute)
        else:
            # For other languages, if hour is already > 12, it's 24-hour format
            if hour > 12:
                return (hour, minute)
            # Otherwise, we have ambiguity - log warning and assume PM if hour < 7, AM otherwise
            if hour < 7:
                _LOGGER.warning(f"Ambiguous hour {hour} without AM/PM, assuming PM")
                return (hour + 12 if hour != 12 else 12, minute)
            else:
                _LOGGER.warning(f"Ambiguous hour {hour} without AM/PM, assuming AM")
                return (hour if hour != 12 else 0, minute)

    config = LANGUAGE_CONFIGS.get(lang, LANGUAGE_CONFIGS["en"])
    am_pm_lower = am_pm.lower()

    is_am = any(indicator in am_pm_lower for indicator in config["am_indicators"])
    is_pm = any(indicator in am_pm_lower for indicator in config["pm_indicators"])

    if is_am:
        # AM: 12 AM = 0:00, 1-11 AM = 1-11
        if hour == 12:
            return (0, minute)
        else:
            return (hour, minute)
    elif is_pm:
        # PM: 12 PM = 12:00, 1-11 PM = 13-23
        if hour == 12:
            return (12, minute)
        else:
            return (hour + 12, minute)
    else:
        # No clear AM/PM indicator, return as-is
        _LOGGER.warning(f"Could not determine AM/PM from '{am_pm}'")
        return (hour, minute)


def parse_datetime_string(datetime_str: str) -> Dict[str, any]:
    """
    Parse natural language datetime string into time and date objects.

    Args:
        datetime_str: Natural language datetime string (e.g., "tomorrow at 3 PM")

    Returns:
        Dictionary with "time" (time object) and "date" (date object)

    Raises:
        ValueError: If parsing fails
    """
    _LOGGER.debug(f"Parsing datetime string: '{datetime_str}'")

    # Detect language
    lang = detect_language(datetime_str)
    _LOGGER.debug(f"Detected language: {lang}")

    # Get current datetime in HA's timezone
    now = dt_util.now()

    # Extract date component
    target_date = None

    # Check for relative dates
    date_offset = extract_relative_date(datetime_str, lang)
    if date_offset is not None:
        target_date = now.date() + timedelta(days=date_offset)
        _LOGGER.debug(f"Using relative date with offset {date_offset}: {target_date}")

    # Check for weekday names
    if target_date is None:
        target_date = extract_weekday(datetime_str, lang, now)
        if target_date:
            _LOGGER.debug(f"Using weekday date: {target_date}")

    # Default to today if no date specified
    if target_date is None:
        target_date = now.date()
        _LOGGER.debug(f"No date found, defaulting to today: {target_date}")

    # Extract time components
    time_components = extract_time_components(datetime_str, lang)
    if time_components is None:
        raise ValueError(f"Could not extract time from '{datetime_str}'")

    hour, minute, am_pm = time_components

    # Convert to 24-hour format
    hour_24, minute_24 = convert_to_24hour(hour, minute, am_pm, lang)

    # Create time object
    target_time = time(hour_24, minute_24, 0)

    _LOGGER.info(f"Parsed '{datetime_str}' -> date={target_date}, time={target_time} (language={lang})")

    return {
        "time": target_time,
        "date": target_date,
    }
