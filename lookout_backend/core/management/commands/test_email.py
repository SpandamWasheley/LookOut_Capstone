"""Send one test email and print everything that happened.

The app deliberately hides email failures: the password-reset endpoint returns
the same body whether or not the address exists, and registration reports a
generic "we couldn't send an email". Neither says *why*. This command removes
the app from the loop -- same settings, same backend, same key -- so the cause
shows up in the terminal:

    python manage.py test_email you@example.com
    python manage.py test_email you@example.com --debug   # + the JSON payload

Exit code is 0 only if Brevo accepted the message.
"""
import logging
import time

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.core.management.base import BaseCommand, CommandError


def _mask(value):
    if not value:
        return "<EMPTY>"
    return f"{value[:12]}...{value[-4:]} (len {len(value)})"


class Command(BaseCommand):
    help = "Send a test email and report exactly what the mail backend did."

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="Address to send the test message to.")
        parser.add_argument(
            "--subject", default="LookOut test email",
            help="Subject line (default: 'LookOut test email').",
        )
        parser.add_argument(
            "--debug", action="store_true",
            help="Log at DEBUG, which also prints the JSON payload sent to Brevo.",
        )

    def handle(self, *args, **options):
        recipient = options["recipient"]

        # The command's own console handler, so output appears regardless of
        # what LOGGING in settings.py is set to.
        handler = logging.StreamHandler(self.stderr)
        handler.setFormatter(logging.Formatter("[{asctime}] {levelname} {name}: {message}", style="{"))
        mail_log = logging.getLogger("core.mail")
        mail_log.addHandler(handler)
        mail_log.setLevel(logging.DEBUG if options["debug"] else logging.INFO)

        self.stdout.write(self.style.MIGRATE_HEADING("Email configuration"))
        rows = [
            ("EMAIL_BACKEND", settings.EMAIL_BACKEND),
            ("DEFAULT_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL or "<EMPTY>"),
            ("BREVO_API_KEY", _mask(getattr(settings, "BREVO_API_KEY", ""))),
            ("BREVO_TIMEOUT", getattr(settings, "BREVO_TIMEOUT", "-")),
            ("Recipient", recipient),
        ]
        for label, value in rows:
            self.stdout.write(f"  {label:<20} {value}")

        # The two mistakes worth naming before a single byte goes out.
        key = getattr(settings, "BREVO_API_KEY", "")
        if settings.EMAIL_BACKEND.endswith("BrevoAPIBackend"):
            if not key:
                self.stderr.write(self.style.ERROR(
                    "\n  BREVO_API_KEY is empty. Add it to lookout_backend/.env."))
            elif key.startswith("xsmtpsib-"):
                self.stderr.write(self.style.ERROR(
                    "\n  That is the SMTP key (xsmtpsib-...), not the API key. The HTTP\n"
                    "  API needs the key that starts with 'xkeysib-'. Brevo -> SMTP & API."))
            elif not key.startswith("xkeysib-"):
                self.stderr.write(self.style.WARNING(
                    "\n  BREVO_API_KEY does not start with 'xkeysib-' -- check it is the\n"
                    "  API key and not something else."))
        if not settings.DEFAULT_FROM_EMAIL:
            self.stderr.write(self.style.ERROR(
                "  DEFAULT_FROM_EMAIL is empty. It must be an address verified under\n"
                "  Brevo -> Senders, or every send is refused."))

        connection = get_connection()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nSending via {type(connection).__module__}.{type(connection).__name__}"))

        started = time.monotonic()
        try:
            sent = send_mail(
                options["subject"],
                "This is a test message from LookOut. If you are reading it, "
                "verification codes and password-reset codes can reach this address.",
                settings.DEFAULT_FROM_EMAIL,
                [recipient],
                fail_silently=False,
            )
        except Exception as exc:
            elapsed = time.monotonic() - started
            self.stderr.write(self.style.ERROR(
                f"\nFAILED after {elapsed:.2f}s\n  {type(exc).__name__}: {exc}"))
            raise CommandError("The email was not sent. See the error above.")

        elapsed = time.monotonic() - started
        if sent:
            self.stdout.write(self.style.SUCCESS(
                f"\nSent in {elapsed:.2f}s. Check {recipient} (including spam)."))
        else:
            # send_mail returning 0 without raising means fail_silently somewhere.
            raise CommandError(
                f"The backend reported 0 messages sent after {elapsed:.2f}s, without "
                "raising. Nothing was delivered."
            )
