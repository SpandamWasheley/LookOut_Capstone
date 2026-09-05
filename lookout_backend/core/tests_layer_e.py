"""Scenario tests for Layer E — the theft pattern rules (E1-E29).

Every rule in Layer E is temporal: it is defined by how boxes move over seconds,
not by anything visible in one frame. So these tests drive synthetic tracks
through a simulated clock and assert on the Evidence that comes out — which is
the only way to exercise approach/dwell/freeze/custody logic without footage.

No Django models and no YOLO: the engine takes plain boxes, so the whole suite
runs in well under a second.

Geometry convention: people are 60x100 boxes, so one "person height" is 100px
and a d_norm of 0.8 (REACH_NORM) is 80px between centres.
"""

import math
import unittest

from core.vision import theft, tracking

PERSON_W, PERSON_H = 60, 100
FRAME_SHAPE = (720, 1280, 3)
# A fleeing thief covers ~1200px in the 3s E24 grace period, so the snatch
# scenarios need a frame they cannot run off the edge of — otherwise E25
# (correctly) discards the track mid-incident and the pattern never completes.
WIDE_FRAME = (2000, 6000, 3)
DT = 0.1


def pbox(cx, cy, w=PERSON_W, h=PERSON_H, conf=0.9):
    """A person detection tuple centred on (cx, cy)."""
    return (int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2), conf)


def obox(x1, y1, x2, y2, label, conf=0.8):
    """An object detection tuple (carriable or vehicle)."""
    return (int(x1), int(y1), int(x2), int(y2), conf, label)


class Scene:
    """Drives PersonTracker + TheftEngine over a simulated clock.

    The scene velocity baseline (E2) is pinned rather than left to converge, so
    each test's v_norm values are exactly what the scenario intends. Baseline
    convergence itself is covered separately in PrimitiveTests.
    """

    def __init__(self, baseline=1.0, ablate=(), night=False,
                 frame_shape=FRAME_SHAPE):
        self.t = 1000.0
        self.tracker = tracking.PersonTracker()
        self.engine = theft.TheftEngine(ablate=ablate)
        self.engine.baseline.value = baseline
        self.engine.baseline.window = 1e9      # freeze the EMA for determinism
        self.night = night
        self.frame_shape = frame_shape
        self.evidence = []

    def step(self, persons=(), carriables=(), vehicles=(), threats=()):
        self.t += DT
        tracks = self.tracker.update(list(persons), self.t,
                                     frame_shape=self.frame_shape)
        found = self.engine.update(tracks, list(carriables), list(vehicles),
                                   list(threats), self.t, is_night=self.night)
        self.evidence.extend(found)
        return found

    def hold(self, seconds, **kwargs):
        """Repeats one static frame for `seconds` of simulated time."""
        for _ in range(int(round(seconds / DT))):
            self.step(**kwargs)

    def of_kind(self, kind):
        return [e for e in self.evidence if e.kind == kind]

    def best(self, kind):
        found = self.of_kind(kind)
        return max(found, key=lambda e: e.score) if found else None


class PrimitiveTests(unittest.TestCase):
    """E1-E3 — the normalized primitives everything else is expressed in."""

    def test_e1_norm_distance_is_depth_invariant(self):
        # The same real-world gap at two depths: near, people are 100px tall and
        # 80px apart; far, they are 50px tall and 40px apart. E1 must call both
        # the same distance, which is the whole reason the constants are
        # per-camera-free.
        near = tracking.norm_distance(pbox(500, 300)[:4], pbox(580, 300)[:4])
        far = tracking.norm_distance(
            pbox(500, 300, h=50, w=30)[:4], pbox(540, 300, h=50, w=30)[:4])
        self.assertAlmostEqual(near, 0.8, places=6)
        self.assertAlmostEqual(far, 0.8, places=6)

    def test_e3_path_efficiency_separates_transit_from_milling(self):
        walker = tracking.Track(1, pbox(100, 300)[:4], 0.0)
        loiterer = tracking.Track(2, pbox(600, 300)[:4], 0.0)
        for i in range(200):
            t = i * DT
            walker.box = pbox(100 + i * 5, 300)[:4]
            walker.last_seen = t
            walker.observe(t)
            loiterer.box = pbox(600 + 40 * math.sin(t * 2 * math.pi / 5), 300)[:4]
            loiterer.last_seen = t
            loiterer.observe(t)
        t = 199 * DT
        self.assertGreater(walker.path_efficiency(t), 0.9)
        self.assertLess(loiterer.path_efficiency(t), theft.EFF_LOITER)

    def test_e3_abstains_without_enough_history(self):
        t = tracking.Track(1, pbox(100, 300)[:4], 0.0)
        t.last_seen = 0.0
        t.observe(0.0)
        self.assertIsNone(t.path_efficiency(0.0))

    def test_e2_baseline_converges_to_the_median_and_floors(self):
        base = tracking.SceneBaseline(window=1.0)
        still = tracking.Track(1, pbox(100, 300)[:4], 0.0)
        still.last_seen = 0.0
        still.observe(0.0)
        # An empty/static scene must not drive the reference to zero, or v_norm
        # would report every twitch as a sprint.
        base.update([still], 0.0)
        self.assertGreaterEqual(base.reference, tracking.MIN_SCENE_SPEED)

        mover = tracking.Track(2, pbox(100, 300)[:4], 0.0)
        for i in range(60):
            t = i * DT
            mover.box = pbox(100 + i * 10, 300)[:4]   # 100px/s = 1.0 heights/s
            mover.last_seen = t
            mover.observe(t)
            base.update([mover], t)
        self.assertAlmostEqual(base.reference, 1.0, delta=0.15)

    def test_e26_id_switch_needs_an_impossible_speed(self):
        # A walker at far mode's ~1 FPS crosses more than a box width per frame.
        # That must NOT read as an identity switch.
        walker = tracking.Track(1, pbox(100, 300)[:4], 0.0)
        for i in range(5):
            t = i * 1.0
            walker.box = pbox(100 + i * 120, 300)[:4]
            walker.last_seen = t
            walker.observe(t)
        self.assertFalse(walker.id_switched_within(4.0, theft.EVIDENCE_WINDOW))

        teleport = tracking.Track(2, pbox(100, 300)[:4], 0.0)
        teleport.last_seen = 0.0
        teleport.observe(0.0)
        teleport.box = pbox(900, 300)[:4]      # 800px in 0.1s = 80 heights/s
        teleport.last_seen = 0.1
        teleport.observe(0.1)
        self.assertTrue(teleport.id_switched_within(0.1, theft.EVIDENCE_WINDOW))

    def test_e25_edge_truncation_is_flagged(self):
        t = tracking.Track(1, (0, 300, 60, 400), 0.0)
        t.last_seen = 0.0
        t.observe(0.0, FRAME_SHAPE)
        self.assertTrue(t.truncated)


class SnatchTests(unittest.TestCase):
    """E6-E9 and the E24 greeting suppression."""

    def _run_snatch(self, greeting=False, with_bag=True):
        s = Scene(frame_shape=WIDE_FRAME)
        vx, tx = 600.0, 1000.0
        bag = [vx + 10, 310]        # rides with whoever holds it

        def frame(persons, bag_owner_xy):
            carriables = []
            if with_bag:
                bx, by = bag_owner_xy
                carriables = [obox(bx - 20, by - 20, bx + 20, by + 20, "handbag")]
            return s.step(persons=persons, carriables=carriables)

        # Approach: thief closes monotonically until d_norm < 0.8 (80px).
        while tx - vx > 70:
            vx -= 3          # victim strolling, v_norm 0.3 — under E7's 0.4
            tx -= 25
            bag = [vx + 10, 310]
            frame([pbox(vx, 300), pbox(tx, 300)], bag)

        # Contact: held together for ~0.5s, inside E6's 0.3-2.0s bounds.
        contact_frames = 5
        for i in range(contact_frames):
            vx -= 3
            tx -= 3
            # The grab lands on the last contact frame.
            bag = [tx, 310] if i == contact_frames - 1 else [vx + 10, 310]
            frame([pbox(vx, 300), pbox(tx, 300)], bag)

        # Separation: thief bursts away, victim keeps strolling.
        for i in range(20):
            vx -= 3
            tx += 40         # 400px/s = 4.0 heights/s, over E7's 2.5
            bag = [tx, 310]
            frame([pbox(vx, 300), pbox(tx, 300)], bag)

        if greeting:
            # Both resume ordinary gait on a shared heading — E24's signature.
            for _ in range(20):
                vx += 10
                tx += 10
                bag = [tx, 310]
                frame([pbox(vx, 300), pbox(tx, 300)], bag)
        else:
            for _ in range(20):
                vx -= 3
                tx += 40
                bag = [tx, 310]
                frame([pbox(vx, 300), pbox(tx, 300)], bag)
        return s

    def test_snatch_with_custody_transfer_reaches_candidate(self):
        s = self._run_snatch()
        ev = s.best("snatch")
        self.assertIsNotNone(ev, "no snatch evidence emitted")
        self.assertIn("E7", ev.cues, "separation burst (E7) did not fire")
        self.assertIn("E9", ev.cues, "custody transfer (E9) did not fire")
        self.assertIn("E8", ev.cues, "heading divergence (E8) did not fire")
        # 0.20 + 0.10 + 0.35 = 0.65, over the 0.55 alert band.
        self.assertAlmostEqual(ev.score, 0.65, places=6)
        self.assertEqual(ev.band, theft.CANDIDATE)

    def test_snatch_without_custody_transfer_is_discarded(self):
        # Burst + divergence alone is 0.30 — under the Observe floor. A hurried
        # departure after a brief encounter must not become a record.
        s = self._run_snatch(with_bag=False)
        ev = s.best("snatch")
        if ev is not None:
            self.assertLess(ev.score, theft.SCORE_OBSERVE)
            self.assertEqual(ev.band, theft.DISCARD)

    def test_greeting_is_suppressed(self):
        s = self._run_snatch(greeting=True)
        self.assertEqual(s.of_kind("snatch"), [],
                         "a greeting was scored as a snatch")
        self.assertGreaterEqual(s.engine.stats.get("SUPPRESSED_GREETING", 0), 1)


class HoldupTests(unittest.TestCase):
    """E10-E14 — loiter, rapid close, confrontation freeze, weapon escalation."""

    def _run_holdup(self, armed=False, night=False):
        s = Scene(night=night)
        lx = 600.0

        # E10 — 25s of milling, so path efficiency falls under 0.3 and the track
        # is older than the 20s loiter minimum.
        for i in range(250):
            lx = 600 + 40 * math.sin(i * DT * 2 * math.pi / 5)
            s.step(persons=[pbox(lx, 300)])

        # E11 — a second track closes from d_norm > 3.0 to < 1.0 in under 3s.
        ax = 1000.0
        lx = 600.0
        while ax - lx > 85:
            ax -= 12.6
            s.step(persons=[pbox(lx, 300), pbox(ax, 300)])

        # E12 — both stationary at arm's length (d_norm 0.85) for over 3s.
        threats = []
        if armed:
            threats = [obox(ax - 15, 285, ax + 15, 315, "knife")]
        s.hold(5.0, persons=[pbox(lx, 300), pbox(ax, 300)], threats=threats)
        return s

    def test_freeze_plus_loiter_lands_in_observe(self):
        s = self._run_holdup()
        ev = s.best("holdup")
        self.assertIsNotNone(ev, "no holdup evidence emitted")
        self.assertIn("E12", ev.cues, "confrontation freeze (E12) did not fire")
        self.assertIn("E10", ev.cues, "loiter (E10) did not fire")
        # 0.25 + 0.20 = 0.45: a near miss, kept for calibration, not dispatched.
        self.assertAlmostEqual(ev.score, 0.45, places=6)
        self.assertEqual(ev.band, theft.OBSERVE)

    def test_armed_holdup_reaches_candidate(self):
        s = self._run_holdup(armed=True)
        ev = s.best("holdup")
        self.assertIsNotNone(ev)
        self.assertIn("E14", ev.cues, "weapon escalation (E14) did not fire")
        self.assertAlmostEqual(ev.score, 0.90, places=6)
        self.assertEqual(ev.band, theft.CANDIDATE)

    def test_nocturnal_amplifier_lifts_the_freeze_over_the_band(self):
        # 0.45 x 1.3 = 0.585 — E20 is exactly what turns this near miss into a
        # candidate, which is the behaviour the regional crime statistics argue for.
        s = self._run_holdup(night=True)
        ev = s.best("holdup")
        self.assertIsNotNone(ev)
        self.assertIn("E20", ev.multipliers)
        self.assertAlmostEqual(ev.score, 0.585, places=6)
        self.assertEqual(ev.band, theft.CANDIDATE)


class CarnappingTests(unittest.TestCase):
    """E15-E20 — anchor registration, then interaction, tampering and push-away."""

    BIKE = (580, 300, 700, 380)

    def _register_unattended_bike(self, s):
        bike = [obox(*self.BIKE, label="motorcycle")]
        # E5: 60s still to register as a ParkedAnchor, then E15: 120s with
        # nobody near before any carnapping rule evaluates at all.
        s.hold(theft.ANCHOR_STATIC_SECONDS + theft.ABSENCE_SECONDS + 2,
               vehicles=bike)
        anchor = s.engine.anchors.anchors[0]
        self.assertIsNotNone(anchor.anchor_since, "E5 never registered the anchor")
        self.assertTrue(anchor.unattended, "E15 never marked the anchor unattended")
        return bike

    def test_e15_gates_everything_until_the_absence_timer_expires(self):
        s = Scene()
        bike = [obox(*self.BIKE, label="motorcycle")]
        s.hold(theft.ANCHOR_STATIC_SECONDS + 2, vehicles=bike)
        anchor = s.engine.anchors.anchors[0]
        self.assertIsNotNone(anchor.anchor_since)
        self.assertFalse(anchor.unattended)
        # A person manipulating an ATTENDED vehicle is presumptively legitimate.
        s.hold(20.0, persons=[pbox(647, 330, w=95)], vehicles=bike)
        self.assertEqual(s.of_kind("carnapping"), [])

    def test_tamper_then_push_reaches_candidate(self):
        s = Scene()
        bike = self._register_unattended_bike(s)

        # E16 + E17: crouched over the lock, overlapping the bike, for 15s.
        s.hold(theft.INTERACT_DWELL + 1, persons=[pbox(647, 330, w=95)],
               vehicles=bike)

        # Stand up and step round to the side. Jumping straight to the pushing
        # position would move the centroid 70px in one frame — 7 heights/s,
        # over the E26 identity-switch guard, which would (correctly) discard
        # the track and with it every cue latched against this anchor.
        for i in range(1, 11):
            s.step(persons=[pbox(647 - 6.3 * i, 330 - 3 * i, w=95 - 3.5 * i)],
                   vehicles=bike)

        # E18: now beside it, not on the seat, walking it away at 40px/s.
        # Standing off the bike's left edge keeps d_norm at ~0.72 (inside E18's
        # 0.9 reach) while overlapping the seat region by only ~0.03 IoU.
        bx = float(self.BIKE[0])
        for _ in range(int(round((theft.PUSH_SECONDS + 2) / DT))):
            bx += 4
            moving = [obox(bx, 300, bx + 120, 380, "motorcycle")]
            s.step(persons=[pbox(bx, 300)], vehicles=moving)

        ev = s.best("carnapping")
        self.assertIsNotNone(ev, "no carnapping evidence emitted")
        self.assertIn("E16", ev.cues, "interaction dwell (E16) did not fire")
        self.assertIn("E17", ev.cues, "tamper posture (E17) did not fire")
        self.assertIn("E18", ev.cues, "push-away (E18) did not fire")
        # 0.20 + 0.15 + 0.35 = 0.70
        self.assertAlmostEqual(ev.score, 0.70, places=6)
        self.assertEqual(ev.band, theft.CANDIDATE)

    def test_e19_abstains_without_a_reid_embedding(self):
        s = Scene()
        self._register_unattended_bike(s)
        bx = float(self.BIKE[0])
        for _ in range(int(round((theft.PUSH_SECONDS + 20) / DT))):
            bx += 4
            moving = [obox(bx, 300, bx + 120, 380, "motorcycle")]
            s.step(persons=[pbox(bx, 300)], vehicles=moving)
        ev = s.best("carnapping")
        self.assertIsNotNone(ev)
        # The module stays functional with E19 abstaining — it is excluded from
        # the sum without penalty rather than counting against the evidence.
        self.assertIn("E19", ev.abstained)
        self.assertNotIn("E19", ev.cues)

    def test_riding_away_is_not_a_push(self):
        # A person ON the seat is riding, which E18 must not score.
        s = Scene()
        self._register_unattended_bike(s)
        bx = float(self.BIKE[0])
        for _ in range(int(round((theft.PUSH_SECONDS + 2) / DT))):
            bx += 4
            moving = [obox(bx, 300, bx + 120, 380, "motorcycle")]
            s.step(persons=[pbox(bx + 60, 320, w=95)], vehicles=moving)
        for ev in s.of_kind("carnapping"):
            self.assertNotIn("E18", ev.cues, "riding was scored as a push")


class PropertyTests(unittest.TestCase):
    """E21-E22 — an unattended bag taken by someone other than its owner."""

    BAG = (700, 400, 740, 440)

    def test_custody_change_at_an_unattended_bag(self):
        s = Scene()
        bag = [obox(*self.BAG, label="backpack")]

        # The owner sets it down...
        s.hold(2.0, persons=[pbox(720, 390)], vehicles=(), carriables=bag)
        # ...walks away, and it sits alone past the 30s timer.
        s.hold(theft.OBJ_STATIC_SECONDS + 3, persons=[pbox(1100, 390)],
               carriables=bag)
        anchor = s.engine.anchors.anchors[0]
        self.assertTrue(anchor.unattended, "E21 never marked the bag unattended")
        self.assertIsNotNone(anchor.retained_owner, "E21 lost the retained owner")

        # A different person picks it up and carries it off.
        bx = 720.0
        for _ in range(40):
            bx += 6
            moving = [obox(bx - 20, 400, bx + 20, 440, "backpack")]
            s.step(persons=[pbox(bx, 390)], carriables=moving)

        ev = s.best("property")
        self.assertIsNotNone(ev, "no property evidence emitted")
        self.assertIn("E22", ev.cues)
        self.assertAlmostEqual(ev.score, 0.35, places=6)
        self.assertEqual(ev.band, theft.OBSERVE)

    def test_owner_reclaiming_their_own_bag_is_not_theft(self):
        s = Scene()
        bag = [obox(*self.BAG, label="backpack")]
        s.hold(2.0, persons=[pbox(720, 390)], carriables=bag)
        s.hold(theft.OBJ_STATIC_SECONDS + 3, persons=[pbox(1100, 390)],
               carriables=bag)
        anchor = s.engine.anchors.anchors[0]
        owner = anchor.retained_owner

        # The SAME track id returns for it — no change of custody, no theft.
        bx = 720.0
        for _ in range(40):
            bx += 6
            moving = [obox(bx - 20, 400, bx + 20, 440, "backpack")]
            s.step(persons=[pbox(bx, 390)], carriables=moving)
        for ev in s.of_kind("property"):
            self.assertNotEqual(ev.tracks, [owner],
                                "the original owner was scored as a thief")


class SuppressionTests(unittest.TestCase):
    """E23-E27 — abstain rather than reject, and log the reason."""

    def test_e23_hard_crowd_guard_abstains(self):
        s = Scene()
        crowd = [pbox(200 + i * 70, 300) for i in range(theft.CROWD_HARD + 2)]
        s.hold(2.0, persons=crowd)
        self.assertGreaterEqual(s.engine.stats.get("SUPPRESSED_CROWD", 0), 1)
        self.assertEqual(s.evidence, [])

    def test_e23_soft_guard_widens_the_gates_without_abstaining(self):
        s = Scene()
        crowd = [pbox(200 + i * 200, 300) for i in range(theft.CROWD_SOFT + 1)]
        s.hold(1.0, persons=crowd)
        self.assertEqual(s.engine.stats.get("SUPPRESSED_CROWD", 0), 0)

    def test_e25_truncated_tracks_are_discarded(self):
        s = Scene()
        # Two people at arm's length, but one is clipped by the frame edge, so
        # its centroid and height — and every quantity derived from them — are wrong.
        s.hold(1.0, persons=[pbox(30, 300), pbox(110, 300)])
        self.assertGreaterEqual(
            s.engine.stats.get("discarded (E25): edge-truncated track", 0), 1)

    def test_ablating_a_single_cue_removes_only_that_cue(self):
        armed = HoldupTests()._run_holdup(armed=True)
        self.assertIn("E14", armed.best("holdup").cues)

        # The ablation harness the spec calls for: one rule off, everything else
        # untouched, so the cue's contribution can be measured not asserted.
        s = Scene(ablate={"e14"})
        s.tracker = tracking.PersonTracker()
        lx = 600.0
        for i in range(250):
            lx = 600 + 40 * math.sin(i * DT * 2 * math.pi / 5)
            s.step(persons=[pbox(lx, 300)])
        ax, lx = 1000.0, 600.0
        while ax - lx > 85:
            ax -= 12.6
            s.step(persons=[pbox(lx, 300), pbox(ax, 300)])
        s.hold(5.0, persons=[pbox(lx, 300), pbox(ax, 300)],
               threats=[obox(ax - 15, 285, ax + 15, 315, "knife")])
        ev = s.best("holdup")
        self.assertIsNotNone(ev)
        self.assertNotIn("E14", ev.cues)
        self.assertEqual(s.of_kind("weapon"), [])


class ScoringTests(unittest.TestCase):
    """E28-E29 — the weighted sum and the three-band decision."""

    def test_band_boundaries(self):
        self.assertEqual(theft.band_of(0.34), theft.DISCARD)
        self.assertEqual(theft.band_of(theft.SCORE_OBSERVE), theft.OBSERVE)
        self.assertEqual(theft.band_of(0.55), theft.OBSERVE)
        self.assertEqual(theft.band_of(0.56), theft.CANDIDATE)

    def test_multipliers_apply_after_the_sum(self):
        ev = theft.Evidence("holdup", (0, 0, 10, 10),
                            {"E12": 0.25, "E10": 0.20},
                            {"E13": 1.5, "E20": 1.3}, set(), [1, 2], "test")
        self.assertAlmostEqual(ev.score, 0.45 * 1.5 * 1.3, places=6)

    def test_weapon_alone_sits_below_the_alert_band_as_written(self):
        # Documented conflict: E14's rationale calls a weapon "sufficient alone
        # to reach the alert band", but 0.45 < 0.55. As written it is Observe.
        ev = theft.Evidence("weapon", (0, 0, 10, 10), {"E14": 0.45}, {},
                            set(), [1], "test")
        self.assertEqual(ev.band, theft.OBSERVE)

    def test_weapon_alone_alerts_when_the_operator_opts_in(self):
        ev = theft.Evidence("weapon", (0, 0, 10, 10), {"E14": 0.45}, {},
                            set(), [1], "test", weapon_alone_alerts=True)
        self.assertEqual(ev.band, theft.CANDIDATE)

    def test_abstained_cues_are_excluded_without_penalty(self):
        ev = theft.Evidence("carnapping", (0, 0, 10, 10), {"E18": 0.35}, {},
                            {"E19"}, [1], "test")
        self.assertAlmostEqual(ev.score, 0.35, places=6)
        self.assertIn("E19", ev.abstained)


if __name__ == "__main__":
    unittest.main()
