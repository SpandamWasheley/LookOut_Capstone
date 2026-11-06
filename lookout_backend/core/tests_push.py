"""Push notification to an officer's phone when they are assigned a violation.

The thing actually being protected here is WHO gets told and WHEN. The officer
app polls `/alerts/` every four seconds, but only while it is open in the
foreground, so this is the only path that reaches a phone nobody is looking
at — and the easy way to get it wrong is to notify everyone on the alert
every time it is touched, which trains officers to ignore the notifications.

Nothing in this file talks to Expo. `core.push.deliver` is the seam: it is the
only function that makes a network call, so patching it tests everything up to
the wire without one.
"""
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core import push
from core.models import Alert, Camera, DevicePushToken, Officer, ViolationType, Zone

PUSH_TOKEN_URL = "/api/auth/push-token/"
User = get_user_model()


def expo_token(suffix):
    return f"ExponentPushToken[{suffix}]"


class PushMessageTests(TestCase):
    """What ends up in the notification an officer sees on their lock screen."""

    def setUp(self):
        self.zone = Zone.objects.create(name="Tetuan Junction")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision", zone=self.zone)
        self.vt = ViolationType.objects.create(code="smoking", label="Smoking", color="#6b8cae", icon="alert")
        self.alert = Alert.objects.create(
            type=self.vt, camera=self.cam, confidence=0.8, level="violation", timestamp=timezone.now(),
        )
        self.user = User.objects.create_user(username="ofc1", password="x", role="officer")
        self.officer = Officer.objects.create(name="Ofc One", user=self.user)

    def test_message_names_the_violation_the_place_and_the_level(self):
        DevicePushToken.objects.create(user=self.user, token=expo_token("aaa"))
        [message] = push.messages_for(self.alert, [self.officer])

        self.assertEqual(message["to"], expo_token("aaa"))
        self.assertIn("Smoking", message["title"])
        self.assertIn("Tetuan Junction", message["body"])
        # "Likely" is the label for the `violation` level -- an officer
        # deciding whether to move needs the strength of the case, not a score.
        self.assertIn("Likely", message["body"])
        # The tap handler routes on this, so it has to survive the round trip.
        self.assertEqual(message["data"]["alertId"], str(self.alert.pk))
        self.assertEqual(message["data"]["code"], self.alert.code)
        # Android buckets anything without a channel into a default the
        # officer never configured and may have silenced.
        self.assertEqual(message["channelId"], push.CHANNEL_ID)
        self.assertEqual(message["priority"], "high")

    def test_one_message_per_device_so_a_spare_handset_is_reached(self):
        DevicePushToken.objects.create(user=self.user, token=expo_token("phone"))
        DevicePushToken.objects.create(user=self.user, token=expo_token("spare"))

        messages = push.messages_for(self.alert, [self.officer])
        self.assertEqual(
            {m["to"] for m in messages},
            {expo_token("phone"), expo_token("spare")},
        )

    def test_an_officer_with_no_device_is_not_an_error(self):
        # The normal state until an officer has opened the app and allowed
        # notifications. It must produce nothing, not raise.
        self.assertEqual(push.messages_for(self.alert, [self.officer]), [])

    def test_an_officer_with_no_account_is_skipped(self):
        # Officer.user is nullable -- a roster entry for somebody who has no
        # login cannot be notified and must not break the send for those who can.
        paper_only = Officer.objects.create(name="No Login")
        DevicePushToken.objects.create(user=self.user, token=expo_token("aaa"))

        messages = push.messages_for(self.alert, [paper_only])
        self.assertEqual(messages, [])

    def test_a_deactivated_account_is_not_messaged(self):
        """Deactivating stops an account signing in; it does not delete its
        devices. Without this the handset of a suspended or departed officer
        keeps receiving the place and evidence level of live violations."""
        DevicePushToken.objects.create(user=self.user, token=expo_token("aaa"))
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.assertEqual(push.messages_for(self.alert, [self.officer]), [])

    def test_a_camera_with_no_zone_still_produces_a_message(self):
        self.cam.zone = None
        self.cam.save(update_fields=["zone"])
        DevicePushToken.objects.create(user=self.user, token=expo_token("aaa"))

        [message] = push.messages_for(self.alert, [self.officer])
        self.assertIn("your area", message["body"])


class DeliveryTests(TestCase):
    """Talking to Expo: what is counted, and what is cleaned up."""

    def setUp(self):
        self.user = User.objects.create_user(username="ofc1", password="x", role="officer")

    def expo_replies(self, tickets):
        response = type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"data": tickets},
        })()
        return patch("core.push.requests.post", return_value=response)

    def test_a_token_expo_calls_unregistered_is_deleted(self):
        """An uninstalled app would otherwise cost a failed send forever."""
        DevicePushToken.objects.create(user=self.user, token=expo_token("gone"))
        DevicePushToken.objects.create(user=self.user, token=expo_token("live"))
        messages = [{"to": expo_token("gone")}, {"to": expo_token("live")}]

        with self.expo_replies([
            {"status": "error", "message": "...", "details": {"error": "DeviceNotRegistered"}},
            {"status": "ok"},
        ]):
            accepted = push.deliver(messages)

        self.assertEqual(accepted, 1)
        self.assertEqual(
            list(DevicePushToken.objects.values_list("token", flat=True)),
            [expo_token("live")],
        )

    def test_another_kind_of_rejection_keeps_the_token(self):
        # A rate limit or a transient Expo fault says nothing about whether the
        # device still exists, so throwing the token away would lose a working
        # address over a temporary problem.
        DevicePushToken.objects.create(user=self.user, token=expo_token("live"))

        with self.expo_replies([{"status": "error", "message": "Too many requests", "details": {}}]):
            accepted = push.deliver([{"to": expo_token("live")}])

        self.assertEqual(accepted, 0)
        self.assertTrue(DevicePushToken.objects.filter(token=expo_token("live")).exists())

    def test_expo_being_unreachable_is_survivable(self):
        DevicePushToken.objects.create(user=self.user, token=expo_token("live"))
        with patch("core.push.requests.post", side_effect=requests.ConnectionError("no route")):
            self.assertEqual(push.deliver([{"to": expo_token("live")}]), 0)
        # Nothing was learned about the device, so nothing is discarded.
        self.assertTrue(DevicePushToken.objects.filter(token=expo_token("live")).exists())

    def test_messages_are_split_into_batches_expo_will_accept(self):
        messages = [{"to": expo_token(str(i))} for i in range(push.BATCH_SIZE + 5)]
        with self.expo_replies([{"status": "ok"}] * push.BATCH_SIZE) as post:
            push.deliver(messages)
        self.assertEqual(post.call_count, 2)

    def test_nothing_to_send_makes_no_request(self):
        with patch("core.push.requests.post") as post:
            self.assertEqual(push.deliver([]), 0)
        post.assert_not_called()


class AssignmentTriggerTests(TestCase):
    """The hook: assigning an officer is what sends the notification."""

    def setUp(self):
        self.admin = User.objects.create_user(username="adm", password="x", role="admin", display_name="Admin A")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision")
        self.vt = ViolationType.objects.create(code="smoking", label="Smoking", color="#000", icon="x")
        self.alert = Alert.objects.create(
            type=self.vt, camera=self.cam, confidence=0.6, level="warning", timestamp=timezone.now(),
        )

        self.user_a = User.objects.create_user(username="ofc_a", password="x", role="officer")
        self.user_b = User.objects.create_user(username="ofc_b", password="x", role="officer")
        self.officer_a = Officer.objects.create(name="Ofc A", user=self.user_a)
        self.officer_b = Officer.objects.create(name="Ofc B", user=self.user_b)

        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def patch_alert(self, **body):
        with patch("core.push.notify_assignment_async") as notify:
            response = self.c.patch(f"/api/alerts/{self.alert.pk}/", body, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return notify

    def notified_names(self, notify):
        if not notify.called:
            return []
        _alert, officers = notify.call_args[0]
        return sorted(o.name for o in officers)

    def test_assigning_an_officer_notifies_exactly_that_officer(self):
        notify = self.patch_alert(status="dispatched", officers_assigned=[self.officer_a.pk])
        self.assertEqual(self.notified_names(notify), ["Ofc A"])

    def test_adding_a_second_officer_does_not_notify_the_first_again(self):
        """The one that matters. A dispatcher widening an assignment must not
        re-ping the officer who is already on their way — doing that is how
        officers learn to ignore these."""
        self.patch_alert(status="dispatched", officers_assigned=[self.officer_a.pk])

        notify = self.patch_alert(officers_assigned=[self.officer_a.pk, self.officer_b.pk])
        self.assertEqual(self.notified_names(notify), ["Ofc B"])

    def test_touching_an_alert_without_changing_officers_notifies_nobody(self):
        self.patch_alert(status="dispatched", officers_assigned=[self.officer_a.pk])

        notify = self.patch_alert(notes="En route")
        self.assertFalse(notify.called)

    def test_closing_an_alert_notifies_nobody(self):
        self.patch_alert(status="dispatched", officers_assigned=[self.officer_a.pk])

        notify = self.patch_alert(status="resolved")
        self.assertFalse(notify.called)

    def test_an_officer_accepting_is_not_notified_of_their_own_acceptance(self):
        """`accept` adds the CALLER to the alert. They are holding the phone."""
        officer_client = APIClient()
        officer_client.force_authenticate(self.user_a)

        with patch("core.push.notify_assignment_async") as notify:
            response = officer_client.post(f"/api/alerts/{self.alert.pk}/accept/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(notify.called)

    def test_an_officer_cannot_use_a_patch_to_ring_other_officers(self):
        """AlertViewSet sets no permission_classes, so it falls back to
        IsAuthenticated: in this system the officer-vs-web split is a
        client-side gate, and any signed-in account can PATCH
        officers_assigned. That was already true of the database write.
        Ungated it would also be a way to buzz every handset in the barangay
        on demand, so only a dispatcher's or admin's assignment sends."""
        officer_client = APIClient()
        officer_client.force_authenticate(self.user_a)

        with patch("core.push.notify_assignment_async") as notify:
            response = officer_client.patch(
                f"/api/alerts/{self.alert.pk}/",
                {"officers_assigned": [self.officer_b.pk]},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(notify.called)
        # The assignment itself still happened — this gate is about who may
        # wake a phone, and deliberately changes no existing authorization.
        self.alert.refresh_from_db()
        self.assertEqual(
            list(self.alert.officers_assigned.values_list("pk", flat=True)),
            [self.officer_b.pk],
        )

    def test_a_dispatcher_assigning_does_send(self):
        dispatcher = User.objects.create_user(username="disp", password="x", role="dispatcher")
        dispatch_client = APIClient()
        dispatch_client.force_authenticate(dispatcher)

        with patch("core.push.notify_assignment_async") as notify:
            response = dispatch_client.patch(
                f"/api/alerts/{self.alert.pk}/",
                {"status": "dispatched", "officers_assigned": [self.officer_a.pk]},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.notified_names(notify), ["Ofc A"])

    def test_assigning_still_succeeds_when_expo_is_unreachable(self):
        """The dispatcher's request must not fail, or hang, on a third party."""
        with patch("core.push.requests.post", side_effect=requests.ConnectionError("no route")):
            DevicePushToken.objects.create(user=self.user_a, token=expo_token("aaa"))
            response = self.c.patch(
                f"/api/alerts/{self.alert.pk}/",
                {"status": "dispatched", "officers_assigned": [self.officer_a.pk]},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "dispatched")


class PushTokenEndpointTests(TestCase):
    """Registering and forgetting a device."""

    def setUp(self):
        self.user_a = User.objects.create_user(username="ofc_a", password="x", role="officer")
        self.user_b = User.objects.create_user(username="ofc_b", password="x", role="officer")
        self.c = APIClient()
        self.c.force_authenticate(self.user_a)

    def test_registering_stores_the_token_against_the_caller(self):
        r = self.c.post(PUSH_TOKEN_URL, {"token": expo_token("aaa"), "platform": "android"}, format="json")
        self.assertEqual(r.status_code, 204, r.content)

        row = DevicePushToken.objects.get(token=expo_token("aaa"))
        self.assertEqual(row.user, self.user_a)
        self.assertEqual(row.platform, "android")

    def test_registering_the_same_device_twice_does_not_duplicate_it(self):
        # The app re-registers on every launch, because Expo can reissue a
        # token at any time. That must be idempotent.
        for _ in range(3):
            self.c.post(PUSH_TOKEN_URL, {"token": expo_token("aaa")}, format="json")
        self.assertEqual(DevicePushToken.objects.count(), 1)

    def test_a_shared_handset_moves_to_whoever_signed_in_last(self):
        """The reason registration keys on the token and not on the user.

        Keyed the other way, both officers would stay registered on the one
        handset and each would be notified of the other's assignments."""
        self.c.post(PUSH_TOKEN_URL, {"token": expo_token("shared")}, format="json")

        second = APIClient()
        second.force_authenticate(self.user_b)
        second.post(PUSH_TOKEN_URL, {"token": expo_token("shared")}, format="json")

        self.assertEqual(DevicePushToken.objects.count(), 1)
        self.assertEqual(DevicePushToken.objects.get().user, self.user_b)

    def test_signing_out_forgets_the_device(self):
        self.c.post(PUSH_TOKEN_URL, {"token": expo_token("aaa")}, format="json")

        r = self.c.delete(PUSH_TOKEN_URL, {"token": expo_token("aaa")}, format="json")
        self.assertEqual(r.status_code, 204, r.content)
        self.assertFalse(DevicePushToken.objects.exists())

    def test_one_account_cannot_unregister_anothers_phone(self):
        DevicePushToken.objects.create(user=self.user_b, token=expo_token("theirs"))

        r = self.c.delete(PUSH_TOKEN_URL, {"token": expo_token("theirs")}, format="json")
        self.assertEqual(r.status_code, 204)
        self.assertTrue(DevicePushToken.objects.filter(token=expo_token("theirs")).exists())

    def test_a_value_that_is_not_an_expo_token_is_refused(self):
        # Storing one only guarantees a rejected send on every assignment.
        for bad in ["", "   ", "not-a-token", "fcm:abc123"]:
            with self.subTest(bad=bad):
                r = self.c.post(PUSH_TOKEN_URL, {"token": bad}, format="json")
                self.assertEqual(r.status_code, 400)
        self.assertFalse(DevicePushToken.objects.exists())

    def test_an_unknown_platform_is_dropped_rather_than_stored(self):
        self.c.post(PUSH_TOKEN_URL, {"token": expo_token("aaa"), "platform": "symbian"}, format="json")
        self.assertEqual(DevicePushToken.objects.get().platform, "")

    def test_only_officer_accounts_may_register_a_device(self):
        """Registration upserts on the token, so anyone who can call this and
        holds another device's token could point it at their own account and
        silence the real officer. A dispatcher or admin has no reason to be
        on that path — neither can sign in to the officer app."""
        for role in ("admin", "dispatcher"):
            with self.subTest(role=role):
                desk = User.objects.create_user(username=f"desk_{role}", password="x", role=role)
                client = APIClient()
                client.force_authenticate(desk)
                r = client.post(PUSH_TOKEN_URL, {"token": expo_token("aaa")}, format="json")
                self.assertEqual(r.status_code, 403, r.content)
        self.assertFalse(DevicePushToken.objects.exists())

    def test_a_both_account_may_register(self):
        # "both" is an officer who also dispatches; they do use the app.
        hybrid = User.objects.create_user(username="hybrid", password="x", role="both")
        client = APIClient()
        client.force_authenticate(hybrid)

        r = client.post(PUSH_TOKEN_URL, {"token": expo_token("aaa")}, format="json")
        self.assertEqual(r.status_code, 204, r.content)

    def test_signing_in_is_required(self):
        anon = APIClient()
        r = anon.post(PUSH_TOKEN_URL, {"token": expo_token("aaa")}, format="json")
        self.assertIn(r.status_code, (401, 403))
