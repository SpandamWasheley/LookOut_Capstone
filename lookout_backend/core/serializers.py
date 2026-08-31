from rest_framework import serializers

from .models import (
    Alert,
    Camera,
    Citation,
    DetectionJob,
    FaceEmbedding,
    Officer,
    Person,
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
    # static image_url. The raw stream_url (which holds credentials) is never
    # serialized — it is write-only, so an admin can set it but it never leaves
    # the server in a response.
    is_live = serializers.BooleanField(read_only=True)
    stream_url = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Camera
        fields = [
            "id", "code", "name", "zone", "status", "fps",
            "last_motion_at", "image_url", "is_live", "stream_url",
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


class FaceEmbeddingSerializer(serializers.ModelSerializer):
    """Full representation — used for the standalone embeddings admin endpoint.
    Never exposes the raw `embedding` vector field."""

    class Meta:
        model = FaceEmbedding
        fields = ["id", "person", "angle", "image", "det_score", "created_at"]
        read_only_fields = fields


class PersonEmbeddingSerializer(serializers.ModelSerializer):
    """Nested-in-Person representation: angle + image URL only, per spec —
    no det_score/timestamps, and never the raw embedding vector."""

    class Meta:
        model = FaceEmbedding
        fields = ["id", "angle", "image"]


class PersonSerializer(serializers.ModelSerializer):
    embeddings = PersonEmbeddingSerializer(many=True, read_only=True)

    class Meta:
        model = Person
        fields = [
            "id", "person_code", "full_name", "status",
            "enrolled_at", "notes", "created_at", "embeddings",
        ]
        # status/enrolled_at are only ever changed by the enroll-face /
        # embeddings actions, never directly by the client.
        read_only_fields = ["person_code", "status", "enrolled_at", "created_at"]


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
            "normalized_name", "matched_person", "aliases", "first_seen", "last_seen",
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

    class Meta:
        model = Citation
        fields = [
            "id", "alert", "violator", "violator_name",
            "first_name_entered", "middle_name_entered", "last_name_entered", "suffix_entered",
            "officer", "officer_name", "barangay_of_violation", "violator_barangay",
            "violations", "violation_labels",
            "matched_person", "match_confidence", "notes", "created_by", "created_at",
        ]
        read_only_fields = ["created_by", "created_at"]

    def validate_violations(self, value):
        if not value:
            raise serializers.ValidationError("At least one violation must be selected.")
        return value

    def get_violator_name(self, obj):
        return _format_full_name(obj.last_name_entered, obj.first_name_entered, obj.middle_name_entered, obj.suffix_entered)

    def get_violation_labels(self, obj):
        return [v.label for v in obj.violations.all()]


class AlertSerializer(serializers.ModelSerializer):
    type = serializers.SlugRelatedField(slug_field="code", queryset=ViolationType.objects.all())
    camera = serializers.SlugRelatedField(slug_field="code", queryset=Camera.objects.all(), required=False, allow_null=True)
    camera_zone = serializers.CharField(source="camera.name", read_only=True)
    # Written/read by stable Officer id, not display name — Officer.name has
    # no uniqueness constraint, so matching by name risked merging two
    # different officers that happen to share a name (or silently failing
    # with MultipleObjectsReturned). officers_assigned_names is a read-only
    # convenience for clients that just want to display the names.
    officers_assigned = serializers.PrimaryKeyRelatedField(
        queryset=Officer.objects.all(), required=False, many=True
    )
    officers_assigned_names = serializers.SerializerMethodField()
    matched_person_name = serializers.SerializerMethodField()

    class Meta:
        model = Alert
        fields = [
            "id", "code", "type", "status", "camera", "camera_zone", "timestamp",
            "confidence", "description", "image_url", "video_url", "raw_video_url",
            "officers_assigned", "officers_assigned_names", "suspect", "notes",
            "matched_person", "matched_person_name", "match_confidence",
        ]
        # Set only by the watchers' recognition step (see core/face_registry.py),
        # never by a client PATCH.
        read_only_fields = ["matched_person", "match_confidence"]

    def get_officers_assigned_names(self, obj):
        return [o.name for o in obj.officers_assigned.all()]

    def get_matched_person_name(self, obj):
        return obj.matched_person.full_name if obj.matched_person_id else None


class SystemSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemSettings
        fields = [
            "curfew_start", "curfew_end", "curfew_age", "curfew_confidence", "curfew_dwell",
            "guardian_check", "unknown_alert",
            "noise_enabled", "noise_threshold_db", "noise_duration",
            "waste_enabled", "waste_confidence", "waste_dwell",
            "waste_collection_start", "waste_collection_end",
            "parking_enabled", "parking_confidence", "parking_dwell",
            "parking_move_tolerance",
            "smoking_enabled", "smoking_confidence", "smoking_dwell",
            "thief_enabled", "thief_confidence", "thief_dwell",
            "drinking_enabled", "drinking_confidence", "drinking_dwell",
            "drinking_hours_enabled", "drinking_start", "drinking_end",
            "alert_cooldown", "evidence_retention_days",
            "auto_dispatch", "email_alerts", "sms_alerts",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class DetectionJobSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.display_name", read_only=True, default="")

    class Meta:
        model = DetectionJob
        fields = [
            "id", "violation_type", "source_filename", "status", "started_at",
            "finished_at", "error", "created_by_name",
        ]
        # Every field here is set by the server (upload handling / the watcher
        # thread) — the client only ever POSTs the file + violation_type, which
        # the view's create() reads straight off request.data/request.FILES,
        # not through this serializer.
        read_only_fields = fields
