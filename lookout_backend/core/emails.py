"""The emails LookOut sends, one function each.

Every message is multipart: a plain-text part and an HTML alternative. Clients
that refuse HTML (and the spam filters that score a mail lower for having no
text part) get the text; everyone else gets the styled version. `core.mail`
already maps `attach_alternative(..., "text/html")` onto Brevo's `htmlContent`,
so nothing in the backend had to change for this.

Each function raises on a delivery failure rather than returning a flag. The
caller decides what that means -- registration reports it to the user, the
password-reset path deliberately swallows it so the response cannot be used to
tell whether an address has an account. Both log it. See core/views.py.

Both messages carry a one-time code rather than a link. That is why the HTML
centres on a code block instead of a button: there is nothing to click, and the
number has to survive being read off a phone screen and typed into another
device.
"""
from django.utils.html import escape
from django.core.mail import EmailMultiAlternatives

# The dashboard's dark surface (#0c0f16), reused so the mail does not look like
# it came from a different product than the screen the code is typed into.
INK = "#0c0f16"
MUTED = "#64748b"
BORDER = "#e2e8f0"

PRODUCT = "LookOut"
TAGLINE = "Barangay security monitoring"


def _expiry_sentence(minutes):
    unit = "minute" if minutes == 1 else "minutes"
    return f"This code expires in {minutes} {unit}."


def _code_message(subject, recipient, lead, code, minutes, closing):
    """A one-time-code email, as text plus an HTML alternative.

    `lead` and `closing` are the only per-message prose; everything else --
    the code block, the expiry line, the footer -- is identical between the two
    emails and lives here so they cannot drift apart.
    """
    expiry = _expiry_sentence(minutes)

    text = (
        f"{lead}\n\n"
        f"    {code}\n\n"
        f"{expiry}\n\n"
        f"{closing}\n\n"
        f"-- {PRODUCT}, {TAGLINE.lower()}"
    )

    # Inline styles only, and no external CSS or images: Gmail strips <style>
    # blocks in some views and most clients block remote images by default, so
    # anything load-bearing has to be inline and text.
    html = (
        f'<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'max-width:480px;margin:0 auto;padding:8px 4px;color:{INK}">'
        f'<p style="font-size:13px;letter-spacing:0.08em;text-transform:uppercase;'
        f'color:{MUTED};margin:0 0 24px">{PRODUCT}</p>'
        f'<p style="font-size:15px;line-height:1.5;margin:0 0 20px">{escape(lead)}</p>'
        # The code is the one thing that must be unmistakable and selectable --
        # monospace, spaced out, large enough to read off a phone at arm's length.
        f'<p style="font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
        f'font-size:32px;font-weight:700;letter-spacing:0.18em;background:#f8fafc;'
        f'border:1px solid {BORDER};border-radius:8px;padding:18px 24px;margin:0 0 20px;'
        f'text-align:center">{escape(code)}</p>'
        f'<p style="font-size:14px;line-height:1.5;margin:0 0 20px">{escape(expiry)}</p>'
        f'<p style="font-size:13px;line-height:1.5;color:{MUTED};margin:0 0 24px">'
        f'{escape(closing)}</p>'
        f'<p style="font-size:12px;color:{MUTED};border-top:1px solid {BORDER};'
        f'padding-top:16px;margin:0">{PRODUCT} — {TAGLINE}</p>'
        f'</div>'
    )

    # from_email is left out so DEFAULT_FROM_EMAIL applies. Setting it per
    # message is how a sender that Brevo has not verified gets in and silently
    # drops the mail.
    message = EmailMultiAlternatives(subject, text, to=[recipient])
    message.attach_alternative(html, "text/html")
    return message


def send_verification_code(recipient: str, code: str, minutes: int) -> None:
    """Email a registration verification code. Raises on delivery failure."""
    _code_message(
        f"Your {PRODUCT} verification code",
        recipient,
        lead=(
            f"An account is being created for you on {PRODUCT}, the barangay "
            "security monitoring system. Enter this code to verify your email "
            "address:"
        ),
        code=code,
        minutes=minutes,
        closing="If you didn't request this, you can ignore this email.",
    ).send(fail_silently=False)


def send_password_reset_code(recipient: str, code: str, minutes: int) -> None:
    """Email a password-reset code. Raises on delivery failure."""
    _code_message(
        f"Your {PRODUCT} password reset code",
        recipient,
        lead=(
            f"Someone asked to reset the password for your {PRODUCT} account. "
            "Enter this code to choose a new one:"
        ),
        code=code,
        minutes=minutes,
        closing=(
            "If you didn't request this, you can ignore this email — your "
            "password has not been changed."
        ),
    ).send(fail_silently=False)
