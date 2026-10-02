import re
from datetime import time

from django.contrib.auth.models import AbstractUser
from django.db import models

from core.constants import ZAMBOANGA_BARANGAYS
from core.vision.scoring import LEVEL_CHOICES as SCORE_LEVEL_CHOICES

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(first_name, middle_name, last_name):
    """lowercase, strip, collapse whitespace, strip punctuation — suffix is
    deliberately excluded so 'Dela Cruz, Juan Jr.' and '... Sr.' still
    normalize to the same person for exact-match lookups."""
    raw = " ".join(part for part in (first_name, middle_name, last_name) if part)
    no_punct = _PUNCTUATION_RE.sub("", raw)
    return _WHITESPACE_RE.sub(" ", no_punct).strip().lower()


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        DISPATCHER = "dispatcher", "Dispatcher"
        OFFICER = "officer", "Officer"
        BOTH = "both", "Officer & Dispatcher"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OFFICER)
    display_name = models.CharField(max_length=150, blank=True)
    must_change_password = models.BooleanField(default=False)

    def __str__(self):
        return self.display_name or self.username


class EmailVerificationCode(models.Model):
    email = models.EmailField()
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    verified = models.BooleanField(default=False)
    used = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email} - {self.code}"


def _next_code(model, prefix, width=2, field="code"):
    last = model.objects.order_by(f"-{field}").first()
    if last:
        try:
            n = int(getattr(last, field).split("-")[-1]) + 1
        except (ValueError, IndexError):
            n = model.objects.count() + 1
    else:
        n = 1
    return f"{prefix}-{str(n).zfill(width)}"


class Zone(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ViolationType(models.Model):
    code = models.SlugField(max_length=30, unique=True)
    label = models.CharField(max_length=100)
    color = models.CharField(max_length=7, default="#64748b")
    icon = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return self.label


class Camera(models.Model):
    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        DEGRADED = "degraded", "Degraded"
        OFFLINE = "offline", "Offline"

    code = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    zone = models.ForeignKey(Zone, on_delete=models.SET_NULL, null=True, related_name="cameras")
    # Free-text street address, set per camera from the Live Feeds page.
    #
    # Separate from `zone`, which is a barangay subdivision and too coarse to
    # dispatch against -- "Zone 3" does not tell a tanod which street to walk
    # to. An alert inherits this, so where the camera IS becomes where the
    # violation HAPPENED, which is the only location the system can honestly
    # claim: it knows which camera saw the event, not where in the frame the
    # person stood.
    address = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ONLINE)
    fps = models.PositiveSmallIntegerField(default=0)
    last_motion_at = models.DateTimeField(null=True, blank=True)
    image_url = models.URLField(blank=True)
    # RTSP URL for a live camera (e.g. rtsp://user:pass@192.168.1.64:554/...).
    # Browsers cannot play RTSP, so the dashboard never receives this directly —
    # the /cameras/{id}/snapshot/ endpoint reads it server-side, pulls a JPEG
    # from the camera, and streams that to the grid. Credentials therefore stay
    # on the backend and are never exposed by the serializer.
    stream_url = models.CharField(max_length=500, blank=True)
    # Road-edge lines for parking-obstruction monitoring: {"left": {"points":
    # [[x,y],...], "side": 1}, "right": {...}}, in the pixel coordinates of
    # whatever frame size they were drawn against (edges_width/edges_height).
    # Empty dict means no obstruction config — watch_parking falls back to
    # plain dwell detection. watch_parking scales these to the camera's actual
    # capture resolution at read time, since that resolution is not
    # guaranteed to match what the edges were drawn on.
    edges = models.JSONField(default=dict, blank=True)
    edges_width = models.PositiveIntegerField(null=True, blank=True)
    edges_height = models.PositiveIntegerField(null=True, blank=True)
    obstruction_pct = models.PositiveSmallIntegerField(default=50)
    obstruction_minutes = models.FloatField(default=5.0)

    class Meta:
        ordering = ["code"]

    @property
    def is_live(self):
        return bool(self.stream_url)

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_code(Camera, "CAM")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Officer(models.Model):
    class Status(models.TextChoices):
        RESPONDING = "responding", "Responding"
        ON_DUTY = "on-duty", "On Duty"
        OFF_DUTY = "off-duty", "Off Duty"

    code = models.CharField(max_length=20, unique=True, blank=True)
    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="officer_profile")
    name = models.CharField(max_length=150)
    badge = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ON_DUTY)
    location = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    shift = models.CharField(max_length=50, blank=True)
    joined_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["code"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_code(Officer, "OFC")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Alert(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISPATCHED = "dispatched", "Dispatched"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    code = models.CharField(max_length=20, unique=True, blank=True)
    type = models.ForeignKey(ViolationType, on_delete=models.PROTECT, related_name="alerts")
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    camera = models.ForeignKey(Camera, on_delete=models.SET_NULL, null=True, related_name="alerts")
    timestamp = models.DateTimeField()
    confidence = models.FloatField()
    description = models.TextField(blank=True)
    image_url = models.URLField(blank=True)
    # ~10s evidence clip with detection boxes drawn, written by the watchers'
    # ClipRecorder. Blank for older alerts / still-only detectors.
    video_url = models.URLField(blank=True)
    # Unannotated evidence clip at full source frame rate/resolution — cut
    # directly from the source file, or from a live raw-frame buffer for a
    # stream source. Blank for alerts created before this field existed, or
    # when the cut/buffer save failed.
    raw_video_url = models.URLField(blank=True)
    officers_assigned = models.ManyToManyField(Officer, blank=True, related_name="alerts")
    suspect = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)
    # --- weighted-sum scoring (core/vision/scoring.py) ----------------------
    # `confidence` above is now the FINAL score for detectors that have been
    # ported to the scoring model — an estimate of "how likely is this a
    # violation", not "how sure is YOLO that this is a bottle". `level` is the
    # band that score fell into, and is what the dashboard should badge on.
    # Blank for detectors still on the old hard-gate chain.
    level = models.CharField(max_length=10, blank=True, choices=SCORE_LEVEL_CHOICES)
    # The full cue vector: which indicators fired, their weights, the
    # multipliers, and anything suppressed as redundant or abstained. Kept even
    # for alerts that barely cleared the bar, because this is the training data
    # calibrate_weights fits the final weights against — discarding it would
    # discard every labelled example.
    cues = models.JSONField(default=dict, blank=True)
    # Set by an officer/admin reviewing the alert: was this a real violation?
    # Null until reviewed. This is the LABEL for calibration — without it the
    # cue vectors above have no target to fit.
    reviewed_valid = models.BooleanField(null=True, blank=True)
    # WHO reviewed the footage and closed the alert, and when — the officer or
    # dispatcher who dismissed it or marked it resolved. A record saying a
    # violation was dismissed, without saying who dismissed it, is not an
    # audit trail: that is a decision not to act on a reported violation, and
    # somebody has to own it.
    #
    # Stamped server-side from the requesting user on the status transition
    # (see AlertViewSet.perform_update) and never accepted from the client, or
    # a reviewer could attribute their own call to someone else. Cleared again
    # if the alert is reopened.
    reviewed_by = models.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_alerts",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # What the OBJECT DETECTOR was sure of: the YOLO confidence of the box
    # that anchored this alert, 0-1. Kept separate from `confidence` above,
    # which is the violation likelihood across every indicator.
    #
    # They answer different questions and were being conflated on the violation
    # card. "How sure is the model that this is a bottle?" is not "how likely is
    # it that this is a drinking violation?" -- a crisp bottle detection on a
    # man walking home is high on the first and low on the second, and that gap
    # is the whole point of the scoring layer.
    object_confidence = models.FloatField(null=True, blank=True)

    # --- VLM verification (core/vision/vlm.py) ------------------------------
    # Verdict on the evidence crop: yes / no / unclear, or blank when the VLM is
    # disabled or the call failed. NEVER gates alert creation -- a VLM that is
    # down must not stop a security system from alerting.
    vlm_verdict = models.CharField(max_length=10, blank=True)
    # The model's own certainty in its verdict, 0-1. Distinct from
    # `confidence`, which is the violation likelihood across all indicators.
    vlm_confidence = models.FloatField(null=True, blank=True)
    # One sentence, shown on the violation card. The main reason the VLM is
    # worth its latency: a human-readable justification instead of a number.
    vlm_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_code(Alert, "ALT", width=4)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class Violator(models.Model):
    class Suffix(models.TextChoices):
        NONE = "", "—"
        JR = "Jr.", "Jr."
        SR = "Sr.", "Sr."
        II = "II", "II"
        III = "III", "III"
        IV = "IV", "IV"

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    suffix = models.CharField(max_length=5, choices=Suffix.choices, blank=True)
    # Auto-generated in save() from first+middle+last (suffix excluded) — the
    # exact-match half of violator search, and how repeat citations for the
    # same typed name resolve to one record without a fuzzy pass.
    normalized_name = models.CharField(max_length=310, db_index=True, editable=False)
    # Prior full names this record has absorbed via merge() — see
    # ViolatorViewSet.merge. Plain strings, not FKs: the loser row is gone.
    aliases = models.JSONField(default=list, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    # Bumped on every new citation for this violator (see
    # CitationViewSet.perform_create) — NOT auto_now, since an unrelated edit
    # (e.g. a merge appending an alias) shouldn't count as a new sighting.
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_name(self.first_name, self.middle_name, self.last_name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.last_name}, {self.first_name} {self.suffix}".strip()


class Citation(models.Model):
    class Barangay(models.TextChoices):
        TETUAN = "TETUAN", "Tetuan"

    alert = models.ForeignKey(Alert, on_delete=models.SET_NULL, null=True, blank=True, related_name="citations")
    violator = models.ForeignKey(Violator, on_delete=models.PROTECT, related_name="citations")
    # Snapshot of exactly what was typed on THIS citation — kept even after
    # `violator` is set/merged, so a later merge (which can rewrite which
    # Violator a citation points to) never loses what was actually written
    # on the paper/screen at the time.
    first_name_entered = models.CharField(max_length=100)
    last_name_entered = models.CharField(max_length=100)
    middle_name_entered = models.CharField(max_length=100, blank=True)
    suffix_entered = models.CharField(max_length=5, choices=Violator.Suffix.choices, blank=True)
    officer = models.ForeignKey(Officer, on_delete=models.PROTECT, related_name="citations")
    barangay_of_violation = models.CharField(max_length=30, choices=Barangay.choices, default=Barangay.TETUAN)
    violator_barangay = models.CharField(max_length=50, choices=ZAMBOANGA_BARANGAYS)
    violations = models.ManyToManyField(ViolationType, related_name="citations")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="citations")
    created_at = models.DateTimeField(auto_now_add=True)
    # Client-generated at citation-creation time (not send time) by the mobile
    # app, so a retried/queued submission after a timeout re-sends the same
    # key instead of minting a new one — see CitationViewSet.perform_create,
    # which treats a repeat client_uuid as "already filed" rather than
    # creating a duplicate. Null for the web dashboard, which doesn't queue.
    client_uuid = models.UUIDField(null=True, blank=True, unique=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Citation - {self.last_name_entered}, {self.first_name_entered} ({self.created_at:%Y-%m-%d})"


class DetectionJob(models.Model):
    """An admin-triggered test run of one detector (watch_smoking etc.) against
    an uploaded video file, launched as a plain subprocess (see views.py) rather
    than through a task queue — a testing/demo tool, not a production pipeline.
    Alerts it produces are ordinary Alert rows, tagged onto a dedicated
    "<CODE>-TEST" camera so they're distinguishable from live-camera alerts.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    violation_type = models.CharField(max_length=20)      # key into views.DETECTION_COMMANDS
    source_filename = models.CharField(max_length=255)    # original upload name, for display
    source_path = models.CharField(max_length=500)        # saved temp path (or a live camera's
                                                            # stream_url) — subprocess arg + cleanup
    # Set when this job runs against a live camera's stream_url instead of an
    # uploaded file — never cleaned up as a temp file (see _watch_detection_job),
    # and alerts land on this real camera rather than a synthetic "-TEST" one.
    camera = models.ForeignKey("Camera", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="detection_jobs")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    pid = models.IntegerField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # Tail of the subprocess's combined stdout/stderr log — only set on failure.
    error = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="detection_jobs")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.violation_type} job #{self.id} ({self.status})"


class SystemSettings(models.Model):
    parking_enabled = models.BooleanField(default=True)
    # YOLO detection confidence as a 0-100 percent (watch_parking divides by 100).
    parking_confidence = models.PositiveSmallIntegerField(default=35)
    # Seconds a vehicle must stay put before it counts as illegally parked.
    parking_dwell = models.PositiveSmallIntegerField(default=60)
    # Pixels a vehicle may drift and still count as "stationary" (resets the
    # dwell timer if exceeded, so a car merely driving through never alerts).
    parking_move_tolerance = models.PositiveSmallIntegerField(default=40)

    smoking_enabled = models.BooleanField(default=True)
    # Detection confidence as a 0-100 percent (watch_smoking divides by 100).
    # The custom smoking model scores genuine cigarette/smoking matches lower
    # than a percentage intuition (~0.3-0.9), so this default is calibrated to
    # that scale rather than a generic "high-confidence" bar.
    smoking_confidence = models.PositiveSmallIntegerField(default=30)
    # Seconds smoking must be seen continuously before it counts as a violation
    # (filters one-frame false positives). Smoking is transient, so this is
    # short compared to the parking dwell.
    smoking_dwell = models.PositiveSmallIntegerField(default=3)

    thief_enabled = models.BooleanField(default=True)
    # Detection confidence as a 0-100 percent (watch_thief divides by 100).
    # Like the smoking model, the custom thief model scores genuine matches on
    # the raw YOLO scale (~0.3-0.9), so this is calibrated to that, not a
    # "high-confidence percent" intuition.
    thief_confidence = models.PositiveSmallIntegerField(default=30)
    # Seconds a gun/knife/robbery detection must persist before alerting
    # (filters one-frame false positives). Kept short: unlike parking, an armed
    # robbery should alert fast.
    thief_dwell = models.PositiveSmallIntegerField(default=3)

    drinking_enabled = models.BooleanField(default=True)
    # Detection confidence as a 0-100 percent (watch_drinking divides by 100).
    # Same raw-YOLO scale as the smoking/thief models, not a "percent sure" bar.
    drinking_confidence = models.PositiveSmallIntegerField(default=35)
    # Seconds a bottle must stay with a person before it counts as public
    # drinking. Longer than smoking's: a bottle is frequently present without
    # being consumed (carried home, on a table, held by a bystander), so the
    # dwell is doing more work here than it does for a cigarette.
    drinking_dwell = models.PositiveSmallIntegerField(default=8)
    # Public-drinking ordinances are usually scoped by hour, the way curfew is.
    # Off by default so enabling the detector doesn't silently stop alerting
    # during the day; turn it on and set the window to match the local ordinance.
    # Kept as a hard gate for operators who need one, but it is OFF by default
    # and the scoring model no longer depends on it: the time band is now a
    # SCORED CUE worth scoring.DRINKING_WEIGHTS["time_band"], so drinking
    # outside the window loses points instead of being silently dropped.
    drinking_hours_enabled = models.BooleanField(default=False)
    # 16:00-24:00, the high band from Omamalin (2022) and the Thai/W. Australia
    # ED series — tagay is an afternoon-into-evening activity. The previous
    # 22:00-05:00 was copied from the curfew window and matched no source.
    drinking_start = models.TimeField(default=time(16, 0))
    drinking_end = models.TimeField(default=time(0, 0))

    # Gathering ("inuman") detection: a second, independent path to an alert
    # alongside the per-person one above. Near the camera an individual's
    # bottle is verifiable on its own; at range it isn't, but a sustained
    # gathering still is — so this scales the evidence standard with what the
    # camera can actually establish, instead of one fixed per-person rule.
    drinking_min_group = models.PositiveSmallIntegerField(default=2)
    # 600s (10 minutes), the low end of the 10-15 minute range implied by
    # Omamalin's (2022) 3-5 hour tagay sessions. Raised from a 25s testing
    # value: at 25s a few people briefly standing near each other registered as
    # a drinking session.
    #
    # NOTE FOR CALIBRATION: sub-minute test clips can no longer complete a
    # gathering. Use real long-form footage, or lower this in Settings for the
    # duration of a clip-based test run.
    drinking_group_duration = models.PositiveSmallIntegerField(default=600)

    # A bottle merely HELD (not raised to the mouth, or no face resolvable to
    # check) still counts as evidence, but only after this much longer than
    # drinking_dwell (which now means the AT-MOUTH requirement specifically —
    # see watch_drinking._dwell_for). Replaces the old fixed 2x-of-dwell
    # scaling: possession alone is much weaker evidence of ACTUAL drinking
    # than a raised bottle is, so it needs its own, independently-tunable
    # bar rather than being pegged to whatever drinking_dwell happens to be.
    drinking_held_dwell = models.PositiveSmallIntegerField(default=24)
    # A gathering's bottle evidence (Cluster.evidence) must have been seen
    # within this many seconds, or it no longer counts — a cluster must not
    # stay armed indefinitely on one old sighting (Phase B3). Kept short:
    # null-footage calibration produced Bottle false positives up to 0.87
    # confidence, so a long eligibility window per spurious detection would
    # just reintroduce the sticky-evidence problem this exists to close.
    # Raise it in Settings if real gatherings start getting missed between
    # bottle sightings.
    drinking_evidence_max_age = models.PositiveSmallIntegerField(default=12)
    # Distance from the mouth, in FACE WIDTHS, within which a bottle counts
    # as raised (watch_drinking._posture). Larger than smoking's equivalent:
    # a bottle is held further from the face and is a much larger object.
    drinking_mouth_proximity = models.FloatField(default=3.0)
    # Radius (as a fraction of person-box height) within which a later alert
    # is considered "the same spot" for cooldown purposes, regardless of
    # which track id it came from (watch_drinking._cooldown_blocks) — keeps
    # the cooldown pinned to a place in the frame instead of a track id that
    # can churn.
    drinking_cooldown_center_dist = models.FloatField(default=1.5)

    # --- VLM verification ---------------------------------------------------
    # Second-stage vision-language check on the evidence crop at alert time
    # (core/vision/vlm.py).
    #
    # ON by default, because the safe behaviour is already the DEFAULT one: with
    # no credentials configured, build_verifier hands back an inert verifier and
    # every detector runs exactly as it did before. So "enabled" here means "use
    # it if it is usable", not "require it" — a fresh install with no API key
    # behaves identically to having this switched off, and a deployment that
    # adds a key gets verification on the next run with no settings visit.
    #
    # Set it to False to keep the VLM off even when a key IS present — for a
    # metered connection, a privacy constraint, or an ablation run.
    #
    # It raises PRECISION, not recall — it only ever sees crops the detectors
    # already produced, so it cannot find a violation YOLO missed. What it buys
    # is the context geometry can't see (is this inuman or a family lunch? a
    # holdup or a fish vendor?) and a readable reason on the alert card.
    vlm_enabled = models.BooleanField(default=True)
    vlm_provider = models.CharField(max_length=20, default="ollama")
    # Where the local Ollama server listens. v3 §8 runs the checker on this
    # machine, so there is no API key and no outbound request -- what used to
    # be "is the credential valid" is now "is the server up and is the model
    # pulled", which build_verifier checks once at startup.
    vlm_endpoint = models.CharField(max_length=200,
                                    default="http://localhost:11434")
    # Held as a setting because model ids turn over far faster than this code
    # will. If a run reports the model as not found, change it here.
    vlm_model = models.CharField(max_length=60, default="qwen3-vl:4b")
    # Blank falls back to the GOOGLE_API_KEY (or GEMINI_API_KEY) environment
    # variable, which is where it belongs in a deployment — the field exists so
    # a barangay admin can set it from the dashboard without shell access.
    vlm_api_key = models.CharField(max_length=200, blank=True)
    # Seconds before a call is abandoned and treated as unavailable. The alert
    # is published either way; this only bounds how long it waits.
    # A local 4B model on CPU is slower than a cloud call, and the alert path
    # can afford to wait -- the frame loop has already moved on.
    vlm_timeout = models.PositiveSmallIntegerField(default=60)

    # --- per-call cost (the only knobs that matter on CPU-only hardware) -----
    # A vision call's wall clock is dominated by PREFILL: every image becomes
    # hundreds of visual tokens that must all be processed before the first
    # word is generated. With a GPU this is seconds and the defaults are right.
    # Without one -- a 15W laptop chip, no CUDA -- the same call can take
    # minutes, and these two fields are how it is brought back into range.
    #
    # They trade accuracy for speed honestly, and the trade should be reported:
    # fewer images means the motion questions (hand_to_mouth_activity compares
    # frames) have less to compare, and a smaller edge means small objects are
    # harder to see. Measure with `manage.py vlm_selftest --benchmark` rather
    # than guessing which setting your hardware needs.
    #
    # Total images sent = 1 scene frame (if enabled) + up to vlm_frames crops.
    vlm_frames = models.PositiveSmallIntegerField(default=3)
    # Longest edge of each image in pixels. Visual tokens grow with AREA, so
    # 1024 -> 512 is roughly a 4x cut in prefill work.
    vlm_max_edge = models.PositiveSmallIntegerField(default=1024)
    # The full uncropped frame. It is what makes the scene questions ("is this
    # a store?", "is there seating?") answerable at all, so turn it off only
    # when the machine cannot afford it.
    vlm_send_scene = models.BooleanField(default=True)

    # Run the checker WITHOUT making the alert wait for it (spec 6: "Async:
    # never block the video loop").
    #
    # Inline, the checker's latency is the frame loop's latency -- fine at a few
    # seconds on a GPU, unusable at minutes on CPU, which is why the only option
    # on slow hardware was to switch it off entirely.
    #
    # Asynchronous, the alert publishes immediately on the system indicators and
    # the context is attached whenever the answer arrives. Slow stops being a
    # reason not to run the checker; it just means the context lands later.
    #
    # The cost is that a late answer cannot be scored: the alert has already
    # been filed and possibly dispatched against, and silently moving its number
    # minutes afterwards would mean two people looking at the same event saw
    # different scores with nothing on screen to explain it. The context is
    # additive, and the card marks an ordinary-activity reading plainly.
    #
    # Default ON, because an alert that arrives now with context later beats an
    # alert that arrives minutes late, and beats no checker at all.
    vlm_async = models.BooleanField(default=True)
    # Minimum self-reported certainty for a verdict to score at all. Below this
    # the verdict is recorded on the alert for audit but contributes no cue —
    # the model saying "maybe, 20%" is not evidence.
    vlm_min_confidence = models.PositiveSmallIntegerField(default=50)


    alert_cooldown = models.PositiveSmallIntegerField(default=120)
    # How long alert evidence (images and clips) is kept before
    # `manage.py purge_old_evidence` may delete it. Supports RA 10173 storage
    # limitation: footage of identifiable people is not kept longer than needed.
    evidence_retention_days = models.PositiveSmallIntegerField(default=30)
    # Master switch for the scheduled purge (`purge_old_evidence --auto`). OFF by
    # default and never turned on by code: an operator enables it deliberately,
    # because the purge deletes files.
    evidence_auto_purge = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System settings"
        verbose_name_plural = "System settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "System settings"
