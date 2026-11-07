"""Assignment texts to an officer's handset, via UniSMS.

What is actually being protected here is WHO gets charged for WHAT. Unlike
push, every message on this path costs money and arrives on a phone whose
owner had to consent to it, so the gates are the feature: the system-wide
switch, the account's own opt-in, a number on file, and a caller entitled to
spend credits. Each of those is a test below, because a regression in any one
of them is either an unwanted text or a silent one.

The other thing worth guarding is the segment count. A single character
outside GSM-7 re-encodes a message from 160 characters per segment to 70, and
the body interpolates operator-entered text (a zone name) -- so the cost of
every text the system sends can double because somebody pasted a typographic
apostrophe into a zone name.

Nothing in this file talks to the provider. `core.sms.deliver` is the seam: it
is the only function that makes a network call, so patching it tests everything
up to the wire without one. The HTTP-level tests patch `requests.Session` and
assert on what would have gone out.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core import sms
from core.models import (
    Alert, AssignmentNotice, Camera, Officer, SystemSettings, ViolationType, Zone,
)

User = get_user_model()

# A configured provider. Without these `messages_for` short-circuits, which is
# correct behaviour but would make every test below vacuously pass.
CONFIGURED = dict(UNISMS_API_KEY="sk_test_abcdef123456", UNISMS_SENDER_ID="BRGYTETUAN")


class _InlineThread:
    """A stand-in for threading.Thread that runs the target on `start()`.

    Used where a test needs to see what the background send actually did. A
    real thread would both race the assertions and, by outliving the test's
    patches, reach the real `requests` -- putting a live call to the provider
    inside the suite.
    """

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


def enable_sms():
    settings_row = SystemSettings.load()
    settings_row.sms_alerts_enabled = True
    settings_row.save(update_fields=["sms_alerts_enabled"])
    return settings_row


@override_settings(**CONFIGURED)
class RecipientTests(TestCase):
    """Who is texted, and -- mostly -- who is not."""

    def setUp(self):
        enable_sms()
        self.zone = Zone.objects.create(name="Tetuan Junction")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision", zone=self.zone)
        # An URGENT type on purpose: only holdup and illegal parking text an
        # officer the moment they are assigned, so every gate test below would
        # pass vacuously on a smoking alert -- which produces no message for a
        # reason that has nothing to do with the gate being tested.
        self.vt = ViolationType.objects.create(
            code="theft", label="Holdup in Public Area", color="#ef4444", icon="siren")
        self.alert = Alert.objects.create(
            type=self.vt, camera=self.cam, confidence=0.8, level="violation",
            timestamp=timezone.now(),
        )
        self.user = User.objects.create_user(
            username="ofc1", password="x", role="officer",
            phone="+639171234567", sms_alerts=True,
        )
        self.officer = Officer.objects.create(name="Ofc One", user=self.user)

    def test_an_opted_in_officer_with_a_number_is_texted(self):
        [message] = sms.messages_for(self.alert, [self.officer])

        self.assertEqual(message["recipient"], "+639171234567")
        self.assertEqual(message["sender_id"], "BRGYTETUAN")
        # Traceable from a charge on the provider's dashboard back to the
        # alert that caused it, without a table of our own.
        self.assertEqual(message["metadata"], {"alert": self.alert.code, "officer": self.officer.code})

    def test_the_system_wide_switch_stops_everything(self):
        """One switch an admin can reach when credits run out or a detector
        starts misfiring at 2am -- without clearing anybody's number or their
        consent, which is the whole point of it being separate."""
        row = SystemSettings.load()
        row.sms_alerts_enabled = False
        row.save(update_fields=["sms_alerts_enabled"])

        self.assertEqual(sms.messages_for(self.alert, [self.officer]), [])

    def test_an_officer_who_has_not_opted_in_is_not_texted(self):
        self.user.sms_alerts = False
        self.user.save(update_fields=["sms_alerts"])

        self.assertEqual(sms.messages_for(self.alert, [self.officer]), [])

    def test_a_blank_number_sends_nothing_even_when_opted_in(self):
        """Opting in is consent, not an address. The default state of an
        account is a blank number, and that must be quiet rather than an
        error."""
        self.user.phone = ""
        self.user.save(update_fields=["phone"])

        self.assertEqual(sms.messages_for(self.alert, [self.officer]), [])

    def test_a_deactivated_account_is_not_texted(self):
        """Checked here and not only at login: deactivating an account stops it
        signing in, but its number and its opt-in survive, so a departed
        officer would otherwise keep being told where live violations are."""
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        self.assertEqual(sms.messages_for(self.alert, [self.officer]), [])

    def test_an_officer_with_no_account_is_skipped(self):
        """Officer.user is nullable -- a roster entry for somebody with no
        login cannot have consented to anything."""
        roster_only = Officer.objects.create(name="Ofc Legacy", phone="+639171112222")

        self.assertEqual(sms.messages_for(self.alert, [roster_only]), [])

    @override_settings(UNISMS_API_KEY="")
    def test_no_api_key_means_nothing_is_attempted(self):
        """The normal state on the on-site detection PC, which has no reason to
        hold the credential. Not an error."""
        self.assertEqual(sms.messages_for(self.alert, [self.officer]), [])

    def test_one_message_each_for_two_officers(self):
        other_user = User.objects.create_user(
            username="ofc2", password="x", role="officer",
            phone="+639179876543", sms_alerts=True,
        )
        other = Officer.objects.create(name="Ofc Two", user=other_user)

        messages = sms.messages_for(self.alert, [self.officer, other])
        self.assertEqual(
            {m["recipient"] for m in messages},
            {"+639171234567", "+639179876543"},
        )


@override_settings(**CONFIGURED)
class MessageBodyTests(TestCase):
    """What the officer reads, and what it costs to send."""

    def setUp(self):
        enable_sms()
        self.zone = Zone.objects.create(name="Tetuan Junction")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision", zone=self.zone)
        self.vt = ViolationType.objects.create(
            code="smoking", label="Smoking", color="#6b8cae", icon="alert")
        self.alert = Alert.objects.create(
            type=self.vt, camera=self.cam, confidence=0.8, level="violation",
            timestamp=timezone.now(),
        )

    def test_body_names_the_code_the_violation_the_place_and_the_level(self):
        body = sms._body(self.alert)

        # The code leads so a radio call or a reply can name the alert.
        self.assertIn(self.alert.code, body)
        self.assertIn("Smoking", body)
        self.assertIn("Tetuan Junction", body)
        # "Likely" is the label for the `violation` level -- an officer
        # deciding whether to move needs the strength of the case, not a score.
        self.assertIn("Likely", body)
        # Identifies the sender even on a handset that does not know the number.
        self.assertTrue(body.startswith("LookOut "))

    def test_body_fits_one_billed_segment(self):
        body = sms._body(self.alert)

        self.assertLessEqual(len(body), sms.SEGMENT_CHARS)
        self.assertEqual(sms.segments(body), 1)

    def test_a_camera_with_no_zone_still_says_something_useful(self):
        bare = Camera.objects.create(code="CAM-SMOKE-02", name="No zone")
        alert = Alert.objects.create(
            type=self.vt, camera=bare, confidence=0.8, level="violation",
            timestamp=timezone.now(),
        )

        self.assertIn("your area", sms._body(alert))

    def test_a_typographic_zone_name_cannot_double_the_bill(self):
        """The cost regression. A zone name is operator-entered free text, and
        one character outside GSM-7 cuts the segment from 160 to 70 -- so
        pasting a smart apostrophe into a zone name would double the cost of
        every text the system sends, invisibly."""
        self.zone.name = "Mayor’s Office — Gate·3"
        self.zone.save(update_fields=["name"])

        body = sms._body(self.alert)

        self.assertTrue(sms.is_gsm7(body), f"not GSM-7: {body!r}")
        self.assertEqual(sms.segments(body), 1)
        # Folded to plain equivalents rather than dropped, so it is still
        # readable as the place it names.
        self.assertIn("Mayor's Office - Gate-3", body)

    def test_an_unanticipated_character_is_replaced_not_passed_through(self):
        """`_GSM_SUBSTITUTIONS` cannot enumerate every character a human might
        type. Anything left outside the alphabet becomes "?" -- losing a
        character from a nudge beats paying twice for every message."""
        self.zone.name = "Barangay 文字 Hall"
        self.zone.save(update_fields=["name"])

        body = sms._body(self.alert)

        self.assertTrue(sms.is_gsm7(body), f"not GSM-7: {body!r}")
        self.assertNotIn("文", body)

    def test_a_very_long_zone_name_is_trimmed_to_one_segment(self):
        self.zone.name = "Purok " + "Mariano Marcos Avenue Extension " * 8
        self.zone.save(update_fields=["name"])

        body = sms._body(self.alert)

        self.assertEqual(len(body), sms.SEGMENT_CHARS)
        self.assertEqual(sms.segments(body), 1)
        self.assertTrue(body.endswith("..."))

    def test_segments_counts_the_ucs2_penalty(self):
        self.assertEqual(sms.segments("a" * 160), 1)
        self.assertEqual(sms.segments("a" * 161), 2)
        # One non-GSM character re-encodes the whole message.
        self.assertEqual(sms.segments("文" + "a" * 69), 1)
        self.assertEqual(sms.segments("文" + "a" * 70), 2)
        self.assertEqual(sms.segments(""), 0)


@override_settings(**CONFIGURED)
class DeliveryTests(TestCase):
    """The HTTP call: how it is authenticated, and how failure is absorbed."""

    def setUp(self):
        self.message = {
            "recipient": "+639171234567",
            "content": "LookOut ALT-0001: Smoking at Tetuan Junction (Likely).",
            "sender_id": "BRGYTETUAN",
            "metadata": {"alert": "ALT-0001"},
        }

    def session_returning(self, *responses):
        """Patch requests.Session so `deliver` gets `responses` in order."""
        session = MagicMock()
        session.__enter__ = lambda s: s
        session.__exit__ = lambda s, *a: False
        session.post.side_effect = list(responses)
        return patch("core.sms.requests.Session", return_value=session), session

    @staticmethod
    def response(status, body=None, headers=None):
        reply = MagicMock()
        reply.status_code = status
        reply.headers = headers or {}
        reply.json.return_value = body if body is not None else {}
        reply.text = str(body or "")
        return reply

    def test_the_secret_key_is_the_basic_auth_username_with_no_password(self):
        """The provider's scheme, and the easy thing to get wrong -- a key sent
        as a bearer token or as the password is simply a 401."""
        patcher, session = self.session_returning(
            self.response(201, {"message": {"reference_id": "msg_1", "status": "pending"}}))
        with patcher:
            self.assertEqual(sms.deliver([self.message]), 1)

        _args, kwargs = session.post.call_args
        self.assertEqual(kwargs["auth"], ("sk_test_abcdef123456", ""))
        self.assertEqual(session.post.call_args[0][0], sms.SEND_URL)
        self.assertEqual(kwargs["json"], self.message)

    def test_a_201_counts_as_accepted(self):
        patcher, _ = self.session_returning(
            self.response(201, {"message": {"reference_id": "msg_1", "status": "pending"}}))
        with patcher:
            self.assertEqual(sms.deliver([self.message]), 1)

    def test_a_422_is_absorbed_and_not_retried(self):
        """An unregistered sender ID or a bad number. Retrying cannot fix it,
        and raising would fail a dispatcher's Assign."""
        patcher, session = self.session_returning(
            self.response(422, {"error": "sender_id not registered"}))
        with patcher:
            self.assertEqual(sms.deliver([self.message]), 0)
        self.assertEqual(session.post.call_count, 1)

    def test_a_429_waits_the_time_it_asks_for_and_retries_once(self):
        """Assigning two officers at once produces two requests back to back,
        so a 429 is predictable rather than exotic -- and a silently un-texted
        second officer is a real failure."""
        patcher, session = self.session_returning(
            self.response(429, {"error": "slow down"}, {sms.RETRY_AFTER_HEADER: "1500"}),
            self.response(201, {"message": {"reference_id": "msg_2", "status": "pending"}}),
        )
        with patcher, patch("core.sms.time.sleep") as slept:
            self.assertEqual(sms.deliver([self.message]), 1)

        self.assertEqual(session.post.call_count, 2)
        slept.assert_called_once_with(1.5)

    def test_the_retry_wait_is_capped(self):
        """A provider claiming a long cooldown must not park a thread."""
        patcher, _ = self.session_returning(
            self.response(429, {}, {sms.RETRY_AFTER_HEADER: "600000"}),
            self.response(201, {"message": {}}),
        )
        with patcher, patch("core.sms.time.sleep") as slept:
            sms.deliver([self.message])

        slept.assert_called_once_with(float(sms.MAX_RETRY_WAIT_SECONDS))

    def test_a_second_429_gives_up_rather_than_looping(self):
        patcher, session = self.session_returning(
            self.response(429, {}, {sms.RETRY_AFTER_HEADER: "100"}),
            self.response(429, {}, {sms.RETRY_AFTER_HEADER: "100"}),
        )
        with patcher, patch("core.sms.time.sleep"):
            self.assertEqual(sms.deliver([self.message]), 0)

        self.assertEqual(session.post.call_count, 2)

    def test_a_timeout_is_absorbed(self):
        """The officer still has the poll and the push nudge."""
        patcher, _ = self.session_returning(requests.Timeout("too slow"))
        with patcher:
            self.assertEqual(sms.deliver([self.message]), 0)

    def test_a_connection_failure_is_absorbed(self):
        patcher, _ = self.session_returning(requests.ConnectionError("no route"))
        with patcher:
            self.assertEqual(sms.deliver([self.message]), 0)

    def test_one_bad_message_does_not_stop_the_others(self):
        patcher, _ = self.session_returning(
            self.response(422, {"error": "bad number"}),
            self.response(201, {"message": {"reference_id": "msg_2"}}),
        )
        with patcher:
            self.assertEqual(sms.deliver([self.message, dict(self.message)]), 1)

    def test_nothing_to_send_makes_no_request(self):
        patcher, session = self.session_returning()
        with patcher:
            self.assertEqual(sms.deliver([]), 0)
        session.post.assert_not_called()

    def test_the_api_key_is_never_written_to_a_log(self):
        patcher, _ = self.session_returning(self.response(201, {"message": {}}))
        with patcher, self.assertLogs("core.sms", level="INFO") as logs:
            sms.deliver([self.message])

        joined = "\n".join(logs.output)
        self.assertNotIn("sk_test_abcdef123456", joined)
        # Enough of it to tell which key is loaded.
        self.assertIn("sk_tes", joined)

    def test_the_recipients_number_is_masked_in_logs(self):
        """A log is a less protected place than the database, and the last four
        digits are enough to tell two officers apart while chasing a failure."""
        patcher, _ = self.session_returning(self.response(422, {"error": "nope"}))
        with patcher, self.assertLogs("core.sms", level="INFO") as logs:
            sms.deliver([self.message])

        joined = "\n".join(logs.output)
        self.assertNotIn("+639171234567", joined)
        self.assertIn("4567", joined)


@override_settings(**CONFIGURED)
class AssignmentTriggerTests(TestCase):
    """The hook: assigning an officer is what sends the text."""

    def setUp(self):
        enable_sms()
        self.admin = User.objects.create_user(
            username="adm", password="x", role="admin", display_name="Admin A")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision")
        # Urgent, so the assignment really does produce a message -- see
        # UrgencySplitTests for the other half of the rule.
        self.vt = ViolationType.objects.create(
            code="theft", label="Holdup in Public Area", color="#000", icon="x")
        self.alert = Alert.objects.create(
            type=self.vt, camera=self.cam, confidence=0.6, level="warning",
            timestamp=timezone.now(),
        )

        self.user_a = User.objects.create_user(
            username="ofc_a", password="x", role="officer",
            phone="+639171234567", sms_alerts=True)
        self.user_b = User.objects.create_user(
            username="ofc_b", password="x", role="officer",
            phone="+639179876543", sms_alerts=True)
        self.officer_a = Officer.objects.create(name="Ofc A", user=self.user_a)
        self.officer_b = Officer.objects.create(name="Ofc B", user=self.user_b)

        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def patch_alert(self, client=None, **body):
        """PATCH the alert with the SMS path stubbed, returning the stub."""
        client = client or self.c
        with patch("core.sms.notify_assignment_async") as notify:
            response = client.patch(f"/api/alerts/{self.alert.pk}/", body, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return notify

    def texted_names(self, notify):
        if not notify.called:
            return []
        _alert, officers = notify.call_args[0]
        return sorted(o.name for o in officers)

    def test_assigning_an_officer_texts_exactly_that_officer(self):
        notify = self.patch_alert(status="dispatched", officers_assigned=[self.officer_a.pk])
        self.assertEqual(self.texted_names(notify), ["Ofc A"])

    def test_adding_a_second_officer_does_not_text_the_first_again(self):
        """Same rule as push, and it costs money to get wrong: a dispatcher
        widening an assignment must not re-text the officer already on their
        way."""
        self.patch_alert(status="dispatched", officers_assigned=[self.officer_a.pk])

        notify = self.patch_alert(officers_assigned=[self.officer_a.pk, self.officer_b.pk])
        self.assertEqual(self.texted_names(notify), ["Ofc B"])

    def test_touching_an_alert_without_changing_officers_texts_nobody(self):
        self.patch_alert(status="dispatched", officers_assigned=[self.officer_a.pk])

        notify = self.patch_alert(notes="En route")
        self.assertFalse(notify.called)

    def test_closing_an_alert_texts_nobody(self):
        self.patch_alert(status="dispatched", officers_assigned=[self.officer_a.pk])

        notify = self.patch_alert(status="resolved")
        self.assertFalse(notify.called)

    def test_an_officer_accepting_is_not_texted_about_their_own_acceptance(self):
        """`accept` adds the CALLER to the alert. They are holding the phone."""
        officer_client = APIClient()
        officer_client.force_authenticate(self.user_a)

        with patch("core.sms.notify_assignment_async") as notify:
            response = officer_client.post(f"/api/alerts/{self.alert.pk}/accept/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(notify.called)

    def test_an_officer_cannot_use_a_patch_to_spend_the_sms_credits(self):
        """AlertViewSet sets no permission_classes, so it falls back to
        IsAuthenticated -- any signed-in account can PATCH officers_assigned.
        Ungated, that is a way to spend the barangay's SMS credits from any
        account, which is worse than the equivalent push abuse because it is
        billed. Only a dispatcher's or an admin's assignment sends."""
        officer_client = APIClient()
        officer_client.force_authenticate(self.user_a)

        notify = self.patch_alert(
            client=officer_client, officers_assigned=[self.officer_b.pk])
        self.assertFalse(notify.called)

    def test_both_channels_fire_on_one_assignment(self):
        """Push and SMS are independent on purpose: push needs the app
        installed with permission granted and a data connection, a text needs
        only signal. Neither is a substitute for the other."""
        with patch("core.sms.notify_assignment_async") as texted, \
             patch("core.push.notify_assignment_async") as pushed:
            response = self.c.patch(
                f"/api/alerts/{self.alert.pk}/",
                {"status": "dispatched", "officers_assigned": [self.officer_a.pk]},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(texted.called)
        self.assertTrue(pushed.called)

    def test_a_provider_outage_does_not_fail_the_dispatchers_assign(self):
        """The end-to-end version of the absorption tests: `deliver` is reached
        for real (nothing stubbed above it) and the network throws.

        `threading.Thread` is replaced with an inline runner rather than left to
        run concurrently. Two reasons, and the second is the important one: the
        assertion would otherwise race the thread, and a thread that outlived
        this test's patches would reach the real `requests` and put a live call
        to the provider inside the test suite.
        """
        with patch("core.sms.requests.Session", side_effect=requests.ConnectionError("down")), \
             patch("core.sms.threading.Thread", _InlineThread), \
             patch("core.push.notify_assignment_async"):
            response = self.c.patch(
                f"/api/alerts/{self.alert.pk}/",
                {"status": "dispatched", "officers_assigned": [self.officer_a.pk]},
                format="json",
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, "dispatched")

    def test_the_messages_are_built_before_the_thread_starts(self):
        """`notify_assignment_async` must read both opt-in gates on the request
        thread. If it deferred them, the decision would be made against a
        settings row that could have changed, using an ORM connection Django
        has already cleaned up."""
        with patch("core.sms.deliver", return_value=1) as deliver, \
             patch("core.sms.threading.Thread", _InlineThread):
            sms.notify_assignment_async(self.alert, [self.officer_a])

        [sent], _kwargs = deliver.call_args
        self.assertEqual([m["recipient"] for m in sent], ["+639171234567"])

    def test_nothing_is_queued_when_no_one_may_be_texted(self):
        """No thread, no call -- the common case once both switches are off."""
        row = SystemSettings.load()
        row.sms_alerts_enabled = False
        row.save(update_fields=["sms_alerts_enabled"])

        # A plain MagicMock here, not _InlineThread: the point is that no
        # thread is constructed at all, which a stand-in class could not show.
        with patch("core.sms.deliver") as deliver, \
             patch("core.sms.threading.Thread") as thread:
            sms.notify_assignment_async(self.alert, [self.officer_a])

        deliver.assert_not_called()
        thread.assert_not_called()


@override_settings(**CONFIGURED)
class UrgencySplitTests(TestCase):
    """Which violations text an officer now, and which wait for the digest.

    The split is the cost control: an instant text per violation is one billed
    message per violation, so only the two that cannot wait get one. Getting it
    wrong in either direction is a real failure -- a holdup batched into a
    digest arrives half an hour late, and a smoking alert texted instantly
    multiplies the bill by however many the detectors file.
    """

    def setUp(self):
        enable_sms()
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision")
        self.user = User.objects.create_user(
            username="ofc1", password="x", role="officer",
            phone="+639171234567", sms_alerts=True)
        self.officer = Officer.objects.create(name="Ofc One", user=self.user)
        self.admin = User.objects.create_user(
            username="adm", password="x", role="admin", display_name="Admin A")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def alert_of(self, code):
        vt, _ = ViolationType.objects.get_or_create(
            code=code, defaults={"label": code.title()})
        return Alert.objects.create(
            type=vt, camera=self.cam, confidence=0.8, level="violation",
            timestamp=timezone.now())

    def assign(self, alert, client=None):
        """PATCH an officer onto `alert` as an admin, with push stubbed."""
        client = client or self.c
        with patch("core.push.notify_assignment_async"):
            response = client.patch(
                f"/api/alerts/{alert.pk}/",
                {"status": "dispatched", "officers_assigned": [self.officer.pk]},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_holdup_texts_immediately(self):
        self.assertTrue(sms.messages_for(self.alert_of("theft"), [self.officer]))

    def test_an_obstruction_texts_immediately(self):
        self.assertTrue(sms.messages_for(self.alert_of("parking"), [self.officer]))

    def test_the_other_three_produce_no_instant_text(self):
        for code in ("smoking", "drinking", "disposal"):
            with self.subTest(code=code):
                self.assertEqual(
                    sms.messages_for(self.alert_of(code), [self.officer]), [])

    def test_a_non_urgent_assignment_is_logged_for_the_digest(self):
        """The M2M has no timestamp, so this row is the only record that the
        assignment is new. Without it the digest cannot tell an assignment made
        a minute ago from one made last week."""
        alert = self.alert_of("disposal")

        self.assign(alert)

        notice = AssignmentNotice.objects.get(alert=alert, officer=self.officer)
        self.assertIsNone(notice.texted_at, "not reported yet")

    def test_an_urgent_assignment_is_not_logged(self):
        """It was texted on the spot, so there is nothing for a digest to add
        half an hour later -- logging it would text the officer twice."""
        alert = self.alert_of("theft")

        self.assign(alert)

        self.assertFalse(AssignmentNotice.objects.filter(alert=alert).exists())

    def test_removing_and_re_adding_an_officer_logs_one_row(self):
        """A dispatcher correcting an assignment has not given the officer a
        second violation to attend. The unique constraint is what says so."""
        alert = self.alert_of("smoking")
        self.assign(alert)
        with patch("core.push.notify_assignment_async"):
            self.c.patch(f"/api/alerts/{alert.pk}/",
                         {"officers_assigned": []}, format="json")

        self.assign(alert)

        self.assertEqual(
            AssignmentNotice.objects.filter(alert=alert, officer=self.officer).count(), 1)

    def test_an_officers_own_patch_logs_nothing(self):
        """The caller gate guards the LOG as well as the send. Ungated, any
        signed-in account could inflate somebody's digest count -- and the
        digest is billed."""
        alert = self.alert_of("smoking")
        officer_client = APIClient()
        officer_client.force_authenticate(self.user)

        self.assign(alert, client=officer_client)

        self.assertFalse(AssignmentNotice.objects.filter(alert=alert).exists())


@override_settings(**CONFIGURED)
class DigestTests(TestCase):
    """The half-hourly round: who is counted, what it says, what it marks.

    `core.sms.deliver` is the seam here too, so nothing below reaches the
    provider. What is being protected is the arithmetic and the bookkeeping: a
    count that is wrong is worse than no count, and a window that is consumed
    without being reported is an officer who is never told.
    """

    def setUp(self):
        enable_sms()
        self.zone = Zone.objects.create(name="Tetuan Junction")
        self.cam = Camera.objects.create(
            code="CAM-SMOKE-01", name="Hikvision", zone=self.zone)
        self.vt = ViolationType.objects.create(
            code="smoking", label="Smoking", color="#6b8cae", icon="alert")
        self.dispatcher = User.objects.create_user(
            username="disp", password="x", role="dispatcher",
            phone="+639170000001", sms_alerts=True)
        self.officer_user = User.objects.create_user(
            username="ofc", password="x", role="officer",
            phone="+639170000002", sms_alerts=True)
        self.officer = Officer.objects.create(name="Ofc One", user=self.officer_user)

    def file(self, count=1, camera=None, level="violation", **kwargs):
        """`count` filed alerts. `camera` and `level` are named rather than
        left in **kwargs because popping inside the comprehension would apply
        them to the first alert only."""
        camera = camera or self.cam
        return [
            Alert.objects.create(
                type=self.vt, camera=camera, confidence=0.8, level=level,
                timestamp=timezone.now(), **kwargs)
            for _ in range(count)
        ]

    def assign(self, count=1):
        """`count` unreported assignments for self.officer."""
        for alert in self.file(count):
            AssignmentNotice.objects.create(officer=self.officer, alert=alert)

    def entry_for(self, plan, user):
        matches = [e for e in plan["entries"] if e["user"].pk == user.pk]
        return matches[0] if matches else None

    def test_a_dispatcher_is_told_how_many_were_detected(self):
        self.file(3)

        plan = sms.digest_plan()

        self.assertEqual(plan["detected"], 3)
        entry = self.entry_for(plan, self.dispatcher)
        self.assertIn("3 new violations detected", entry["message"]["content"])

    def test_one_violation_is_singular(self):
        """A count in a message is read by a person, and "1 new violations"
        reads as a bug in the system that sent it."""
        self.file(1)

        entry = self.entry_for(sms.digest_plan(), self.dispatcher)

        self.assertIn("1 new violation detected", entry["message"]["content"])

    def test_an_officer_is_told_what_is_theirs_and_not_the_whole_count(self):
        """An officer is dispatched work, not given situational awareness of
        the barangay -- and 30 detected violations would read as 30 for them."""
        self.file(8)
        self.assign(2)

        entry = self.entry_for(sms.digest_plan(), self.officer_user)

        self.assertIn("2 new violations assigned to you", entry["message"]["content"])
        self.assertNotIn("detected", entry["message"]["content"])

    def test_a_both_account_gets_one_message_with_both_numbers(self):
        """"both" is a real role here. Two messages would bill twice to say
        what one line says."""
        self.officer_user.role = "both"
        self.officer_user.save(update_fields=["role"])
        self.file(5)
        self.assign(2)

        plan = sms.digest_plan()
        entries = [e for e in plan["entries"] if e["user"].pk == self.officer_user.pk]

        self.assertEqual(len(entries), 1)
        content = entries[0]["message"]["content"]
        self.assertIn("7 new violations detected", content)   # 5 + the 2 assigned
        self.assertIn("2 assigned to you", content)

    def test_a_quiet_window_sends_nothing(self):
        """The reason this is a plan rather than a timer that texts everybody:
        a digest every half hour saying "0" would bill 48 messages a day per
        account to say nothing happened."""
        plan = sms.digest_plan()

        self.assertEqual(plan["entries"], [])

    def test_monitoring_events_are_not_counted(self):
        """They are a quiet watchlist that by definition notifies nobody, and
        AlertViewSet keeps them out of the alert list -- so counting them would
        text a dispatcher about violations they cannot find on the dashboard."""
        self.file(2, peak_level="monitoring", level="monitoring")
        self.file(1)

        self.assertEqual(sms.digest_plan()["detected"], 1)

    def test_an_alert_with_no_peak_falls_back_to_its_current_level(self):
        """Rows older than `peak_level` have it blank. The view splits the same
        way, so the two must agree about what is on the watchlist."""
        self.file(1, peak_level="", level="monitoring")

        self.assertEqual(sms.digest_plan()["detected"], 0)

    def test_test_camera_alerts_are_not_counted(self):
        """Uploaded-footage evaluation runs are not events in the barangay, and
        one run over a long clip can file dozens in a minute."""
        test_cam = Camera.objects.create(code="CAM-SMOKE-TEST", name="Upload")
        self.file(4, camera=test_cam)
        self.file(1)

        self.assertEqual(sms.digest_plan()["detected"], 1)

    def test_the_body_fits_one_billed_segment(self):
        self.file(1200)          # an implausible count, to push the length
        self.assign(340)

        for entry in sms.digest_plan()["entries"]:
            content = entry["message"]["content"]
            with self.subTest(role=entry["user"].role):
                self.assertLessEqual(len(content), sms.SEGMENT_CHARS)
                self.assertEqual(sms.segments(content), 1)

    def test_the_watermark_moves_so_the_next_run_does_not_recount(self):
        self.file(3)

        with patch("core.sms.deliver", return_value=1):
            first = sms.send_digest()
            second = sms.send_digest()

        self.assertEqual(first["detected"], 3)
        self.assertEqual(second["detected"], 0)
        self.assertEqual(second["entries"], [])

    def test_the_window_is_consumed_even_when_nobody_was_reachable(self):
        """The watermark is deliberately NOT conditional on a successful send.
        One that only advanced on success would grow without bound and one day
        text somebody a count of several thousand; the dashboard shows the real
        list anyway. The officer's own count is the part that is protected --
        see the next test."""
        self.file(3)

        with patch("core.sms.deliver", return_value=0):
            sms.send_digest()

        self.assertIsNotNone(SystemSettings.load().sms_digest_sent_at)
        with patch("core.sms.deliver", return_value=1):
            self.assertEqual(sms.send_digest()["detected"], 0)

    def test_an_assignment_the_text_never_reached_is_counted_again(self):
        """The asymmetry that matters: a detection count is situational
        awareness, but an assignment nobody was told about is work that never
        got dispatched. So notices are marked only on an accepted message."""
        self.assign(2)

        with patch("core.sms.deliver", return_value=0):
            sms.send_digest()

        self.assertEqual(
            AssignmentNotice.objects.filter(texted_at__isnull=True).count(), 2)
        with patch("core.sms.deliver", return_value=1):
            entry = self.entry_for(sms.send_digest(), self.officer_user)
        self.assertIn("2 new violations assigned to you", entry["message"]["content"])

    def test_a_reported_assignment_is_not_counted_twice(self):
        self.assign(2)

        with patch("core.sms.deliver", return_value=1):
            sms.send_digest()

        self.assertEqual(
            AssignmentNotice.objects.filter(texted_at__isnull=True).count(), 0)
        self.assertIsNone(self.entry_for(sms.digest_plan(), self.officer_user))

    def test_a_missed_run_reports_what_it_missed(self):
        """The watermark is a watermark, not a fixed 30-minute window: the PC
        being off for two hours must not lose two hours of violations."""
        self.file(5)
        row = SystemSettings.load()
        row.sms_digest_sent_at = timezone.now() - timedelta(hours=3)
        row.save(update_fields=["sms_digest_sent_at"])

        self.assertEqual(sms.digest_plan()["detected"], 5)

    def test_the_first_ever_run_counts_one_interval_not_the_whole_database(self):
        """With no watermark stored, the fallback is one interval back. The
        alternative is that switching the feature on texts somebody a count of
        every violation ever detected."""
        old = self.file(1)[0]
        Alert.objects.filter(pk=old.pk).update(
            timestamp=timezone.now() - timedelta(hours=5))
        self.file(2)

        self.assertIsNone(SystemSettings.load().sms_digest_sent_at)
        self.assertEqual(sms.digest_plan()["detected"], 2)

    def test_the_master_switch_stops_the_digest_and_keeps_the_window(self):
        """Turning the switch off must not quietly consume the windows that
        pass while it is off, or turning it back on would skip them."""
        self.file(3)
        row = SystemSettings.load()
        row.sms_alerts_enabled = False
        row.save(update_fields=["sms_alerts_enabled"])

        with patch("core.sms.deliver") as deliver:
            plan = sms.send_digest()

        deliver.assert_not_called()
        self.assertEqual(plan["entries"], [])
        self.assertIsNone(SystemSettings.load().sms_digest_sent_at)
        # And the window survives: it is reported once the switch comes back.
        enable_sms()
        self.assertEqual(sms.digest_plan()["detected"], 3)

    @override_settings(UNISMS_API_KEY="")
    def test_no_api_key_means_no_digest(self):
        self.file(3)

        self.assertEqual(sms.digest_plan()["entries"], [])

    def test_an_account_with_no_opt_in_is_not_texted(self):
        self.dispatcher.sms_alerts = False
        self.dispatcher.save(update_fields=["sms_alerts"])
        self.file(3)

        self.assertIsNone(self.entry_for(sms.digest_plan(), self.dispatcher))

    def test_an_account_with_no_number_is_not_texted(self):
        self.dispatcher.phone = ""
        self.dispatcher.save(update_fields=["phone"])
        self.file(3)

        self.assertIsNone(self.entry_for(sms.digest_plan(), self.dispatcher))

    def test_a_deactivated_account_is_not_texted(self):
        """Deactivating stops an account signing in but leaves its number and
        its consent intact -- a departed dispatcher would otherwise keep being
        told how many violations the barangay has."""
        self.dispatcher.is_active = False
        self.dispatcher.save(update_fields=["is_active"])
        self.file(3)

        self.assertIsNone(self.entry_for(sms.digest_plan(), self.dispatcher))

    def test_an_officer_account_with_no_roster_row_is_not_an_error(self):
        """Officer.user is nullable and an account can exist without one. It
        simply has no assignments, which is the correct answer."""
        User.objects.create_user(
            username="ofc_noroster", password="x", role="officer",
            phone="+639170000003", sms_alerts=True)
        self.file(2)

        plan = sms.digest_plan()

        self.assertEqual([e["user"].username for e in plan["entries"]], ["disp"])

    def test_a_dry_run_sends_nothing_and_keeps_the_window(self):
        """`digest_plan` is what --dry-run calls, so it must not have side
        effects: a real run afterwards has to report the same window."""
        self.file(3)

        with patch("core.sms.deliver") as deliver:
            sms.digest_plan()

        deliver.assert_not_called()
        self.assertIsNone(SystemSettings.load().sms_digest_sent_at)

    def test_each_recipient_is_delivered_separately(self):
        """One call per recipient, so the bookkeeping knows who was actually
        reached. A single batch would only report how many were accepted, not
        which -- and an officer's notices may only be marked on their own."""
        self.file(2)
        self.assign(1)

        with patch("core.sms.deliver", return_value=1) as deliver:
            sms.send_digest()

        self.assertEqual(deliver.call_count, 2)
        for call in deliver.call_args_list:
            self.assertEqual(len(call.args[0]), 1)

    def test_the_digest_carries_no_link(self):
        """Provider constraint, not a style choice: the shared Unisoft sender
        ID cannot send a message containing a URL."""
        self.file(3)

        for entry in sms.digest_plan()["entries"]:
            content = entry["message"]["content"]
            self.assertNotIn("http", content)
            self.assertNotIn("www.", content)
