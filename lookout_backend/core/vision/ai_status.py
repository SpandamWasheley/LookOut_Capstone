"""The AI checker's DISPLAY-ONLY outputs (spec v6 section 8): the badge and the
suggested status.

Nothing here touches the official score or status. The suggested status is a
lookup rule over the AI's reply, not a calculation. It moves the official
status by at most one step, up or down, is recomputed whenever the official
status changes, and never affects notifications or priority.

Going UP is stricter than going down: a wrong downward suggestion makes the
tanod look more carefully; a wrong upward one can push an officer to act on
someone who did nothing wrong (and people over-trust an AI that says
"violation"). So up needs HIGH confidence and, for drinking and holdup, two
answers that agree; puff-only smoking is never suggested above Possible.
"""

from core.vision import scoring

HIGH = "high"
MONITORING, POSSIBLE, LIKELY = "Monitoring", "Possible", "Likely"
_ORDER = [MONITORING, POSSIBLE, LIKELY]

UNAVAILABLE = "AI context unavailable"

# Required fields per violation. A reply missing any of them is "not valid".
REQUIRED_FIELDS = {
    "drinking": ("observations", "drinking_likelihood", "table_chairs_or_seating_visible",
                 "drinking_items_visible", "scene_type", "confidence"),
    "smoking": ("observations", "smoking_item_visible", "hand_to_mouth_activity", "confidence"),
    "holdup": ("observations", "object_pointed_at_a_person", "victim_response_visible",
               "holdup_likelihood", "scene_type", "confidence"),
}

_CHOICES = {
    "drinking_likelihood": {"likely", "possible", "unlikely"},
    "holdup_likelihood": {"likely", "possible", "unlikely"},
    "scene_type": {"drinking_session", "confrontation", "other_activity", "unclear"},
    "hand_to_mouth_activity": {"smoking", "other_activity", "none", "unclear"},
    "confidence": {"high", "medium", "low"},
}

OBSERVATION_WORD_LIMIT = 20


def confidence_tier(confidence):
    """Bucket a confidence into high / medium / low (words are the contract; a
    float from an older provider is bucketed at 0.70 / 0.40)."""
    if isinstance(confidence, str):
        c = confidence.lower()
        return c if c in ("high", "medium", "low") else "low"
    if confidence is None:
        return "low"
    if confidence >= 0.70:
        return "high"
    if confidence >= 0.40:
        return "medium"
    return "low"


def validate_reply(kind, reply):
    """Return a cleaned copy of a parsed AI reply, or None if it is not valid.

    None is what turns every AI card into "AI context unavailable" while the
    official status stays exactly as it was.
    """
    required = REQUIRED_FIELDS.get(kind)
    if required is None or not isinstance(reply, dict):
        return None
    for field in required:
        if field not in reply:
            return None
    for field, allowed in _CHOICES.items():
        if field in reply and str(reply[field]).lower() not in allowed:
            return None
    out = dict(reply)
    for field in _CHOICES:
        if field in out:
            out[field] = str(out[field]).lower()
    for field in required:
        if field.endswith("_visible") or field.startswith("object_pointed") or field.startswith("victim"):
            if not isinstance(out[field], bool):
                return None
    words = str(out.get("observations", "")).split()
    out["observations"] = " ".join(words[:OBSERVATION_WORD_LIMIT])
    return out


def _step(level, delta):
    return _ORDER[max(0, min(len(_ORDER) - 1, _ORDER.index(level) + delta))]


def display_level(level):
    """Stored level name -> the word on screen."""
    return scoring.label_of(level)


def ai_badge(kind, reply):
    """The badge on the AI context card: supports / may be ordinary / unclear / unavailable."""
    r = validate_reply(kind, reply) if reply is not None else None
    if r is None:
        return {"code": "unavailable", "text": UNAVAILABLE}
    scene = r.get("scene_type")
    htm = r.get("hand_to_mouth_activity")
    if scene == "other_activity" or htm == "other_activity":
        return {"code": "ordinary", "text": "AI: may be ordinary activity"}
    supports = (
        (kind == "drinking" and (r.get("drinking_likelihood") == "likely" or scene == "drinking_session"))
        or (kind == "smoking" and htm == "smoking")
        or (kind == "holdup" and (r.get("holdup_likelihood") == "likely" or scene == "confrontation"))
    )
    if supports:
        return {"code": "supports", "text": "AI: supports this alert"}
    return {"code": "unclear", "text": "AI: unclear"}


def _supports_up(kind, r):
    """The (stricter) conditions for suggesting one step HIGHER."""
    if kind == "drinking":
        return r.get("drinking_likelihood") == "likely" and r.get("scene_type") == "drinking_session"
    if kind == "smoking":
        return r.get("hand_to_mouth_activity") == "smoking"
    if kind == "holdup":
        return (r.get("holdup_likelihood") == "likely"
                and (r.get("object_pointed_at_a_person") is True or r.get("scene_type") == "confrontation"))
    return False


_UP_REASON = {
    "drinking": "AI sees a drinking session",
    "smoking": "AI sees smoking",
    "holdup": "AI sees a confrontation",
}


def suggest_status(official_level, kind, reply, puff_only=False):
    """The Status-with-AI-context card: a dict
    {"text", "suggested", "changed", "direction"} -- display only.

    official_level  stored level name ("monitoring" | "warning" | "violation")
    reply           the parsed AI JSON, or None when the call failed / was invalid
    """
    official = display_level(official_level)
    if official not in _ORDER:
        return {"text": "", "suggested": None, "changed": False, "direction": "none"}

    r = validate_reply(kind, reply) if reply is not None else None
    if r is None:
        return {"text": UNAVAILABLE, "suggested": None, "changed": False, "direction": "none"}

    def same(text):
        return {"text": text, "suggested": official, "changed": False, "direction": "none"}

    # Only HIGH confidence moves the suggestion, up or down. Medium, low and any
    # other answer: no change.
    if r["confidence"] != HIGH:
        return same(f"No change — {official}")

    ordinary = r.get("scene_type") == "other_activity" or r.get("hand_to_mouth_activity") == "other_activity"
    if ordinary:
        if official == MONITORING:
            return same("No change — Monitoring (AI: likely ordinary activity)")
        lower = _step(official, -1)
        return {"text": f"{official} → {lower} (suggested) — AI sees ordinary activity",
                "suggested": lower, "changed": True, "direction": "down"}

    if _supports_up(kind, r):
        if official == LIKELY:
            return same("Likely — AI agrees")
        higher = _step(official, +1)
        if puff_only and higher == LIKELY:      # puff-only is never suggested above Possible
            return same(f"No change — {official}")
        return {"text": f"{official} → {higher} (suggested) — {_UP_REASON[kind]}",
                "suggested": higher, "changed": True, "direction": "up"}

    return same(f"No change — {official}")


# --- what the API serves for an alert (recomputed on every read) -------------

_FIELD_LABELS = {
    "table_chairs_or_seating_visible": "Table, chairs or seating visible",
    "drinking_items_visible": "Drinks or items set out",
    "smoking_item_visible": "Smoking item visible",
    "object_pointed_at_a_person": "Object pointed at a person",
    "victim_response_visible": "Person reacting (hands up, backing away)",
}
_CHOICE_LABELS = {
    "drinking_likelihood": "Drinking likelihood",
    "holdup_likelihood": "Holdup likelihood",
    "scene_type": "Scene",
    "hand_to_mouth_activity": "Hand-to-mouth activity",
}


def ai_checklist(kind, reply):
    """The answered fields as a checklist: [{"field", "label", "value"}], true/false
    fields first. Empty for an invalid / missing reply."""
    r = validate_reply(kind, reply) if reply is not None else None
    if r is None:
        return []
    out = []
    for field in REQUIRED_FIELDS[kind]:
        if field in _FIELD_LABELS:
            out.append({"field": field, "label": _FIELD_LABELS[field], "value": bool(r[field])})
    for field in REQUIRED_FIELDS[kind]:
        if field in _CHOICE_LABELS:
            out.append({"field": field, "label": _CHOICE_LABELS[field], "value": r[field].replace("_", " ")})
    return out


def ai_context(kind, ai, official_level, puff_only=False):
    """Everything the AI cards need, from the stored `Alert.ai` and the alert's CURRENT
    official level. Display only: the suggested status is recomputed here on every
    read, so it always follows the official status as it moves."""
    ai = ai or {}
    reply = ai.get("reply")
    valid = validate_reply(kind, reply) if (kind and reply is not None) else None
    state = ai.get("state") or ""
    if valid is None:
        shown = "pending" if state == "pending" else "unavailable"
        return {"state": shown, "badge": {"code": "unavailable", "text": UNAVAILABLE},
                "suggestion": {"text": UNAVAILABLE, "suggested": None, "changed": False, "direction": "none"},
                "observations": "", "checklist": [], "confidence": None,
                "model": ai.get("model", ""), "seconds": ai.get("seconds"), "frames": ai.get("frame_files", []),
                "system_note": ai.get("system_note", ""), "error": ai.get("error", "")}
    return {"state": "done", "badge": ai_badge(kind, valid),
            "suggestion": suggest_status(official_level, kind, valid, puff_only=puff_only),
            "observations": valid["observations"], "checklist": ai_checklist(kind, valid),
            "confidence": valid["confidence"], "model": ai.get("model", ""), "seconds": ai.get("seconds"),
            "frames": ai.get("frame_files", []), "system_note": ai.get("system_note", ""), "error": ""}
