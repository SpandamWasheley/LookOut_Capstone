"""What an officer can tick on a citation slip.

The slip filters the API's ViolationType rows through its own allowlist, so a
type the backend serves is still uncitable until it is named there too. These
guard that the two stay in step, and that the slip's own wording does not drift
into being a second identity for a violation.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from core.models import ViolationType

SLIP = (Path(settings.BASE_DIR).parent / "officer_app" / "app"
        / "assignment" / "[id].tsx")

# The override map started life inside the slip screen and has since moved to
# its own module, applied wherever the app shows a label rather than on the
# slip alone -- which is the point of it: the alert an officer opens and the
# citation they write from it must not disagree about what the offence is
# called. These tests follow it there; they were asserting against a constant
# that no longer existed, and so had stopped guarding anything.
LABELS = (Path(settings.BASE_DIR).parent / "officer_app" / "lib"
          / "violationLabels.ts")


def slip_source():
    return SLIP.read_text(encoding="utf-8")


def allowlist():
    source = slip_source()
    block = source[source.index("const IN_SCOPE_VIOLATION_CODES = ["):]
    return re.findall(r'"([^"]+)"', block[:block.index("]")])


def labels_source():
    return LABELS.read_text(encoding="utf-8")


def slip_labels():
    source = labels_source()
    start = source.index("export const DISPLAY_LABELS: Record<string, string> = {")
    block = source[start:source.index("};", start)]
    return dict(re.findall(r"(\w+):\s*\"([^\"]+)\"", block))


class SlipScopeTests(TestCase):
    def test_all_five_in_scope_violations_can_be_cited(self):
        """The failure this closes: disposal was missing while its detector was
        being built, so an officer dispatched to a disposal alert reached a slip
        with nothing on it they could tick."""
        self.assertEqual(
            sorted(allowlist()),
            ["disposal", "drinking", "parking", "smoking", "theft"])

    def test_nothing_out_of_scope_is_offered(self):
        """Curfew, noise and waste_* are permanently out of scope. The
        allowlist exists so a stale row in some environment's database cannot
        render as selectable."""
        for gone in ("curfew", "noise", "waste", "unknown"):
            self.assertNotIn(gone, allowlist())

    def test_every_allowed_code_is_a_real_violation_type(self):
        """An allowlisted code the API never serves is simply an option that
        never appears — silent, and indistinguishable from a filter bug."""
        from django.core.management import call_command
        call_command("seed_core", verbosity=0)
        seeded = set(ViolationType.objects.values_list("code", flat=True))
        for code in allowlist():
            with self.subTest(code=code):
                self.assertIn(code, seeded)


class SlipWordingTests(TestCase):
    def test_disposal_is_cited_as_abandoned_waste(self):
        """A citation names the offence the way the ordinance does; the
        detector's own name describes the thing it watches for."""
        self.assertEqual(slip_labels().get("disposal"), "Abandoned Waste")

    def test_the_override_is_display_only(self):
        """The code, colour and icon are untouched, so the dashboard and the
        slip cannot come to disagree about WHICH violation a citation is for."""
        from django.core.management import call_command
        call_command("seed_core", verbosity=0)
        row = ViolationType.objects.get(code="disposal")
        self.assertEqual(row.label, "Improper Disposal")
        self.assertEqual(row.color.lower(), "#22c55e")

    def test_an_un_overridden_type_keeps_its_own_label(self):
        """The map is a fallback chain, not a replacement list — adding one
        entry must not blank the other four."""
        self.assertIn("DISPLAY_LABELS[type.code] ?? type.label", labels_source())
        for code in ("smoking", "drinking", "parking", "theft"):
            self.assertNotIn(code, slip_labels())

    def test_the_slip_never_shows_a_raw_label_beside_an_overridden_one(self):
        """Every place the slip prints a violation name goes through
        slipLabel(). One that did not would show "Improper Disposal" on the
        review page under an "Abandoned Waste" tickbox."""
        source = slip_source()
        body = source[source.index("export default function"):]
        self.assertNotIn("{t.label}", body)
        self.assertNotIn(".map((t) => t.label)", body)

    def test_theft_is_named_for_what_the_camera_can_establish(self):
        """"Holdup" named a crime; the detector finds a knife near someone.
        The CODE stays "theft", so nothing already filed has to be re-matched
        -- see migration 0079."""
        from django.core.management import call_command
        call_command("seed_core", verbosity=0)
        row = ViolationType.objects.get(code="theft")
        self.assertEqual(row.label, "Dangerous Weapon Observed")
