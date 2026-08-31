import re
from datetime import time

from django.contrib.auth.models import AbstractUser
from django.db import models

from core.constants import ZAMBOANGA_BARANGAYS

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


class Person(models.Model):
    """A face-registry entry: someone enrolled for facial recognition
    (curfew/violator matching), not a resident household record."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ENROLLED = "enrolled", "Enrolled"

    person_code = models.CharField(max_length=20, unique=True, blank=True)
    full_name = models.CharField(max_length=150)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["person_code"]

    def save(self, *args, **kwargs):
        if not self.person_code:
            self.person_code = _next_code(Person, "BRG-TET", width=4, field="person_code")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.person_code} - {self.full_name}"


class FaceEmbedding(models.Model):
    class Angle(models.TextChoices):
        FRONT = "front", "Front"
        RIGHT = "right", "Right"
        LEFT = "left", "Left"

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="embeddings")
    angle = models.CharField(max_length=10, choices=Angle.choices)
    image = models.ImageField(upload_to="face_enrollment/")
    embedding = models.JSONField()
    det_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["person", "angle"], name="unique_person_angle"),
        ]

    def __str__(self):
        return f"{self.person.person_code} - {self.angle}"


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
    # Set by watch_smoking/watch_drinking (see core/face_registry.py) when a
    # face in the alert frame matches an enrolled Person above
    # SystemSettings.curfew_confidence — the citation form prefills from
    # these. Never gates alert creation: null on no match, no enrolled
    # faces, or a recognition failure.
    matched_person = models.ForeignKey(
        Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="alerts"
    )
    match_confidence = models.FloatField(null=True, blank=True)

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
    matched_person = models.ForeignKey(
        Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="violators"
    )
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
    matched_person = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="citations")
    match_confidence = models.FloatField(null=True, blank=True)
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

    violation_type = models.CharField(max_length=20)      # key into views.DETECTION_COMMANDS
    source_filename = models.CharField(max_length=255)    # original upload name, for display
    source_path = models.CharField(max_length=500)        # saved temp path — subprocess arg + cleanup
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
    curfew_start = models.TimeField(default=time(22, 0))
    curfew_end = models.TimeField(default=time(6, 0))
    curfew_age = models.PositiveSmallIntegerField(default=18)
    # Compared directly against the face-recognition match score (insightface/
    # ArcFace cosine similarity * 100). A genuine match typically scores
    # 35-70, not 90+, so this default is calibrated to that scale rather than
    # a generic "75% confident" percentage.
    curfew_confidence = models.PositiveSmallIntegerField(default=45)
    curfew_dwell = models.PositiveSmallIntegerField(default=5)
    guardian_check = models.BooleanField(default=True)
    unknown_alert = models.BooleanField(default=True)

    noise_enabled = models.BooleanField(default=True)
    noise_threshold_db = models.PositiveSmallIntegerField(default=65)
    noise_duration = models.PositiveSmallIntegerField(default=10)

    waste_enabled = models.BooleanField(default=True)
    waste_confidence = models.PositiveSmallIntegerField(default=70)
    waste_dwell = models.PositiveSmallIntegerField(default=8)
    waste_collection_start = models.TimeField(default=time(6, 0))
    waste_collection_end = models.TimeField(default=time(9, 0))

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
    drinking_hours_enabled = models.BooleanField(default=False)
    drinking_start = models.TimeField(default=time(22, 0))
    drinking_end = models.TimeField(default=time(5, 0))

    # Gathering ("inuman") detection: a second, independent path to an alert
    # alongside the per-person one above. Near the camera an individual's
    # bottle is verifiable on its own; at range it isn't, but a sustained
    # gathering still is — so this scales the evidence standard with what the
    # camera can actually establish, instead of one fixed per-person rule.
    drinking_min_group = models.PositiveSmallIntegerField(default=2)
    # Default of 25s is deliberately short for testing against sub-minute
    # clips — a real deployment should set this much higher, ~600-900s
    # (10-15 minutes), so a few people briefly standing near each other isn't
    # mistaken for a drinking session.
    drinking_group_duration = models.PositiveSmallIntegerField(default=25)

    alert_cooldown = models.PositiveSmallIntegerField(default=120)
    evidence_retention_days = models.PositiveSmallIntegerField(default=30)
    auto_dispatch = models.BooleanField(default=False)
    email_alerts = models.BooleanField(default=True)
    sms_alerts = models.BooleanField(default=True)

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
