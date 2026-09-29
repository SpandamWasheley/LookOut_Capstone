"""Tests for the parking-obstruction area shapes.

The point of these is the SHARED INTERFACE. An edge and a zone answer the same
question - "is this ground point somewhere a vehicle must not be?" - so
fraction_past(), the hysteresis band and the five-minute dwell must all work on
either without branching. A test that only exercised the polygon maths would
miss the thing most likely to break: a new shape that does not drop cleanly
into the rule that already exists.

Pure geometry, no Django and no YOLO, same rule as obstruction.py itself.
"""

import unittest

from core.vision import obstruction as obs


# A camera looks DOWN a road, so the carriageway appears as a trapezium that
# narrows towards the vanishing point: wide at y=400 (near), narrow at y=200
# (far). Using a trapezium rather than a rectangle throughout is deliberate -
# it is the shape a real road actually projects to, and a rectangle would hide
# exactly the mistakes this module exists to avoid.
ROAD = [[100, 400], [500, 400], [420, 200], [180, 200]]


def zone(points=None):
    return obs.build_edge({"type": "zone", "points": points or ROAD})


class BuildEdgeDispatchTests(unittest.TestCase):
    def test_a_zone_spec_builds_a_zone(self):
        self.assertIsInstance(zone(), obs.RoadZone)

    def test_a_spec_with_no_type_is_still_an_edge(self):
        """Backward compatibility: every camera configured before zones
        existed stores exactly this, and must keep working untouched."""
        two = obs.build_edge({"points": [[0, 300], [600, 300]], "side": 1})
        self.assertIsInstance(two, obs.EdgeLine)
        bent = obs.build_edge({"points": [[0, 300], [300, 280], [600, 320]], "side": 1})
        self.assertIsInstance(bent, obs.PolyEdge)

    def test_an_explicit_edge_type_behaves_identically_to_omitting_it(self):
        spec = {"points": [[0, 300], [600, 300]], "side": 1}
        implicit = obs.build_edge(dict(spec))
        explicit = obs.build_edge({**spec, "type": obs.EDGE})
        probe = (300, 100)
        self.assertEqual(implicit.is_protected(probe), explicit.is_protected(probe))

    def test_a_zone_needs_three_points(self):
        with self.assertRaises(ValueError):
            obs.build_edge({"type": "zone", "points": [[0, 0], [10, 10]]})

    def test_an_edge_needs_two_points(self):
        with self.assertRaises(ValueError):
            obs.build_edge({"points": [[0, 0]]})


class RoadZoneGeometryTests(unittest.TestCase):
    def test_inside_and_outside(self):
        z = zone()
        self.assertTrue(z.contains((300, 300)))     # middle of the road
        self.assertFalse(z.contains((50, 300)))     # footpath, camera-left
        self.assertFalse(z.contains((550, 300)))    # footpath, camera-right
        self.assertFalse(z.contains((300, 100)))    # past the far end

    def test_is_protected_is_the_shared_spelling_of_contains(self):
        z = zone()
        for point in [(300, 300), (50, 300), (300, 100), (110, 399)]:
            self.assertEqual(z.is_protected(point), z.contains(point), point)

    def test_signed_distance_is_positive_inside(self):
        """Sign matches the edge shapes' convention, so anything reading it
        generically behaves the same for both."""
        z = zone()
        self.assertGreater(z.signed_distance((300, 300)), 0)
        self.assertLess(z.signed_distance((50, 300)), 0)

    def test_the_boundary_counts_as_inside(self):
        self.assertTrue(zone().contains((100, 400)))

    def test_it_narrows_with_the_road(self):
        """The reason a polygon beats a bounding box. A point 60px right of
        centre is on the road near the camera and on the footpath in the
        distance - an axis-aligned box could not tell those apart."""
        z = zone()
        self.assertTrue(z.contains((460, 390)))    # near, still carriageway
        self.assertFalse(z.contains((460, 210)))   # far, beyond the kerb


class FractionPastTests(unittest.TestCase):
    """fraction_past() measures the vehicle's GROUND CONTACT - the bottom edge
    of its box - not the whole box. A truck roof overhanging the kerb blocks
    nobody."""

    def frac(self, box):
        return obs.fraction_past(zone(), box)

    def test_fully_inside(self):
        self.assertEqual(self.frac((250, 260, 400, 350)), 1.0)

    def test_fully_outside(self):
        self.assertEqual(self.frac((0, 260, 90, 350)), 0.0)

    def test_straddling_is_partial(self):
        f = self.frac((60, 260, 260, 350))
        self.assertGreater(f, 0.0)
        self.assertLess(f, 1.0)

    def test_a_vehicle_below_the_zone_does_not_count(self):
        """Ground contact at y=420 is nearer the camera than the zone reaches,
        so it is off the marked road even though the BOX overlaps it."""
        self.assertEqual(self.frac((150, 350, 350, 420)), 0.0)


class ZoneWithTheSharedRuleTests(unittest.TestCase):
    """The load-bearing property: a zone drops into the existing hysteresis +
    dwell rule with no change to either."""

    def setUp(self):
        self.inside = (250, 260, 400, 350)     # 100% inside the road
        self.outside = (0, 260, 90, 350)       # 0%

    def _state(self, box, t=0.0):
        v = obs.VehicleState(1, box, t)
        return v

    def test_a_parked_vehicle_becomes_an_obstruction_after_the_dwell(self):
        z = zone()
        v = self._state(self.inside)
        v.obstruction_seconds = 300.0
        t = 0.0
        verdict = obs.CLEAR
        while t < 400.0:
            t += 1.0
            verdict = v.update(self.inside, obs.fraction_past(z, self.inside), t)
        self.assertEqual(verdict, obs.OBSTRUCTION)

    def test_a_vehicle_off_the_road_never_engages(self):
        z = zone()
        v = self._state(self.outside)
        v.obstruction_seconds = 300.0
        t = 0.0
        while t < 400.0:
            t += 1.0
            verdict = v.update(self.outside, obs.fraction_past(z, self.outside), t)
        self.assertEqual(verdict, obs.CLEAR)
        self.assertEqual(v.held, 0.0)

    def test_briefly_passing_through_is_not_an_obstruction(self):
        z = zone()
        v = self._state(self.inside)
        v.obstruction_seconds = 300.0
        t = 0.0
        for _ in range(3):
            t += 1.0
            verdict = v.update(self.inside, obs.fraction_past(z, self.inside), t)
        self.assertEqual(verdict, obs.PASSING)

    def test_hysteresis_applies_to_zones_too(self):
        """Between EXIT and ENTER the previous state persists, so box jitter
        around the threshold cannot chop the dwell into fragments."""
        v = self._state(self.inside)
        t = 0.0
        t += 1.0
        v.update(self.inside, obs.ENTER_FRACTION + 0.01, t)
        self.assertTrue(v.over)
        t += 1.0
        v.update(self.inside, (obs.ENTER_FRACTION + obs.EXIT_FRACTION) / 2, t)
        self.assertTrue(v.over, "a value inside the band flipped the state")
        t += 1.0
        v.update(self.inside, obs.EXIT_FRACTION - 0.01, t)
        self.assertFalse(v.over)


class ZoneVersusEdgeTests(unittest.TestCase):
    """A zone and an edge marking the same boundary must agree about a vehicle
    sitting against it. If they disagree, one of the two sign conventions is
    inverted - the exact bug PolyEdge's own docstring warns about."""

    def test_they_agree_on_the_same_boundary(self):
        # Edge along y=300 protecting everything ABOVE it (smaller y), and a
        # big rectangular zone covering that same upper region.
        edge = obs.build_edge({"points": [[0, 300], [640, 300]], "side": 1})
        if not edge.is_protected((320, 100)):
            edge = obs.build_edge({"points": [[0, 300], [640, 300]], "side": -1})
        region = obs.build_edge(
            {"type": "zone", "points": [[0, 0], [640, 0], [640, 300], [0, 300]]})

        for box in [(100, 200, 300, 280),    # fully in the upper region
                    (100, 320, 300, 400)]:   # fully below it
            self.assertAlmostEqual(
                obs.fraction_past(edge, box), obs.fraction_past(region, box),
                places=6, msg=f"edge and zone disagreed on {box}")

    def test_the_two_differ_only_exactly_on_the_boundary(self):
        """A known, deliberate divergence, pinned here so it cannot drift.

        An edge is boundary-EXCLUSIVE (is_protected uses a strict inequality,
        so a point exactly on the line is outside); a zone is boundary-
        INCLUSIVE, because the operator traced the kerb and a vehicle sitting
        on the kerb is in the road.

        It matters only for a footprint landing on exactly the same float as
        the boundary, which real detections do not produce - and where they
        did, "wheels on the kerb counts as obstructing" is the answer a
        barangay official would give.
        """
        edge = obs.build_edge({"points": [[0, 300], [640, 300]], "side": 1})
        if not edge.is_protected((320, 100)):
            edge = obs.build_edge({"points": [[0, 300], [640, 300]], "side": -1})
        region = obs.build_edge(
            {"type": "zone", "points": [[0, 0], [640, 0], [640, 300], [0, 300]]})

        on_the_line = (320, 300)
        self.assertFalse(edge.is_protected(on_the_line))
        self.assertTrue(region.is_protected(on_the_line))

        # One pixel either way and they agree again.
        self.assertEqual(edge.is_protected((320, 299)), region.is_protected((320, 299)))
        self.assertEqual(edge.is_protected((320, 301)), region.is_protected((320, 301)))


class DrawingTests(unittest.TestCase):
    def test_a_zone_draws_without_mutating_its_own_points(self):
        import numpy as np

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        z = zone()
        before = list(z.points)
        z.draw(frame)
        self.assertEqual(z.points, before)
        self.assertTrue(frame.any(), "draw() left the frame untouched")
