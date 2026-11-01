"""Plain-language alert descriptions.

What an officer reads on an alert: no track ids, camera codes or scores (the status and the
evidence are shown in their own cards). Every detector builds its description here so the
wording stays the same across violation types.
"""


def _object(name, fallback):
    name = (name or "").strip().lower()
    return name or fallback


def smoking(obj=None, puffs=0):
    thing = _object(obj, "")
    if not thing or thing == "puff-only" or "movement" in thing:
        return "Hand-to-mouth smoking movement seen on a person."
    text = f"{thing.capitalize()} detected on a person"
    if puffs:
        text += ", with repeated hand-to-mouth movement"
    return text + "."


def drinking(obj=None, at_mouth=False):
    thing = _object(obj, "bottle")
    text = f"{thing.capitalize()} detected on a person"
    if at_mouth:
        text += ", held up to the mouth"
    return text + "."


def gathering(people, seconds=0):
    text = f"Drinking gathering of {people} people"
    if seconds >= 60:
        text += f", together for about {round(seconds / 60)} min"
    elif seconds >= 10:
        text += f", together for about {round(seconds)} s"
    return text + "."


def holdup(obj=None, people_near=False):
    thing = _object(obj, "weapon")
    if "," in thing:                      # several classes seen on the same person
        thing = thing.split(",")[0].strip()
    text = f"{thing.capitalize()} detected on a person"
    text += ", with a second person nearby." if people_near else "."
    return text


def parking(obj=None, seconds=0):
    thing = _object(obj, "vehicle")
    if seconds <= 0:
        return f"{thing.capitalize()} stopped in a no-parking area."
    if seconds >= 60:
        return f"{thing.capitalize()} stopped in a no-parking area for about {round(seconds / 60)} min."
    return f"{thing.capitalize()} stopped in a no-parking area for {round(seconds)} s."


def disposal(obj=None, seconds=0, away_seconds=None, window=None):
    """Improper disposal. The wording leads with LEFT BEHIND, not "bag seen" --
    that is the thing the ordinance is about, and the thing the static-object
    rule actually established.

    `away_seconds` is how long the owner has been gone, when the
    abandoned-object rule had an owner to watch. Said out loud because it is
    the strongest fact the detector holds: somebody was standing over this and
    then walked away from it, which is dumping described exactly. None means
    nobody was ever detected beside it, and the wording falls back to what the
    static rule alone can honestly claim.

    `window` is the waste-collection hours label (e.g. "4:00–7:00 AM") when the
    object was set out inside them. The alert is then only Monitoring, and this
    is the sentence that tells a reviewer why."""
    text = _disposal_base(obj, seconds, away_seconds)
    if window:
        text += f" Within collection window ({window})."
    return text


def _disposal_base(obj, seconds, away_seconds):
    thing = _object(obj, "garbage")
    if away_seconds is not None:
        return (f"{thing.capitalize()} left behind; person walked away "
                f"{round(away_seconds)} s ago, unattended for "
                f"{round(seconds)} s.")
    if seconds >= 60:
        return (f"{thing.capitalize()} left behind and unattended for about "
                f"{round(seconds / 60)} min.")
    if seconds > 0:
        return (f"{thing.capitalize()} left behind and unattended for "
                f"{round(seconds)} s.")
    return f"{thing.capitalize()} left behind and unattended."


def road_edge(side, fraction, minutes):
    return (f"Vehicle blocking the {side} edge of the road, {round(fraction * 100)}% over the line, "
            f"for about {minutes:.1f} min.")
