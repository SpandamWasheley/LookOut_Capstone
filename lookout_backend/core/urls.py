from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register("zones", views.ZoneViewSet)
router.register("violation-types", views.ViolationTypeViewSet)
router.register("cameras", views.CameraViewSet)
router.register("officers", views.OfficerViewSet)
router.register("residents", views.ResidentViewSet)
router.register("households", views.HouseholdViewSet)
router.register("household-members", views.HouseholdMemberViewSet)
router.register("alerts", views.AlertViewSet)

urlpatterns = [
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/me/", views.me, name="me"),
    path("dashboard/stats/", views.dashboard_stats, name="dashboard_stats"),
    path("settings/", views.SystemSettingsView.as_view(), name="system_settings"),
    path("", include(router.urls)),
]
