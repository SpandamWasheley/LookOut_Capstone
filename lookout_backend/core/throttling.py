from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginThrottle(AnonRateThrottle):
    scope = "login"
    rate = "10/min"


class OtpSendThrottle(UserRateThrottle):
    scope = "otp_send"
    rate = "5/hour"


class OtpVerifyThrottle(UserRateThrottle):
    scope = "otp_verify"
    rate = "10/hour"


class PasswordChangeThrottle(UserRateThrottle):
    """change_password verifies the CURRENT password, so it is a guessing
    oracle unless it is capped. 10/hour leaves room for an honest mistype and
    makes brute force useless. Keyed per user, which is the account under
    attack -- an anon key would be the attacker's IP, which they can change."""

    scope = "password_change"
    rate = "10/hour"


class PasswordResetSendThrottle(AnonRateThrottle):
    scope = "password_reset_send"
    rate = "5/hour"


class PasswordResetConfirmThrottle(AnonRateThrottle):
    scope = "password_reset_confirm"
    rate = "10/hour"
