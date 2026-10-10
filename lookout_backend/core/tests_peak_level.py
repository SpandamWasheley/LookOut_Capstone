"""peak_level: an event that earned attention keeps it.

`level` falls as well as rises — a Possible event whose cues fade drops back to
Monitoring — and the alert lists used to split on it. So an event could vanish
from Potential Violations while a reviewer was looking at it. The lists now
split on the highest status the event ever reached; the badge still shows the
current one.

Two halves: that the field only ever rises (IncidentMixin), and that the two
lists partition on it cleanly (AlertViewSet).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.management.commands._incidents import Incident, IncidentMixin
from core.models import Alert, Camera, ViolationType
from core.vision import scoring


class _Writer(IncidentMixin):
    """The smallest thing that can drive _incident_write."""

    spec_snapshot = None
    clock_start = None
    _source_path = None


def _score(level):
    """A Score whose level is `level`, built from a single cue worth enough."""
    weights = {"cigarette": {scoring.MONITORING: 0.10,
                             scoring.WARNING: 0.60,
                             scoring.VIOLATION: 0.80}[level]}
    s = scoring.Score("smoking", weights, {"cigarette"})
    assert s.level == level, f"wanted {level}, built {s.level}"
    return s


class PeakOnlyRisesTests(TestCase):
    def setUp(self):
        self.vt = ViolationType.objects.create(code="smoking", label="Smoking",
                                               color="#f59e0b", icon="cigarette")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision")
        self.alert = Alert.objects.create(type=self.vt, camera=self.cam,
                                          timestamp=timezone.now(), confidence=0.2,
                                          level=scoring.MONITORING,
                                          peak_level=scoring.MONITORING)
        self.inc = Incident(0.0)
        self.inc.alert = self.alert
        self.inc.peak = scoring.MONITORING
        self.writer = _Writer()

    def _write(self, level, now):
        """One row update, mirroring what _incident_update does around it."""
        self.writer._incident_write(self.inc, _score(level), level, None, None,
                                    now=now, changed=True)
        if scoring.LEVEL_ORDER[level] > scoring.LEVEL_ORDER[self.inc.peak]:
            self.inc.peak = level       # _incident_update advances it after the write
        self.alert.refresh_from_db()

    def test_a_rise_raises_the_peak(self):
        self._write(scoring.WARNING, 1.0)
        self.assertEqual(self.alert.level, scoring.WARNING)
        self.assertEqual(self.alert.peak_level, scoring.WARNING)

    def test_a_fall_moves_level_but_leaves_the_peak(self):
        # The whole point: Possible, then the cues fade.
        self._write(scoring.WARNING, 1.0)
        self._write(scoring.MONITORING, 2.0)
        self.assertEqual(self.alert.level, scoring.MONITORING)
        self.assertEqual(self.alert.peak_level, scoring.WARNING)

    def test_it_survives_a_whole_sequence_of_flips(self):
        for level, t in ((scoring.WARNING, 1.0), (scoring.MONITORING, 2.0),
                         (scoring.VIOLATION, 3.0), (scoring.MONITORING, 4.0),
                         (scoring.WARNING, 5.0), (scoring.MONITORING, 6.0)):
            self._write(level, t)
        self.assertEqual(self.alert.level, scoring.MONITORING)
        self.assertEqual(self.alert.peak_level, scoring.VIOLATION)

    def test_a_write_that_does_not_rise_leaves_the_column_untouched(self):
        # Not merely "writes the same value": peak_level must not even be in the
        # update, so a concurrent write can't be clobbered with a stale peak.
        self._write(scoring.VIOLATION, 1.0)
        Alert.objects.filter(pk=self.alert.pk).update(peak_level=scoring.VIOLATION)
        self._write(scoring.MONITORING, 2.0)
        self.assertEqual(self.alert.peak_level, scoring.VIOLATION)


class ListPartitionTests(TestCase):
    """The two lists split on the current level and must stay a clean partition."""

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)
        self.vt = ViolationType.objects.create(code="smoking", label="Smoking",
                                               color="#f59e0b", icon="cigarette")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision")

    def _alert(self, level, peak):
        return Alert.objects.create(type=self.vt, camera=self.cam,
                                    timestamp=timezone.now(), confidence=0.5,
                                    level=level, peak_level=peak)

    def _codes(self, **params):
        r = self.c.get("/api/alerts/", params)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        rows = body["results"] if isinstance(body, dict) else body   # paginated or not
        return {a["code"] for a in rows}

    def test_a_faded_event_leaves_potential_violations(self):
        faded = self._alert(scoring.MONITORING, scoring.WARNING)
        self.assertNotIn(faded.code, self._codes())

    def test_a_faded_event_moves_to_the_watchlist(self):
        # Listed in exactly one place: the watchlist, matching its Monitoring badge.
        faded = self._alert(scoring.MONITORING, scoring.WARNING)
        self.assertIn(faded.code, self._codes(level="monitoring"))

    def test_an_event_that_never_rose_stays_on_the_watchlist_only(self):
        quiet = self._alert(scoring.MONITORING, scoring.MONITORING)
        self.assertNotIn(quiet.code, self._codes())
        self.assertIn(quiet.code, self._codes(level="monitoring"))

    def test_include_monitoring_still_returns_both(self):
        quiet = self._alert(scoring.MONITORING, scoring.MONITORING)
        faded = self._alert(scoring.MONITORING, scoring.WARNING)
        both = self._codes(include_monitoring=1)
        self.assertIn(quiet.code, both)
        self.assertIn(faded.code, both)

    def test_every_alert_is_in_exactly_one_of_the_two_lists(self):
        made = {self._alert(scoring.MONITORING, scoring.MONITORING).code,
                self._alert(scoring.MONITORING, scoring.WARNING).code,
                self._alert(scoring.WARNING, scoring.WARNING).code,
                self._alert(scoring.VIOLATION, scoring.VIOLATION).code,
                self._alert("", "").code}          # a parking alert: no scoring at all
        main, watch = self._codes(), self._codes(level="monitoring")
        self.assertEqual(main & watch, set(), "an alert is in both lists")
        self.assertEqual(made - (main | watch), set(), "an alert is in neither list")

    def test_a_pre_scoring_alert_is_still_listed(self):
        # The 231 rows with no level at all (parking, and everything older than
        # the scoring model). Backfilled to "", which must not read as monitoring.
        old = self._alert("", "")
        self.assertIn(old.code, self._codes())


class SerialisationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)
        vt = ViolationType.objects.create(code="smoking", label="Smoking",
                                          color="#f59e0b", icon="cigarette")
        cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision")
        self.alert = Alert.objects.create(type=vt, camera=cam, timestamp=timezone.now(),
                                          confidence=0.5, level=scoring.MONITORING,
                                          peak_level=scoring.WARNING)

    def _body(self):
        r = self.c.get(f"/api/alerts/{self.alert.pk}/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_the_badge_shows_the_current_status_not_the_peak(self):
        body = self._body()
        self.assertEqual(body["level_label"], "Monitoring")
        self.assertEqual(body["peak_level_label"], "Possible")

    def test_a_pre_scoring_alert_labels_as_empty_rather_than_guessing(self):
        self.alert.level = self.alert.peak_level = ""
        self.alert.save(update_fields=["level", "peak_level"])
        body = self._body()
        self.assertEqual(body["level_label"], "")
        self.assertEqual(body["peak_level_label"], "")

    def test_a_client_cannot_rewrite_the_peak(self):
        # It is detector-written. A client that could raise its own peak could
        # park any event permanently in Potential Violations.
        r = self.c.patch(f"/api/alerts/{self.alert.pk}/",
                         {"peak_level": scoring.VIOLATION}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.peak_level, scoring.WARNING)
