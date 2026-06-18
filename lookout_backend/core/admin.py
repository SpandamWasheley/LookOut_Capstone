from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

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


@admin.register(Resident)
class ResidentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "barangay_id", "age", "status")
    list_filter = ("status",)


class HouseholdMemberInline(admin.TabularInline):
    model = HouseholdMember
    extra = 0


@admin.register(Household)
class HouseholdAdmin(admin.ModelAdmin):
    list_display = ("code", "family_name", "purok", "zone", "enrolled_date")
    inlines = [HouseholdMemberInline]


@admin.register(HouseholdMember)
class HouseholdMemberAdmin(admin.ModelAdmin):
    list_display = ("code", "first_name", "last_name", "household", "status", "relation")
    list_filter = ("status",)


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("code", "type", "status", "camera", "timestamp", "confidence", "officer_assigned")
    list_filter = ("status", "type")


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "updated_at")

    def has_add_permission(self, request):
        return not SystemSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
