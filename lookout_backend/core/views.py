from django.db.models import Count
from django.utils import timezone
from rest_framework import generics, permissions, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import (
    Alert,
    Camera,
    Household,
    HouseholdMember,
    Officer,
    Resident,
    SystemSettings,
    ViolationType,
    Zone,
)
from .serializers import (
    AlertSerializer,
    CameraSerializer,
    HouseholdMemberSerializer,
    HouseholdSerializer,
    OfficerSerializer,
    ResidentSerializer,
    SystemSettingsSerializer,
    UserSerializer,
    ViolationTypeSerializer,
    ZoneSerializer,
)


class LookoutTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["name"] = user.display_name or user.get_full_name() or user.username
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


class LoginView(TokenObtainPairView):
    serializer_class = LookoutTokenObtainPairSerializer


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def me(request):
    return Response(UserSerializer(request.user).data)


class SystemSettingsView(generics.RetrieveUpdateAPIView):
    serializer_class = SystemSettingsSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return SystemSettings.load()


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def dashboard_stats(request):
    now = timezone.now()
    week_ago = now - timezone.timedelta(days=7)

    by_status = dict(
        Alert.objects.values_list("status").annotate(count=Count("id")).values_list("status", "count")
    )
    by_type = list(
        Alert.objects.filter(timestamp__gte=week_ago)
        .values("type__code", "type__label")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    weekly_trend = (
        Alert.objects.filter(timestamp__gte=week_ago)
        .extra(select={"day": "date(timestamp)"})
        .values("day")
        .annotate(violations=Count("id"))
        .order_by("day")
    )

    return Response({
        "cameras_online": Camera.objects.filter(status=Camera.Status.ONLINE).count(),
        "cameras_total": Camera.objects.count(),
        "alerts_by_status": by_status,
        "alerts_by_type_7d": by_type,
        "weekly_trend": list(weekly_trend),
        "officers_on_duty": Officer.objects.exclude(status=Officer.Status.OFF_DUTY).count(),
        "residents_total": Resident.objects.count(),
        "households_total": Household.objects.count(),
    })


class ZoneViewSet(viewsets.ModelViewSet):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer


class ViolationTypeViewSet(viewsets.ModelViewSet):
    queryset = ViolationType.objects.all()
    serializer_class = ViolationTypeSerializer


class CameraViewSet(viewsets.ModelViewSet):
    queryset = Camera.objects.select_related("zone").all()
    serializer_class = CameraSerializer
    filterset_fields = ["zone", "status"]


class OfficerViewSet(viewsets.ModelViewSet):
    queryset = Officer.objects.all()
    serializer_class = OfficerSerializer
    filterset_fields = ["status"]


class ResidentViewSet(viewsets.ModelViewSet):
    queryset = Resident.objects.all()
    serializer_class = ResidentSerializer
    filterset_fields = ["status"]


class HouseholdViewSet(viewsets.ModelViewSet):
    queryset = Household.objects.select_related("zone").prefetch_related("members").all()
    serializer_class = HouseholdSerializer


class HouseholdMemberViewSet(viewsets.ModelViewSet):
    queryset = HouseholdMember.objects.all()
    serializer_class = HouseholdMemberSerializer
    filterset_fields = ["household", "status"]


class AlertViewSet(viewsets.ModelViewSet):
    queryset = Alert.objects.select_related("type", "camera", "officer_assigned").all()
    serializer_class = AlertSerializer
    filterset_fields = ["status", "type", "camera"]
