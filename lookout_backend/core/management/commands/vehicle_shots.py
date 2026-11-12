"""Turn a finished obstruction run into evidence a person can check.

    python manage.py vehicle_shots --job 196
    python manage.py vehicle_shots --job 196 --id 1
    python manage.py vehicle_shots --job 196 --moving        # include pass-bys

For every vehicle the run watched it writes ONE picture with three panels --
arrived, stopped, left -- each cut from the clip at that moment, with the area
drawn, the vehicle boxed, and the readings that decided its fate printed
underneath. Then a table of all of them, in the columns an evaluation sheet
uses.

WHY THE PICTURES EXIST
----------------------
Half of that table is ground truth and no camera can supply it: when did the
vehicle REALLY arrive, really stop, really leave. The only way to fill it is to
watch the clip, and the only way to watch the right part of the clip is to know
where to look. These are that: the system's own answer at each of the three
moments, with the vehicle pointed at, so a person can confirm or correct it
against the footage instead of scrubbing a 17-minute file hunting for a car.

The system's columns are filled in. The person's are left blank on purpose.
"""
import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from core import evaluation


class Command(BaseCommand):
    help = "Per-vehicle evidence shots and the evaluation table for one obstruction run."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, required=True)
        parser.add_argument("--id", type=int, default=None,
                            help="Only this vehicle (default: every one that mattered).")
        parser.add_argument("--alerted", type=int, default=1,
                            help="How many alerting vehicles to picture (default 1).")
        parser.add_argument("--short", type=int, default=1,
                            help="How many that parked but fell short of the threshold "
                                 "(default 1).")
        parser.add_argument("--passing", type=int, default=3,
                            help="How many pass-bys, as true-negative examples (default 3).")
        parser.add_argument("--moving", action="store_true",
                            help="Include vehicles that banked no time at all. There are "
                                 "usually hundreds; by default only the ones that stopped "
                                 "or alerted get pictures.")
        parser.add_argument("--out", default=None,
                            help="Where to write (default: media/evaluation/<clip>/).")

    def handle(self, *args, **opts):
        from django.conf import settings

        from core.models import DetectionJob

        job = DetectionJob.objects.filter(pk=opts["job"]).first()
        if job is None:
            raise SystemExit(f"No detection job {opts['job']}.")
        source = Path(str(job.source_path))

        log = ""
        log_path = Path(str(job.source_path) + ".log")
        if log_path.is_file():
            log = log_path.read_text(encoding="utf-8", errors="replace")
        data = evaluation.read_ledger(job.source_path, log)
        rows = data.get("vehicles") or []
        if not rows:
            raise SystemExit(
                "This run kept no vehicle records. Runs now checkpoint the ledger every "
                "30s, so even a stopped one leaves something — but a run from before "
                "that, or one stopped inside its first half-minute, has nothing to read.")

        threshold = data.get("threshold_seconds") or 0
        zone_points = data.get("zone_points")
        # The SAME folder the run itself writes to, keyed on the clip rather
        # than the job number: the detector has no job id to hand, and two
        # folders for one run's evidence is how people end up looking at the
        # wrong pack.
        out_dir = Path(opts["out"] or (Path(settings.MEDIA_ROOT) / "evaluation" / source.stem))
        out_dir.mkdir(parents=True, exist_ok=True)

        if opts["id"] is not None:
            rows = [r for r in rows if int(r.get("display_id", -1)) == opts["id"]]
            if not rows:
                raise SystemExit(f"This run has no vehicle #{opts['id']}.")
        elif not opts["moving"]:
            rows = evaluation.choose(rows, alerted=opts["alerted"],
                                     short=opts["short"], passing=opts["passing"])

        self.stdout.write(f"{data.get('source')} · threshold {threshold:.0f}s · "
                          f"{len(rows)} vehicle(s) to picture")
        if data.get("source") == "log":
            self.stdout.write(self.style.WARNING(
                "  Read from the run log: no boxes and no stop times were kept, so the "
                "stills cannot point at the vehicle. Re-run the clip for full shots."))

        # The same builder every run calls at its own finish, so a pack made
        # by hand here and one made automatically cannot drift apart.
        # only_mattering is False: the selection has already been applied
        # above, by this command's own flags.
        made, table = evaluation.build_pack(
            source, {**data, "vehicles": rows}, out_dir,
            only_mattering=False, log=lambda m: self.stdout.write(m))
        self.stdout.write(self.style.SUCCESS(
            f"\n{len(made)} picture(s) and the table in {out_dir}"))
        if table:
            self.stdout.write(f"  table: {table.name}")

