"""Send one test text and print everything that happened.

The SMS sibling of test_email, and it exists for the same reason: the app
deliberately swallows notification failures (core/sms.py never raises, because
a dispatcher's Assign must not fail over a text), so a number that never
receives anything looks identical to one that was never meant to. This removes
the app from the loop -- same settings, same key, same provider -- so the cause
shows up in the terminal:

    python manage.py test_sms 0917 123 4567
    python manage.py test_sms +639171234567 --debug   # + the provider's reply

It deliberately BYPASSES the two database switches (SystemSettings.sms_alerts_
enabled and the recipient's own User.sms_alerts) and texts the number given on
the command line. Those switches are consent and cost control for real alerts;
this is a wiring check an admin runs against their own handset. Their current
state is printed, because "the credentials work but nothing sends" is the
likeliest confusion and that is where the answer is.

Exit code is 0 only if the provider accepted the message.
"""
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import sms
from core.models import SystemSettings, normalize_ph_mobile


class Command(BaseCommand):
    help = "Send a test SMS and report exactly what the provider did."

    def add_arguments(self, parser):
        # nargs="+" so a number pasted with its spaces ("0917 123 4567") works
        # without the operator having to quote it; normalize_ph_mobile strips
        # the separators anyway.
        parser.add_argument(
            "recipient", nargs="+",
            help="Philippine mobile number, however it is written (0917..., +63917...).",
        )
        parser.add_argument(
            "--message", default="LookOut test message. If you are reading this, "
                                 "assignment texts can reach this number.",
            help="Text to send instead of the default.",
        )
        parser.add_argument(
            "--debug", action="store_true",
            help="Log at DEBUG, which also prints the provider's reply.",
        )

    def handle(self, *args, **options):
        raw = " ".join(options["recipient"])
        try:
            recipient = normalize_ph_mobile(raw)
        except ValueError as exc:
            raise CommandError(f"{exc} (you gave {raw!r})")
        if not recipient:
            raise CommandError("No number given.")

        # The command's own console handler, so output appears regardless of
        # what LOGGING in settings.py is set to.
        handler = logging.StreamHandler(self.stderr)
        handler.setFormatter(logging.Formatter("[{asctime}] {levelname} {name}: {message}", style="{"))
        sms_log = logging.getLogger("core.sms")
        sms_log.addHandler(handler)
        sms_log.setLevel(logging.DEBUG if options["debug"] else logging.INFO)

        key = getattr(settings, "UNISMS_API_KEY", "")
        sender_id = getattr(settings, "UNISMS_SENDER_ID", "")

        self.stdout.write(self.style.MIGRATE_HEADING("SMS configuration"))
        for label, value in [
            ("Provider", sms.SEND_URL),
            ("UNISMS_API_KEY", sms._mask(key)),
            ("UNISMS_SENDER_ID", sender_id or "<EMPTY>"),
            ("Recipient", recipient),
        ]:
            self.stdout.write(f"  {label:<20} {value}")

        # The two mistakes worth naming before a single byte goes out.
        if not key:
            self.stderr.write(self.style.ERROR(
                "\n  UNISMS_API_KEY is empty. Add it to lookout_backend/.env -- the\n"
                "  secret key from unismsapi.com -> API keys."))
            raise CommandError("No API key, so nothing was attempted.")
        if not sender_id:
            self.stderr.write(self.style.ERROR(
                "\n  UNISMS_SENDER_ID is empty. Philippine telcos require a registered\n"
                "  sender, so the provider will refuse this with a 422. Register one\n"
                "  under unismsapi.com -> Sender IDs (new accounts get a temporary\n"
                "  random one to test with)."))

        # Printed, not enforced: this command ignores them on purpose, but an
        # admin whose test arrives while real alerts stay silent needs to see
        # that these are why.
        self.stdout.write(self.style.MIGRATE_HEADING("\nSwitches that gate REAL alerts (not this test)"))
        enabled = SystemSettings.load().sms_alerts_enabled
        self.stdout.write(
            "  sms_alerts_enabled   {}".format(
                self.style.SUCCESS("on") if enabled
                else self.style.WARNING("OFF -- Settings -> 'SMS notifications'")
            )
        )
        opted_in = _recipients_opted_in(recipient)
        self.stdout.write(
            "  accounts opted in    {}".format(
                self.style.SUCCESS(opted_in) if opted_in
                else self.style.WARNING("none with this number (Profile -> allow texts)")
            )
        )

        message = {
            "recipient": recipient,
            "content": options["message"],
            "sender_id": sender_id,
            "metadata": {"test": "manage.py test_sms"},
        }

        content = message["content"]
        note = "" if sms.is_gsm7(content) else (
            " -- contains non-GSM-7 characters, so segments are 70 chars, not 160")
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nSending {len(content)} characters"
            f" ({sms.segments(content)} segment(s), billed per segment){note}"))

        started = time.monotonic()
        # `deliver` never raises -- it returns a count -- so there is no
        # try/except here. The reason for any failure has already gone to the
        # logger that was attached above, including the status-code hint.
        accepted = sms.deliver([message])
        elapsed = time.monotonic() - started

        if accepted:
            self.stdout.write(self.style.SUCCESS(
                f"\nAccepted in {elapsed:.2f}s. The provider has queued it; delivery to\n"
                f"{recipient} is up to the network from here."))
            return
        raise CommandError(
            f"The provider did not accept the message ({elapsed:.2f}s). See the "
            "error above for which setting to change."
        )


def _recipients_opted_in(phone):
    """How many accounts would actually be texted at this number, as a label."""
    from core.models import User

    count = User.objects.filter(phone=phone, sms_alerts=True, is_active=True).count()
    return f"{count} account(s)" if count else ""
