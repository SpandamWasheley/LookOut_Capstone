from datetime import time

from django.contrib.auth.models import AbstractUser
from django.db import models


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

    class Meta:
        ordering = ["code"]

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


class Resident(models.Model):
    class Status(models.TextChoices):
        VERIFIED = "verified", "Verified"
        PENDING = "pending", "Pending"
        FLAGGED = "flagged", "Flagged"

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    code = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    barangay_id = models.CharField(max_length=30, unique=True, blank=True)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    guardian_name = models.CharField(max_length=150, blank=True)
    image_url = models.URLField(blank=True)
    phone = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["code"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_code(Resident, "RES")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Household(models.Model):
    code = models.CharField(max_length=30, unique=True, blank=True)
    family_name = models.CharField(max_length=150)
    address = models.CharField(max_length=255, blank=True)
    contact = models.CharField(max_length=30, blank=True)
    enrolled_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["code"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_code(Household, "HH-TET", width=4)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.family_name}"


class HouseholdMember(models.Model):
    class Status(models.TextChoices):
        VERIFIED = "verified", "Verified"
        PENDING = "pending", "Pending"
        FLAGGED = "flagged", "Flagged"

    code = models.CharField(max_length=30, unique=True, blank=True)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="members")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    birthdate = models.DateField(null=True, blank=True)
    barangay_id = models.CharField(max_length=30, unique=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    relation = models.CharField(max_length=50, blank=True)
    image_url = models.URLField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    guardians = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="wards"
    )

    class Meta:
        ordering = ["code"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_code(HouseholdMember, "MEM", width=3)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.first_name} {self.last_name}"


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
    officers_assigned = models.ManyToManyField(Officer, blank=True, related_name="alerts")
    suspect = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _next_code(Alert, "ALT", width=4)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


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
