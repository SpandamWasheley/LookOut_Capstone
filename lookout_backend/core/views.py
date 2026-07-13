import random
from datetime import timedelta

from django.conf import settings as django_settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import generics, permissions, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import (
    Alert,
    Camera,
    EmailVerificationCode,
    Household,
    HouseholdMember,
    Officer,
    Resident,
    SystemSettings,
    User,
    ViolationType,
    Zone,
)
from .permissions import IsAdmin, IsAdminOrReadOnly
from .throttling import (
    LoginThrottle,
    OtpSendThrottle,
    OtpVerifyThrottle,
    PasswordResetConfirmThrottle,
    PasswordResetSendThrottle,
)

CODE_EXPIRY_MINUTES = 10
from .serializers import (
    AlertSerializer,
    CameraSerializer,
    DispatcherSerializer,
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
    throttle_classes = [LoginThrottle]


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def me(request):
    return Response(UserSerializer(request.user).data)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([OtpSendThrottle])
def send_officer_code(request):
    email = (request.data.get("email") or "").strip().lower()
    if not email:
        return Response({"email": "Email is required."}, status=400)
    if User.objects.filter(email__iexact=email).exists():
        return Response({"email": "An account with this email already exists."}, status=400)

    code = f"{random.randint(0, 999999):06d}"
    EmailVerificationCode.objects.create(email=email, code=code)

    send_mail(
        "Your LookOut verification code",
        f"Your verification code is {code}. It expires in {CODE_EXPIRY_MINUTES} minutes.",
        django_settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )
    return Response({"detail": "Verification code sent."})


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([OtpVerifyThrottle])
def verify_officer_code(request):
    email = (request.data.get("email") or "").strip().lower()
    code = (request.data.get("code") or "").strip()
    cutoff = timezone.now() - timedelta(minutes=CODE_EXPIRY_MINUTES)
    record = (
        EmailVerificationCode.objects.filter(email=email, code=code, used=False, created_at__gte=cutoff)
        .order_by("-created_at")
        .first()
    )
    if not record:
        return Response({"code": "Invalid or expired code."}, status=400)
    record.verified = True
    record.save(update_fields=["verified"])
    return Response({"detail": "Code verified."})


def _validate_new_account_fields(data, *, require_phone=False):
    fields = {
        "first_name": (data.get("first_name") or "").strip(),
        "last_name": (data.get("last_name") or "").strip(),
        "username": (data.get("username") or "").strip(),
        "email": (data.get("email") or "").strip().lower(),
        "phone": (data.get("phone") or "").strip(),
        "password": data.get("password") or "",
        "code": (data.get("code") or "").strip(),
    }

    errors = {}
    for field in ["first_name", "last_name", "username", "email", "password"]:
        if not fields[field]:
            errors[field] = "Required."
    if require_phone and not fields["phone"]:
        errors["phone"] = "Required."
    if errors:
        return fields, errors

    if User.objects.filter(username__iexact=fields["username"]).exists():
        return fields, {"username": "This username is already taken."}
    if User.objects.filter(email__iexact=fields["email"]).exists():
        return fields, {"email": "An account with this email already exists."}

    return fields, None


def _consume_verified_code(email, code):
    cutoff = timezone.now() - timedelta(minutes=CODE_EXPIRY_MINUTES)
    record = (
        EmailVerificationCode.objects.filter(
            email=email, code=code, verified=True, used=False, created_at__gte=cutoff
        )
        .order_by("-created_at")
        .first()
    )
    return record


_PERSONNEL_ROLES = {
    "officer": User.Role.OFFICER,
    "dispatcher": User.Role.DISPATCHER,
    "both": User.Role.BOTH,
}


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated, IsAdmin])
def register_personnel(request):
    role_key = (request.data.get("role") or "").strip().lower()
    if role_key not in _PERSONNEL_ROLES:
        return Response({"role": "Must be one of officer, dispatcher, both."}, status=400)

    needs_officer_record = role_key in ("officer", "both")
    fields, errors = _validate_new_account_fields(request.data, require_phone=needs_officer_record)
    if errors:
        return Response(errors, status=400)

    record = _consume_verified_code(fields["email"], fields["code"])
    if not record:
        return Response({"code": "Email is not verified. Please verify the email first."}, status=400)

    display_name = f"{fields['first_name']} {fields['last_name']}".strip()
    with transaction.atomic():
        user = User.objects.create_user(
            username=fields["username"], email=fields["email"], password=fields["password"],
            role=_PERSONNEL_ROLES[role_key], display_name=display_name,
            must_change_password=True,
        )
        officer = None
        if needs_officer_record:
            officer = Officer.objects.create(
                user=user, name=display_name, phone=fields["phone"],
                badge=f"B-{random.randint(100, 999)}",
                status=Officer.Status.ON_DUTY,
                joined_date=timezone.now().date(),
            )
        record.used = True
        record.save(update_fields=["used"])

    if officer:
        return Response(OfficerSerializer(officer).data, status=201)
    return Response(DispatcherSerializer(user).data, status=201)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def change_password(request):
    new_password = request.data.get("new_password") or ""
    if len(new_password) < 8:
        return Response({"new_password": "Password must be at least 8 characters."}, status=400)
    request.user.set_password(new_password)
    request.user.must_change_password = False
    request.user.save(update_fields=["password", "must_change_password"])
    return Response({"detail": "Password updated."})


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([PasswordResetSendThrottle])
def forgot_password_send_code(request):
    email = (request.data.get("email") or "").strip().lower()
    if not email:
        return Response({"email": "Email is required."}, status=400)

    user = User.objects.filter(email__iexact=email).first()
    if user:
        code = f"{random.randint(0, 999999):06d}"
        EmailVerificationCode.objects.create(email=email, code=code)
        send_mail(
            "Your LookOut password reset code",
            f"Your password reset code is {code}. It expires in {CODE_EXPIRY_MINUTES} minutes. "
            "If you didn't request this, you can ignore this email.",
            django_settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
    # Same response whether or not the email exists, so this can't be used to enumerate accounts.
    return Response({"detail": "If an account exists for this email, a reset code has been sent."})


@api_view(["POST"])
@permission_classes([permissions.AllowAny])
@throttle_classes([PasswordResetConfirmThrottle])
def forgot_password_reset(request):
    email = (request.data.get("email") or "").strip().lower()
    code = (request.data.get("code") or "").strip()
    new_password = request.data.get("new_password") or ""

    if len(new_password) < 8:
        return Response({"new_password": "Password must be at least 8 characters."}, status=400)

    cutoff = timezone.now() - timedelta(minutes=CODE_EXPIRY_MINUTES)
    record = (
        EmailVerificationCode.objects.filter(email=email, code=code, used=False, created_at__gte=cutoff)
        .order_by("-created_at")
        .first()
    )
    if not record:
        return Response({"code": "Invalid or expired code."}, status=400)

    user = User.objects.filter(email__iexact=email).first()
    if not user:
        return Response({"email": "No account found for this email."}, status=400)

    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    record.used = True
    record.verified = True
    record.save(update_fields=["used", "verified"])

    return Response({"detail": "Password reset successfully."})


class SystemSettingsView(generics.RetrieveUpdateAPIView):
    serializer_class = SystemSettingsSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

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


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def send_sms(request):
    import requests as http_requests

    recipients = request.data.get("recipients", [])
    message = request.data.get("message", "")
    if not recipients:
        return Response({"detail": "No recipients specified."}, status=400)
    if not message.strip():
        return Response({"detail": "Message cannot be empty."}, status=400)

    api_key = django_settings.SEMAPHORE_API_KEY
    sender  = getattr(django_settings, "SEMAPHORE_SENDER_NAME", "LookOut")

    if not api_key:
        # No key configured — log only (dev/demo mode)
        for number in recipients:
            print(f"[SMS stub] → {number}: {message[:120]}")
        return Response({"sent": len(recipients), "recipients": recipients})

    failed = []
    for number in recipients:
        try:
            resp = http_requests.post(
                "https://api.semaphore.co/api/v4/messages",
                data={
                    "apikey": api_key,
                    "number": number,
                    "message": message,
                    "sendername": sender,
                },
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            failed.append({"number": number, "error": str(exc)})

    if failed:
        return Response(
            {"sent": len(recipients) - len(failed), "failed": failed},
            status=207,
        )
    return Response({"sent": len(recipients), "recipients": recipients})


class ZoneViewSet(viewsets.ModelViewSet):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class ViolationTypeViewSet(viewsets.ModelViewSet):
    queryset = ViolationType.objects.all()
    serializer_class = ViolationTypeSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class CameraViewSet(viewsets.ModelViewSet):
    queryset = Camera.objects.select_related("zone").all()
    serializer_class = CameraSerializer
    filterset_fields = ["zone", "status"]
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class OfficerViewSet(viewsets.ModelViewSet):
    queryset = Officer.objects.all()
    serializer_class = OfficerSerializer
    filterset_fields = ["status"]
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]

    def perform_destroy(self, instance):
        user = instance.user
        instance.delete()
        if user:
            user.delete()


class DispatcherViewSet(viewsets.ModelViewSet):
    queryset = User.objects.filter(role__in=[User.Role.DISPATCHER, User.Role.BOTH])
    serializer_class = DispatcherSerializer
    http_method_names = ["get", "delete", "head", "options"]
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def perform_destroy(self, instance):
        Officer.objects.filter(user=instance).delete()
        instance.delete()


class ResidentViewSet(viewsets.ModelViewSet):
    queryset = Resident.objects.all()
    serializer_class = ResidentSerializer
    filterset_fields = ["status"]
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class HouseholdViewSet(viewsets.ModelViewSet):
    queryset = Household.objects.prefetch_related("members").all()
    serializer_class = HouseholdSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class HouseholdMemberViewSet(viewsets.ModelViewSet):
    queryset = HouseholdMember.objects.all()
    serializer_class = HouseholdMemberSerializer
    filterset_fields = ["household", "status"]
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class AlertViewSet(viewsets.ModelViewSet):
    queryset = Alert.objects.select_related("type", "camera").prefetch_related("officers_assigned").all()
    serializer_class = AlertSerializer
    filterset_fields = ["status", "type", "camera"]

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Adds the requesting officer to officers_assigned atomically.

        Unlike a PATCH that replaces the whole officers_assigned list, M2M
        .add() is a safe additive operation under concurrent requests — two
        officers accepting the same alert at the same time can't overwrite
        each other the way a client-computed read-modify-write PATCH can.
        """
        officer = getattr(request.user, "officer_profile", None)
        if officer is None:
            return Response({"detail": "Only officer accounts can accept assignments."}, status=403)

        alert = self.get_object()
        alert.officers_assigned.add(officer)
        if alert.status == Alert.Status.ACTIVE:
            alert.status = Alert.Status.DISPATCHED
            alert.save(update_fields=["status"])

        return Response(self.get_serializer(alert).data)
