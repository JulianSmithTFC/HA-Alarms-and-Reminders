# custom_components/alarms_and_reminders/sentences/alarm.py
DEFAULT_SENTENCES = {
    "language": "fr",
    "intents": {
        "SetAlarm": {
            "data": [
                {
                    "sentences": [
                        "mets une alarme [pour|à] {datetime}",
                        "réveille moi à {datetime}",
                    ]
                }
            ]
        },
        "StopAlarm": {
            "data": [
                {
                    "sentences": [
                        "arrête l'alarme",
                        "éteins l'alarme",
                        "désactive l'alarme",
                        "annule l'alarme",
                    ]
                }
            ]
        },
        "SnoozeAlarm": {
            "data": [
                {
                    "sentences": [
                        "repousse l'alarme",
                        "repousse l'alarme de {minutes} minutes",
                        "donne-moi encore {minutes} minutes",
                    ]
                }
            ]
        }
    },
    "lists": {
        "datetime": {
            "type": "text",
            "values": [
                "vers {time}",
                "à {time}",
                "{time} le {date}",
                "aujourd'hui à {time}",
                "demain à {time}",
                "après-demain à {time}",
                "le {date} à {time}",
            ]
        },
        "time": {
            "type": "text",
            "values": [
                "{hour} heures {minute}",
                "{hour} heures",
                "{hour} heures du matin",
                "{hour} heures de l'après-midi",
                "{hour} heures du soir",
            ]
        },
        "hour": {
            "type": "number",
            "range": {
                "from": 1,
                "to": 23
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
                "Lundi",
                "Mardi",
                "Mercredi",
                "Jeudi",
                "Vendredi",
                "Samedi",
                "Dimanche",
                "Lundi prochain",
                "Mardi prochain",
                "Mercredi prochain",
                "Jeudi prochain",
                "Vendredi prochain",
                "Samedi prochain",
                "Dimanche prochain",
            ]
        },
        "minutes": {
            "type": "number",
            "range": {
                "from": 1,
                "to": 60
            }
        }
    }
}