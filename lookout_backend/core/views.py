import base64
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import timedelta

import cv2
import django_filters
import psutil
from django.conf import settings as django_settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
import threading
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.text import get_valid_filename
from rapidfuzz import process as rapidfuzz_process
from django.db import connection
from rest_framework import generics, permissions, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from core.constants import ZAMBOANGA_BARANGAYS

from .models import (
    Alert,
    Camera,
    Citation,
    DetectionJob,
    EmailVerificationCode,
    Officer,
    SystemSettings,
    UploadSession,
    User,
    ViolationType,
    Violator,
    Zone,
    normalize_name,
)
from .permissions import CanEditOwnOpenCitation, IsAdmin, IsAdminOrReadOnly
from .throttling import (
    LoginThrottle,
    OtpSendThrottle,
    OtpVerifyThrottle,
    PasswordResetConfirmThrottle,
    PasswordResetSendThrottle,
)

def send_mail_async(subject, body, recipient):
    """Queue an email and return immediately.

    SMTP is slow -- measured at ~6.6s from this machine, on the Gmail relay this
    used to send through -- and it used to run inside the request. React Native's
    HTTP client gives up after 10s, so a phone on Wi-Fi would abort while the
    server was still talking to the relay: the code arrived in the inbox, the app
    showed a network error, and the screen never advanced to the code input.

    The caller no longer waits. The recipient does not care whether the message
    took 200ms or 8s to leave, and nothing in the response depends on it: the
    endpoint deliberately returns the same body whether or not an account
    exists, so there was never anything to report back.

    A daemon thread rather than a task queue: this is one email on a verification
    path, and Celery or Redis for it would be a lot of moving parts for a problem
    that is four lines. Daemon so a shutdown is not held open by a pending send.
    """
    def _send():
        try:
            send_mail(subject, body, django_settings.DEFAULT_FROM_EMAIL,
                      [recipient], fail_silently=False)
        except Exception:
            # Logged, never surfaced. A delivery failure must not tell a caller
            # whether the address belongs to a real account.
            logger.exception("Failed to send mail to %s", recipient)

    threading.Thread(target=_send, daemon=True).start()



CODE_EXPIRY_MINUTES = 10
logger = logging.getLogger(__name__)
from .serializers import (
    AlertSerializer,
    CameraSerializer,
    CitationSerializer,
    DetectionJobSerializer,
    DispatcherSerializer,
    OfficerSerializer,
    SystemSettingsSerializer,
    UploadSessionSerializer,
    UserSerializer,
    ViolationTypeSerializer,
    ViolatorSerializer,
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
@permission_classes([permissions.AllowAny])
def health(request):
    """Liveness/readiness probe for the hosting platform and uptime monitors.

    Deliberately checks the DATABASE, not just that Python is running. A Django
    process stays perfectly responsive after its database has gone away, so a
    probe that only proves the web server answers would keep a broken instance
    in the load balancer, serving 500s to every real request.

    Returns 200 when healthy and 503 when not, which is what platform health
    checks and uptime monitors act on. Unauthenticated on purpose - the probe
    runs before anything has a token - so it reports component status only and
    never version numbers, settings or connection strings.
    """
    checks = {}
    healthy = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:                      # noqa: BLE001 - report any failure
        # Class name only: the message can contain the host, user and password
        # from the connection string, and this endpoint is public.
        checks["database"] = f"error: {type(exc).__name__}"
        healthy = False
        logger.exception("Health check: database unreachable")

    return Response(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )


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
    try:
        validate_email(email)
    except ValidationError:
        return Response({"email": "Enter a valid email address."}, status=400)
    if User.objects.filter(email__iexact=email).exists():
        return Response({"email": "An account with this email already exists."}, status=400)

    code = f"{random.randint(0, 999999):06d}"
    # Deliberately SYNCHRONOUS, unlike the password-reset path above.
    #
    # Registration happens on the web dashboard, where the browser has no short
    # fetch timeout, and waiting buys something real: a typo'd address is caught
    # here and reported, instead of the user staring at a code that will never
    # arrive. There is no anti-enumeration concern either -- this endpoint
    # already says whether an account exists.
    #
    # The reset path has the opposite shape: it runs on a phone whose HTTP
    # client aborts at 10s, and it must never reveal whether delivery worked.
    try:
        send_mail(
            "Your LookOut verification code",
            f"Your verification code is {code}. It expires in {CODE_EXPIRY_MINUTES} minutes.",
            django_settings.DEFAULT_FROM_EMAIL,
            [email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send verification email to %s", email)
        return Response(
            {"email": "We couldn't send an email to this address. Double-check it and try again."},
            status=400,
        )

    EmailVerificationCode.objects.create(email=email, code=code)
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
        try:
            validate_password(fields["password"], user=User(
                username=fields["username"], email=fields["email"],
                first_name=fields["first_name"], last_name=fields["last_name"],
            ))
        except ValidationError as exc:
            return Response({"password": exc.messages}, status=400)

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
    try:
        validate_email(email)
    except ValidationError:
        return Response({"email": "Enter a valid email address."}, status=400)

    user = User.objects.filter(email__iexact=email).first()
    if user:
        code = f"{random.randint(0, 999999):06d}"
        # The row is written FIRST, then the mail is queued. It used to be the
        # other way round -- row only on a successful send -- which was neat but
        # meant the caller had to wait for SMTP to know whether to write it.
        # Writing first costs an unused row when delivery fails, and buys a
        # response that returns in milliseconds instead of seconds.
        EmailVerificationCode.objects.create(email=email, code=code)
        send_mail_async(
            "Your LookOut password reset code",
            f"Your password reset code is {code}. It expires in "
            f"{CODE_EXPIRY_MINUTES} minutes. If you didn't request this, you "
            "can ignore this email.",
            email,
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


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated, IsAdmin])
def reset_spec_defaults(request):
    """'Reset to spec defaults' for one violation group: {"violation": "drinking" | "smoking" |
    "holdup" | "all"}. Only the adjustable timings / conditions are touched."""
    from core.vision import spec_settings
    group = request.data.get("violation", "")
    if group not in spec_settings.GROUPS:
        return Response({"detail": f"violation must be one of {', '.join(spec_settings.GROUPS)}."}, status=400)
    cfg = SystemSettings.load()
    fields = spec_settings.defaults_for(group)
    for name, value in fields.items():
        setattr(cfg, name, value)
    cfg.save(update_fields=list(fields))
    return Response(SystemSettingsSerializer(cfg).data)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, IsAdmin])
def monitor_status(request):
    """Live monitoring status for the Live Feeds page (admin only)."""
    from core.monitor import monitor
    return Response(monitor.status())


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated, IsAdmin])
def monitor_start(request):
    from core.monitor import monitor
    ok, message = monitor.start(request.user)
    body = monitor.status()
    body["detail"] = message
    return Response(body, status=200 if ok else 400)


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated, IsAdmin])
def monitor_stop(request):
    from core.monitor import monitor
    monitor.stop()
    return Response(monitor.status())


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated, IsAdmin])
def monitor_state(request):
    """The live processing view of the running live monitor (same shape as a job's /state/)."""
    from core import debug_state
    from core.monitor import live_dir, monitor
    since = request.query_params.get("since")
    data = debug_state.payload(live_dir(), int(since) if since and since.isdigit() else None)
    data["monitor"] = monitor.status()
    return Response(data)


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
        "cameras_online": Camera.objects.exclude(code__endswith="-TEST").filter(status=Camera.Status.ONLINE).count(),
        "cameras_total": Camera.objects.exclude(code__endswith="-TEST").count(),
        "alerts_by_status": by_status,
        "alerts_by_type_7d": by_type,
        "weekly_trend": list(weekly_trend),
        "officers_on_duty": Officer.objects.exclude(status=Officer.Status.OFF_DUTY).count(),
    })


class ZoneViewSet(viewsets.ModelViewSet):
    queryset = Zone.objects.all()
    serializer_class = ZoneSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class ViolationTypeViewSet(viewsets.ModelViewSet):
    queryset = ViolationType.objects.all()
    serializer_class = ViolationTypeSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


class CameraViewSet(viewsets.ModelViewSet):
    # "-TEST" cameras exist only to tag alerts from uploaded footage (see
    # DetectionJobViewSet.create). They are not real cameras, so they never
    # appear in any camera list; their alerts are still shown.
    queryset = Camera.objects.select_related("zone").exclude(code__endswith="-TEST")
    serializer_class = CameraSerializer
    filterset_fields = ["zone", "status"]
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]

    @action(detail=True, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def snapshot(self, request, pk=None):
        """Proxies a single still frame from the camera to the dashboard.

        Browsers cannot play RTSP and the camera may sit on an isolated subnet
        the browser can't route to, so the frame is fetched here (server-side,
        with the camera's own credentials) and streamed back as JPEG. The
        dashboard polls this a few times a second for a near-live feed.

        Hikvision exposes a still at ISAPI/Streaming/channels/<ch>/picture; the
        RTSP channel in the stream URL (…/Streaming/Channels/102) maps to it.
        """
        import urllib.parse

        import requests
        from django.http import HttpResponse
        from requests.auth import HTTPDigestAuth

        camera = self.get_object()
        stream_url = camera.resolved_stream_url
        if not stream_url:
            return Response({"detail": "Camera has no stream URL configured."}, status=404)

        parsed = urllib.parse.urlparse(stream_url)
        host = parsed.hostname
        user = urllib.parse.unquote(parsed.username or "")
        pw = urllib.parse.unquote(parsed.password or "")
        # RTSP path .../Channels/101 -> ISAPI snapshot channel; default to sub-stream.
        channel = "102"
        m = re.search(r"/Channels/(\d+)", parsed.path)
        if m:
            channel = m.group(1)

        # The scheme follows the stream URL, and the port with it.
        #
        # A camera on the LAN is addressed rtsp://user:pass@192.168.1.64:554/...
        # and its ISAPI stills are plain http on port 80 — so rtsp (and
        # anything else) means http, as it always did.
        #
        # But when the API runs in the cloud and the camera sits behind an
        # ngrok tunnel, the stream URL holds the tunnel instead:
        # https://user:pass@abc.ngrok-free.app/Streaming/Channels/102. Forcing
        # http:// there fails outright — ngrok's edge only speaks TLS — and the
        # symptom is "Camera unreachable" with a connection error that says
        # nothing about the scheme. Honouring an explicit http/https lets the
        # same field describe either topology.
        # The PORT is only carried over for an explicit http/https URL. An RTSP
        # one names the RTSP port (554), and ISAPI is not served there — reusing
        # it produces http://camera:554/ISAPI/... which never answers.
        if parsed.scheme in ("http", "https"):
            scheme = parsed.scheme
            netloc = host if parsed.port is None else f"{host}:{parsed.port}"
        else:
            scheme, netloc = "http", host
        snap_url = f"{scheme}://{netloc}/ISAPI/Streaming/channels/{channel}/picture"

        try:
            r = requests.get(snap_url, auth=HTTPDigestAuth(user, pw), timeout=6)
        except requests.RequestException as exc:
            return Response({"detail": f"Camera unreachable: {exc}"}, status=502)
        if r.status_code != 200:
            return Response({"detail": f"Camera returned HTTP {r.status_code}."},
                            status=502)

        resp = HttpResponse(r.content, content_type=r.headers.get("Content-Type", "image/jpeg"))
        resp["Cache-Control"] = "no-store"
        return resp

    @action(detail=True, methods=["post"], url_path="edge-frame",
            parser_classes=[MultiPartParser, FormParser],
            permission_classes=[permissions.IsAuthenticated, IsAdmin])
    def edge_frame(self, request, pk=None):
        """Grabs the first frame of an uploaded clip, for drawing obstruction
        edges when the camera itself isn't reachable for a live snapshot.

        Mirrors detection_sandbox/obstruction_web.py's /frame route, minus its
        canonical 960px resize — the frame is returned at its native
        resolution and that resolution is reported back so the caller can
        record it as edges_width/edges_height. watch_parking scales from
        whatever resolution is recorded, so there's nothing to gain from
        downscaling here and one less thing to keep in sync.
        """
        self.get_object()  # 404s early, and confirms the admin may act on this camera

        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "No file uploaded."}, status=400)

        ext = os.path.splitext(upload.name)[1].lower()
        if ext not in DETECTION_UPLOAD_EXTENSIONS:
            return Response(
                {"detail": f"Unsupported file type {ext!r}. Allowed: "
                           f"{', '.join(sorted(DETECTION_UPLOAD_EXTENSIONS))}."},
                status=400,
            )
        if upload.size > DETECTION_MAX_UPLOAD_BYTES:
            limit_mb = DETECTION_MAX_UPLOAD_BYTES // (1024 * 1024)
            return Response({"detail": f"File too large — limit is {limit_mb}MB."}, status=400)

        upload_dir = django_settings.MEDIA_ROOT / "uploads"
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = get_valid_filename(upload.name)
        tmp_path = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
        with open(tmp_path, "wb") as dest:
            for chunk in upload.chunks():
                dest.write(chunk)

        try:
            cap = cv2.VideoCapture(str(tmp_path))
            ok, frame = cap.read()
            cap.release()
        finally:
            os.remove(tmp_path)

        if not ok:
            return Response({"detail": "Could not read that video file."}, status=400)

        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            return Response({"detail": "Could not encode that frame."}, status=500)

        h, w = frame.shape[:2]
        return Response({
            "image": "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii"),
            "width": w,
            "height": h,
        })


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


class CitationFilter(django_filters.FilterSet):
    # lookup_expr="date__..." compares the calendar date, not the raw
    # datetime — plain "gte"/"lte" against a date would compare against
    # midnight UTC, silently excluding almost every timestamp on date_to's day.
    date_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    date_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    violation_type = django_filters.CharFilter(field_name="violations__code", lookup_expr="iexact")

    class Meta:
        model = Citation
        fields = ["alert", "officer", "violator", "violation_type", "date_from", "date_to"]


class CitationViewSet(viewsets.ModelViewSet):
    queryset = Citation.objects.select_related("alert", "officer").prefetch_related("violations").all()
    serializer_class = CitationSerializer
    filterset_class = CitationFilter
    permission_classes = [permissions.IsAuthenticated, CanEditOwnOpenCitation]

    def perform_create(self, serializer):
        """Filing a citation against an alert resolves that alert in the same
        transaction, UNLESS the client explicitly opts out via resolve_alert
        (the mobile app does, while more violators from the same scene are
        still being cited) — this replaces the old client-driven 'PATCH
        status to resolved' flow the dashboard used for resident-linked
        resolution, and the dashboard's own request never sets the flag, so
        its behaviour is unchanged.

        Also resolves/creates the Violator this citation belongs to: the
        client may pass an explicit `violator` (an officer confirming a
        "did you mean" suggestion from /api/violators/search); if omitted,
        an exact normalized-name match is reused, or a new Violator is
        created from the entered names.

        client_uuid makes retries safe: it's generated on-device when the
        citation is created (not when it's sent), so a queued submission
        resent after a dropped connection reuses the same key. A repeat key
        short-circuits here and hands back the citation that already exists
        instead of filing a duplicate."""
        client_uuid = serializer.validated_data.pop("client_uuid", None)
        resolve_alert = serializer.validated_data.pop("resolve_alert", True)

        if client_uuid:
            existing = Citation.objects.filter(client_uuid=client_uuid).first()
            if existing is not None:
                serializer.instance = existing
                return

        with transaction.atomic():
            violator = serializer.validated_data.get("violator")
            if violator is None:
                first = serializer.validated_data.get("first_name_entered", "")
                middle = serializer.validated_data.get("middle_name_entered", "")
                last = serializer.validated_data.get("last_name_entered", "")
                suffix = serializer.validated_data.get("suffix_entered", "")
                violator, _ = Violator.objects.get_or_create(
                    normalized_name=normalize_name(first, middle, last),
                    defaults={
                        "first_name": first, "middle_name": middle,
                        "last_name": last, "suffix": suffix,
                    },
                )

            violator.last_seen = timezone.now()
            violator.save(update_fields=["last_seen"])

            citation = serializer.save(created_by=self.request.user, violator=violator, client_uuid=client_uuid)
            if citation.alert_id and resolve_alert:
                from core import timeline
                who = getattr(self.request.user, "display_name", "") or self.request.user.username
                Alert.objects.filter(pk=citation.alert_id).update(
                    status=Alert.Status.RESOLVED, reviewed_by=self.request.user,
                    reviewed_at=timezone.now(), reviewed_valid=True)
                timeline.add(citation.alert_id, "resolved", "Resolved · citation issued", by=who)

    def perform_update(self, serializer):
        """Correcting a filed citation. Who may, and when, is CanEditOwnOpenCitation.

        The entered names are the citation's own snapshot of what was typed,
        but `violator` is a LINK to a person record resolved from them. Saving
        a corrected name without re-resolving that link leaves the citation
        reading "Juan Cruz" while still counting against whoever it was first
        matched to — the kind of wrong that looks right on screen and only
        surfaces much later, in somebody's citation history. So the same
        resolution perform_create runs happens again here whenever the
        normalized name actually changes.

        Deliberately NOT done here:

        * The alert is never touched. resolve_alert is a create-time decision
          about finishing a scene; an edit is not a second filing, and
          re-resolving (or un-resolving) on a correction would let a typo fix
          silently reopen or close an incident.
        * The old Violator is left alone. It may be shared with other
          citations, and a person record with no citations is still a real
          record — pruning it here would be a side effect nobody asked for.
        * client_uuid is left alone. It identifies the original submission for
          retry purposes; an edit is not a new submission.
        """
        citation = serializer.instance
        data = serializer.validated_data
        # Create-time instructions, not part of the record. Dropped quietly
        # because an older client may still send them; the identity fields
        # (alert / officer / violator) are REFUSED instead, in
        # CitationSerializer.validate — see there for why the two differ. The
        # pops below are belt-and-braces for any future caller that reaches
        # perform_update without passing through that validator.
        for field in ("resolve_alert", "client_uuid", *CitationSerializer.IDENTITY_FIELDS):
            data.pop(field, None)

        def entered(field):
            """The value this save will leave on the row — PATCH is partial, so
            a field the client left out keeps what is already stored."""
            return data.get(f"{field}_entered", getattr(citation, f"{field}_entered"))

        with transaction.atomic():
            # Always from the names. There is no "did you mean" confirmation
            # flow on update the way there is on create, so a name is the only
            # thing a correction can be resolved from.
            first, middle = entered("first_name"), entered("middle_name")
            last, suffix = entered("last_name"), entered("suffix")
            normalized = normalize_name(first, middle, last)
            if normalized == citation.violator.normalized_name:
                serializer.save()
                return
            violator, _ = Violator.objects.get_or_create(
                normalized_name=normalized,
                defaults={"first_name": first, "middle_name": middle,
                          "last_name": last, "suffix": suffix},
            )
            violator.last_seen = timezone.now()
            violator.save(update_fields=["last_seen"])
            serializer.save(violator=violator)


class ViolatorViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ViolatorSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Violator.objects.annotate(citation_count=Count("citations")).order_by("last_name", "first_name")

    @action(detail=False, methods=["get"])
    def search(self, request):
        """Exact match on normalized_name first, then a rapidfuzz pass, top 5
        either way.

        The fuzzy pass scores against each violator's first+last name only
        (middle name excluded) — the web form wires this to First+Last (see
        ViolationModal.jsx), so scoring against the full normalized_name
        (which includes middle name) would dock a correct match just for
        omitting a middle name the officer never typed."""
        q = request.query_params.get("q", "").strip()
        if not q:
            return Response([])

        normalized_q = normalize_name(q, "", "")
        queryset = self.get_queryset()

        exact = list(queryset.filter(normalized_name=normalized_q)[:5])
        if exact:
            return Response(ViolatorSerializer(exact, many=True).data)

        violators = list(queryset)
        by_id = {v.id: v for v in violators}
        core_names = {v.id: normalize_name(v.first_name, "", v.last_name) for v in violators}
        matches = rapidfuzz_process.extract(
            normalized_q, core_names, score_cutoff=85, limit=5,
        )
        fuzzy = [by_id[vid] for _name, _score, vid in matches]
        return Response(ViolatorSerializer(fuzzy, many=True).data)

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        """Merges `loser_id` into this (winning) violator: reassigns the
        loser's citations, appends the loser's name to aliases, deletes the
        loser. The winner is the one named in the URL; the loser never
        outlives this call, so citations, not the loser row, are the thing
        that must never silently disappear here."""
        winner = self.get_object()
        loser_id = request.data.get("loser_id")
        if not loser_id:
            return Response({"detail": "loser_id is required."}, status=400)
        if str(loser_id) == str(winner.pk):
            return Response({"detail": "Cannot merge a violator into itself."}, status=400)

        loser = Violator.objects.filter(pk=loser_id).first()
        if loser is None:
            return Response({"detail": "loser_id does not match an existing violator."}, status=404)

        with transaction.atomic():
            Citation.objects.filter(violator=loser).update(violator=winner)
            winner.aliases = [*winner.aliases, str(loser)]
            if loser.last_seen and (not winner.last_seen or loser.last_seen > winner.last_seen):
                winner.last_seen = loser.last_seen
            winner.save(update_fields=["aliases", "last_seen"])
            loser.delete()

        winner.refresh_from_db()
        return Response(ViolatorSerializer(winner).data)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def barangays(request):
    return Response([{"value": value, "label": label} for value, label in ZAMBOANGA_BARANGAYS])


class AlertViewSet(viewsets.ModelViewSet):
    queryset = (
        Alert.objects
        .select_related("type", "camera", "reviewed_by")
        .prefetch_related("officers_assigned")
        .all()
    )
    serializer_class = AlertSerializer
    filterset_fields = ["status", "type", "camera"]

    def get_queryset(self):
        """Monitoring events are a quiet watchlist (spec v6): they never appear in
        the normal alert list or notify anyone. Ask for them explicitly with
        ?level=monitoring (the dashboard watchlist) or ?include_monitoring=1.

        Both lists split on peak_level, NOT on the current level, so the two
        remain a clean partition and nothing can fall between them. An event
        that reached Possible and then faded back to Monitoring stays in
        Potential Violations and does not reappear on the watchlist: it has
        already earned a reviewer's attention, and having it vanish from under
        them mid-review was the behaviour this replaces. Its badge still reads
        the CURRENT status — only where it is listed is decided by the peak.
        """
        qs = super().get_queryset()
        if self.action == "list":
            params = self.request.query_params
            if params.get("level") == "monitoring":
                return qs.filter(peak_level="monitoring")
            if params.get("include_monitoring") not in ("1", "true"):
                qs = qs.exclude(peak_level="monitoring")
        return qs

    # Statuses that mean somebody has looked at the footage and closed the
    # matter: resolved (attended and dealt with) or acknowledged (judged a
    # false alarm and dismissed). These are exactly the alerts the Records
    # page lists, which is where the reviewer needs to be shown.
    REVIEWED_STATUSES = (Alert.Status.RESOLVED, Alert.Status.ACKNOWLEDGED)

    def perform_update(self, serializer):
        """Records WHO closed the alert, keeps its timeline, and silently records the verdict.

        The reviewer is whoever dismissed the alert or marked it resolved, taken from the
        authenticated request (never from the payload). The verdict `reviewed_valid` has no UI; it
        is recorded for evaluating the system:
            Dismiss (web or officer app, also after assignment)  -> False  (a false alarm)
            Assign officers / an officer accepting               -> True   (worth attending)
        """
        from core import timeline
        before = serializer.instance
        previous_status = before.status
        previous_officers = set(before.officers_assigned.values_list("pk", flat=True))
        alert = serializer.save()
        user = self.request.user if self.request.user.is_authenticated else None
        who = (getattr(user, "display_name", "") or getattr(user, "username", "")) if user else ""
        officers = list(alert.officers_assigned.all())
        events = []
        update = {}

        if set(o.pk for o in officers) != previous_officers and officers:
            events.append(("assigned", "Assigned to " + ", ".join(o.name for o in officers)))
            update["reviewed_valid"] = True
        elif alert.status == Alert.Status.DISPATCHED and previous_status != Alert.Status.DISPATCHED:
            events.append(("assigned", "Assigned"))
            update["reviewed_valid"] = True

        if alert.status != previous_status:
            if alert.status == Alert.Status.ACKNOWLEDGED:
                events.append(("dismissed", alert.notes or "Dismissed"))
                update["reviewed_valid"] = False          # a false alarm, even after it was assigned
            elif alert.status == Alert.Status.RESOLVED:
                events.append(("resolved", "Resolved"))
                update["reviewed_valid"] = True
            elif previous_status in self.REVIEWED_STATUSES:
                events.append(("reopened", "Reopened"))
            if alert.status in self.REVIEWED_STATUSES:
                update["reviewed_by"] = user
                update["reviewed_at"] = timezone.now()
            else:
                # Reopened (or assigned): the earlier reviewer no longer closed anything.
                update["reviewed_by"] = None
                update["reviewed_at"] = None

        for kind, label in events:
            timeline.add(alert.pk, kind, label, by=who)
        if update:
            Alert.objects.filter(pk=alert.pk).update(**update)
            for name, value in update.items():
                setattr(alert, name, value)

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
        was_assigned = alert.officers_assigned.filter(pk=officer.pk).exists()
        alert.officers_assigned.add(officer)
        if alert.status == Alert.Status.ACTIVE:
            alert.status = Alert.Status.DISPATCHED
            alert.save(update_fields=["status"])
        if not was_assigned:
            from core import timeline
            timeline.add(alert.pk, "assigned", f"Assigned to {officer.name}", by=officer.name)
            Alert.objects.filter(pk=alert.pk).update(reviewed_valid=True)

        return Response(self.get_serializer(alert).data)


# ---- Detection job upload/launch (admin test harness) ----------------------
# Only detectors with a working --source (file) mode can be driven from an
# upload — watch_curfew is webcam-only, and watch_smoking_pose/watch_all are
# alternate/composite entry points rather than a single selectable type.
# watch_merged is also composite (it can produce smoking/drinking/thief
# alerts from one run) but, unlike watch_all, is deliberately exposed here —
# it's the one composite command meant to be launched from the upload UI.
DETECTION_COMMANDS = {
    "smoking": "watch_smoking",
    "drinking": "watch_drinking",
    "thief": "watch_thief",
    "parking": "watch_parking",
    # Merged Bottle/Cigarette/knife model — one detection pass, routed to
    # smoking/drinking/thief's own rule layers (see watch_merged.py). A
    # single run can therefore produce alerts of all three of those
    # ViolationTypes; "merged" itself is only the DetectionJob's own label,
    # not a ViolationType.
    "merged": "watch_merged",
    # The same three plus road-edge obstruction, from one feed (see
    # watch_merged_all.py). Parking's own detector and rule layer run
    # alongside the merged model's, so a single run can produce alerts of all
    # four ViolationTypes. Obstruction only — it refuses to start without a
    # marked no-parking area, which is why it is in EDGE_REQUIRED below.
    "merged4": "watch_merged_all",
}
# Detectors that judge vehicles against a marked no-parking area. Two tiers,
# because the two mean different things to a caller:
#
#   EDGE_CAPABLE   an area is used if one was drawn, and the run is still
#                  valid without it. "parking" falls back to its plain dwell
#                  rule (see watch_parking.py).
#   EDGE_REQUIRED  the run is refused without one. watch_merged_all has no
#                  fallback on purpose, so rejecting it here turns what would
#                  be a FAILED job with the reason buried in a subprocess log
#                  into an immediate, visible error on the page.
EDGE_REQUIRED = frozenset({"merged4"})
EDGE_CAPABLE = frozenset({"parking"}) | EDGE_REQUIRED
DETECTION_UPLOAD_EXTENSIONS = {".mp4", ".mkv", ".avi"}
DETECTION_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1GB


def _check_clip_name(filename):
    """The extension complaint for `filename`, or None if it is acceptable.

    Checked at the START of a chunked upload as well as on a one-shot one: a
    200 MB clip should be refused for its type before any of it crosses the
    wire, not after.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in DETECTION_UPLOAD_EXTENSIONS:
        return (f"Unsupported file type {ext!r}. Allowed: "
                f"{', '.join(sorted(DETECTION_UPLOAD_EXTENSIONS))}.")
    return None


def _staged_frame_response(saved_path, token, source_filename):
    """Reads the staged clip's first frame and shapes the staging reply.

    Shared by DetectionJobViewSet.frame (one-shot upload) and
    UploadSessionViewSet.complete (chunked), so both hand the page the same
    payload — and so the decode check that catches a corrupt or mislabelled
    file (a matching extension can be spoofed) lives on exactly one path.
    Deletes the staged clip on failure: there is nothing a detector could do
    with a file that will not open.
    """
    cap = cv2.VideoCapture(str(saved_path))
    ok, first = cap.read()
    cap.release()
    if not ok:
        try:
            os.remove(saved_path)
        except OSError:
            pass
        return None, Response({"detail": "Could not read that video file."}, status=400)

    ok, buf = cv2.imencode(".jpg", first)
    if not ok:
        try:
            os.remove(saved_path)
        except OSError:
            pass
        return None, Response({"detail": "Could not encode that frame."}, status=500)

    h, w = first.shape[:2]
    return {
        "staged_token": token,
        "source_filename": source_filename,
        "image": "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii"),
        "width": w,
        "height": h,
    }, None


def _tail_log(path, max_chars=4000):
    """Last bit of a detection job's combined stdout/stderr, for surfacing why
    it failed without shipping the whole log to the client."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_chars))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _watch_detection_job(job_id, proc, log_path, source_path):
    """Runs in a daemon thread per launched job — blocks on the subprocess's
    exit code (liveness alone can't tell success from failure) and updates the
    DB row once it's known. No task queue: this assumes a single long-lived
    `runserver` process, which is what this project actually runs; it wouldn't
    generalise to a multi-worker WSGI deployment without a real queue."""
    returncode = proc.wait()
    job = DetectionJob.objects.filter(id=job_id).first()
    if job is None:
        return
    # A cancel request kills this same subprocess, which is exactly what
    # unblocks proc.wait() above — so this thread and DetectionJobViewSet.cancel
    # race to write the final status for the same exit. cancel() writes
    # CANCELLED to the DB BEFORE it signals the process, so if it got there
    # first this thread must not clobber it with DONE/FAILED (a killed
    # process's non-zero returncode would otherwise read as a crash, not a
    # deliberate stop). Cleanup below still runs either way.
    if job.status != DetectionJob.Status.CANCELLED:
        job.status = DetectionJob.Status.DONE if returncode == 0 else DetectionJob.Status.FAILED
        job.finished_at = timezone.now()
        if returncode != 0:
            job.error = _tail_log(log_path)
        job.save(update_fields=["status", "finished_at", "error"])
    # The uploaded source clip is scratch input, not evidence — the watcher's
    # own evidence clips (media/violations/) are separate and untouched. A
    # live job's source_path is the camera's stream URL, not a file — nothing
    # to clean up, and it's never a real path to begin with.
    if job.camera_id is None:
        try:
            os.remove(source_path)
        except OSError:
            pass


# Abandoned chunked uploads are swept on this schedule. The machine this runs
# on has been down to 0.44 GB free (see core/apps._close_orphaned_jobs), so
# half-sent 200 MB clips cannot be left lying around indefinitely.
#
# Two different clocks, because the two kinds of leftover mean different
# things. A session still UPLOADING is only abandoned once nothing has touched
# it for a while — the window has to be comfortably longer than a slow upload
# of a 1 GB clip over a bad link, or the sweep would delete parts out from
# under a user who is still sending them. A session already COMPLETE holds a
# whole staged clip that nobody started a run with; a day is long enough for
# somebody to come back and press Start.
STALE_UPLOAD_HOURS = 12
UNUSED_STAGED_CLIP_HOURS = 24
# Most a single user may have part-way through at once. Nothing legitimate
# needs more (the page uploads one clip at a time); the cap is what stops a
# stuck client from filling the disk with part files faster than the sweep
# clears them. Over the cap, that user's OLDEST unfinished upload is dropped.
MAX_LIVE_UPLOADS_PER_USER = 3


def _discard_upload_session(session):
    """Deletes a session's row and its part files. Never touches a staged clip
    a detection job is using."""
    shutil.rmtree(session.part_dir, ignore_errors=True)
    if session.staged_token:
        staged = django_settings.MEDIA_ROOT / "uploads" / session.staged_token
        # A run may already have been started from this clip — the subprocess
        # is reading the file right now, and _watch_detection_job removes it
        # when the run ends. Deleting it here would break a run in progress.
        in_use = DetectionJob.objects.filter(source_path=str(staged)).exists()
        if not in_use:
            try:
                os.remove(staged)
            except OSError:
                pass
    session.delete()


def _purge_stale_uploads():
    """Clears out upload sessions nobody is coming back to.

    Called when a session is created or listed rather than on a timer: those
    are the moments a client is about to need disk, there is no task queue in
    this project to schedule a sweep in, and it keeps the work proportional to
    actual use.
    """
    now = timezone.now()
    stale = UploadSession.objects.filter(
        status=UploadSession.Status.UPLOADING,
        updated_at__lt=now - timedelta(hours=STALE_UPLOAD_HOURS),
    )
    unused = UploadSession.objects.filter(
        status=UploadSession.Status.COMPLETE,
        updated_at__lt=now - timedelta(hours=UNUSED_STAGED_CLIP_HOURS),
    )
    for session in list(stale) + list(unused):
        _discard_upload_session(session)


class UploadSessionViewSet(viewsets.GenericViewSet):
    """Resumable chunked upload of a source clip, for Run Detection.

    The problem it solves: the clips this system is pointed at are routinely
    200 MB, and a single multipart POST of 200 MB is one request that must
    survive end to end. Over a LAN that is merely slow; through a tunnel or a
    proxy with a read timeout it fails outright, and every failure costs the
    whole upload. Worse, a page refresh (which this dashboard treats as a full
    re-login, by design — see lookout/src/api.js) threw the upload away
    entirely: the browser cannot re-read a File it no longer holds a handle to.

    So the clip is sent as `chunk_size` pieces against one session row:

        POST   /api/uploads/                 declare filename + size, get a session
        POST   /api/uploads/<id>/chunk/      one piece (form fields: index, chunk)
        GET    /api/uploads/<id>/            which pieces have landed
        POST   /api/uploads/<id>/complete/   stitch them, decode-check, stage
        DELETE /api/uploads/<id>/            give up, delete the pieces

    Each chunk is its own small request, so a failure retries a few megabytes
    instead of a few hundred, and pieces may go up in parallel. Nothing about
    progress is stored beyond the files themselves, so "where was I" is
    answered the same way after a dropped request, a new tab or a reboot: GET
    the session and send whatever is missing from `received_indices`.

    complete() returns exactly what DetectionJobViewSet.frame returns —
    including the staged_token — so the rest of the flow (draw edges, trim,
    Start) is unchanged and cannot tell a chunked upload from a one-shot one.
    """

    queryset = UploadSession.objects.all()
    serializer_class = UploadSessionSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    # Multipart carries the chunks; JSON is here because complete() and
    # destroy() are called through the dashboard's ordinary JSON fetch helper
    # and would otherwise be one added request.data read away from a 415.
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ["get", "post", "delete", "head"]

    def get_queryset(self):
        # Sessions are strictly per-user: one admin must never be able to
        # resume, read or abort another's upload by guessing an id.
        return UploadSession.objects.filter(created_by=self.request.user)

    def list(self, request):
        """This user's unfinished uploads, newest first.

        The page asks on load, so it can say "clip.mp4 was 62% uploaded — pick
        the same file to carry on". That is the honest version of surviving a
        refresh: the server kept the bytes, but only the user can hand the
        browser the file again. Resuming itself needs no call here — create()
        matches on the fingerprint and returns the existing session.

        `?fingerprint=` narrows the list to one file, for a client that wants to
        ask about a specific clip rather than list everything.
        """
        _purge_stale_uploads()
        sessions = self.get_queryset().filter(status=UploadSession.Status.UPLOADING)
        fingerprint = request.query_params.get("fingerprint")
        if fingerprint:
            sessions = sessions.filter(fingerprint=fingerprint)
        return Response(self.get_serializer(sessions, many=True).data)

    def create(self, request):
        """Starts an upload — or hands back the one already in progress for the
        same file, which is what makes a resume a resume rather than a restart."""
        if not getattr(django_settings, "DETECTION_ENABLED", True):
            # Refused HERE, before a single chunk is sent, rather than at job
            # creation: a clip staged on a server that cannot run detectors is
            # 200 MB uploaded for nothing.
            return Response({"detail": DetectionJobViewSet.DETECTION_DISABLED}, status=503)

        _purge_stale_uploads()

        filename = (request.data.get("filename") or "").strip()
        if not filename:
            return Response({"detail": "filename is required."}, status=400)
        problem = _check_clip_name(filename)
        if problem:
            return Response({"detail": problem}, status=400)

        try:
            size = int(request.data.get("size"))
        except (TypeError, ValueError):
            return Response({"detail": "size must be the file's length in bytes."}, status=400)
        if size <= 0:
            return Response({"detail": "size must be the file's length in bytes."}, status=400)
        if size > DETECTION_MAX_UPLOAD_BYTES:
            limit_mb = DETECTION_MAX_UPLOAD_BYTES // (1024 * 1024)
            return Response({"detail": f"File too large — limit is {limit_mb}MB."}, status=400)

        fingerprint = (request.data.get("fingerprint") or "")[:200]

        # Rejoin an unfinished upload of the same file. The size has to match
        # as well as the fingerprint: an edited file reusing a name and
        # timestamp would otherwise resume onto chunks of the old one and
        # stitch two different videos together.
        if fingerprint:
            existing = self.get_queryset().filter(
                status=UploadSession.Status.UPLOADING, fingerprint=fingerprint, size=size,
            ).first()
            if existing is not None:
                os.makedirs(existing.part_dir, exist_ok=True)
                return Response(self.get_serializer(existing).data, status=200)

        session = UploadSession.objects.create(
            filename=filename[:255], size=size, fingerprint=fingerprint,
            chunk_size=UploadSession.CHUNK_BYTES, created_by=request.user,
        )
        os.makedirs(session.part_dir, exist_ok=True)

        # Keep only the newest few unfinished uploads for this user; see
        # MAX_LIVE_UPLOADS_PER_USER.
        surplus = list(self.get_queryset().filter(
            status=UploadSession.Status.UPLOADING,
        )[MAX_LIVE_UPLOADS_PER_USER:])
        for old in surplus:
            _discard_upload_session(old)

        return Response(self.get_serializer(session).data, status=201)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def destroy(self, request, pk=None):
        """Give up on an upload and reclaim its disk straight away, instead of
        waiting out STALE_UPLOAD_HOURS."""
        _discard_upload_session(self.get_object())
        return Response(status=204)

    @action(detail=True, methods=["post"], url_path="chunk",
            parser_classes=[MultiPartParser, FormParser])
    def chunk(self, request, pk=None):
        """Stores one piece. Idempotent: re-sending a piece that already
        landed is harmless, so a client that gave up waiting for the reply can
        simply send it again without corrupting anything."""
        session = self.get_object()
        if session.status == UploadSession.Status.COMPLETE:
            return Response({"detail": "This upload is already complete."}, status=409)

        try:
            index = int(request.data.get("index"))
        except (TypeError, ValueError):
            return Response({"detail": "index must be the chunk's 0-based position."}, status=400)
        if not 0 <= index < session.total_chunks:
            return Response(
                {"detail": f"index out of range — this upload has {session.total_chunks} chunks."},
                status=400,
            )

        piece = request.FILES.get("chunk")
        if piece is None:
            return Response({"detail": "No chunk uploaded."}, status=400)

        # The one real integrity check: every chunk but the last must be
        # exactly chunk_size, and the last exactly the remainder. Together
        # these make the stitched file's length provably the size declared (and
        # already bounds-checked) at create(), so no sequence of chunk posts
        # can write a file larger than the upload limit.
        expected = session.chunk_length(index)
        if piece.size != expected:
            return Response(
                {"detail": f"Chunk {index} should be {expected} bytes, got {piece.size}."},
                status=400,
            )

        os.makedirs(session.part_dir, exist_ok=True)
        # Written under a scratch name and renamed into place, so a request
        # that dies mid-write never leaves a short file under a name the
        # resume logic would read as "this chunk already landed". os.replace is
        # atomic, which also makes a re-sent chunk safe to overwrite.
        tmp = session.part_dir / f"{index}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp, "wb") as dest:
                for block in piece.chunks():
                    dest.write(block)
            os.replace(tmp, session.part_path(index))
        except OSError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return Response({"detail": f"Could not store that chunk: {exc}"}, status=500)

        # Marks the session as still moving, so the sweep leaves it alone.
        session.save(update_fields=["updated_at"])
        return Response({"index": index, "received_bytes": session.received_bytes()})

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        """Stitches the pieces into an ordinary staged clip and returns its
        first frame — the same payload, and the same staged_token contract, as
        DetectionJobViewSet.frame."""
        session = self.get_object()
        upload_dir = django_settings.MEDIA_ROOT / "uploads"

        if session.status == UploadSession.Status.COMPLETE:
            # A client whose complete() reply was lost retries it. The clip is
            # already stitched, so re-read its frame rather than fail — unless
            # the clip is gone (a run consumed it), in which case there is
            # genuinely nothing left to stage.
            staged = upload_dir / session.staged_token
            if staged.is_file():
                payload, error = _staged_frame_response(staged, session.staged_token, session.filename)
                return error or Response(payload)
            return Response({"detail": "Staged upload expired — upload the clip again."}, status=410)

        missing = sorted(set(range(session.total_chunks)) - set(session.received_indices()))
        if missing:
            return Response(
                {"detail": f"{len(missing)} chunk(s) are still missing.", "missing": missing[:50]},
                status=409,
            )

        os.makedirs(upload_dir, exist_ok=True)
        token = f"{uuid.uuid4().hex}_{get_valid_filename(session.filename)}"
        staged = upload_dir / token
        try:
            with open(staged, "wb") as dest:
                for index in range(session.total_chunks):
                    part = session.part_path(index)
                    with open(part, "rb") as piece:
                        shutil.copyfileobj(piece, dest, 1024 * 1024)
                    # Dropped as it is consumed, so stitching needs roughly the
                    # clip's own size in free space rather than twice it. The
                    # disk this runs on has been full enough for that to matter.
                    try:
                        os.remove(part)
                    except OSError:
                        pass
        except OSError as exc:
            try:
                os.remove(staged)
            except OSError:
                pass
            return Response({"detail": f"Could not assemble the upload: {exc}"}, status=500)

        shutil.rmtree(session.part_dir, ignore_errors=True)

        written = staged.stat().st_size
        if written != session.size:
            # Belt and braces — the per-chunk length checks should make this
            # impossible. If it ever fires the clip is wrong, and a wrong clip
            # must not be handed to a detector that produces violation records.
            os.remove(staged)
            session.delete()
            return Response(
                {"detail": f"Assembled {written} bytes but the file was declared as "
                           f"{session.size}. Upload it again."},
                status=400,
            )

        payload, error = _staged_frame_response(staged, token, session.filename)
        if error is not None:
            # _staged_frame_response already deleted the unreadable clip; the
            # session goes with it, so a retry starts clean rather than
            # resuming into a file that will never decode.
            session.delete()
            return error

        session.status = UploadSession.Status.COMPLETE
        session.staged_token = token
        session.save(update_fields=["status", "staged_token", "updated_at"])
        return Response(payload)


class DetectionJobViewSet(viewsets.ModelViewSet):
    """Admin-only test harness: upload a video clip, run one of the existing
    watch_* management commands against it exactly as it runs from the
    terminal (no detection logic is duplicated here), and expose the run's
    status. Alerts it produces land in the normal Alert table via a dedicated
    "<TYPE>-TEST" camera, so they're visible in the Violations tab like any
    other alert but distinguishable from live-camera ones.
    """

    queryset = DetectionJob.objects.select_related("created_by").all()
    serializer_class = DetectionJobSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ["get", "post", "head"]

    # staged_token shape: "<32 hex uuid>_<safe filename>", exactly what frame()
    # and create()'s own direct-upload path both write below — anchoring the
    # regex this tightly (rather than just checking for path separators) is
    # what makes resolving it into a MEDIA_ROOT/uploads path safe from
    # traversal, since get_valid_filename() already stripped '/'/'\\' when
    # the token was created.
    _STAGED_TOKEN_RE = re.compile(r"^[0-9a-f]{32}_.+$")

    @action(detail=False, methods=["post"], url_path="frame",
            parser_classes=[MultiPartParser, FormParser])
    def frame(self, request):
        """Saves the upload and returns its first frame, so a violation type
        that needs spatial config (currently just parking) can have its edges
        drawn before detection starts. Mirrors CameraViewSet.edge_frame /
        detection_sandbox/obstruction_web.py's own /frame route, except no
        Camera needs to exist yet — create() below resolves the returned
        staged_token back into this same saved file rather than requiring a
        second upload of a potentially huge clip.
        """
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "No file uploaded."}, status=400)

        problem = _check_clip_name(upload.name)
        if problem:
            return Response({"detail": problem}, status=400)
        if upload.size > DETECTION_MAX_UPLOAD_BYTES:
            limit_mb = DETECTION_MAX_UPLOAD_BYTES // (1024 * 1024)
            return Response({"detail": f"File too large — limit is {limit_mb}MB."}, status=400)

        upload_dir = django_settings.MEDIA_ROOT / "uploads"
        os.makedirs(upload_dir, exist_ok=True)
        safe_name = get_valid_filename(upload.name)
        token = f"{uuid.uuid4().hex}_{safe_name}"
        saved_path = upload_dir / token
        with open(saved_path, "wb") as dest:
            for chunk in upload.chunks():
                dest.write(chunk)

        payload, error = _staged_frame_response(saved_path, token, upload.name)
        return error or Response(payload)

    # Refused where the detectors cannot run. A job here Popens `manage.py
    # watch_*`, which needs the GPU, the model weights and the camera — none of
    # which exist on the hosted API. Without this the job row is created, the
    # subprocess dies on a missing .pt file, and the page sits on "processing"
    # for ever with the reason in a log nobody opens. 503 says it is the
    # server's capability, not the request, that is wrong.
    DETECTION_DISABLED = (
        "This server does not run detectors — it has no GPU, no model weights "
        "and no camera. Run detection on the machine beside the camera; its "
        "alerts appear here automatically."
    )

    def create(self, request, *args, **kwargs):
        if not getattr(django_settings, "DETECTION_ENABLED", True):
            return Response({"detail": self.DETECTION_DISABLED}, status=503)
        violation_type = request.data.get("violation_type", "")
        command = DETECTION_COMMANDS.get(violation_type)
        if command is None:
            return Response(
                {"detail": f"Unknown violation_type. Choose one of: {', '.join(DETECTION_COMMANDS)}."},
                status=400,
            )

        # A live camera runs the same watch_* command against its stream URL
        # instead of an uploaded file: no staging, no decode check, and
        # --camera is the real camera's own code (not a synthetic "-TEST"
        # one), so alerts land where a live-camera alert belongs. Parking
        # edges for a live camera are set separately via CameraViewSet's Edge
        # Zones (EdgeEditorModal) — that already draws against this exact
        # camera's live snapshot and writes to camera.edges directly, so
        # there's nothing to stage here; watch_parking picks it up the same
        # way it does for any other --camera.
        camera_id = request.data.get("camera_id")
        camera = None
        if camera_id:
            try:
                camera = Camera.objects.get(pk=camera_id)
            except (Camera.DoesNotExist, ValueError, TypeError):
                return Response({"detail": "Camera not found."}, status=400)
            stream_url = camera.resolved_stream_url
            if not stream_url:
                return Response({"detail": "Camera has no stream URL configured."}, status=400)
            # A live run takes the area off the camera record (Live Feeds →
            # Edge Zones), so there is nothing to stage — but an EDGE_REQUIRED
            # detector still cannot run without one, and the camera row is the
            # only place to check.
            if violation_type in EDGE_REQUIRED and not camera.edges:
                return Response(
                    {"detail": f"{camera.name} has no no-parking area saved. Draw one on "
                               "Live Feeds → Edge Zones first — this detector judges "
                               "vehicles by how much of them sits inside that area."},
                    status=400,
                )
            source_arg = stream_url
            source_filename = f"Live — {camera.name}"
            camera_code = camera.code
        else:
            # Either a clip already staged (staged_token), or a fresh direct
            # upload (file). Every entry point in the web dashboard now stages
            # first, and stages in CHUNKS (UploadSessionViewSet) — a single
            # request carrying the 200 MB clips this is pointed at is what
            # times out and loses the lot. The direct file path is kept because
            # it is the simplest way to drive a run from a script or a test,
            # where the clip is small and one request is fine.
            staged_token = request.data.get("staged_token", "")
            upload = request.FILES.get("file")
            if staged_token:
                if not self._STAGED_TOKEN_RE.match(staged_token):
                    return Response({"detail": "Invalid staged_token."}, status=400)
                saved_path = django_settings.MEDIA_ROOT / "uploads" / staged_token
                if not saved_path.is_file():
                    return Response({"detail": "Staged upload expired — upload the clip again."}, status=400)
                source_filename = request.data.get("source_filename") or staged_token
            elif upload is not None:
                ext = os.path.splitext(upload.name)[1].lower()
                if ext not in DETECTION_UPLOAD_EXTENSIONS:
                    return Response(
                        {"detail": f"Unsupported file type {ext!r}. Allowed: "
                                   f"{', '.join(sorted(DETECTION_UPLOAD_EXTENSIONS))}."},
                        status=400,
                    )
                if upload.size > DETECTION_MAX_UPLOAD_BYTES:
                    limit_mb = DETECTION_MAX_UPLOAD_BYTES // (1024 * 1024)
                    return Response({"detail": f"File too large — limit is {limit_mb}MB."}, status=400)

                upload_dir = django_settings.MEDIA_ROOT / "uploads"
                os.makedirs(upload_dir, exist_ok=True)
                safe_name = get_valid_filename(upload.name)
                saved_path = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
                with open(saved_path, "wb") as dest:
                    for chunk in upload.chunks():
                        dest.write(chunk)

                # A matching extension can be spoofed — a quick decode check catches
                # a corrupt or non-video file before a detector is launched against
                # it. Skipped for the staged_token path above since frame() already
                # proved the file decodes.
                cap = cv2.VideoCapture(str(saved_path))
                opened = cap.isOpened()
                cap.release()
                if not opened:
                    os.remove(saved_path)
                    return Response({"detail": "File could not be read as a video."}, status=400)
                source_filename = upload.name
            else:
                return Response({"detail": "No file uploaded."}, status=400)

            source_arg = str(saved_path)
            # The "-TEST" suffix is load-bearing: it marks alerts from uploaded
            # footage (kept for accuracy evaluation) so they stay separable from
            # live-camera alerts, and CameraViewSet / dashboard_stats hide any
            # camera with that suffix. Do not rename or drop it.
            camera_code = f"CAM-{violation_type.upper()}-TEST"

            # Edge-using detectors only: edges drawn against the staged frame
            # are written onto the shared CAM-<TYPE>-TEST camera before the
            # subprocess starts, so watch_parking's own self.camera.edges
            # branch (which correctly rescales from edges_width/height to
            # whatever the clip actually decodes at — see
            # watch_parking._build_monitors) picks them up, whether it is
            # running as the parking watcher or as watch_merged_all's fourth
            # engine. Two runs of the same type started close together will
            # race on this shared row; accepted as a known limitation of the
            # existing single-camera test harness rather than fixed here.
            edges_raw = request.data.get("edges")
            if violation_type in EDGE_REQUIRED and not edges_raw:
                return Response(
                    {"detail": "Draw the no-parking area on the clip's first frame "
                               "before starting this run — it judges vehicles by how "
                               "much of them sits inside that area, so without one "
                               "there is nothing to judge them against."},
                    status=400,
                )
            if violation_type in EDGE_CAPABLE and edges_raw:
                try:
                    edges = json.loads(edges_raw) if isinstance(edges_raw, str) else edges_raw
                except ValueError:
                    return Response({"detail": "Invalid edges JSON."}, status=400)
                test_camera, _ = Camera.objects.get_or_create(
                    code=camera_code,
                    defaults={"name": f"{violation_type.capitalize()} Monitor",
                              "status": Camera.Status.ONLINE},
                )
                test_camera.edges = edges
                edges_width = request.data.get("edges_width")
                edges_height = request.data.get("edges_height")
                if edges_width:
                    test_camera.edges_width = int(edges_width)
                if edges_height:
                    test_camera.edges_height = int(edges_height)
                obstruction_pct = request.data.get("obstruction_pct")
                obstruction_minutes = request.data.get("obstruction_minutes")
                if obstruction_pct:
                    test_camera.obstruction_pct = int(obstruction_pct)
                if obstruction_minutes:
                    test_camera.obstruction_minutes = float(obstruction_minutes)
                test_camera.save(update_fields=[
                    "edges", "edges_width", "edges_height",
                    "obstruction_pct", "obstruction_minutes",
                ])

        log_dir = django_settings.MEDIA_ROOT / "uploads"
        os.makedirs(log_dir, exist_ok=True)
        log_path = f"{source_arg}.log" if camera is None else str(log_dir / f"{uuid.uuid4().hex}_live.log")
        log_file = open(log_path, "w")
        try:
            # Anaconda's numpy/MKL and PyTorch both bundle libiomp5md.dll; when
            # both get loaded in the same process (as they are here — cv2 +
            # torch/ultralytics inside the watcher) OpenMP aborts with error #15
            # rather than silently picking one. This must be set on the
            # subprocess's own env, not assumed inherited from whatever shell
            # happened to launch `runserver`.
            env = os.environ.copy()
            env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
            # --cascade only exists on watch_smoking (native-res person crops —
            # matches smoking_v5's ~25px training domain). The other three
            # watchers never define this flag, so passing it to them would
            # make argparse reject the whole command outright.
            cascade_args = ["--cascade"] if violation_type == "smoking" else []
            # Trim: run over part of an uploaded clip instead of all of it.
            # Every watcher this page can launch takes --start/--end (see
            # core/vision/trim.py), so unlike --cascade this needs no per-type
            # guard. Never for a live camera, which has no position to seek to.
            if camera is None:
                try:
                    trim_start = float(request.data.get("trim_start") or 0)
                    trim_end = float(request.data.get("trim_end") or 0)
                except (TypeError, ValueError):
                    log_file.close()
                    return Response({"detail": "trim_start / trim_end must be seconds."},
                                    status=400)
                if trim_start < 0 or (trim_end and trim_end <= trim_start):
                    log_file.close()
                    return Response({"detail": "The trim must end after it starts."},
                                    status=400)
                if trim_start:
                    cascade_args += ["--start", f"{trim_start:.3f}"]
                if trim_end:
                    cascade_args += ["--end", f"{trim_end:.3f}"]
            # Footage start time ("recorded at"): drives the holdup time block and the drinking
            # evening band. Only for uploaded clips, and only the commands that take --clock.
            recorded_at = (request.data.get("recorded_at") or "").strip().replace("T", " ")[:16]
            if recorded_at and camera is None and violation_type in (
                    "drinking", "thief", "merged", "merged4"):
                from core.vision.clock import parse_clock
                try:
                    parse_clock(recorded_at)
                except ValueError:
                    log_file.close()
                    return Response({"detail": "recorded_at must look like 2026-08-24 16:17."}, status=400)
                cascade_args += ["--clock", recorded_at]
            else:
                recorded_at = ""
            job = DetectionJob.objects.create(
                violation_type=violation_type,
                source_filename=source_filename,
                source_path=source_arg,
                camera=camera,
                status=DetectionJob.Status.RUNNING,
                pid=None,
                created_by=request.user,
                recorded_at=recorded_at,
            )
            # Where the detector publishes its live processing view (Run Detection page).
            from core import monitor as live_monitor
            view_dir = live_monitor.job_dir(job.id)
            os.makedirs(view_dir, exist_ok=True)
            env["LOOKOUT_DEBUG_DIR"] = str(view_dir)
            try:
                proc = subprocess.Popen(
                    [sys.executable, "manage.py", command,
                     "--source", source_arg, "--camera", camera_code, *cascade_args],
                    cwd=str(django_settings.BASE_DIR),
                    stdout=log_file, stderr=subprocess.STDOUT,
                    env=env,
                )
            except OSError:
                job.status = DetectionJob.Status.FAILED
                job.finished_at = timezone.now()
                job.save(update_fields=["status", "finished_at"])
                raise
            job.pid = proc.pid
            job.save(update_fields=["pid"])
        finally:
            # The child inherits its own duplicated handle — safe to close ours.
            log_file.close()

        threading.Thread(
            target=_watch_detection_job,
            args=(job.id, proc, log_path, source_arg),
            daemon=True,
        ).start()

        return Response(self.get_serializer(job).data, status=201)

    @action(detail=True, methods=["get"], url_path="state")
    def state(self, request, pk=None):
        """The live processing view of one job: the tracked subjects and the latest clean frame.
        ?since=<seq> leaves the frame out when the page already has it."""
        from core import debug_state, monitor as live_monitor
        job = self.get_object()
        since = request.query_params.get("since")
        data = debug_state.payload(live_monitor.job_dir(job.id), int(since) if since and since.isdigit() else None)
        data["job"] = {"id": job.id, "status": job.status, "violation_type": job.violation_type,
                       "started_at": job.started_at, "finished_at": job.finished_at}
        return Response(data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Stops a running job's subprocess outright — there's no cooperative
        "please stop" flag the watch_* commands poll for, and none is needed:
        this is a real OS process (see `create()`'s subprocess.Popen), so
        killing it by the PID already stored on the job is immediate and
        works identically for every detector without touching watch_*.py.

        Writes CANCELLED to the DB BEFORE sending the kill signal, not after
        — _watch_detection_job's own thread is blocked on this exact
        process's exit and will unblock the moment it's killed, so if that
        write happened second it could lose a race and get clobbered back to
        FAILED (a killed process's exit code reads as a crash otherwise).
        """
        job = self.get_object()
        if job.status != DetectionJob.Status.RUNNING:
            return Response(
                {"detail": f"Job is {job.status}, not running — nothing to cancel."},
                status=400,
            )

        job.status = DetectionJob.Status.CANCELLED
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])

        if job.pid:
            try:
                proc = psutil.Process(job.pid)
                # Kill children too (e.g. an in-progress ffmpeg raw-clip cut)
                # so cancelling doesn't leave an orphaned process behind.
                procs = proc.children(recursive=True) + [proc]
                for p in procs:
                    try:
                        p.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(procs, timeout=3)
                for p in alive:
                    try:
                        p.kill()
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                # Already gone — most likely it finished naturally in the gap
                # between the status check above and here. The DB write above
                # already stands, which is fine either way.
                pass

        return Response(self.get_serializer(job).data)
