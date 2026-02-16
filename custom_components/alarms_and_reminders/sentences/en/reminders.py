DEFAULT_SENTENCES = {
    "language": "en",
    "intents": {
        "SetReminder": {
            "data": [
                {
                    "sentences": [
                        "remind me (to|for|about) {task} at {time} [(on|in)] [{date}]",
                        "remind me (to|for|about) {task} [(on|in)] [{date}]  at {time}",
                        "(set|add|create|make|put) [a] reminder (for|to|about) {task} (at|for) {time} [(on|in)] [{date}]",
                        "(set|add|create|make|put) [a] reminder (for|to|about) {task} [(on|in)] [{date}] (at|for) {time}"
                    ]
                }
            ]
        },
        "StopReminder": {
            "data": [
                {
                    "sentences": [
                        "stop [the] reminder",
                        "turn off [the] reminder",
                        "disable [the] reminder",
                        "cancel [the] reminder",
                        "dismiss [the] reminder"
                    ]
                }
            ]
        },
        "SnoozeReminder": {
            "data": [
                {
                    "sentences": [
                        "(snooze|postpone) [the] reminder",
                        "(snooze|postpone) [the] reminder [for] {minutes} [more] minutes",
                        "(remind me again in|give me) [more] {minutes} [more] minutes"
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
        "minutes": {
            "type": "number",
            "range": {
                "from": 1,
                "to": 60
            }
        },
        "task": {
            "wildcard": True
        }
    }
}
