from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Alert,
    Camera,
    Citation,
    EmailVerificationCode,
    FaceEmbedding,
    Officer,
    Person,
    SystemSettings,
    User,
    ViolationType,
    Violator,
    Zone,
)


@admin.register(User)
class LookoutUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Lookout", {"fields": ("role", "display_name")}),
    )
    list_display = ("username", "display_name", "role", "is_staff")


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(ViolationType)
class ViolationTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "color", "icon")


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "zone", "status", "fps", "last_motion_at")
    list_filter = ("status", "zone")


@admin.register(Officer)
class OfficerAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "badge", "status", "location")
    list_filter = ("status",)


class FaceEmbeddingInline(admin.TabularInline):
    model = FaceEmbedding
    extra = 0


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("person_code", "full_name", "status", "enrolled_at")
    list_filter = ("status",)
    inlines = [FaceEmbeddingInline]


@admin.register(Citation)
class CitationAdmin(admin.ModelAdmin):
    list_display = ("id", "last_name_entered", "first_name_entered", "officer", "barangay_of_violation", "violator_barangay", "created_at")
    list_filter = ("barangay_of_violation", "violator_barangay")


@admin.register(Violator)
class ViolatorAdmin(admin.ModelAdmin):
    list_display = ("id", "last_name", "first_name", "suffix", "matched_person", "first_seen", "last_seen")
    search_fields = ("first_name", "last_name", "normalized_name")


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("code", "type", "status", "camera", "timestamp", "confidence", "officers_list")
    list_filter = ("status", "type")

    def officers_list(self, obj):
        return ", ".join(o.name for o in obj.officers_assigned.all()) or "—"
    officers_list.short_description = "Officers assigned"


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "updated_at")

    def has_add_permission(self, request):
        return not SystemSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    list_display = ("email", "code", "verified", "used", "created_at")
    list_filter = ("verified", "used")
