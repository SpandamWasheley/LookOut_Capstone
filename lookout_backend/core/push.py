"""Push notifications to officers' phones, via Expo.

Why this exists: the officer app learns about new work by polling `/alerts/`
every four seconds (AssignmentContext.tsx), and that timer only runs while the
app is open in the foreground. An officer with the app backgrounded or killed
— which is every officer, most of the time — found out about an assignment
when they next happened to open it. This is the only path that reaches a phone
that is not being looked at.

Delivery is best-effort and deliberately not authoritative. The poll remains
the source of truth for what an officer is assigned; a notification is a nudge
to go and look. So nothing here is retried into a queue, and nothing here is
allowed to fail a dispatcher's request — see `notify_assignment_async`.

Expo's service is used rather than talking to FCM directly because the app is
built with EAS, which already owns the FCM credentials; going direct would
mean a second copy of them living here.
"""
import json
import logging
import threading

import requests

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

# Expo rejects more than 100 messages in one request.
BATCH_SIZE = 100

# Short on purpose. This runs inside the request that assigns an officer when
# it is called synchronously (tests, management commands), and a dispatcher
# pressing Assign must not wait on a third party.
TIMEOUT_SECONDS = 10

# The Android notification channel the app creates at startup. It must match
# NOTIFICATION_CHANNEL_ID in officer_app, or Android files these under a
# default channel the officer never configured and may have silenced.
CHANNEL_ID = "assignments"


def messages_for(alert, officers):
    """One Expo message per registered device of each officer in `officers`.

    An officer with no device registered simply produces nothing — not an
    error. Most accounts will be in that state until they have opened the app
    once and granted permission.
    """
    from core.models import DevicePushToken

    user_ids = [o.user_id for o in officers if o.user_id]
    if not user_ids:
        return []

    # `is_active` is checked here and not only at login: deactivating an
    # account stops it signing in, but its device rows survive, so a
    # suspended or departed officer's handset would otherwise keep receiving
    # the location and evidence level of live violations.
    tokens = (
        DevicePushToken.objects
        .filter(user_id__in=user_ids, user__is_active=True)
        .values_list("token", flat=True)
    )

    from core.vision.scoring import label_of

    violation = alert.type.label if alert.type_id else "Violation"
    where = (alert.camera.zone.name if alert.camera_id and alert.camera.zone_id else "") or "your area"
    # `level_label` is a serializer field, not a model attribute — the label
    # is derived from the stored level, so derive it the same way here.
    level = label_of(alert.level) if alert.level else ""

    return [
        {
            "to": token,
            "title": f"{violation} assigned to you",
            # The officer decides whether to move on this from the lock screen,
            # so it carries the two things that decision needs: where, and how
            # strong the evidence is.
            "body": f"{where}{f' · {level}' if level else ''}",
            "sound": "default",
            # Android may otherwise hold this until the next maintenance
            # window, which for a violation in progress is useless.
            "priority": "high",
            "channelId": CHANNEL_ID,
            # Read by the tap handler to open straight onto the assignment.
            # Values are strings because this round-trips through JSON and the
            # app routes on a string id either way.
            "data": {"alertId": str(alert.pk), "code": alert.code},
        }
        for token in tokens
    ]


def deliver(messages):
    """POST the messages to Expo and drop any token it reports as dead.

    Returns the number of messages Expo accepted. Never raises: every caller
    is either a user request that must not fail over a notification, or a
    background thread with nowhere to report to.
    """
    from core.models import DevicePushToken

    if not messages:
        return 0

    accepted = 0
    for start in range(0, len(messages), BATCH_SIZE):
        batch = messages[start:start + BATCH_SIZE]
        try:
            response = requests.post(
                EXPO_PUSH_URL,
                data=json.dumps(batch),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    # Expo compresses replies by default; asking for identity
                    # keeps this readable in a tunnel capture when it misbehaves.
                    "Accept-Encoding": "identity",
                },
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            tickets = response.json().get("data", [])
        except (requests.RequestException, ValueError) as exc:
            # No signal, Expo down, or a reply that is not JSON. The officer
            # still has the 4-second poll; losing a nudge is survivable.
            logger.warning("push: batch of %d not delivered: %s", len(batch), exc)
            continue

        # Tickets come back positionally, one per message.
        dead = []
        for message, ticket in zip(batch, tickets):
            if ticket.get("status") == "ok":
                accepted += 1
                continue
            error = (ticket.get("details") or {}).get("error")
            if error == "DeviceNotRegistered":
                # The app was uninstalled, or the token was rotated. Keeping it
                # means paying for a failed send on every future assignment.
                dead.append(message["to"])
            else:
                logger.warning("push: %s rejected: %s", message["to"][:24], ticket.get("message"))

        if dead:
            removed, _ = DevicePushToken.objects.filter(token__in=dead).delete()
            logger.info("push: removed %d unregistered device(s)", removed)

    return accepted


def notify_assignment(alert, officers):
    """Tell `officers` they have been assigned `alert`. Synchronous."""
    return deliver(messages_for(alert, officers))


def notify_assignment_async(alert, officers):
    """The same, off the request thread.

    This is what the API calls. A dispatcher pressing Assign gets their
    response the moment the database is written; whether a phone in the field
    was reachable is not their problem and must never be their latency, nor a
    500 if Expo is having a bad day.

    A bare thread rather than a task queue because this system has no broker,
    and introducing one to deliver a best-effort nudge would be the larger
    mistake. The messages are built HERE, on the request thread, so the thread
    does not touch the ORM after the request's connection has gone.
    """
    messages = messages_for(alert, officers)
    if not messages:
        return

    def run():
        from django.db import connection
        try:
            deliver(messages)
        finally:
            # `deliver` prunes dead tokens, so this thread opens a connection
            # of its own. Django only cleans up connections at the end of a
            # REQUEST, and this thread is not serving one — without this the
            # connection stays checked out until the process exits.
            connection.close()

    threading.Thread(target=run, daemon=True).start()
