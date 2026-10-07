from rest_framework import serializers

from .models import (
    Alert,
    Camera,
    Citation,
    DetectionJob,
    Officer,
    SystemSettings,
    User,
    ViolationType,
    Violator,
    Zone,
)


class UserSerializer(serializers.ModelSerializer):
    officer_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "display_name", "role", "email", "must_change_password", "officer_id"]

    def get_officer_id(self, obj):
        officer = getattr(obj, "officer_profile", None)
        return officer.id if officer else None


class DispatcherSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "display_name", "email", "role"]


class ZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = Zone
        fields = ["id", "name"]


class ViolationTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ViolationType
        fields = ["id", "code", "label", "color", "icon"]


class CameraSerializer(serializers.ModelSerializer):
    zone = serializers.SlugRelatedField(slug_field="name", queryset=Zone.objects.all())
    # `is_live` tells the dashboard to poll the snapshot endpoint instead of the
    # static image_url. It follows `resolved_stream_url`, so it is also true for
    # a camera whose URL comes from STREAM_URL in the .env rather than the row.
    # The raw stream_url (which holds credentials) is never serialized — it is
    # write-only, so an admin can set it but it never leaves the server in a
    # response.
    is_live = serializers.BooleanField(read_only=True)
    stream_url = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Camera
        fields = [
            "id", "code", "name", "zone", "address", "status",
            "image_url", "is_live", "stream_url",
            "edges", "edges_width", "edges_height",
            "obstruction_pct", "obstruction_minutes",
        ]


class OfficerSerializer(serializers.ModelSerializer):
    email = serializers.SerializerMethodField()
    username = serializers.SerializerMethodField()

    class Meta:
        model = Officer
        fields = [
            "id", "code", "name", "badge", "status", "location",
            "phone", "shift", "joined_date", "email", "username",
        ]

    def get_email(self, obj):
        return obj.user.email if obj.user_id else ""

    def get_username(self, obj):
        return obj.user.username if obj.user_id else ""


def _format_full_name(last, first, middle, suffix):
    name = f"{last}, {first}"
    if middle:
        name += f" {middle}"
    if suffix:
        name += f" {suffix}"
    return name


class ViolatorSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    citation_count = serializers.SerializerMethodField()

    class Meta:
        model = Violator
        fields = [
            "id", "first_name", "middle_name", "last_name", "suffix", "full_name",
            "normalized_name", "aliases", "first_seen", "last_seen",
            "citation_count",
        ]
        read_only_fields = ["normalized_name", "aliases", "first_seen", "last_seen"]

    def get_full_name(self, obj):
        return _format_full_name(obj.last_name, obj.first_name, obj.middle_name, obj.suffix)

    def get_citation_count(self, obj):
        # Prefer the annotation ViolatorViewSet.get_queryset adds (avoids an
        # extra query per row in list views); fall back to a live count for
        # any context that hands this serializer an un-annotated instance
        # (e.g. the merge response).
        annotated = getattr(obj, "citation_count", None)
        return annotated if annotated is not None else obj.citations.count()


class CitationSerializer(serializers.ModelSerializer):
    # Writable-optional: the client can pass an explicit id when the officer
    # picks a "did you mean" suggestion from /api/violators/search; if
    # omitted, CitationViewSet.perform_create resolves or creates one from
    # the *_entered names instead.
    violator = serializers.PrimaryKeyRelatedField(queryset=Violator.objects.all(), required=False, allow_null=True)
    violator_name = serializers.SerializerMethodField()
    officer_name = serializers.CharField(source="officer.name", read_only=True)
    violation_labels = serializers.SerializerMethodField()
    # Web sends neither field and gets today's behaviour unchanged: resolve
    # the alert on save, no idempotency key. Mobile sends both — false while
    # more violators from the same scene are still being cited, and a
    # per-citation client_uuid so a queued retry can't double-file. See
    # CitationViewSet.perform_create for how each is used.
    resolve_alert = serializers.BooleanField(write_only=True, required=False, default=True)
    # validators=[] disables ModelSerializer's automatic UniqueValidator for
    # this field — a repeat client_uuid must reach perform_create so it can
    # return the existing citation, not fail validation before we get there.
    client_uuid = serializers.UUIDField(required=False, allow_null=True, validators=[])

    class Meta:
        model = Citation
        fields = [
            "id", "alert", "violator", "violator_name",
            "first_name_entered", "middle_name_entered", "last_name_entered", "suffix_entered",
            "officer", "officer_name", "barangay_of_violation", "violator_barangay",
            "violations", "violation_labels",
            "notes", "created_by", "created_at",
            "resolve_alert", "client_uuid",
        ]
        read_only_fields = ["created_by", "created_at"]

    def validate_violations(self, value):
        if not value:
            raise serializers.ValidationError("At least one violation must be selected.")
        return value

    # Fields that say WHICH citation this is, rather than what it records.
    # Writable on create, frozen afterwards — see validate().
    IDENTITY_FIELDS = ("alert", "officer", "violator")

    def validate(self, attrs):
        """A correction may change what the citation SAYS, never what it IS.

        `alert`, `officer` and `violator` are writable on create and must not
        be on update. CitationViewSet's permission authorises the edit against
        the citation as it currently stands — your own, on an open alert — so
        letting the same request move it onto a different alert, or credit it
        to a different officer, authorises one thing and performs another.

        `violator` is the sharpest of the three: it is resolved server-side
        FROM the entered names (see perform_create/perform_update), so an
        explicit id on update would pin the citation to a person record that
        does not match the name printed on it — which is precisely the
        mismatch perform_update re-resolves to prevent.

        Rejected rather than silently dropped: a client sending these is
        either a bug worth seeing or an attempt worth refusing, and neither
        should look like success. (resolve_alert and client_uuid ARE dropped
        quietly in perform_update — they are create-time instructions, not
        identity, and an older client may still send them.)
        """
        if self.instance is not None:
            frozen = [f for f in self.IDENTITY_FIELDS if f in attrs]
            if frozen:
                raise serializers.ValidationError({
                    f: "Cannot be changed after the citation is filed." for f in frozen
                })
        return attrs

    def get_violator_name(self, obj):
        return _format_full_name(obj.last_name_entered, obj.first_name_entered, obj.middle_name_entered, obj.suffix_entered)

    def get_violation_labels(self, obj):
        return [v.label for v in obj.violations.all()]


class AlertSerializer(serializers.ModelSerializer):
    type = serializers.SlugRelatedField(slug_field="code", queryset=ViolationType.objects.all())
    camera = serializers.SlugRelatedField(slug_field="code", queryset=Camera.objects.all(), required=False, allow_null=True)
    # Footage uploaded for testing is filed on a "<CODE>-TEST" camera; show it for what it is.
    camera_zone = serializers.SerializerMethodField()
    # True once a citation has been filed against this alert (closed banner: "Citation issued").
    citation_issued = serializers.SerializerMethodField()
    # "recorded" (uploaded clip with a Recorded-at time), "processed" (uploaded clip without one:
    # the time is when it was processed) or "live" (a real camera, real time).
    time_source = serializers.SerializerMethodField()
    # Where the camera is. The alert shows this as the location of the
    # violation -- the system knows which camera saw it, so the camera's own
    # address is the most precise honest answer it can give.
    camera_address = serializers.CharField(source="camera.address", read_only=True,
                                           default="")
    # Written/read by stable Officer id, not display name — Officer.name has
    # no uniqueness constraint, so matching by name risked merging two
    # different officers that happen to share a name (or silently failing
    # with MultipleObjectsReturned). officers_assigned_names is a read-only
    # convenience for clients that just want to display the names.
    officers_assigned = serializers.PrimaryKeyRelatedField(
        queryset=Officer.objects.all(), required=False, many=True
    )
    officers_assigned_names = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()
    # The watchers store a relative path (see core/media.py) — resolved to an
    # absolute URL here, against THIS request, so the host always matches
    # whatever the client actually connected through (localhost, a LAN IP, or
    # whichever ngrok forwarding host is live this session) instead of
    # whatever SITE_BASE_URL happened to be frozen as at write time. No
    # client writes these three fields via the API (only the watch_* commands
    # ever set them, directly through the ORM), so making them
    # SerializerMethodFields — always read-only — doesn't remove a write path.
    image_url = serializers.SerializerMethodField()
    video_url = serializers.SerializerMethodField()
    raw_video_url = serializers.SerializerMethodField()

    # Spec 2: "watch/warning/violation" reads as three severities of the same
    # claim; Monitoring/Possible/Confirmed reads as three degrees of certainty,
    # which is what the score actually measures. Derived here rather than mapped
    # in the client so both dashboards and the officer app agree by default.
    level_label = serializers.SerializerMethodField()
    peak_level_label = serializers.SerializerMethodField()
    # AI checker cards (display only): badge, observations, checklist and the suggested
    # status, recomputed on every read from the stored reply and the CURRENT level.
    ai_context = serializers.SerializerMethodField()

    def get_camera_zone(self, obj):
        cam = obj.camera
        if cam is None:
            return ""
        return "Uploaded footage" if cam.code.endswith("-TEST") else cam.name

    def get_citation_issued(self, obj):
        return obj.citations.exists()

    def get_time_source(self, obj):
        return (obj.cues or {}).get("time_source") or ("processed" if obj.camera and obj.camera.code.endswith("-TEST") else "live")

    def update(self, instance, validated_data):
        """Save only the fields the client sent. A full-row save would write back the whole row as
        it was loaded, and could undo an update the detector made to the same event in between
        (its status, evidence clip, last-seen time) while an officer is being assigned."""
        officers = validated_data.pop("officers_assigned", None)
        for name, value in validated_data.items():
            setattr(instance, name, value)
        if validated_data:
            instance.save(update_fields=list(validated_data))
        if officers is not None:
            instance.officers_assigned.set(officers)
        return instance

    def get_ai_context(self, obj):
        """The AI cards' data, or None when this kind has no checker at all.

        None is not the same as "unavailable". Unavailable means a check was
        expected and did not produce an answer — Ollama unreachable, the reply
        unparseable, still running — and the card should say so, because the
        reader is entitled to wonder where the AI's opinion went.

        Parking has no checker and never has: its question is a measurement
        ("how much of the vehicle is in the area, for how long"), already
        answered exactly by geometry, with nothing for a vision model to
        adjudicate. Rendering "AI context unavailable" on every one of its
        alerts advertises a missing feature that was never meant to be there.
        So the field is omitted entirely and the clients simply draw no card.
        """
        from core.vision import ai_checker, ai_status
        cues = obj.cues or {}
        kind = ai_checker.kind_for(cues.get("kind"))
        if kind is None:
            return None
        ctx = ai_status.ai_context(kind, obj.ai, obj.level, puff_only=bool(cues.get("puff_only")))
        request = self.context.get("request")
        if request is not None:
            ctx["frames"] = [request.build_absolute_uri(u) for u in ctx["frames"]]
        return ctx

    def get_level_label(self, obj):
        from core.vision.scoring import label_of
        return label_of(obj.level) if obj.level else ""

    def get_peak_level_label(self, obj):
        """The highest status this event reached. The UI badges `level_label`
        (what it is NOW) and lists on this (what it earned)."""
        from core.vision.scoring import label_of
        return label_of(obj.peak_level) if obj.peak_level else ""

    class Meta:
        model = Alert
        fields = [
            "id", "code", "type", "status", "camera", "camera_zone",
            "camera_address", "timestamp",
            "confidence", "description", "image_url", "video_url", "raw_video_url",
            "officers_assigned", "officers_assigned_names", "suspect", "notes",
            # Weighted-sum scoring (core/vision/scoring.py). `level` is what the
            # dashboard should badge on -- `confidence` is now a violation
            # likelihood, so a bare percentage badge reads differently than it
            # used to. `cues` is the audit trail: which indicators fired and
            # what each was worth.
            "level", "level_label", "peak_level", "peak_level_label", "last_seen_at",
            "object_confidence", "cues", "ai_context", "timeline", "citation_issued", "time_source",
            # `reviewed_valid` is the only one of these a client writes: it is
            # the human label calibrate_weights fits the final weights against.
            # The reviewer's identity is stamped server-side alongside it.
            "reviewed_valid", "reviewed_by", "reviewed_by_name", "reviewed_at",
        ]
        read_only_fields = [
            # Written by the detectors through the ORM only. A client that
            # could PATCH its own cue vector could rewrite the calibration
            # training data after the fact.
            "level", "level_label", "peak_level", "peak_level_label", "last_seen_at",
            "object_confidence", "cues", "ai_context", "timeline",
            # Who reviewed it is recorded FROM the authenticated request, so a
            # client cannot name somebody else as the reviewer.
            "reviewed_by", "reviewed_by_name", "reviewed_at",
        ]

    def get_officers_assigned_names(self, obj):
        return [o.name for o in obj.officers_assigned.all()]

    def get_reviewed_by_name(self, obj):
        """Display name of whoever reviewed this, or "" if nobody has.

        Survives the reviewer's account being deleted: reviewed_by is
        SET_NULL, so the alert keeps its label and simply loses the name
        rather than losing the review.
        """
        return str(obj.reviewed_by) if obj.reviewed_by else ""

    def _resolve_media_url(self, value):
        if not value:
            return value
        # Already absolute — seed_demo.py's Unsplash CDN stills, or (pre-
        # migration-0027) an old row that was somehow missed — pass through
        # untouched rather than mangle a genuinely external URL.
        if value.startswith("http://") or value.startswith("https://"):
            return value
        request = self.context.get("request")
        if request is None:
            # No request in context (e.g. a serializer used outside a view) —
            # nothing to resolve against; return the bare path rather than
            # raise, so this never breaks a non-HTTP caller.
            return value
        return request.build_absolute_uri(value)

    def get_image_url(self, obj):
        return self._resolve_media_url(obj.image_url)

    def get_video_url(self, obj):
        return self._resolve_media_url(obj.video_url)

    def get_raw_video_url(self, obj):
        return self._resolve_media_url(obj.raw_video_url)


class SystemSettingsSerializer(serializers.ModelSerializer):
    # The spec value of every adjustable timing, so the UI can show 'default' and reset.
    spec_defaults = serializers.SerializerMethodField()

    def get_spec_defaults(self, obj):
        import datetime
        from core.vision.spec_settings import SPEC_DEFAULTS
        return {k: (v.strftime('%H:%M:%S') if isinstance(v, datetime.time) else v) for k, v in SPEC_DEFAULTS.items()}

    class Meta:
        model = SystemSettings
        fields = [
            "parking_enabled", "parking_confidence", "parking_dwell",
            "parking_move_tolerance",
            "smoking_enabled", "smoking_confidence", "smoking_dwell",
            "thief_enabled", "thief_confidence", "thief_dwell",
            "drinking_enabled", "drinking_confidence", "drinking_dwell",
            "drinking_held_dwell", "drinking_evidence_max_age",
            "drinking_mouth_proximity", "drinking_cooldown_center_dist",
            "drinking_hours_enabled", "drinking_start", "drinking_end",
            "drinking_min_group", "drinking_group_duration",
            "vlm_enabled", "vlm_model", "vlm_model_holdup", "vlm_endpoint",
            "vlm_timeout",
            "object_confirm_seconds", "cue_hold_seconds", "monitoring_min_seconds", "smoking_puff_count", "smoking_puff_window_minutes",
            "holdup_loiter_seconds", "holdup_near_person_heights",
            "vlm_frames", "vlm_max_edge", "vlm_async",
            "alert_cooldown", "evidence_retention_days", "evidence_auto_purge", "show_testing_tools", "auto_start_detection",
            "updated_at", "spec_defaults",
        ]
        read_only_fields = ["updated_at", "spec_defaults"]

    def validate(self, attrs):
        """Timings / conditions stay inside the ranges that still mean what the spec says."""
        from core.vision.spec_settings import LIMITS
        errors = {}
        for field, (low, high) in LIMITS.items():
            if field in attrs and not (low <= attrs[field] <= high):
                errors[field] = f"Must be between {low:g} and {high:g}."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class DetectionJobSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.display_name", read_only=True, default="")
    camera_code = serializers.CharField(source="camera.code", read_only=True, default="")
    # True for a job running against a live camera's stream_url — it has no
    # natural end (no EOF), so the dashboard should treat "Cancel" as "Stop"
    # rather than implying an in-progress run that will finish on its own.
    is_live = serializers.SerializerMethodField()

    class Meta:
        model = DetectionJob
        fields = [
            "id", "violation_type", "source_filename", "status", "started_at",
            "finished_at", "error", "created_by_name", "camera_code", "is_live", "recorded_at",
        ]
        # Every field here is set by the server (upload handling / the watcher
        # thread) — the client only ever POSTs the file + violation_type (or
        # camera_id for a live job), which the view's create() reads straight
        # off request.data/request.FILES, not through this serializer.
        read_only_fields = fields

    def get_is_live(self, obj):
        return obj.camera_id is not None
