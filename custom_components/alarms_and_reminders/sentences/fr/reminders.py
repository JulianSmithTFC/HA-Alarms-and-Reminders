
# custom_components/alarms_and_reminders/sentences/fr/reminders.py
DEFAULT_SENTENCES = {
    "language": "fr",
    "intents": {
        "SetReminder_FR": {
            "data": [
                {
                    "sentences": [
                        "rappelle moi de {task} à {datetime}",
                        "mets un rappel de {task} à {datetime}",
                    ]
                }
            ]
        },
        "StopReminder_FR": {
            "data": [
                {
                    "sentences": [
                        "annules le rappel",
                        "enlèves le rappel",
                        "désactives le rappel",
                    ]
                }
            ]
        },
        "SnoozeReminder_FR": {
            "data": [
                {
                    "sentences": [
                        "repousses le rappel",
                        "repousses le rappel de {minutes} minutes",
                    ]
                }
            ]
        }
    },
    "lists": {    
        "minutes": {
            "type": "number",
            "range": {
                "from": 1,
                "to": 60
            }
        }
    }
}

