"""The two-token contract both clients depend on.

Login hands back a SHORT-lived access token and a LONG-lived refresh token, and
/auth/refresh/ trades the second for a new first. The web dashboard and the
officer app both now retry a 401 by refreshing, so the shape asserted here is
load-bearing for staying logged in -- not just for logging in.

Historically none of this was exercised: the refresh token was issued, the
officer app even stored it in SecureStore, and no client ever redeemed it. The
lifetimes made that rational (8-hour access against a 1-day refresh bought 16
extra hours), which is why test_access_expires_well_before_refresh exists.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

LOGIN = "/api/auth/login/"
REFRESH = "/api/auth/refresh/"


class TokenPairTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="dispatch1", password="pw-correct-horse", role="dispatcher",
            display_name="Dispatch One")
        self.c = APIClient()

    def _login(self):
        r = self.c.post(LOGIN, {"username": "dispatch1", "password": "pw-correct-horse"},
                        format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_login_returns_both_tokens_and_the_user(self):
        data = self._login()
        self.assertTrue(data["access"])
        self.assertTrue(data["refresh"])
        self.assertNotEqual(data["access"], data["refresh"])
        # The clients read role/name off the echoed user, not by decoding a token.
        self.assertEqual(data["user"]["username"], "dispatch1")
        self.assertEqual(data["user"]["role"], "dispatcher")

    def test_the_access_token_carries_the_role_and_name_claims(self):
        token = AccessToken(self._login()["access"])
        self.assertEqual(token["role"], "dispatcher")
        self.assertEqual(token["name"], "Dispatch One")

    def test_the_refresh_token_buys_a_working_access_token(self):
        refresh = self._login()["refresh"]
        r = self.c.post(REFRESH, {"refresh": refresh}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        fresh = r.json()["access"]
        self.assertTrue(fresh)

        # Usable, not merely well-formed: this is the whole point of the retry.
        self.c.credentials(HTTP_AUTHORIZATION=f"Bearer {fresh}")
        self.assertEqual(self.c.get("/api/auth/me/").status_code, 200)

    def test_a_refreshed_token_carries_the_custom_claims_too(self):
        # TokenRefreshView mints from the refresh token's own payload, so the
        # claims added in LookoutTokenObtainPairSerializer.get_token must
        # survive a refresh. If they did not, a long session would quietly lose
        # the role claim partway through.
        refresh = self._login()["refresh"]
        fresh = self.c.post(REFRESH, {"refresh": refresh}, format="json").json()["access"]
        token = AccessToken(fresh)
        self.assertEqual(token["role"], "dispatcher")
        self.assertEqual(token["name"], "Dispatch One")

    def test_an_expired_access_token_is_rejected(self):
        # The 401 both clients now treat as "refresh and retry" rather than
        # "log the user out".
        token = AccessToken.for_user(self.user)
        token.set_exp(from_time=datetime.datetime.now(datetime.timezone.utc),
                      lifetime=-datetime.timedelta(seconds=1))
        self.c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(self.c.get("/api/auth/me/").status_code, 401)

    def test_a_junk_refresh_token_mints_nothing(self):
        r = self.c.post(REFRESH, {"refresh": "not-a-token"}, format="json")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("access", r.json())

    def test_an_access_token_cannot_be_used_as_a_refresh_token(self):
        r = self.c.post(REFRESH, {"refresh": self._login()["access"]}, format="json")
        self.assertEqual(r.status_code, 401)

    def test_access_expires_well_before_refresh(self):
        # REGRESSION: these were 8 hours and 1 day. A refresh token barely
        # outliving its access token makes the second token pointless, which is
        # why /auth/refresh/ sat unused. The short-access/long-refresh split is
        # the reason a leaked bearer token stops working quickly.
        from django.conf import settings
        access = settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]
        refresh = settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]
        self.assertLessEqual(access, datetime.timedelta(hours=1), "access token is too long-lived")
        self.assertGreaterEqual(refresh, access * 24, "refresh token should far outlive the access token")
