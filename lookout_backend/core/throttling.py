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


class PasswordResetSendThrottle(AnonRateThrottle):
    scope = "password_reset_send"
    rate = "5/hour"


class PasswordResetConfirmThrottle(AnonRateThrottle):
    scope = "password_reset_confirm"
    rate = "10/hour"
