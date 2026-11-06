"""The per-account mobile number and its SMS opt-in.

Nothing here sends a text -- no provider is wired up yet. What is asserted is
the record an eventual sender will read: every account can hold a number, the
number is stored in ONE spelling however it was typed, the switch is off until
somebody turns it on, and the two columns that now hold a phone number
(User.phone and the older Officer.phone) cannot disagree.

That last one is the real risk in this change. Officer.phone existed first and
is still what the personnel roster shows, so a write that updated only one of
them would leave the roster and the account each confidently showing a
different number.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Officer, normalize_ph_mobile

ME = "/api/auth/me/"
User = get_user_model()


class NormalizePhMobileTests(TestCase):
    def test_every_spelling_of_one_number_collapses_to_e164(self):
        for written in [
            "09171234567",        # as it is printed on a Philippine SIM pack
            "+63 917 123 4567",   # as the dashboard formats it back
            "63917 123 4567",
            "0063-917-123-4567",  # pasted with an IDD prefix
            "(0917) 123 4567",
        ]:
            with self.subTest(written=written):
                self.assertEqual(normalize_ph_mobile(written), "+639171234567")

    def test_blank_stays_blank(self):
        # No number on file is the normal state of a fresh account, so this is
        # not an error -- it must not raise.
        for empty in ["", None, "   "]:
            self.assertEqual(normalize_ph_mobile(empty), "")

    def test_a_number_that_cannot_be_texted_is_rejected(self):
        for bad in [
            "0912345",          # too short
            "091712345678",     # too long
            "081712345678",     # not a mobile prefix -- PH mobiles are 9XX
            "02 8123 4567",     # a Manila landline
            "not a number",
        ]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_ph_mobile(bad)


class AccountDefaultsTests(TestCase):
    def test_a_new_account_has_no_number_and_is_not_opted_in(self):
        user = User.objects.create_user(username="newbie", password="pw-correct-horse")
        self.assertEqual(user.phone, "")
        self.assertFalse(user.sms_alerts)

    def test_setting_the_number_mirrors_onto_the_officer_row(self):
        user = User.objects.create_user(
            username="ofc1", password="pw-correct-horse", role="officer")
        officer = Officer.objects.create(
            user=user, name="Dela Cruz, Juan", phone="+63 999 888 7777")

        user.set_account_phone("0917 123 4567")

        officer.refresh_from_db()
        self.assertEqual(user.phone, "+639171234567")
        self.assertEqual(officer.phone, "+639171234567",
                         "the roster would still be showing the old number")

    def test_an_officer_with_no_account_reads_as_not_opted_in(self):
        # Officer.user is nullable, and a derived property that assumed an
        # account would raise on the roster query instead of answering.
        officer = Officer.objects.create(name="No Account", phone="+639171234567")
        self.assertFalse(officer.sms_alerts)


class SelfServiceTests(TestCase):
    """PATCH /auth/me/ -- the profile screen on both clients."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="dispatch1", password="pw-correct-horse", role="dispatcher",
            display_name="Dispatch One")
        self.c = APIClient()
        self.c.force_authenticate(self.user)

    def test_me_reports_the_number_and_the_switch(self):
        body = self.c.get(ME).json()
        self.assertIn("phone", body)
        self.assertIn("sms_alerts", body)

    def test_a_user_can_set_their_own_number_and_opt_in(self):
        r = self.c.patch(ME, {"phone": "0917 123 4567", "sms_alerts": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["phone"], "+639171234567")
        self.assertTrue(r.json()["sms_alerts"])

        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "+639171234567")
        self.assertTrue(self.user.sms_alerts)

    def test_a_user_can_opt_back_out(self):
        self.user.sms_alerts = True
        self.user.save(update_fields=["sms_alerts"])
        self.c.patch(ME, {"sms_alerts": False}, format="json")
        self.user.refresh_from_db()
        self.assertFalse(self.user.sms_alerts)

    def test_clearing_the_number_is_allowed(self):
        self.user.phone = "+639171234567"
        self.user.save(update_fields=["phone"])
        r = self.c.patch(ME, {"phone": ""}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "")

    def test_a_typo_is_rejected_rather_than_stored(self):
        r = self.c.patch(ME, {"phone": "0912345"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("phone", r.json())
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "")

    def test_the_account_cannot_promote_itself(self):
        # The point of a self-service route is that it is narrow. Role and
        # email are an admin's to set, so sending them here must be ignored
        # rather than obeyed.
        r = self.c.patch(ME, {"role": "admin", "email": "me@evil.test"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "dispatcher")
        self.assertNotEqual(self.user.email, "me@evil.test")

    def test_a_stranger_cannot_read_or_write_an_account(self):
        anon = APIClient()
        self.assertEqual(anon.get(ME).status_code, 401)
        self.assertEqual(
            anon.patch(ME, {"phone": "0917 123 4567"}, format="json").status_code, 401)


class AdminEditsPersonnelTests(TestCase):
    """The admin's Personnel page: numbers for officers and for dispatchers."""

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            username="admin1", password="pw-correct-horse", role="admin")
        self.dispatcher = User.objects.create_user(
            username="dispatch1", password="pw-correct-horse", role="dispatcher",
            display_name="Dispatch One")
        self.officer_user = User.objects.create_user(
            username="ofc1", password="pw-correct-horse", role="officer")
        self.officer = Officer.objects.create(user=self.officer_user, name="Dela Cruz, Juan")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)

    def test_admin_sets_a_dispatchers_number(self):
        # PATCH on this route used to 405 while the web client called it
        # anyway, so Edit Credentials could never have saved anything.
        r = self.c.patch(f"/api/dispatchers/{self.dispatcher.id}/",
                         {"phone": "0917 123 4567", "sms_alerts": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.dispatcher.refresh_from_db()
        self.assertEqual(self.dispatcher.phone, "+639171234567")
        self.assertTrue(self.dispatcher.sms_alerts)

    def test_a_dispatchers_identity_is_not_editable_here(self):
        r = self.c.patch(f"/api/dispatchers/{self.dispatcher.id}/",
                         {"username": "stolen", "role": "admin"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.dispatcher.refresh_from_db()
        self.assertEqual(self.dispatcher.username, "dispatch1")
        self.assertEqual(self.dispatcher.role, "dispatcher")

    def test_admin_sets_an_officers_number_through_the_officer_route(self):
        r = self.c.patch(f"/api/officers/{self.officer.id}/",
                         {"phone": "+63 917 123 4567", "sms_alerts": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        # The response must carry what was saved, not the pre-edit row.
        self.assertEqual(r.json()["phone"], "+639171234567")
        self.assertTrue(r.json()["sms_alerts"])

        self.officer.refresh_from_db()
        self.officer_user.refresh_from_db()
        self.assertEqual(self.officer.phone, "+639171234567")
        self.assertEqual(self.officer_user.phone, "+639171234567",
                         "an officer's number must land on the account a sender reads")
        self.assertTrue(self.officer_user.sms_alerts)

    def test_an_officer_without_an_account_still_takes_a_number(self):
        orphan = Officer.objects.create(name="No Account")
        r = self.c.patch(f"/api/officers/{orphan.id}/",
                         {"phone": "0917 123 4567"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        orphan.refresh_from_db()
        self.assertEqual(orphan.phone, "+639171234567")

    def test_a_dispatcher_cannot_edit_other_peoples_numbers(self):
        c = APIClient()
        c.force_authenticate(self.dispatcher)
        self.assertEqual(
            c.patch(f"/api/dispatchers/{self.dispatcher.id}/", {"phone": "0917 123 4567"},
                    format="json").status_code, 403)
        self.assertEqual(
            c.patch(f"/api/officers/{self.officer.id}/", {"phone": "0917 123 4567"},
                    format="json").status_code, 403)
