# custom_components/alarms_and_reminders/sentences/alarm.py
DEFAULT_SENTENCES = {
    "language": "en",
    "intents": {
        "SetAlarm": {
            "data": [
                {
                    "sentences": [
                        "wake me [up] (for|at) {time} [(on|in)] [{date}]",
                        "wake me [up] [(on|in)] [{date}] [(for|at)] {time}",
                        "(set|add|create|make|put) [(the|an)] alarm (for|at) {time} [(on|in)] [{date}]",
                        "(set|add|create|make|put) [(the|an)] alarm [(on|in)] [{date}] [(for|at)] {time}"
                    ]
                }
            ]
        },
        "StopAlarm": {
            "data": [
                {
                    "sentences": [
                        "(stop|disable|dismiss|cancel|turn off) [the] alarm"
                    ]
                }
            ]
        },
        "SnoozeAlarm": {
            "data": [
                {
                    "sentences": [
                        "(snooze|postpone) [the] alarm",
                        "(snooze|postpone) [for] {minutes_to_snooze} minutes",
                        "(wake me [up] again in|give me) [more] {minutes_to_snooze} [more] minutes"
                    ]
                }
            ]
        }
    },
    "lists": {
        "time": {
            "type": "text",
            "values": [
                "{hour}[(:|.|)]{minute}(A.M|P.M|AM|PM)",
                "{hour}[(:|.|)]{minute} (A.M|P.M|AM|PM)",
                "{hour} (A.M|P.M|AM|PM)",
                "{hour}(A.M|P.M|AM|PM)"
            ]
        },
        "hour": {
            "type": "number",
            "range": {
                "from": 1,
                "to": 12
            }
        },
        "minute": {
            "type": "number",
            "range": {
                "from": 0,
                "to": 59,
                "step": 1
            }
        },
        "date": {
            "type": "text",
            "values": [
                "today",
                "tomorrow",
                "after tomorrow",
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
                "next Monday",
                "next Tuesday",
                "next Wednesday",
                "next Thursday",
                "next Friday",
                "next Saturday",
                "next Sunday"
            ]
        },
        "minutes_to_snooze": {
            "type": "number",
            "range": {
                "from": 1,
                "to": 60
            }
        }
    }
}
