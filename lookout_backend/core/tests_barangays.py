"""The barangay list, and the fact that two projects hold a copy of it.

The API validates `violator_barangay` against ZAMBOANGA_BARANGAYS as model
choices. The officer app has its own copy, because the two projects share no
code. So an option offered on the phone but missing from the backend is a form
an officer can fill in and cannot submit — and nothing but this file stops the
two drifting.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

from core.constants import ZAMBOANGA_BARANGAYS
from core.models import Citation

APP_SCREEN = (Path(settings.BASE_DIR).parent / "officer_app" / "app"
              / "assignment" / "[id].tsx")


def mobile_options():
    """(value, label) pairs out of the app's BARANGAY_OPTIONS literal."""
    source = APP_SCREEN.read_text(encoding="utf-8")
    start = source.index("const BARANGAY_OPTIONS = [")
    block = source[start:source.index("];", start)]
    return re.findall(r'\{\s*value:\s*"([^"]+)",\s*label:\s*"([^"]+)"\s*\}', block)


class BarangayListTests(TestCase):
    def test_the_two_projects_agree_exactly(self):
        """Order included: the picker shows them in list order, and a list that
        agrees on membership but not on order is still two different lists."""
        self.assertEqual(mobile_options(), [list(p) and tuple(p)
                                            for p in ZAMBOANGA_BARANGAYS])

    def test_every_value_fits_the_column(self):
        limit = Citation._meta.get_field("violator_barangay").max_length
        for value, _label in ZAMBOANGA_BARANGAYS:
            with self.subTest(value=value):
                self.assertLessEqual(len(value), limit)

    def test_values_are_ascii_and_punctuation_free(self):
        """A stored key travels through JSON, a URL and a CSV export. Folding
        it ("Sto. Niño" -> STO_NINO) is what stops an encoding difference
        becoming a second, unequal barangay."""
        for value, _label in ZAMBOANGA_BARANGAYS:
            with self.subTest(value=value):
                self.assertRegex(value, r"^[A-Z0-9]+(?:_[A-Z0-9]+)*$")

    def test_no_duplicate_values_or_labels(self):
        values = [v for v, _ in ZAMBOANGA_BARANGAYS]
        labels = [l for _, l in ZAMBOANGA_BARANGAYS]
        self.assertEqual(len(set(values)), len(values))
        self.assertEqual(len(set(labels)), len(labels))

    def test_the_keys_already_in_the_database_still_exist(self):
        """These five were the whole list before the city was filled in. A row
        already stores them, so renaming or dropping one makes that row fail
        validation the next time it is saved."""
        values = dict(ZAMBOANGA_BARANGAYS)
        for value, label in (("TUGBUNGAN", "Tugbungan"), ("TETUAN", "Tetuan"),
                             ("MERCEDES", "Mercedes"), ("LUNZURAN", "Lunzuran"),
                             ("TUMAGA", "Tumaga")):
            with self.subTest(value=value):
                self.assertEqual(values.get(value), label)

    def test_unknown_is_offered_and_is_last(self):
        """An officer who cannot establish where a violator lives has to be
        able to say so; the alternative is a guessed barangay recorded as
        fact. Last because it is not a barangay."""
        self.assertEqual(ZAMBOANGA_BARANGAYS[-1][0], "UNKNOWN")
        self.assertIn("UNKNOWN", [v for v, _ in mobile_options()])

    def test_both_districts_are_represented(self):
        values = {v for v, _ in ZAMBOANGA_BARANGAYS}
        for west in ("AYALA", "CALARIAN", "PASONANCA", "RECODO", "STO_NINO"):
            self.assertIn(west, values, "District I (West Coast)")
        for east in ("GUIWAN", "PUTIK", "TALON_TALON", "VITALI", "ZONE_I"):
            self.assertIn(east, values, "District II (East Coast)")

    def test_the_two_barangays_both_called_dulian_stay_distinct(self):
        """District I's Dulian (Pasonanca) and District II's Dulian (Upper
        Bunguiao) are different places that share a first word."""
        values = {v for v, _ in ZAMBOANGA_BARANGAYS}
        self.assertIn("DULIAN_PASONANCA", values)
        self.assertIn("DULIAN_UPPER_BUNGUIAO", values)

    def test_the_two_san_joses_stay_distinct(self):
        values = {v for v, _ in ZAMBOANGA_BARANGAYS}
        self.assertIn("SAN_JOSE_CAWA_CAWA", values)
        self.assertIn("SAN_JOSE_GUSU", values)

    def test_the_whole_city_is_covered(self):
        """32 (District I) + 65 (District II) + Tumaga + Unknown."""
        self.assertEqual(len(ZAMBOANGA_BARANGAYS), 99)


class BarangayPickerTests(TestCase):
    """The sheet has to stay usable now the list is ninety-nine long."""

    def _source(self):
        return APP_SCREEN.read_text(encoding="utf-8")

    def test_the_sheet_can_be_filtered(self):
        source = self._source()
        self.assertIn("barangayQuery", source)
        self.assertIn("barangayMatches", source)

    def test_the_filter_ignores_case_and_punctuation(self):
        """So "sto nino" finds "Sto. Niño" — an officer should not have to
        reproduce a full stop or an n-tilde mid-report."""
        source = self._source()
        self.assertIn("normalize(\"NFD\")", source)
        self.assertIn("toLowerCase()", source)

    def test_the_rows_scroll(self):
        """Ninety-nine of them do not fit a bottom sheet."""
        self.assertIn("barangayList", self._source())

    def test_an_empty_result_says_what_to_do(self):
        self.assertIn("No barangay matches", self._source())
