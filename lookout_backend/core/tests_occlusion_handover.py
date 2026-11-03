"""Occlusion and tracker-ID churn: a parked vehicle keeps its banked seconds.

The failure these close: a vehicle being hidden behind a passing one has its
box CLIPPED by the detector on the way out — it sees only the part still
showing. _absorb_lost matched the returning vehicle against that sliver, so the
overlap test failed on exactly the occlusions it exists to survive, and a car
parked for half a minute restarted from zero under its new tracker id.
"""

from django.test import TestCase

from core.vision.obstruction_zone import ObstructionZone, VehicleState, _iou

FRAME = (720, 1280, 3)
# The whole lower half of the frame, so every box below is comfortably inside.
ZONE = [[0.0, 0.3], [1.0, 0.3], [1.0, 1.0], [0.0, 1.0]]


def box(x, y, w=120, h=90):
    return (float(x), float(y), float(x + w), float(y + h))


class CleanBoxTests(TestCase):
    def test_a_fresh_state_has_no_clean_box_yet(self):
        self.assertIsNone(VehicleState(1, 0.0).clean_box)

    def test_it_keeps_the_largest_stationary_box(self):
        """Occlusion only ever subtracts from a parked vehicle's box — it is
        not moving, so a bigger box is strictly a better look at it."""
        st = VehicleState(1, 0.0)
        st.remember_clean(box(100, 400, 120, 90))
        st.remember_clean(box(100, 400, 40, 90))     # half hidden
        self.assertEqual(st.clean_box, box(100, 400, 120, 90))

    def test_match_box_prefers_the_clean_one(self):
        st = VehicleState(1, 0.0)
        st.box = box(100, 400, 30, 90)               # clipped, as it vanished
        st.remember_clean(box(100, 400, 120, 90))
        self.assertEqual(st.match_box, box(100, 400, 120, 90))

    def test_match_box_falls_back_to_the_last_box(self):
        """A vehicle that never held still long enough to record a clean box
        still has to be comparable."""
        st = VehicleState(1, 0.0)
        st.box = box(100, 400)
        self.assertEqual(st.match_box, box(100, 400))


class OcclusionHandoverTests(TestCase):
    """End to end through ObstructionZone.update."""

    def _zone(self):
        return ObstructionZone(ZONE, alert_score=120.0, moving_weight=0.0,
                               enter_fraction=0.2)

    def _park(self, zone, track_id, where, start=0.0, seconds=20.0, step=0.5):
        """Hold a vehicle still long enough to bank `seconds`."""
        t = start
        while t <= start + seconds:
            zone.update([(track_id, where, "car", 0.9)], t, FRAME)
            t += step
        return t

    def test_a_parked_vehicle_banks_seconds(self):
        zone = self._zone()
        self._park(zone, 1, box(300, 500))
        self.assertGreater(zone.states[1].score, 15.0)

    def test_the_clipped_last_box_no_longer_breaks_the_handover(self):
        """The regression this is all for.

        A car parks and banks seconds. A passer hides most of it, so its last
        box is a sliver. The track dies; it comes back under a new id at its
        real size. Matching the full returning box against the sliver gives an
        IoU far under the 0.55 bar — against the remembered clean box it
        passes, and the seconds transfer.
        """
        zone = self._zone()
        parked = box(300, 500, 120, 90)
        t = self._park(zone, 1, parked)

        banked = zone.states[1].score
        self.assertGreater(banked, 15.0)

        # Being swallowed by a passing vehicle: the detector reports only the
        # uncovered strip, and that becomes `box`.
        sliver = box(300, 500, 25, 90)
        zone.update([(1, sliver, "car", 0.9)], t, FRAME)
        self.assertLess(_iou(parked, sliver), 0.55,
                        "the sliver must be too poor a match on its own")

        # Gone for a few frames, then back at full size under a NEW id.
        t += 1.0
        for _ in range(8):
            zone.update([(2, parked, "car", 0.9)], t, FRAME)
            t += 0.5

        self.assertTrue(zone.states[2].adopted, "the seconds should transfer")
        self.assertGreaterEqual(zone.states[2].score, banked - 5.0)

    def test_a_passing_vehicle_still_cannot_steal_the_seconds(self):
        """The property the 0.55 bar exists to protect, and which the clean
        box must not weaken: a vehicle merely driving past a parked one never
        qualifies, because it is never stationary."""
        zone = self._zone()
        t = self._park(zone, 1, box(300, 500))
        banked = zone.states[1].score

        # #1 stops being reported; #2 drives through the same place.
        for x in range(200, 460, 40):
            zone.update([(2, box(x, 500), "car", 0.9)], t, FRAME)
            t += 0.3

        self.assertFalse(zone.states[2].adopted)
        self.assertLess(zone.states[2].score, banked / 2)

    def test_a_vehicle_parking_somewhere_else_does_not_inherit(self):
        zone = self._zone()
        t = self._park(zone, 1, box(300, 500))
        for _ in range(8):
            zone.update([(2, box(900, 500), "car", 0.9)], t, FRAME)
            t += 0.5
        self.assertFalse(zone.states[2].adopted)

    def test_the_clean_box_only_records_while_stationary(self):
        """A moving vehicle's box is not a record of where anything parked."""
        zone = self._zone()
        t = 0.0
        for x in range(100, 700, 60):
            zone.update([(1, box(x, 500), "car", 0.9)], t, FRAME)
            t += 0.3
        self.assertIsNone(zone.states[1].clean_box)


class StableIdentityTests(TestCase):
    """What the operator SEES called a vehicle.

    The seconds carrying over is not enough on its own: if the label still
    renumbers, one parked motorcycle reads as a succession of different ones
    each apparently starting from zero, which looks exactly like the timer
    being lost even when nothing was.
    """

    def _zone(self):
        return ObstructionZone(ZONE, alert_score=120.0, moving_weight=0.0,
                               enter_fraction=0.2)

    def test_a_fresh_track_shows_its_own_id(self):
        st = VehicleState(7, 0.0)
        self.assertEqual(st.display_id, 7)

    def test_the_name_is_inherited_with_the_seconds(self):
        zone = self._zone()
        parked = box(300, 500, 120, 90)
        t = 0.0
        while t <= 20.0:
            zone.update([(1, parked, "car", 0.9)], t, FRAME)
            t += 0.5
        zone.update([(1, box(300, 500, 25, 90), "car", 0.9)], t, FRAME)
        t += 1.0
        for _ in range(8):
            zone.update([(2, parked, "car", 0.9)], t, FRAME)
            t += 0.5

        state = zone.states[2]
        self.assertTrue(state.adopted)
        self.assertEqual(state.track_id, 2, "association still uses the real id")
        self.assertEqual(state.display_id, 1, "but the operator sees one vehicle")

    def test_the_handover_is_reported(self):
        """So "the timer restarted" and "it was renumbered and carried over"
        stop looking identical on screen."""
        zone = self._zone()
        parked = box(300, 500, 120, 90)
        t = 0.0
        while t <= 20.0:
            zone.update([(1, parked, "car", 0.9)], t, FRAME)
            t += 0.5
        t += 1.0
        for _ in range(8):
            zone.update([(2, parked, "car", 0.9)], t, FRAME)
            t += 0.5
        state = zone.states[2]
        self.assertEqual(state.inherited_from, 1)
        self.assertGreater(state.inherited_seconds, 15.0)

    def test_a_vehicle_that_inherited_nothing_reports_nothing(self):
        zone = self._zone()
        for i in range(8):
            zone.update([(3, box(300, 500), "car", 0.9)], i * 0.5, FRAME)
        self.assertIsNone(zone.states[3].inherited_from)

    def test_the_live_view_is_keyed_on_the_stable_id(self):
        """Keyed as well as labelled, or the view would hold both the dead id
        and the live one as two subjects."""
        import inspect
        from core.management.commands import watch_parking
        source = inspect.getsource(watch_parking.Command._publish_subjects)
        self.assertIn('key=("parking", state.display_id)', source)
        self.assertIn("ident=state.display_id", source)
        self.assertNotIn("ident=state.track_id", source)


class TrackerConfigTests(TestCase):
    """Fewer handovers to recover from in the first place."""

    def _config(self):
        import yaml
        from django.conf import settings
        path = (settings.BASE_DIR / "core" / "vision" / "trackers"
                / "bytetrack.yaml")
        self.assertTrue(path.is_file(), "parking ships its own tracker config")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_lost_tracks_are_held_far_longer_than_stock(self):
        """Stock is 30 frames, and at 1440p far-mode rates that is only a few
        seconds of footage — less than one vehicle takes to drive past."""
        self.assertGreaterEqual(self._config()["track_buffer"], 90)

    def test_the_rest_matches_stock(self):
        """Only the buffer is tuned. Changing the match thresholds as well
        would make any regression impossible to attribute."""
        config = self._config()
        self.assertEqual(config["tracker_type"], "bytetrack")
        self.assertEqual(config["match_thresh"], 0.8)
        self.assertEqual(config["track_high_thresh"], 0.25)

    def test_parking_prefers_it_but_falls_back(self):
        """Falls back to ultralytics' stock file by bare name, so a checkout
        without the config still runs."""
        import inspect
        from core.management.commands import watch_parking
        # The whole module: `handle` is a thin dispatcher to _handle/_handle_timed,
        # so the setup this asserts on does not live in the method of that name.
        source = inspect.getsource(watch_parking)
        self.assertIn("local_tracker", source)
        self.assertIn("local_tracker.is_file()", source)
