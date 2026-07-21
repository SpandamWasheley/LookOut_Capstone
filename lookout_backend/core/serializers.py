from rest_framework import serializers

from .models import (
    Alert,
    Camera,
    Household,
    HouseholdMember,
    Officer,
    Resident,
    SystemSettings,
    User,
    ViolationType,
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

    class Meta:
        model = Camera
        fields = [
            "id", "code", "name", "zone", "status", "fps",
            "last_motion_at", "image_url",
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


class ResidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resident
        fields = [
            "id", "code", "name", "barangay_id", "age", "status",
            "gender", "guardian_name", "image_url", "phone",
        ]


class HouseholdMemberSerializer(serializers.ModelSerializer):
    guardians = serializers.PrimaryKeyRelatedField(many=True, queryset=HouseholdMember.objects.all(), required=False)

    class Meta:
        model = HouseholdMember
        fields = [
            "id", "code", "household", "first_name", "last_name", "birthdate",
            "barangay_id", "status", "relation", "image_url", "phone", "guardians",
        ]


class HouseholdSerializer(serializers.ModelSerializer):
    members = HouseholdMemberSerializer(many=True, read_only=True)

    class Meta:
        model = Household
        fields = [
            "id", "code", "family_name", "address",
            "contact", "enrolled_date", "members",
        ]


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

    class Meta:
        model = Alert
        fields = [
            "id", "code", "type", "status", "camera", "camera_zone", "timestamp",
            "confidence", "description", "image_url", "officers_assigned",
            "officers_assigned_names", "suspect", "notes",
        ]

    def get_officers_assigned_names(self, obj):
        return [o.name for o in obj.officers_assigned.all()]


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
            "alert_cooldown", "evidence_retention_days",
            "auto_dispatch", "email_alerts", "sms_alerts",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]
