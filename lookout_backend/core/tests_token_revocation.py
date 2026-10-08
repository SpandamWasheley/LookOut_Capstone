"""Making a JWT session actually end: rotation, reuse detection, and logout.

A JWT is self-validating, so by default nothing can revoke one — a refresh
token stays good until it expires no matter who is holding it, and "log out"
meant only that one browser forgot it. That mattered here once the web
dashboard began keeping its refresh token in sessionStorage (so that reloading
the page does not end the session), because page script can read it.

Two mechanisms close that, and this file is about proving both:

  ROTATION + BLACKLIST_AFTER_ROTATION  every refresh returns a new refresh
      token and blacklists the one presented, making each single-use. A stolen
      copy therefore stops working the moment the real client refreshes — and
      the legitimate user being logged out is itself the signal that someone
      else used their token.

  POST /auth/logout/  blacklists the token on request, so signing out ends the
      session server-side rather than only in the browser.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

LOGIN = "/api/auth/login/"
REFRESH = "/api/auth/refresh/"
LOGOUT = "/api/auth/logout/"
ME = "/api/auth/me/"


class TokenRotationTests(TestCase):
    def setUp(self):
        # LoginThrottle is an AnonRateThrottle at 10/min and its counter lives
        # in the cache, which is NOT rolled back between tests the way the
        # database is. A file that logs in once per test burns the shared
        # budget and later tests 429 — nothing to do with what they assert.
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="dispatch1", password="pw-correct-horse", role="dispatcher",
            display_name="Dispatch One")
        self.c = APIClient()

    def _login(self):
        r = self.c.post(LOGIN, {"username": "dispatch1", "password": "pw-correct-horse"},
                        format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def _refresh(self, token):
        return self.c.post(REFRESH, {"refresh": token}, format="json")

    def _works(self, access):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        return c.get(ME).status_code == 200

    # -- rotation ---------------------------------------------------------
    def test_refreshing_hands_back_a_new_refresh_token(self):
        # Both clients re-store `data.refresh` when it is present, which is why
        # rotation needed no client change — but if the server ever stopped
        # sending one they would keep replaying a blacklisted token and the
        # session would die on the second refresh.
        first = self._login()["refresh"]
        body = self._refresh(first).json()
        self.assertIn("refresh", body, "rotation must return a replacement token")
        self.assertNotEqual(body["refresh"], first)

    def test_the_replacement_token_works(self):
        first = self._login()["refresh"]
        second = self._refresh(first).json()["refresh"]
        r = self._refresh(second)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(self._works(r.json()["access"]))

    def test_a_long_session_can_keep_refreshing(self):
        # A dispatcher on a full shift refreshes many times; each rotation has
        # to chain cleanly or they are ejected partway through.
        token = self._login()["refresh"]
        for hop in range(5):
            r = self._refresh(token)
            self.assertEqual(r.status_code, 200, f"hop {hop}: {r.content}")
            token = r.json()["refresh"]
        self.assertTrue(self._works(self._refresh(token).json()["access"]))

    # -- reuse detection --------------------------------------------------
    def test_a_used_refresh_token_is_refused_the_second_time(self):
        # THE point of blacklist-after-rotation. This is what makes a token
        # copied out of sessionStorage stop working.
        first = self._login()["refresh"]
        self.assertEqual(self._refresh(first).status_code, 200)

        again = self._refresh(first)
        self.assertEqual(again.status_code, 401, again.content)
        self.assertNotIn("access", again.json())

    def test_a_thiefs_copy_dies_when_the_real_client_refreshes(self):
        # The attack this defends against, played out: the token is stolen, the
        # real client refreshes first (it is the one with a running session),
        # and the stolen copy is now worthless.
        stolen = self._login()["refresh"]
        self.assertEqual(self._refresh(stolen).status_code, 200)   # real client
        self.assertEqual(self._refresh(stolen).status_code, 401)   # thief

    def test_the_rest_of_the_chain_dies_with_a_reused_token(self):
        # A blacklisted token cannot be laundered into a fresh one by replaying
        # an older link in the chain.
        first = self._login()["refresh"]
        second = self._refresh(first).json()["refresh"]
        self._refresh(second)                                       # second now spent
        self.assertEqual(self._refresh(first).status_code, 401)
        self.assertEqual(self._refresh(second).status_code, 401)

    # -- logout -----------------------------------------------------------
    def test_logging_out_kills_the_refresh_token(self):
        data = self._login()
        self.assertEqual(self.c.post(LOGOUT, {"refresh": data["refresh"]}, format="json")
                         .status_code, 204)
        self.assertEqual(self._refresh(data["refresh"]).status_code, 401)

    def test_logout_needs_no_access_token(self):
        # Deliberate: the common case for signing out is an idle session whose
        # access token has already expired. Requiring one would refuse to
        # revoke the refresh token at exactly the moment it was asked to.
        refresh = self._login()["refresh"]
        anon = APIClient()
        self.assertEqual(anon.post(LOGOUT, {"refresh": refresh}, format="json").status_code, 204)
        self.assertEqual(self._refresh(refresh).status_code, 401)

    def test_logging_out_twice_is_not_an_error(self):
        # Clients do not await the call and may retry it; a second logout must
        # not surface a failure on a successful sign-out.
        refresh = self._login()["refresh"]
        self.assertEqual(self.c.post(LOGOUT, {"refresh": refresh}, format="json").status_code, 204)
        self.assertEqual(self.c.post(LOGOUT, {"refresh": refresh}, format="json").status_code, 204)

    def test_logging_out_with_junk_is_not_an_error(self):
        self.assertEqual(self.c.post(LOGOUT, {"refresh": "not-a-token"}, format="json")
                         .status_code, 204)

    def test_logout_without_a_token_is_a_bad_request(self):
        r = self.c.post(LOGOUT, {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("required", r.json()["detail"])

    def test_logging_out_does_not_touch_another_session(self):
        # Two devices, one account. Signing out of the phone must not sign the
        # dashboard out as well.
        one = self._login()["refresh"]
        two = self._login()["refresh"]
        self.assertEqual(self.c.post(LOGOUT, {"refresh": one}, format="json").status_code, 204)

        self.assertEqual(self._refresh(one).status_code, 401)
        self.assertEqual(self._refresh(two).status_code, 200)

    def test_an_access_token_cannot_be_passed_off_as_a_refresh_token(self):
        # Blacklisting reads the jti of a REFRESH token; handing it an access
        # token must be rejected, not silently recorded against the wrong type.
        data = self._login()
        self.assertEqual(self.c.post(LOGOUT, {"refresh": data["access"]}, format="json")
                         .status_code, 204)
        # ...and the real refresh token still works, i.e. nothing was revoked.
        self.assertEqual(self._refresh(data["refresh"]).status_code, 200)

    # -- the settings this all rests on -----------------------------------
    def test_rotation_and_blacklisting_are_both_on(self):
        # REGRESSION GUARD. Either one switched off silently removes the only
        # revocation this system has: without rotation a token is never
        # superseded, and without blacklisting a superseded one still works.
        from django.conf import settings
        self.assertTrue(settings.SIMPLE_JWT.get("ROTATE_REFRESH_TOKENS"))
        self.assertTrue(settings.SIMPLE_JWT.get("BLACKLIST_AFTER_ROTATION"))
        self.assertIn("rest_framework_simplejwt.token_blacklist", settings.INSTALLED_APPS)
