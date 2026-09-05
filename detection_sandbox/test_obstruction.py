"""Tests for the automatic edge-line detector and the obstruction rule.

Run:  python test_obstruction.py

Two halves:

  * DETECTION is tested against a synthetic street (a real painted stripe, in
    perspective, on noisy asphalt) and against this project's OWN sample photos,
    which have no road markings at all. Finding the line in the first and
    correctly finding nothing in the second are equally important - a detector
    that hallucinates an edge on unmarked concrete is worse than useless,
    because every vehicle would then be measured against a line that is really
    a roofline.

  * THE RULE is tested on a simulated clock, because every part of it is
    temporal. Synthetic boxes make the awkward cases - jitter at the threshold,
    a jeepney occluding a parked car - reproducible in a way footage never is.
"""

import glob
import os
import unittest

import cv2
import numpy as np

import edge_line as el
import obstruction as ob

W, H = 640, 480


def street(line_y_left=300, line_y_right=250, paint=True, seed=0):
    """A synthetic street: dark asphalt below, kerb-side surface above, and a
    white stripe between them running in perspective across the frame."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W, 3), 70, np.uint8)                    # asphalt
    for x in range(W):
        y = int(line_y_left + (line_y_right - line_y_left) * x / W)
        img[:y, x] = (110, 112, 115)                          # lighter shoulder
    img = (img + rng.normal(0, 6, img.shape)).clip(0, 255).astype(np.uint8)
    if paint:
        pts = [(x, int(line_y_left + (line_y_right - line_y_left) * x / W))
               for x in range(0, W, 4)]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            cv2.line(img, (x0, y0), (x1, y1), (238, 240, 242), el.LINE_WIDTH_PX)
    return img


def line_at(y_left=300, y_right=250, protected=el.ABOVE):
    return el.EdgeLine((0, y_left), (W, y_right), protected_side=protected)


class DetectionTests(unittest.TestCase):

    def test_finds_a_real_painted_line(self):
        finder = el.EdgeFinder(warmup=12)
        for i in range(12):
            finder.update(street(seed=i))
        self.assertTrue(finder.locked, "should lock onto a clear painted stripe")
        # The fit must sit on the stripe, not merely somewhere in the frame.
        for x, expect in ((0, 300), (W - 1, 250)):
            y = -(finder.line.a * x + finder.line.c) / finder.line.b
            self.assertLess(abs(y - expect), 12, f"line off by {abs(y-expect):.0f}px at x={x}")

    def test_rejects_a_street_with_no_markings(self):
        finder = el.EdgeFinder(warmup=12)
        for i in range(12):
            finder.update(street(paint=False, seed=i))
        self.assertFalse(finder.locked,
                         "must NOT invent a line on an unmarked road")

    def test_rejects_the_projects_own_unmarked_sample_photos(self):
        """The real barangay photos in sample_images/ have no road markings.

        This is the regression that matters most: a naive HSV threshold found
        26-51 'lines' in these, all of them roofline and sky.
        """
        paths = sorted(glob.glob(os.path.join("sample_images", "*.jpg")))[:6]
        if not paths:
            self.skipTest("no sample_images/ present")
        hallucinated = []
        for p in paths:
            img = cv2.imread(p)
            img = cv2.resize(img, (img.shape[1] // 4, img.shape[0] // 4))
            finder = el.EdgeFinder(warmup=6)
            for _ in range(6):
                finder.update(img)
            if finder.locked:
                hallucinated.append(os.path.basename(p))
        self.assertEqual(hallucinated, [],
                         f"invented an edge line on unmarked roads: {hallucinated}")

    def test_a_white_vehicle_does_not_become_the_line(self):
        """A white van sitting in frame is bright and boxy; persistence plus the
        top-hat must keep it from being fitted as paint."""
        finder = el.EdgeFinder(warmup=12)
        for i in range(12):
            img = street(paint=False, seed=i)
            cv2.rectangle(img, (200, 330), (380, 430), (245, 245, 245), -1)
            finder.update(img)
        self.assertFalse(finder.locked, "a white vehicle is not a painted line")

    def test_side_is_decided_by_where_vehicles_drive(self):
        finder = el.EdgeFinder(warmup=8)
        # Vehicles driving BELOW the stripe make below the road, above the way.
        finder.observe_traffic([(x, 420) for x in range(50, 600, 60)])
        for i in range(8):
            finder.update(street(seed=i))
        self.assertTrue(finder.locked)
        self.assertEqual(finder.line.protected_side, el.ABOVE)


class FractionTests(unittest.TestCase):

    def test_fraction_is_zero_on_the_road_side(self):
        line = line_at(300, 300)
        self.assertEqual(ob.fraction_past(line, (100, 320, 200, 400)), 0.0)

    def test_fraction_is_one_when_fully_past(self):
        line = line_at(300, 300)
        self.assertEqual(ob.fraction_past(line, (100, 180, 200, 260)), 1.0)

    def test_fraction_is_half_when_straddling(self):
        """Footprint is the bottom edge, so 'half over' means half that edge is
        past the line - here a slanted line cutting the box's base in two."""
        line = el.EdgeLine((0, 0), (W, 2 * H), protected_side=el.ABOVE)
        frac = ob.fraction_past(line, (150, 100, 250, 300))
        self.assertGreater(frac, 0.3)
        self.assertLess(frac, 0.7)


class RuleTests(unittest.TestCase):
    """The 50%-for-five-minutes rule, driven on a simulated clock."""

    def setUp(self):
        self.line = line_at(300, 300, protected=el.ABOVE)
        self.mon = ob.ObstructionMonitor(self.line)
        self.t = 1000.0

    def run_for(self, seconds, box_fn, step=1.0):
        last = None
        end = self.t + seconds
        while self.t < end:
            self.t += step
            box = box_fn(self.t)
            if box is not None:
                last = self.mon.update([box], self.t)[0]
        return last

    def test_a_car_driving_past_never_alerts(self):
        """Fully over the line, but moving - the 2-5 second case."""
        def moving(t):
            x = int((t - 1000) * 60)
            return (x, 200, x + 100, 280)
        state, verdict = self.run_for(5, moving, step=0.2)
        self.assertNotEqual(verdict, ob.OBSTRUCTION)
        self.assertEqual(state.held, 0.0, "a moving vehicle must accrue nothing")

    def test_parked_over_the_line_for_five_minutes_alerts(self):
        state, verdict = self.run_for(360, lambda t: (200, 200, 300, 280))
        self.assertEqual(verdict, ob.OBSTRUCTION)
        self.assertGreaterEqual(state.held, ob.OBSTRUCTION_SECONDS)

    def test_parked_but_only_a_third_over_never_alerts(self):
        """Below the 50% rule: parked all day, still not an obstruction."""
        # Line crosses the vehicle's bottom edge a third of the way along, so
        # exactly a third of the footprint is on the protected side.
        line = el.EdgeLine((233, 280), (633, 180), protected_side=el.ABOVE)
        mon = ob.ObstructionMonitor(line)
        t = 1000.0
        for _ in range(400):
            t += 1.0
            state, verdict = mon.update([(300, 100, 400, 300)], t)[0]
        self.assertLess(state.fraction, ob.ENTER_FRACTION)
        self.assertEqual(verdict, ob.CLEAR)

    def test_jitter_at_the_threshold_does_not_reset_the_timer(self):
        """The box wobbles either side of 50%; hysteresis must hold the state."""
        def jitter(t):
            dy = 6 if int(t) % 2 else -6
            return (200, 200 + dy, 300, 280 + dy)
        state, verdict = self.run_for(360, jitter)
        self.assertEqual(verdict, ob.OBSTRUCTION,
                         "jitter around the threshold broke the accrual")

    def test_a_brief_occlusion_does_not_reset_the_timer(self):
        """A jeepney hides the parked car for 6s - shorter than the grace."""
        box = (200, 200, 300, 280)
        self.run_for(200, lambda t: box)
        held_before = self.mon.states[0].held
        self.t += 6.0                                   # nothing reported at all
        state, verdict = self.run_for(160, lambda t: box)
        self.assertGreater(state.held, held_before,
                           "dwell was lost across a short occlusion")
        self.assertEqual(verdict, ob.OBSTRUCTION)

    def test_a_long_absence_starts_a_new_parking_event(self):
        box = (200, 200, 300, 280)
        self.run_for(200, lambda t: box)
        self.t += ob.OCCLUSION_GRACE_SECONDS + 5        # the car left
        state, verdict = self.run_for(30, lambda t: box)
        self.assertLess(state.held, 60, "a returning vehicle kept its old dwell")
        self.assertNotEqual(verdict, ob.OBSTRUCTION)

    def test_vehicle_that_shuffles_forward_restarts_its_stay(self):
        self.run_for(200, lambda t: (200, 200, 300, 280))
        state, _ = self.run_for(20, lambda t: (260, 200, 360, 280))
        self.assertLess(state.held, 60, "movement must restart the accrual")

    def test_occluded_time_is_not_credited(self):
        """Dwell survives a gap but must not GROW through it: five minutes has
        to mean five OBSERVED minutes, or an alert could be built from a car
        that was hidden for most of them."""
        box = (200, 200, 300, 280)
        self.run_for(100, lambda t: box)
        held = self.mon.states[0].held
        self.t += 10.0
        state, _ = self.mon.update([box], self.t)[0]
        self.assertLess(state.held - held, 10.0)


class DuplicateAlertTests(unittest.TestCase):
    """One parked vehicle must produce exactly ONE obstruction report."""

    def setUp(self):
        self.line = line_at(300, 300, protected=el.ABOVE)
        self.mon = ob.ObstructionMonitor(self.line)
        self.box = (200, 200, 300, 280)

    def run_for(self, seconds, t, step=1.0, box=None):
        fresh = 0
        end = t + seconds
        while t < end:
            t += step
            state, _ = self.mon.update([box or self.box], t)[0]
            fresh += 1 if state.fresh_alert else 0
        return fresh, t

    def test_a_single_stay_alerts_exactly_once(self):
        """It stays OBSTRUCTION every frame, but fires fresh_alert only once."""
        fresh, _ = self.run_for(600, 1000.0)
        self.assertEqual(fresh, 1)

    def test_a_lost_and_reacquired_track_does_not_alert_again(self):
        """The gap kills the track, so it returns with a new id and a cleared
        flag - only the spatial cooldown can catch that it is the same car."""
        fresh1, t = self.run_for(400, 1000.0)
        self.assertEqual(fresh1, 1)
        t += ob.OCCLUSION_GRACE_SECONDS + 30       # track dies here
        fresh2, _ = self.run_for(400, t)
        self.assertEqual(fresh2, 0, "same parked vehicle was reported twice")

    def test_a_different_spot_is_still_reported(self):
        """Suppression must be local: another vehicle elsewhere still alerts."""
        self.run_for(400, 1000.0)
        far = (700, 200, 800, 280)
        fresh, _ = self.run_for(400, 1500.0, box=far)
        self.assertEqual(fresh, 1)

    def test_the_same_spot_alerts_again_after_the_cooldown(self):
        """A genuinely new vehicle hours later is a new violation, not a repeat."""
        mon = ob.ObstructionMonitor(self.line, cooldown=60.0)
        t = 1000.0
        for _ in range(400):
            t += 1.0
            mon.update([self.box], t)
        t += 200.0                                  # well past the 60s cooldown
        seen = 0
        for _ in range(400):
            t += 1.0
            state, _ = mon.update([self.box], t)[0]
            seen += 1 if state.fresh_alert else 0
        self.assertEqual(seen, 1)


class GuardTests(unittest.TestCase):
    """The abstain guards: truncation, traffic jams, and mask footprints."""

    def setUp(self):
        self.line = line_at(300, 300, protected=el.ABOVE)
        self.mon = ob.ObstructionMonitor(self.line)

    def test_a_box_touching_the_frame_edge_is_flagged_truncated(self):
        self.assertTrue(ob.is_truncated((0, 200, 100, 280), (480, 640, 3)))
        self.assertTrue(ob.is_truncated((500, 200, 640, 280), (480, 640, 3)))
        self.assertFalse(ob.is_truncated((200, 200, 300, 280), (480, 640, 3)))

    def test_a_truncated_vehicle_holds_its_timer_instead_of_accruing(self):
        """Half out of frame means the footprint is wrong, so the system must
        neither accrue on it nor throw away what it already had."""
        box = (200, 200, 300, 280)
        t = 1000.0
        for _ in range(120):
            t += 1.0
            self.mon.update([box], t, frame_shape=(480, 640, 3))
        held = self.mon.states[0].held
        self.assertGreater(held, 60)
        # Same box, so it stays the SAME track - but the frame is now only just
        # taller than the box, so the vehicle is clipped by the bottom edge.
        for _ in range(60):
            t += 1.0
            state, _ = self.mon.update([box], t, frame_shape=(282, 640, 3))[0]
        self.assertTrue(state.truncated)
        self.assertAlmostEqual(state.held, held, delta=3.0,
                               msg="truncated frames must not accrue or reset")

    def test_a_traffic_jam_suppresses_instead_of_flagging_everyone(self):
        """Many stationary vehicles at once is congestion, not mass parking."""
        jam = [(50 + i * 90, 200, 130 + i * 90, 280) for i in range(6)]
        t = 1000.0
        for _ in range(400):
            t += 1.0
            results = self.mon.update(jam, t)
        self.assertTrue(all(v != ob.OBSTRUCTION for _, v in results),
                        "every vehicle in a traffic queue was flagged")

    def test_a_few_parked_vehicles_are_not_treated_as_a_jam(self):
        few = [(50, 200, 130, 280), (300, 200, 380, 280)]
        t = 1000.0
        for _ in range(400):
            t += 1.0
            results = self.mon.update(few, t)
        self.assertTrue(all(v == ob.OBSTRUCTION for _, v in results))

    def test_a_mask_footprint_beats_the_box_when_they_disagree(self):
        """The box is wider than the vehicle; the mask is the real contact."""
        # Box spans x 200-400, but the vehicle really occupies only x 320-400,
        # entirely past a vertical line at x=300. The box says "about half
        # over"; the mask says "completely over", which is the truth.
        box = (200, 200, 400, 300)
        mask = np.zeros((480, 640), np.uint8)
        mask[280:300, 320:400] = 1
        line = el.EdgeLine((300, 0), (300, 480), protected_side=el.ABOVE)
        by_box = ob.fraction_past(line, box)
        by_mask = ob.fraction_past(line, box, mask=mask)
        self.assertAlmostEqual(by_box, 0.48, delta=0.06)
        self.assertAlmostEqual(by_mask, 1.0, delta=0.01)

    def test_pedestrians_forced_onto_the_road_are_counted(self):
        box = (200, 200, 300, 280)
        on_road = [(240, 320, 270, 400)]      # feet below the line = on the road
        on_path = [(240, 120, 270, 190)]      # feet above the line = footpath
        t = 1000.0
        for _ in range(20):
            t += 1.0
            state, _ = self.mon.update([box], t, pedestrians=on_road)[0]
        self.assertGreaterEqual(state.detours, 1)
        mon2 = ob.ObstructionMonitor(self.line)
        t = 1000.0
        for _ in range(20):
            t += 1.0
            s2, _ = mon2.update([box], t, pedestrians=on_path)[0]
        self.assertEqual(s2.detours, 0)


class PolyEdgeTests(unittest.TestCase):
    """A bending footpath needs more than one straight line."""

    def test_a_polyedge_matches_an_edgeline_on_a_straight_path(self):
        line = el.EdgeLine((0, 300), (600, 300), protected_side=el.ABOVE)
        poly = el.PolyEdge([(0, 300), (300, 300), (600, 300)],
                           protected_side=el.ABOVE)
        for p in [(100, 100), (100, 500), (400, 250), (400, 350)]:
            self.assertEqual(line.is_protected(p), poly.is_protected(p),
                             f"disagreed at {p}")

    def test_a_polyedge_follows_a_bend_a_straight_line_cuts(self):
        bend = el.PolyEdge([(0, 300), (400, 300), (600, 500)],
                           protected_side=el.ABOVE)
        straight = el.EdgeLine((0, 300), (600, 500), protected_side=el.ABOVE)
        # At x=200 the bend is at y=300 but the straight fit has already sagged
        # to y~367, so a point at y=330 lies on opposite sides of the two.
        inside = (200, 330)
        self.assertFalse(bend.is_protected(inside))
        self.assertNotEqual(straight.is_protected(inside),
                            bend.is_protected(inside),
                            "the straight fit should cut this corner")

    def test_the_rule_accepts_a_polyedge(self):
        poly = el.PolyEdge([(0, 300), (300, 300), (600, 300)],
                           protected_side=el.ABOVE)
        mon = ob.ObstructionMonitor(poly)
        t = 1000.0
        for _ in range(400):
            t += 1.0
            state, verdict = mon.update([(200, 200, 300, 280)], t)[0]
        self.assertEqual(verdict, ob.OBSTRUCTION)


class TwoLineTests(unittest.TestCase):
    """A street has an edge on each side, and each is judged on its own.

    The web tool runs one monitor per line over the same box list. What matters
    is that they stay INDEPENDENT: a vehicle on the left verge must not raise a
    verdict on the right line just because it is over the left one.
    """

    def setUp(self):
        # Road runs between the two lines; each protects the side facing away.
        self.left = el.EdgeLine((0, 200), (W, 200), protected_side=el.ABOVE)
        self.right = el.EdgeLine((0, 400), (W, 400), protected_side=el.BELOW)
        self.mons = {"left": ob.ObstructionMonitor(self.left),
                     "right": ob.ObstructionMonitor(self.right)}

    def drive(self, box, seconds=360, step=1.0):
        t, out = 1000.0, {}
        end = t + seconds
        while t < end:
            t += step
            for name, mon in self.mons.items():
                out[name] = mon.update([box], t)[0]
        return out

    def test_a_vehicle_on_the_left_verge_only_trips_the_left_line(self):
        out = self.drive((200, 100, 300, 180))       # footprint above the left line
        self.assertEqual(out["left"][1], ob.OBSTRUCTION)
        self.assertEqual(out["right"][1], ob.CLEAR)

    def test_a_vehicle_on_the_right_verge_only_trips_the_right_line(self):
        out = self.drive((200, 420, 300, 470))       # footprint below the right line
        self.assertEqual(out["right"][1], ob.OBSTRUCTION)
        self.assertEqual(out["left"][1], ob.CLEAR)

    def test_a_vehicle_on_the_carriageway_trips_neither(self):
        out = self.drive((200, 250, 300, 350))       # parked between the lines
        self.assertEqual(out["left"][1], ob.CLEAR)
        self.assertEqual(out["right"][1], ob.CLEAR)

    def test_the_two_lines_keep_separate_timers(self):
        """Independent accrual: the left line counts while the right stays at zero."""
        self.drive((200, 100, 300, 180), seconds=120)
        self.assertGreater(self.mons["left"].states[0].held, 60)
        self.assertEqual(self.mons["right"].states[0].held, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
