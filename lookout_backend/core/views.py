import os
import random
import re
import subprocess
import sys
import threading
import uuid
from datetime import timedelta

import cv2
import django_filters
import numpy as np
from django.conf import settings as django_settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.text import get_valid_filename
from rapidfuzz import process as rapidfuzz_process
from rest_framework import generics, permissions, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from core.constants import ZAMBOANGA_BARANGAYS
from core.face_registry import rebuild_face_db
from core.vision import recognition

from .models import (
    Alert,
    Camera,
    Citation,
    DetectionJob,
    EmailVerificationCode,
    FaceEmbedding,
    Officer,
    Person,
    SystemSettings,
    User,
    ViolationType,
    Violator,
    Zone,
    normalize_name,
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
    CitationSerializer,
    DetectionJobSerializer,
    DispatcherSerializer,
    FaceEmbeddingSerializer,
    OfficerSerializer,
    PersonSerializer,
    SystemSettingsSerializer,
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


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def recording_start(request):
    """Starts the continuous CCTV recorder — called on dashboard login. Uses the
    stream URL of whichever camera has one configured. Idempotent: a second call
    while it's already running is a no-op."""
    from . import recording

    cam = Camera.objects.exclude(stream_url="").first()
    if cam is None:
        return Response(
            {"recording": False,
             "detail": "No camera has a stream_url configured to record."},
            status=400,
        )
    started = recording.start_recording(cam.stream_url)
    return Response({"recording": True, "started": started, "camera": cam.code})


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def recording_stop(request):
    """Stops the continuous CCTV recorder — called on dashboard logout."""
    from . import recording

    stopped = recording.stop_recording()
    return Response({"recording": False, "stopped": stopped})


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def recording_status(request):
    from . import recording

    return Response({"recording": recording.is_recording()})


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
        "people_total": Person.objects.count(),
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

    @action(detail=True, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def snapshot(self, request, pk=None):
        """Proxies a single still frame from the camera to the dashboard.

        Browsers cannot play RTSP and the camera may sit on an isolated subnet
        the browser can't route to, so the frame is fetched here (server-side,
        with the camera's own credentials) and streamed back as JPEG. The
        dashboard polls this a few times a second for a near-live feed.

        Hikvision exposes a still at ISAPI/Streaming/channels/<ch>/picture; the
        RTSP channel in stream_url (…/Streaming/Channels/102) maps to it.
        """
        import urllib.parse

        import requests
        from django.http import HttpResponse
        from requests.auth import HTTPDigestAuth

        camera = self.get_object()
        if not camera.stream_url:
            return Response({"detail": "Camera has no stream_url configured."}, status=404)

        parsed = urllib.parse.urlparse(camera.stream_url)
        host = parsed.hostname
        user = urllib.parse.unquote(parsed.username or "")
        pw = urllib.parse.unquote(parsed.password or "")
        # RTSP path .../Channels/101 -> ISAPI snapshot channel; default to sub-stream.
        channel = "102"
        m = re.search(r"/Channels/(\d+)", parsed.path)
        if m:
            channel = m.group(1)
        snap_url = f"http://{host}/ISAPI/Streaming/channels/{channel}/picture"

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


FACE_ENROLL_ANGLES = ["front", "right", "left"]
FACE_MIN_DIMENSION = 200


class PersonViewSet(viewsets.ModelViewSet):
    queryset = Person.objects.prefetch_related("embeddings").all()
    serializer_class = PersonSerializer
    filterset_fields = ["status"]
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]

    def perform_destroy(self, instance):
        for embedding in instance.embeddings.all():
            embedding.image.delete(save=False)
        instance.delete()
        rebuild_face_db()

    @action(detail=True, methods=["post"], url_path="enroll-face", parser_classes=[MultiPartParser, FormParser])
    def enroll_face(self, request, pk=None):
        """All-or-nothing 3-angle enrollment. Validates every image before
        writing anything, so a bad 'left' shot can't leave a person half-enrolled."""
        person = self.get_object()

        decoded = {}
        for angle in FACE_ENROLL_ANGLES:
            upload = request.FILES.get(angle)
            if upload is None:
                return Response(
                    {"detail": f"Missing image for angle '{angle}'.", "angle": angle},
                    status=400,
                )

            data = np.frombuffer(upload.read(), dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if image is None:
                return Response(
                    {"detail": f"Could not decode image for angle '{angle}'.", "angle": angle},
                    status=400,
                )

            height, width = image.shape[:2]
            if width < FACE_MIN_DIMENSION or height < FACE_MIN_DIMENSION:
                return Response(
                    {
                        "detail": f"Image for angle '{angle}' is too small "
                                  f"({width}x{height}); must be at least "
                                  f"{FACE_MIN_DIMENSION}x{FACE_MIN_DIMENSION}.",
                        "angle": angle,
                    },
                    status=400,
                )

            embedding = recognition.compute_face_embedding(image)
            if embedding is None:
                return Response(
                    {"detail": f"No face detected in image for angle '{angle}'.", "angle": angle},
                    status=400,
                )

            upload.seek(0)
            decoded[angle] = {"upload": upload, "embedding": embedding.flatten().tolist()}

        with transaction.atomic():
            for angle, result in decoded.items():
                FaceEmbedding.objects.update_or_create(
                    person=person,
                    angle=angle,
                    defaults={"image": result["upload"], "embedding": result["embedding"]},
                )
            person.status = Person.Status.ENROLLED
            person.enrolled_at = timezone.now()
            person.save(update_fields=["status", "enrolled_at"])

        rebuild_face_db()
        person = self.get_queryset().get(pk=person.pk)  # drop the stale (pre-write) embeddings prefetch cache
        return Response(self.get_serializer(person).data, status=201)

    @action(detail=True, methods=["delete"], url_path="embeddings")
    def embeddings(self, request, pk=None):
        person = self.get_object()
        for embedding in person.embeddings.all():
            embedding.image.delete(save=False)
        person.embeddings.all().delete()
        person.status = Person.Status.PENDING
        person.enrolled_at = None
        person.save(update_fields=["status", "enrolled_at"])
        rebuild_face_db()
        return Response(self.get_serializer(person).data)


class FaceEmbeddingViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only: creation/replacement only happens through
    PersonViewSet.enroll_face, which validates quality and keeps face_db.json
    (and the all-or-nothing 3-angle guarantee) consistent."""

    queryset = FaceEmbedding.objects.all()
    serializer_class = FaceEmbeddingSerializer
    filterset_fields = ["person", "angle"]
    permission_classes = [permissions.IsAuthenticated, IsAdminOrReadOnly]


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
    queryset = Citation.objects.select_related("alert", "officer", "matched_person").prefetch_related("violations").all()
    serializer_class = CitationSerializer
    filterset_class = CitationFilter
    permission_classes = [permissions.IsAuthenticated]

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
                violator, created = Violator.objects.get_or_create(
                    normalized_name=normalize_name(first, middle, last),
                    defaults={
                        "first_name": first, "middle_name": middle,
                        "last_name": last, "suffix": suffix,
                    },
                )
                matched_person = serializer.validated_data.get("matched_person")
                if created and matched_person:
                    violator.matched_person = matched_person
                    violator.save(update_fields=["matched_person"])

            violator.last_seen = timezone.now()
            violator.save(update_fields=["last_seen"])

            citation = serializer.save(created_by=self.request.user, violator=violator, client_uuid=client_uuid)
            if citation.alert_id and resolve_alert:
                Alert.objects.filter(pk=citation.alert_id).update(status=Alert.Status.RESOLVED)


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


# ---- Detection job upload/launch (admin test harness) ----------------------
# Only detectors with a working --source (file) mode can be driven from an
# upload — watch_curfew is webcam-only, and watch_smoking_pose/watch_all are
# alternate/composite entry points rather than a single selectable type.
DETECTION_COMMANDS = {
    "smoking": "watch_smoking",
    "drinking": "watch_drinking",
    "thief": "watch_thief",
    "parking": "watch_parking",
}
DETECTION_UPLOAD_EXTENSIONS = {".mp4", ".mkv", ".avi"}
DETECTION_MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1GB


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
    job.status = DetectionJob.Status.DONE if returncode == 0 else DetectionJob.Status.FAILED
    job.finished_at = timezone.now()
    if returncode != 0:
        job.error = _tail_log(log_path)
    job.save(update_fields=["status", "finished_at", "error"])
    # The uploaded source clip is scratch input, not evidence — the watcher's
    # own evidence clips (media/violations/) are separate and untouched.
    try:
        os.remove(source_path)
    except OSError:
        pass


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

    def create(self, request, *args, **kwargs):
        violation_type = request.data.get("violation_type", "")
        command = DETECTION_COMMANDS.get(violation_type)
        if command is None:
            return Response(
                {"detail": f"Unknown violation_type. Choose one of: {', '.join(DETECTION_COMMANDS)}."},
                status=400,
            )

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
        saved_path = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
        with open(saved_path, "wb") as dest:
            for chunk in upload.chunks():
                dest.write(chunk)

        # A matching extension can be spoofed — a quick decode check catches a
        # corrupt or non-video file before a detector is launched against it.
        cap = cv2.VideoCapture(str(saved_path))
        opened = cap.isOpened()
        cap.release()
        if not opened:
            os.remove(saved_path)
            return Response({"detail": "File could not be read as a video."}, status=400)

        camera_code = f"CAM-{violation_type.upper()}-TEST"
        log_path = f"{saved_path}.log"
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
            proc = subprocess.Popen(
                [sys.executable, "manage.py", command,
                 "--source", str(saved_path), "--camera", camera_code, *cascade_args],
                cwd=str(django_settings.BASE_DIR),
                stdout=log_file, stderr=subprocess.STDOUT,
                env=env,
            )
        finally:
            # The child inherits its own duplicated handle — safe to close ours.
            log_file.close()

        job = DetectionJob.objects.create(
            violation_type=violation_type,
            source_filename=upload.name,
            source_path=str(saved_path),
            status=DetectionJob.Status.RUNNING,
            pid=proc.pid,
            created_by=request.user,
        )

        threading.Thread(
            target=_watch_detection_job,
            args=(job.id, proc, log_path, str(saved_path)),
            daemon=True,
        ).start()

        return Response(self.get_serializer(job).data, status=201)
