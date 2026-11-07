"""Text messages to officers' phones, via UniSMS (unismsapi.com).

This is the SECOND channel to a phone, not a replacement for the first. Push
(`core/push.py`) reaches the officer app; this reaches the handset itself, and
the two fail in different ways — push needs the app installed, a granted
permission and a live data connection, while a text needs only signal. An
officer whose phone has no data, who has denied notification permission, or
who has not opened the app since a reinstall is exactly who this is for.

Which provider: `unismsapi.com`, the Philippine service. There is an unrelated
Chinese provider also called UniSMS (`unisms.apistd.com`, `uni.apistd.com`)
with a completely different API — if a future reader finds `accessKeyId` or a
`signature` field in some tutorial, that is the other one, and its parameters
do not apply here.

Three things must all be true before a single message is sent, and each is
load-bearing:

* `SystemSettings.sms_alerts_enabled` — the system-wide switch. OFF on a fresh
  install. It exists so an admin can stop every message at once (credits
  exhausted, a detector misfiring at 2am, a provider outage) without clearing
  anybody's number or their consent.
* `User.sms_alerts` — the account's own opt-in, also off by default.
* `User.phone` — non-blank. Held in E.164 by `normalize_ph_mobile`, which is
  already the `+639XXXXXXXXX` form this API wants.

Delivery is best-effort and NOT authoritative, for the same reason push is not:
the 4-second poll of `/alerts/` remains the source of truth for what an officer
is assigned, and a text is a nudge to go and look. So nothing here is queued,
and nothing here is allowed to fail a dispatcher's request — see
`notify_assignment_async`.

Unlike push, every message here costs money. That is why the gates are opt-in
rather than opt-out, why `_body` is kept inside one 160-character GSM-7 segment
(see below), and why a 429 is paced rather than hammered.

TWO TRIGGERS, split by how fast the violation needs somebody:

* INSTANT, on assignment -- holdup and illegal parking only (see
  `URGENT_TYPE_CODES`). An armed robbery or a blocked road cannot wait half an
  hour for a text, so these keep the original behaviour: `perform_update` adds
  an officer, that officer's handset buzzes.
* HALF-HOURLY DIGEST -- everything else. Smoking, drinking and improper
  disposal are persistent nuisances rather than emergencies, and one text
  saying "5 new assigned violations" costs one message where five separate
  texts cost five. The same run tells admins and dispatchers how many
  violations were detected at all, which nothing used to text them about.

The digest is driven by `manage.py sms_digest`, not by a request, and it is the
one place in this system that sends without a user having just done something.
It is still not a bulk route: there is no endpoint, no recipient list to pass
in, and who gets a message is decided entirely by role plus the same two opt-in
switches every other message here obeys.
"""
import logging
import threading
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

SEND_URL = "https://unismsapi.com/api/sms"

# Short on purpose. This runs inside the request that assigns an officer when
# called synchronously (tests, management commands), and a dispatcher pressing
# Assign must not wait on a third party.
TIMEOUT_SECONDS = 10

# The provider documents 1 request / 2 seconds per user on its bulk routes. We
# use the single-send route, but assigning two officers at once produces two
# requests back to back, so a 429 is a predictable outcome rather than an
# exotic one -- and a silently un-texted second officer is a real failure, not
# a cosmetic one. `deliver` waits out the retry the response asks for and tries
# that message ONCE more. Still no queue: one bounded wait on a background
# thread, capped so a provider claiming a long cooldown cannot park a thread.
MAX_RETRY_WAIT_SECONDS = 5

# Fallback when a 429 arrives without the header that says how long to wait.
DEFAULT_RETRY_WAIT_SECONDS = 2.5

# How long the provider's cooldown is reported in: milliseconds.
RETRY_AFTER_HEADER = "x-retry-after-ms"

# One GSM-7 segment. Exceeding this does not fail -- the provider accepts up to
# 670 characters -- it just bills as two messages, so `_body` trims to fit.
SEGMENT_CHARS = 160

# What a segment shrinks to once a message contains anything outside the GSM-7
# alphabet and has to be sent as UCS-2. See `_gsm7`.
UCS2_SEGMENT_CHARS = 70

# Violations that text the assigned officer IMMEDIATELY, by `ViolationType.code`
# (the codes seed_core writes -- "theft" is the holdup detector's).
#
# The split is about how long the violation can wait, not how certain the
# detection is. A holdup in progress and a vehicle blocking the road both need
# somebody walking towards them now; an unattended text half an hour later
# would be a record of a failure rather than a dispatch. Smoking, drinking and
# improper disposal are still violations worth attending, but nothing changes
# in the thirty minutes it takes to batch them -- the rubbish is still there.
#
# Push (core/push.py) is NOT filtered this way and still fires for every type
# on assignment: it is free and instant, so there is no reason to batch it. The
# cost is the only reason this list exists.
URGENT_TYPE_CODES = frozenset({"theft", "parking"})

# How long one digest window is. Used for the message's own wording and as the
# first-run fallback when `SystemSettings.sms_digest_sent_at` is null -- see
# `_digest_since`. `manage.py sms_digest --every` defaults to the same number,
# but the two are independent: this is what the message means by "the last 30
# min", and running the command more often than this would make that a lie.
DIGEST_INTERVAL_MINUTES = 30

# Roles that are told how many violations were DETECTED. Dispatchers and admins
# decide who attends, so a count of what is waiting is theirs; an officer is
# told what is theirs to attend instead. "both" is in each list and gets ONE
# message carrying both counts -- see `_digest_body`.
WATCHING_ROLES = frozenset({"admin", "dispatcher", "both"})
ASSIGNED_ROLES = frozenset({"officer", "both"})


def _mask(key):
    """Enough of the key to tell which one is loaded, not enough to use it."""
    if not key:
        return "<empty>"
    if len(key) <= 8:
        return "{}... (len {})".format(key[:2], len(key))
    return "{}...{} (len {})".format(key[:6], key[-4:], len(key))


# Characters that are NOT in the GSM-7 alphabet force the whole message into
# UCS-2, which cuts a segment from 160 characters to 70 -- so one stray
# typographic character doubles the bill for every text the system sends.
#
# The push body uses "·" as a separator (push.messages_for); it is deliberately
# NOT reused here for exactly that reason, and anything else that creeps in via
# a zone name or a violation label is folded down to its plain equivalent.
_GSM_SUBSTITUTIONS = {
    "·": "-",   # · middle dot, the separator push uses
    "–": "-",   # – en dash
    "—": "-",   # — em dash
    "‘": "'",   # ‘
    "’": "'",   # ’
    "“": '"',   # “
    "”": '"',   # ”
    "…": "...",  # …
    " ": " ",   # non-breaking space
}


# The GSM-7 alphabet (3GPP TS 23.038), which is what "160 characters" means.
# Anything outside it re-encodes the message as UCS-2 at 70 characters a
# segment. The basic set, plus the extension characters that cost two septets
# each -- the extension table is not modelled separately because the
# difference only matters within the last few characters of a segment, and
# `_body` trims well clear of the boundary.
GSM7_CHARSET = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
    "^{}\\[~]|€"
)


def is_gsm7(text):
    """Whether `text` fits the 160-character alphabet rather than the 70."""
    return all(char in GSM7_CHARSET for char in text)


def segments(text):
    """How many segments `text` bills as. Each one is charged separately."""
    if not text:
        return 0
    per_segment = SEGMENT_CHARS if is_gsm7(text) else UCS2_SEGMENT_CHARS
    return -(-len(text) // per_segment)


def _gsm7(text):
    """Make `text` safe to bill as GSM-7.

    Two steps, because folding alone is not enough. The substitutions above
    handle the characters that have an obvious plain equivalent and are the
    ones that actually turn up (push's "·" separator, smart quotes pasted from
    a document). But a zone name is operator-entered free text and a violation
    label is editable, so a character nobody anticipated can still reach here
    -- and ONE of them re-encodes the whole message, halving the segment and
    doubling the cost of every text the system sends.

    So anything still outside the alphabet after folding is replaced with "?".
    Losing a character from a nudge is better than silently paying twice for
    every message, and the detail is in the app regardless.
    """
    for char, plain in _GSM_SUBSTITUTIONS.items():
        text = text.replace(char, plain)
    if is_gsm7(text):
        return text
    folded = "".join(char if char in GSM7_CHARSET else "?" for char in text)
    logger.warning(
        "sms: replaced %d non-GSM-7 character(s) to keep the message in one "
        "160-character segment; a single one would have halved it to 70",
        sum(1 for char in text if char not in GSM7_CHARSET),
    )
    return folded


def _body(alert):
    """The text of the message. One segment, plain ASCII.

    It carries the same two facts the push body does -- where, and how strong
    the evidence is -- because they are what an officer decides on before
    opening anything. The alert code leads so a reply or a radio call can name
    it, and "LookOut" identifies the sender even when the number is unknown to
    the handset.

    NO LINK, and that is a provider constraint rather than a style choice: the
    shared `Unisoft` sender ID this system uses cannot send messages containing
    a URL, and a registered sender with links enabled is not available to a
    capstone project. "open the app" is the deep link.
    """
    from core.vision.scoring import label_of

    violation = alert.type.label if alert.type_id else "Violation"
    where = (alert.camera.zone.name if alert.camera_id and alert.camera.zone_id else "") or "your area"
    level = label_of(alert.level) if alert.level else ""

    text = f"LookOut {alert.code}: {violation} at {where}"
    if level:
        text += f" ({level})"
    text += ". Assigned to you - open the app for details."
    text = _gsm7(text)
    if len(text) > SEGMENT_CHARS:
        # Trim rather than split: a second segment doubles the cost of every
        # message, and the detail it would carry is in the app anyway.
        text = text[:SEGMENT_CHARS - 3].rstrip() + "..."
    return text


def is_urgent(alert):
    """Whether this violation texts the officer now or waits for the digest."""
    return bool(alert.type_id) and alert.type.code in URGENT_TYPE_CODES


def _sending_allowed():
    """The two conditions that stop every message, whatever the trigger.

    Shared by the assignment path and the digest so neither can be sent while
    the other is switched off -- the master switch has to mean all of it.
    """
    if not settings.UNISMS_API_KEY:
        logger.debug("sms: no UNISMS_API_KEY configured, nothing to send")
        return False

    from core.models import SystemSettings

    if not SystemSettings.load().sms_alerts_enabled:
        logger.debug("sms: sms_alerts_enabled is off, nothing to send")
        return False
    return True


def messages_for(alert, officers):
    """One UniSMS payload per officer in `officers` who may be texted.

    An officer with no account, no number, or no opt-in simply produces
    nothing -- not an error. That is the normal state of most accounts, since
    both switches start off.

    Returns [] when the system-wide switch is off, so the caller never has to
    check it separately, and [] for any violation that is not urgent: those are
    counted into the half-hourly digest instead (see URGENT_TYPE_CODES). The
    filter is HERE rather than in the view so that every caller inherits it --
    one place decides what is worth a text immediately.
    """
    if not _sending_allowed():
        return []

    if not is_urgent(alert):
        logger.debug(
            "sms: %s is not an urgent type, leaving it to the digest",
            alert.code or alert.pk,
        )
        return []

    # Built once, not per recipient: it is the same text for everyone on the
    # alert, and `_gsm7` logs when it has to fold a character -- which would
    # otherwise repeat identically for each officer.
    content = _body(alert)

    messages = []
    for officer in officers:
        user = officer.user if officer.user_id else None
        if user is None:
            continue
        # `is_active` is checked here and not only at login: deactivating an
        # account stops it signing in, but its number and its opt-in survive,
        # so a suspended or departed officer would otherwise keep being told
        # the location and evidence level of live violations. Same reasoning as
        # push.messages_for.
        if not (user.is_active and user.sms_alerts and user.phone):
            continue
        messages.append({
            "recipient": user.phone,
            "content": content,
            "sender_id": settings.UNISMS_SENDER_ID,
            # Echoed back on the message record and on delivery webhooks, so a
            # charge on the provider's dashboard can be traced to the alert
            # that caused it without keeping a table of our own.
            "metadata": {"alert": alert.code, "officer": officer.code},
        })
    return messages


def _hint(status_code):
    """Map the provider's status codes onto the thing to go and change."""
    hints = {
        401: ("Rejected credentials. UNISMS_API_KEY must be the secret key from "
              "unismsapi.com -> API keys; it is sent as the Basic-auth USERNAME "
              "with an empty password."),
        404: "Endpoint not found -- SEND_URL is wrong.",
        422: ("The provider understood the request and refused its content -- "
              "usually an unregistered UNISMS_SENDER_ID, or a recipient that is "
              "not a Philippine mobile number. A sender ID must be registered "
              "under unismsapi.com -> Sender IDs before it will send."),
        429: "Rate-limited: too many sends too quickly.",
    }
    hint = hints.get(status_code)
    if hint is None and status_code >= 500:
        hint = "Provider-side failure. Nothing to fix locally; retry."
    return "\n    -> " + hint if hint else ""


def _retry_wait(response):
    """How long the provider's 429 asks us to wait, in seconds and bounded."""
    raw = response.headers.get(RETRY_AFTER_HEADER)
    try:
        seconds = float(raw) / 1000.0
    except (TypeError, ValueError):
        seconds = DEFAULT_RETRY_WAIT_SECONDS
    return max(0.0, min(seconds, MAX_RETRY_WAIT_SECONDS))


def deliver(messages):
    """POST each message to UniSMS. Returns the number accepted.

    Never raises: every caller is either a user request that must not fail over
    a notification, or a background thread with nowhere to report to.

    This is the seam the test suite replaces, so nothing in the suite touches
    the network.
    """
    if not messages:
        return 0

    logger.info(
        "sms: sending %d message(s) | api_key=%s | sender_id=%s",
        len(messages), _mask(settings.UNISMS_API_KEY),
        settings.UNISMS_SENDER_ID or "<empty>",
    )

    accepted = 0
    try:
        # One session for the batch: these are TLS handshakes to the same host,
        # and the handshake costs more than the request.
        with requests.Session() as session:
            for message in messages:
                if _send_one(session, message):
                    accepted += 1
    except Exception:
        # `_send_one` already absorbs everything the network does to a request.
        # This outer guard is for the rest: building the session, a provider
        # reply malformed in a way requests itself objects to, an exhausted
        # file-descriptor table. The promise this function makes to its callers
        # is that it does not raise -- one of them is a dispatcher's request and
        # the other is a bare thread with nowhere to report to -- and that
        # promise has to hold for reasons the send loop never sees.
        logger.exception("sms: send loop failed after %d accepted", accepted)
    logger.info("sms: %d/%d accepted", accepted, len(messages))
    return accepted


def _send_one(session, message, allow_retry=True):
    """One message. True if the provider accepted it."""
    # The recipient is masked in logs: a log is a less protected place than the
    # database, and the last four digits are enough to tell two officers apart
    # while chasing a failure.
    who = message["recipient"][-4:] if message.get("recipient") else "????"

    started = time.monotonic()
    try:
        response = session.post(
            SEND_URL,
            json=message,
            timeout=TIMEOUT_SECONDS,
            # The secret key is the Basic-auth username; the password is empty.
            auth=(settings.UNISMS_API_KEY, ""),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    except requests.Timeout:
        logger.warning(
            "sms: TIMED OUT after %.1fs sending to ...%s. The officer still has "
            "the app poll and the push nudge; losing a text is survivable.",
            time.monotonic() - started, who,
        )
        return False
    except requests.RequestException as exc:
        # No signal, provider down, DNS. Same reasoning as the timeout above.
        logger.warning("sms: request FAILED sending to ...%s: %s", who, exc)
        return False

    elapsed = time.monotonic() - started

    if response.status_code in (200, 201, 202):
        try:
            record = (response.json() or {}).get("message") or {}
        except ValueError:
            record = {}
        logger.info(
            "sms: ACCEPTED in %.2fs (HTTP %s) to=...%s reference_id=%s status=%s",
            elapsed, response.status_code, who,
            record.get("reference_id") or "<none>", record.get("status") or "<none>",
        )
        return True

    if response.status_code == 429 and allow_retry:
        wait = _retry_wait(response)
        logger.info(
            "sms: rate-limited sending to ...%s, waiting %.1fs for one retry", who, wait,
        )
        time.sleep(wait)
        return _send_one(session, message, allow_retry=False)

    logger.error(
        "sms: REJECTED in %.2fs: HTTP %s to=...%s body=%s%s",
        elapsed, response.status_code, who, response.text[:500],
        _hint(response.status_code),
    )
    return False


def notify_assignment(alert, officers):
    """Text `officers` that they have been assigned `alert`. Synchronous."""
    return deliver(messages_for(alert, officers))


def notify_assignment_async(alert, officers):
    """The same, off the request thread.

    This is what the API calls. A dispatcher pressing Assign gets their
    response the moment the database is written; whether a handset in the field
    was reachable is not their problem and must never be their latency, nor a
    500 because the provider is having a bad day.

    A bare thread rather than a task queue because this system has no broker,
    and introducing one to deliver a best-effort nudge would be the larger
    mistake. The messages are built HERE, on the request thread, so the thread
    does not touch the ORM after the request's connection has gone -- and that
    is also where both opt-in gates are read, so a decision is never made
    against a settings row that changed while the thread was waiting.
    """
    messages = messages_for(alert, officers)
    if not messages:
        return

    def run():
        from django.db import connection
        try:
            deliver(messages)
        finally:
            # `deliver` touches no models, so normally there is no connection
            # to close. It is closed anyway because Django only cleans up
            # connections at the end of a REQUEST and this thread is not
            # serving one -- so anything that later grows an ORM call in here
            # cannot leak a connection for the life of the process.
            connection.close()

    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------------------
# The half-hourly digest
#
# Everything below is driven by `manage.py sms_digest` rather than by a
# request. It is the only sender here that nobody pressed a button for, which
# is why it counts rather than describes: an officer is told HOW MANY
# violations are theirs to attend, not what each one was. One message of a
# count costs one message; five violations described cost five.
# ---------------------------------------------------------------------------

def _digest_since(settings_row, now):
    """The moment this run counts detections from.

    The stored watermark when there is one, so a run that was skipped reports
    what it missed rather than losing it. One interval back when there is not,
    because the alternative on a fresh install -- or the first run after an
    admin turns the switch on -- is to text somebody a count of every
    violation the database has ever held.
    """
    if settings_row.sms_digest_sent_at:
        return settings_row.sms_digest_sent_at
    return now - timedelta(minutes=DIGEST_INTERVAL_MINUTES)


def detected_since(since):
    """How many violations were filed after `since`, as a person would count them.

    Two exclusions, both of which would otherwise make the number a lie:

    * MONITORING events. They are a quiet watchlist (spec v6) that by
      definition notifies nobody -- AlertViewSet.get_queryset keeps them out of
      the ordinary alert list for the same reason. Counting them here would
      text a dispatcher about 30 violations they cannot find on the dashboard.
      Split on `peak_level` with a fallback to `level`, exactly as the view
      does, so the two agree about what is on the watchlist.
    * Alerts from a "-TEST" camera. Those come from footage uploaded to measure
      the detectors' accuracy, so they are not events in the barangay at all --
      and an evaluation run over a long clip can file dozens in a minute. The
      alerts stay visible and filterable in the dashboard (see CLAUDE.md); what
      they must not do is spend credits or wake anybody.
    """
    from core.models import Alert

    quiet = Q(peak_level="monitoring") | Q(peak_level="", level="monitoring")
    return (
        Alert.objects
        # `timestamp`, because Alert has no created_at: it is the wall clock at
        # which the detector filed the event, which is exactly what "detected
        # in the last 30 min" means. For a live camera it is the moment the
        # violation was seen; for a run over an uploaded clip it is still wall
        # clock (`footage_offset` carries the position in the video), and those
        # alerts are excluded below anyway.
        .filter(timestamp__gt=since)
        .exclude(quiet)
        .exclude(camera__code__endswith="-TEST")
        .count()
    )


def _plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _digest_body(detected, assigned):
    """The text of one digest. One segment, plain ASCII, and no link.

    Three shapes, because a "both" account is a real role in this system and
    deserves ONE message rather than two: what was detected, what is theirs, or
    both in one line. The interval is named so the number has a meaning -- "30
    new violations" without it could be today's or this month's.

    No link, same provider constraint as `_body`: the shared Unisoft sender ID
    cannot send a message containing a URL.
    """
    if detected and assigned:
        body = f"{_plural(detected, 'new violation')} detected, {assigned} assigned to you"
    elif detected:
        body = f"{_plural(detected, 'new violation')} detected"
    else:
        body = f"{_plural(assigned, 'new violation')} assigned to you"

    text = _gsm7(
        f"LookOut: {body} in the last {DIGEST_INTERVAL_MINUTES} min. "
        "Open the app for details."
    )
    if len(text) > SEGMENT_CHARS:
        text = text[:SEGMENT_CHARS - 3].rstrip() + "..."
    return text


def digest_plan(now=None):
    """Work out who gets a digest, what it says, and what to mark afterwards.

    Returns a dict:
        since     -- the watermark this run counted from
        now       -- the moment the run was taken (the next watermark)
        detected  -- how many violations were filed in the window
        entries   -- [{user, message, notice_ids, assigned}] one per recipient

    Separated from `send_digest` because it does ALL of the database work: the
    counts, the opt-in checks and the recipient list are read here, in one
    place, before anything is handed to the network. That is the same
    discipline `notify_assignment_async` follows, and it is what makes this
    testable without sending -- a test can assert on the plan.

    A recipient with nothing to report produces no entry. That is the whole
    reason this returns a plan rather than texting everybody on a timer: a
    quiet half hour must cost nothing, or the system bills 48 messages a day
    per account to say "0".
    """
    from core.models import AssignmentNotice, SystemSettings, User

    now = now or timezone.now()

    if not _sending_allowed():
        return {"since": None, "now": now, "detected": 0, "entries": []}

    since = _digest_since(SystemSettings.load(), now)
    detected = detected_since(since)

    # The same three gates every other message here obeys: active account, own
    # opt-in, a number on file. `is_active` is checked for the same reason as in
    # `messages_for` -- deactivating an account stops it signing in but leaves
    # its number and its consent intact.
    recipients = list(
        User.objects
        .filter(is_active=True, sms_alerts=True)
        .exclude(phone="")
        .select_related("officer_profile")
    )

    # Every unreported assignment belonging to these accounts, in one query
    # rather than one per officer. `texted_at` is null for exactly the rows no
    # digest has counted yet, however long ago they were filed.
    pending = {}
    if recipients:
        rows = (
            AssignmentNotice.objects
            .filter(texted_at__isnull=True, officer__user__in=recipients)
            .values_list("officer__user_id", "pk")
        )
        for user_id, notice_id in rows:
            pending.setdefault(user_id, []).append(notice_id)

    entries = []
    for user in recipients:
        watching = user.role in WATCHING_ROLES
        # An "officer" role with no Officer row cannot be assigned anything, so
        # it has no assignments to be told about -- the dict lookup simply
        # misses, which is the correct answer rather than an error.
        notice_ids = pending.get(user.pk, []) if user.role in ASSIGNED_ROLES else []

        mine = len(notice_ids)
        theirs = detected if watching else 0
        if not (mine or theirs):
            continue

        entries.append({
            "user": user,
            "assigned": mine,
            "notice_ids": notice_ids,
            "message": {
                "recipient": user.phone,
                "content": _digest_body(theirs, mine),
                "sender_id": settings.UNISMS_SENDER_ID,
                # Echoed back on the provider's record, so a charge on their
                # dashboard can be traced to the run that caused it.
                "metadata": {
                    "digest": now.isoformat(),
                    "detected": theirs,
                    "assigned": mine,
                },
            },
        })

    return {"since": since, "now": now, "detected": detected, "entries": entries}


def send_digest(now=None):
    """Send one round of digests and record that it happened. Synchronous.

    Returns the plan, with `sent` added: how many messages the provider
    accepted. `manage.py sms_digest` prints it.

    Each recipient is delivered SEPARATELY, rather than as one batch, because
    the bookkeeping needs to know who was actually reached: an officer's
    assignments are marked as reported only if their own message was accepted,
    so a text that failed is counted again next time instead of vanishing. At
    one barangay's worth of recipients twice an hour, the cost of a separate
    connection each is not worth trading that away for.

    The WATERMARK moves either way, even when nothing was sent and even when
    nobody is opted in. "The last 30 min" has to mean the last 30 minutes, and
    a watermark that only advanced on success would grow without bound until it
    one day texted somebody a count of several thousand. The detected count is
    situational awareness that the dashboard shows properly anyway; an
    officer's own assignment count is the part that is protected.
    """
    from core.models import AssignmentNotice, SystemSettings

    plan = digest_plan(now)
    plan["sent"] = 0

    for entry in plan["entries"]:
        if not deliver([entry["message"]]):
            continue
        plan["sent"] += 1
        if entry["notice_ids"]:
            AssignmentNotice.objects.filter(pk__in=entry["notice_ids"]).update(
                texted_at=plan["now"])

    # Only when the run really examined a window. `digest_plan` returns
    # since=None when sending is switched off, and a run that sent nothing
    # because of that must not consume a window -- otherwise turning the master
    # switch on loses whatever happened while it was off.
    if plan["since"] is not None:
        SystemSettings.objects.filter(pk=1).update(sms_digest_sent_at=plan["now"])

    logger.info(
        "sms: digest %d/%d sent | %d detected since %s",
        plan["sent"], len(plan["entries"]), plan["detected"], plan["since"],
    )
    return plan
