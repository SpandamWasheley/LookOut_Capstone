"""Tests for weight calibration and the reviewer audit trail.

Two things that outlive any particular detector: the harness that turns the
reasoned default weights into a measured result, and the record of WHO closed
each alert.
"""

import io
from django.core.management import call_command
from django.test import TestCase

from core.models import Alert, Camera, ViolationType
from core.vision import scoring


class CalibrateWeightsTests(TestCase):
    """The harness that turns reasoned defaults into a measured result."""

    def setUp(self):
        self.vtype = ViolationType.objects.create(
            code="drinking", label="Public Drinking")
        self.camera = Camera.objects.create(code="CAM-T1", name="Test")

    def _alert(self, fired, valid):
        weights = {c: scoring.DRINKING_WEIGHTS[c] for c in fired}
        return Alert.objects.create(
            type=self.vtype, camera=self.camera,
            timestamp="2026-05-01T12:00:00Z",
            confidence=sum(weights.values()),
            cues={"kind": "drinking", "cues": weights, "level": "warning"},
            reviewed_valid=valid,
        )

    def _run(self, *args):
        out = io.StringIO()
        call_command("calibrate_weights", "drinking", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_it_refuses_to_fit_on_too_little_data(self):
        for _ in range(5):
            self._alert({"bottle", "gathering"}, True)
        self.assertIn("need at least", self._run().lower())

    def test_it_refuses_a_single_class_dataset(self):
        """60 positives and 0 negatives has nothing to separate."""
        for _ in range(25):
            self._alert({"bottle", "gathering_duration", "time_band"}, True)
        self.assertIn("each class", self._run())

    def test_it_learns_which_cue_actually_separates_the_classes(self):
        # `at_mouth` is present on every true violation and on none of the
        # false positives; `time_band` is noise present on both.
        for _ in range(20):
            self._alert({"bottle", "at_mouth", "gathering_duration", "time_band"}, True)
        for _ in range(20):
            self._alert({"bottle", "gathering_duration", "time_band"}, False)

        out = self._run("--json")
        import json
        fitted = json.loads(out)
        self.assertGreater(fitted["at_mouth"], fitted["time_band"],
                           "the fit did not find the separating cue")
        self.assertGreater(fitted["at_mouth"], 0.0)

    def test_it_reports_precision_and_recall_before_and_after(self):
        for _ in range(15):
            self._alert({"bottle", "at_mouth", "gathering_duration", "time_band"}, True)
        for _ in range(15):
            self._alert({"bottle", "time_band"}, False)
        out = self._run()
        self.assertIn("precision", out)
        self.assertIn("current", out)
        self.assertIn("fitted", out)
        # It must say out loud that the figures are optimistic.
        self.assertIn("SAME data", out)

    def test_it_never_writes_the_weights_file(self):
        for _ in range(15):
            self._alert({"bottle", "at_mouth", "gathering"}, True)
        for _ in range(15):
            self._alert({"bottle"}, False)
        before = dict(scoring.DRINKING_WEIGHTS)
        self._run()
        self.assertEqual(scoring.DRINKING_WEIGHTS, before)

    def test_unlabelled_alerts_are_skipped_not_guessed(self):
        for _ in range(15):
            self._alert({"bottle", "at_mouth", "gathering"}, True)
        for _ in range(15):
            self._alert({"bottle"}, False)
        for _ in range(7):
            self._alert({"bottle", "gathering"}, None)
        out = self._run()
        self.assertIn("Skipped 7", out)


class ReviewLabelApiTests(TestCase):
    """The review control's contract: the LABEL is writable, the EVIDENCE is not.

    An alert whose own cue vector could be edited after the fact is no longer
    training data -- a client that could rewrite `cues` could rewrite the
    answer the calibration fits against.
    """

    def setUp(self):
        from core.models import User

        self.vtype = ViolationType.objects.create(code="smoking", label="Smoking")
        self.camera = Camera.objects.create(code="CAM-R1", name="Test")
        self.alert = Alert.objects.create(
            type=self.vtype, camera=self.camera,
            timestamp="2026-05-01T12:00:00Z", confidence=0.8,
            level="violation",
            cues={"kind": "smoking", "cues": {"cigarette": 0.30}},
        )
        self.user = User.objects.create_user(
            username="reviewer", password="x", role="admin", is_staff=True)
        self.client.force_login(self.user)

    def _patch(self, payload):
        return self.client.patch(
            f"/api/alerts/{self.alert.id}/", payload,
            content_type="application/json",
        )

    def test_a_reviewer_can_mark_an_alert_real(self):
        response = self._patch('{"reviewed_valid": true}')
        self.assertEqual(response.status_code, 200, response.content)
        self.alert.refresh_from_db()
        self.assertIs(self.alert.reviewed_valid, True)

    def test_a_reviewer_can_mark_an_alert_a_false_alarm(self):
        self._patch('{"reviewed_valid": false}')
        self.alert.refresh_from_db()
        self.assertIs(self.alert.reviewed_valid, False)

    def test_a_review_can_be_cleared(self):
        """A mis-click must be undoable, or the training data inherits it."""
        self._patch('{"reviewed_valid": true}')
        self._patch('{"reviewed_valid": null}')
        self.alert.refresh_from_db()
        self.assertIsNone(self.alert.reviewed_valid)

    def test_the_cue_vector_cannot_be_rewritten_by_a_client(self):
        self._patch('{"cues": {"kind": "smoking", "cues": {"cigarette": 9.9}}}')
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.cues["cues"]["cigarette"], 0.30)

    def test_the_score_band_cannot_be_rewritten_by_a_client(self):
        self._patch('{"level": "none"}')
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.level, "violation")

    def test_the_score_band_is_returned_for_display(self):
        response = self.client.get(f"/api/alerts/{self.alert.id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["level"], "violation")
        self.assertIsNone(body["reviewed_valid"])


class ReviewerAuditTests(TestCase):
    """WHO reviewed the footage, recorded from the request and not the payload.

    The reviewer is not something a user declares separately -- it is whoever
    dismissed the alert or marked it resolved. Those actions already exist and
    are what actually constitutes reviewing the footage, so the audit trail
    falls out of the normal workflow instead of being an extra step.
    """

    def setUp(self):
        from core.models import User

        self.vtype = ViolationType.objects.create(code="theft", label="Theft")
        self.camera = Camera.objects.create(code="CAM-A1", name="Test")
        self.alert = Alert.objects.create(
            type=self.vtype, camera=self.camera,
            timestamp="2026-05-01T12:00:00Z", confidence=0.8,
            status=Alert.Status.DISPATCHED,
        )
        self.reviewer = User.objects.create_user(
            username="tanod1", password="x", role="officer",
            display_name="PO1 Santos, R.")
        self.other = User.objects.create_user(
            username="tanod2", password="x", role="officer",
            display_name="PO2 Cruz, M.")

    def _patch(self, payload, as_user=None):
        self.client.force_login(as_user or self.reviewer)
        return self.client.patch(
            f"/api/alerts/{self.alert.id}/", payload,
            content_type="application/json",
        )

    def test_marking_an_alert_resolved_records_the_reviewer(self):
        self._patch('{"status": "resolved"}')
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.reviewed_by, self.reviewer)
        self.assertIsNotNone(self.alert.reviewed_at)

    def test_dismissing_an_alert_records_the_reviewer(self):
        """A dismissal is a decision not to act on a reported violation, so it
        needs a name on it just as much as a resolution does."""
        self._patch('{"status": "acknowledged"}')
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.reviewed_by, self.reviewer)

    def test_the_name_is_returned_for_the_records_page(self):
        response = self._patch('{"status": "resolved"}')
        self.assertEqual(response.json()["reviewed_by_name"], "PO1 Santos, R.")
        self.assertIsNotNone(response.json()["reviewed_at"])

    def test_a_client_cannot_name_someone_else_as_the_reviewer(self):
        """The whole point of stamping it server-side."""
        self._patch('{"status": "resolved", "reviewed_by": %d}' % self.other.id)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.reviewed_by, self.reviewer)

    def test_a_client_cannot_backdate_the_review(self):
        self._patch('{"status": "resolved", "reviewed_at": "2020-01-01T00:00:00Z"}')
        self.alert.refresh_from_db()
        self.assertGreater(self.alert.reviewed_at.year, 2020)

    def test_an_unrelated_patch_does_not_restamp_the_reviewer(self):
        """Editing notes or reassigning officers must not silently
        reattribute somebody else's judgement."""
        self._patch('{"status": "resolved"}')
        self.alert.refresh_from_db()
        stamped_at = self.alert.reviewed_at

        self._patch('{"notes": "Filed a citation."}', as_user=self.other)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.reviewed_by, self.reviewer)
        self.assertEqual(self.alert.reviewed_at, stamped_at)

    def test_reopening_an_alert_clears_the_reviewer(self):
        """Nobody has closed it any more, so no name should sit on it."""
        self._patch('{"status": "resolved"}')
        self._patch('{"status": "active"}')
        self.alert.refresh_from_db()
        self.assertIsNone(self.alert.reviewed_by)
        self.assertIsNone(self.alert.reviewed_at)

    def test_whoever_closes_it_last_is_the_recorded_reviewer(self):
        self._patch('{"status": "acknowledged"}')
        self._patch('{"status": "active"}')
        self._patch('{"status": "resolved"}', as_user=self.other)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.reviewed_by, self.other)

    def test_the_record_survives_the_reviewer_being_deleted(self):
        """SET_NULL: the alert keeps its status and loses only the name."""
        self._patch('{"status": "resolved"}')
        self.reviewer.delete()
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.RESOLVED)
        self.assertIsNone(self.alert.reviewed_by)
