"""Send the half-hourly SMS digest: how many violations, how many are yours.

The second of the two SMS triggers (see core/sms.py). A holdup or an
obstruction texts its assigned officer the instant it is assigned, from the
request that assigns them; everything else is counted up and reported here, so
five smoking violations in a half hour cost one message instead of five.

Who gets what:

* admin / dispatcher / both -- "N new violations detected", counted from the
  stored watermark (SystemSettings.sms_digest_sent_at). Nothing used to text
  them at all.
* officer / both -- "N assigned to you", counted from the AssignmentNotice
  rows no digest has reported yet.
* a "both" account gets ONE message carrying both numbers.

Each recipient still needs all three of the usual gates: the system-wide
SystemSettings.sms_alerts_enabled, their own User.sms_alerts, and a number on
file. Unlike `test_sms`, this command does NOT bypass them -- it is the real
sender, not a wiring check. A recipient with nothing to report gets nothing,
so a quiet half hour costs nothing.

Two ways to run it, because this project has no Celery, no beat and no broker:

    python manage.py sms_digest                 # one round, then exit
    python manage.py sms_digest --every 30      # loop, sleeping 30 min between
    python manage.py sms_digest --dry-run       # print what would be sent

The loop is the deployment this was written for: it is started the way the
watch_* detectors are, on the on-site PC, and it keeps the interval in one
place. It does not survive a reboot -- if that matters, use the one-shot form
from Windows Task Scheduler (or cron on a server) instead and let the OS own
the clock.
"""
import logging
import time

from django.core.management.base import BaseCommand, CommandError

from core import sms
from core.models import SystemSettings


class Command(BaseCommand):
    help = "Send the half-hourly SMS digest of new and newly-assigned violations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--every", type=int, default=None, metavar="MINUTES",
            help="Keep running, sending a digest every MINUTES minutes. "
                 f"Omit to send one round and exit. The message says 'the last "
                 f"{sms.DIGEST_INTERVAL_MINUTES} min', so a much shorter "
                 "interval would make it inaccurate.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Work out and print the digests without sending any. Does not "
                 "move the watermark, so a real run afterwards reports the "
                 "same window.",
        )
        parser.add_argument(
            "--verbose", action="store_true",
            help="Log core.sms at DEBUG, which prints why a message was skipped.",
        )

    def handle(self, *args, **options):
        if options["verbose"]:
            logging.basicConfig(level=logging.DEBUG)
            logging.getLogger("core.sms").setLevel(logging.DEBUG)

        every = options["every"]
        if every is not None and every < 1:
            raise CommandError("--every must be at least 1 minute.")

        # Printed once, not per round: these are the two switches that make the
        # difference between "nothing to send" and "not sending", and that is
        # the confusion worth pre-empting. Same reasoning as test_sms.
        row = SystemSettings.load()
        self.stdout.write(
            f"sms_alerts_enabled={row.sms_alerts_enabled}  "
            f"last digest={row.sms_digest_sent_at or '<never>'}"
        )
        if not row.sms_alerts_enabled:
            self.stdout.write(self.style.WARNING(
                "The system-wide switch is OFF, so nothing will be sent. Turn "
                "it on in Settings. The watermark is left alone meanwhile, so "
                "nothing that happens while it is off gets lost."
            ))

        if every is None:
            self._round(options["dry_run"])
            return

        self.stdout.write(f"Looping every {every} min. Ctrl-C to stop.")
        while True:
            self._round(options["dry_run"])
            try:
                time.sleep(every * 60)
            except KeyboardInterrupt:
                self.stdout.write("\nStopped.")
                return

    def _round(self, dry_run):
        """One digest. Never raises: a loop must outlive a bad round."""
        try:
            plan = sms.digest_plan() if dry_run else sms.send_digest()
        except Exception:
            # The loop form of this command is a long-running process on the
            # detection PC, and the thing it is most likely to meet is a
            # database locked by a watcher writing an alert. Dying on it would
            # mean no digests until somebody notices.
            self.stderr.write(self.style.ERROR("This round failed:"))
            logging.getLogger(__name__).exception("sms_digest round failed")
            return

        when = plan["now"].strftime("%H:%M")
        if not plan["entries"]:
            self.stdout.write(
                f"{when}  nothing to report ({plan['detected']} detected since "
                f"{plan['since'] or '<switch off>'})"
            )
            return

        verb = "would send" if dry_run else f"sent {plan.get('sent', 0)}/{len(plan['entries'])}"
        self.stdout.write(
            f"{when}  {verb}  |  {plan['detected']} detected since {plan['since']}"
        )
        for entry in plan["entries"]:
            user = entry["user"]
            # The number is masked to its last four digits for the same reason
            # core/sms.py masks it in the log: a terminal is a less protected
            # place than the database, and four digits is enough to tell two
            # officers apart while chasing a failure.
            self.stdout.write(
                f"    ...{user.phone[-4:]}  {user.role:<10} "
                f"{entry['message']['content']}"
            )
