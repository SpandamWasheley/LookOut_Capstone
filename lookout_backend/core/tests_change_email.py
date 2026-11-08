"""Changing your own username and email.

The security property under test is that A SESSION ALONE CANNOT MOVE THE
EMAIL. It is where a password reset is sent, so a borrowed session that could
repoint it would become permanent ownership of the account. Two things gate
it: the current password, which proves the owner rather than the session
holder, and a code to the new address, which proves the address is reachable.

The password is the gate that stops a takeover. The code does not: whoever is
asking owns the inbox it is sent to.

`core.views.send_async` is the seam, so nothing here touches the network. The
code row is written before the send is queued, so the test can read it back
without waiting on a thread.
"""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import EmailVerificationCode, User


class ChangeEmailTests(APITestCase):
    def setUp(self):
        # OtpSendThrottle is 5/hour and DRF keeps its history in the cache, so
        # without this the later tests in the class 429 on a limit the earlier
        # ones used up.
        cache.clear()
        self.user = User.objects.create_user(
            username="dispatcher1", password="pw", email="old@example.com", role="dispatcher",
        )
        self.other = User.objects.create_user(
            username="taken", password="pw", email="taken@example.com", role="dispatcher",
        )
        self.client.force_authenticate(self.user)
        self.send_url = reverse("change_email_send_code")
        self.confirm_url = reverse("change_email_confirm")
        self.me_url = reverse("me")

    def _send(self, email, password="pw"):
        with patch("core.views.send_async") as queued:
            response = self.client.post(
                self.send_url, {"email": email, "current_password": password}, format="json")
        return response, queued

    # --- the property that matters ---------------------------------------

    def test_patching_me_cannot_change_email(self):
        self.client.patch(self.me_url, {"email": "attacker@example.com"}, format="json")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

    def test_patching_me_cannot_change_role(self):
        self.client.patch(self.me_url, {"role": "admin"}, format="json")
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "dispatcher")

    # --- username ---------------------------------------------------------

    def test_username_is_writable(self):
        response = self.client.patch(self.me_url, {"username": "newname"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "newname")

    def test_username_must_be_unique_case_insensitively(self):
        response = self.client.patch(self.me_url, {"username": "TAKEN"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "dispatcher1")

    def test_username_cannot_be_blank(self):
        response = self.client.patch(self.me_url, {"username": "   "}, format="json")
        self.assertEqual(response.status_code, 400)

    # --- sending the code -------------------------------------------------

    def test_send_code_requires_authentication(self):
        self.client.force_authenticate(None)
        response = self.client.post(self.send_url, {"email": "new@example.com"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_send_code_writes_a_row_and_queues_one_send(self):
        response, queued = self._send("new@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EmailVerificationCode.objects.filter(email="new@example.com").exists())
        self.assertEqual(queued.call_count, 1)

    def test_send_code_rejects_an_address_another_account_holds(self):
        response, queued = self._send("taken@example.com")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(queued.call_count, 0)

    def test_send_code_rejects_the_address_already_in_use_here(self):
        response, _ = self._send("old@example.com")
        self.assertEqual(response.status_code, 400)

    def test_send_code_requires_the_current_password(self):
        with patch("core.views.send_async") as queued:
            response = self.client.post(
                self.send_url, {"email": "new@example.com"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(queued.call_count, 0)
        self.assertFalse(EmailVerificationCode.objects.filter(email="new@example.com").exists())

    def test_send_code_rejects_a_wrong_password(self):
        response, queued = self._send("new@example.com", password="not-my-password")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(queued.call_count, 0)

    def test_send_code_rejects_a_malformed_address(self):
        response, _ = self._send("not-an-email")
        self.assertEqual(response.status_code, 400)

    # --- confirming -------------------------------------------------------

    def _code_for(self, email):
        self._send(email)
        return EmailVerificationCode.objects.filter(email=email).latest("created_at")

    def test_a_good_code_moves_the_account(self):
        row = self._code_for("new@example.com")
        response = self.client.post(
            self.confirm_url, {"email": "new@example.com", "code": row.code}, format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")

    def test_a_wrong_code_does_not(self):
        self._code_for("new@example.com")
        response = self.client.post(
            self.confirm_url, {"email": "new@example.com", "code": "000000"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

    def test_an_expired_code_does_not(self):
        row = self._code_for("new@example.com")
        EmailVerificationCode.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(minutes=30))
        response = self.client.post(
            self.confirm_url, {"email": "new@example.com", "code": row.code}, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

    def test_a_code_cannot_be_spent_twice(self):
        row = self._code_for("new@example.com")
        self.client.post(self.confirm_url, {"email": "new@example.com", "code": row.code}, format="json")
        # Back to the original, then try to reuse the same code to move again.
        self.user.email = "old@example.com"
        self.user.save(update_fields=["email"])
        response = self.client.post(
            self.confirm_url, {"email": "new@example.com", "code": row.code}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_code_issued_to_another_account_cannot_be_spent(self):
        """Codes are bound to the account that asked for one.

        Otherwise a row minted by registration or password-reset -- both of
        which key only on the address -- could be replayed here.
        """
        row = self._code_for("new@example.com")
        row.user = self.other
        row.save(update_fields=["user"])
        response = self.client.post(
            self.confirm_url, {"email": "new@example.com", "code": row.code}, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

    def test_an_unbound_code_cannot_be_spent(self):
        row = self._code_for("new@example.com")
        row.user = None
        row.save(update_fields=["user"])
        response = self.client.post(
            self.confirm_url, {"email": "new@example.com", "code": row.code}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_the_old_address_is_notified(self):
        row = self._code_for("new@example.com")
        with patch("core.views.send_async") as queued:
            self.client.post(
                self.confirm_url, {"email": "new@example.com", "code": row.code}, format="json")
        self.assertEqual(queued.call_count, 1)
        self.assertEqual(queued.call_args.args[2], "old@example.com")

    def test_an_address_claimed_while_the_code_sat_in_an_inbox_is_refused(self):
        row = self._code_for("new@example.com")
        self.other.email = "new@example.com"
        self.other.save(update_fields=["email"])
        response = self.client.post(
            self.confirm_url, {"email": "new@example.com", "code": row.code}, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")


class ChangePasswordGateTests(APITestCase):
    """The sibling path has to hold the same line.

    change_email_send_code asks for the current password. If change_password
    did not, a borrowed session could set the password first and then satisfy
    that check with its own value -- the email gate would be decoration.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="officer1", password="pw-original", email="o@example.com", role="officer",
        )
        self.client.force_authenticate(self.user)
        self.url = reverse("change_password")

    def test_cannot_change_password_without_the_current_one(self):
        response = self.client.post(self.url, {"new_password": "attacker-chosen"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("pw-original"))

    def test_cannot_change_password_with_a_wrong_current_one(self):
        response = self.client.post(
            self.url,
            {"current_password": "guess", "new_password": "attacker-chosen"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("pw-original"))

    def test_the_right_current_password_works(self):
        response = self.client.post(
            self.url,
            {"current_password": "pw-original", "new_password": "a-better-one"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-better-one"))

    def test_a_forced_first_change_does_not_need_it(self):
        """The temporary password was already used to get this session."""
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        response = self.client.post(self.url, {"new_password": "first-real-one"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("first-real-one"))
        self.assertFalse(self.user.must_change_password)

    def test_the_forced_exception_closes_after_it_is_used(self):
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        self.client.post(self.url, {"new_password": "first-real-one"}, format="json")
        # must_change_password is now False, so the gate applies again.
        response = self.client.post(self.url, {"new_password": "second-one"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("first-real-one"))


class ChangePasswordThrottleTests(APITestCase):
    """Verifying the current password makes this a guessing oracle.

    Closing the no-password hole is not enough on its own: unlimited guesses
    against check_password would recover the real one, and that single value
    opens both this endpoint and change_email_send_code.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="officer2", password="pw-original", email="o2@example.com", role="officer",
        )
        self.client.force_authenticate(self.user)
        self.url = reverse("change_password")

    def test_repeated_wrong_guesses_are_throttled(self):
        seen_429 = False
        for _ in range(15):
            response = self.client.post(
                self.url, {"current_password": "guess", "new_password": "x" * 10}, format="json")
            if response.status_code == 429:
                seen_429 = True
                break
            self.assertEqual(response.status_code, 400)
        self.assertTrue(seen_429, "change_password accepted unlimited password guesses")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("pw-original"))
