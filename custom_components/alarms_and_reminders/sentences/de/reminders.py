DEFAULT_SENTENCES = {
    "language": "de",
    "intents": {
        "SetReminder_DE": {
            "data": [
                {
                    "sentences": [
                        "erinnere mich an {task} um {datetime}",
                        "[neue] Erinnerung an {task} um {datetime}",
                        "erstelle eine Erinnerung an {task} um {datetime}",
                    ]
                }
            ]
        },
        "StopReminder_DE": {
            "data": [
                {
                    "sentences": [
                        "Erinnerung (stoppen|löschen|beenden|abbrechen|ausschalten)",
                    ]
                }
            ]
        },
        "SnoozeReminder_DE": {
            "data": [
                {
                    "sentences": [
                        "pausiere [die] Erinnerung",
                        "erinnere mich später [nochmal]",
                        "pausiere die Erinnerung [für] {minutes} Minuten",
                        "erinnere mich [nochmal] in {minutes} Minuten",
                    ]
                }
            ]
        }
    },
    "lists": {
        # ...existing code for task, datetime, time, etc...
        "minutes": {
            "type": "number",
            "range": {
                "from": 1,
                "to": 60
            }
        }
    }
}
