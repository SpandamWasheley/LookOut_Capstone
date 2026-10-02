"""An alert's timeline: detected -> status changes -> assigned -> dismissed / resolved / reopened.

Stored on the Alert as a short list of {"t": ISO time, "type", "label", "by"} and shown on the alert
detail as a small Timeline card. "Detected" is the alert's own timestamp, so it is not stored.
"""
from django.utils import timezone

MAX_EVENTS = 60


def make_event(kind, label, when=None, by=""):
    return {"t": (when or timezone.now()).isoformat(), "type": kind, "label": label, "by": by}


def appended(timeline, event):
    """The timeline with `event` added (last MAX_EVENTS kept). Does not touch the database."""
    items = list(timeline or [])
    items.append(event)
    return items[-MAX_EVENTS:]


def add(alert_id, kind, label, when=None, by=""):
    """Append an event to an alert row (used by the web API, not by the detectors)."""
    from core.models import Alert
    row = Alert.objects.filter(pk=alert_id).values_list("timeline", flat=True).first()
    Alert.objects.filter(pk=alert_id).update(timeline=appended(row, make_event(kind, label, when, by)))
