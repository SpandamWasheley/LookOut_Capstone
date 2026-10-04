"""The single-polygon zone, and the tracker-identity problem underneath it.

The rule is "inside the polygon for N seconds", and on a busy street the hard
part is not the geometry — it is that the tracker does not keep one id on a
parked car. A passing vehicle occludes it for a frame and it comes back as
somebody new. If the banked seconds do not follow it, the timer restarts every
time traffic goes by and a five-minute threshold can never be reached on the
exact road the rule exists for.
"""
from django.test import SimpleTestCase

from core.vision.obstruction_zone import ObstructionZone, VehicleState

SHAPE = (360, 640, 3)
# The lower half of the frame.
ZONE = [[0.05, 0.45], [0.95, 0.45], [0.98, 0.98], [0.02, 0.98]]

PARKED = (200, 200, 320, 300)        # stationary, ground point inside the zone
ABOVE = (200, 20, 320, 120)          # ground point above the zone


def passing(x):
    """A vehicle driving past along the same ground line."""
    return (x, 205, x + 120, 300)


def zone(alert_score=60):
    return ObstructionZone(ZONE, alert_score=alert_score, moving_weight=0.25)


def park(z, tid=5, seconds=40.0, start=0.0, box=PARKED, step=0.5):
    """Hold one stationary vehicle in the zone; returns the clock after it."""
    t = start
    while t < start + seconds:
        z.update([(tid, box, "car", 0.9)], t, SHAPE)
        t += step
    return t


class AccrualTests(SimpleTestCase):
    def test_a_stationary_vehicle_in_the_zone_accrues_seconds(self):
        z = zone()
        park(z, seconds=10)
        self.assertGreater(z.states[5].score, 8)
        self.assertTrue(z.states[5].inside)
        self.assertTrue(z.states[5].stationary)

    def test_a_vehicle_outside_the_zone_is_not_watched_at_all(self):
        # The polygon decides WHAT IS WATCHED. A vehicle that has never had its
        # wheels inside gets no state, so it cannot accrue and never reaches
        # the live view — traffic on the road behind a marked kerb used to
        # appear there as tracked subjects sitting at zero.
        z = zone()
        park(z, seconds=20, box=ABOVE)
        self.assertEqual(z.states, {})

    def test_a_vehicle_that_was_inside_keeps_its_state_when_it_leaves(self):
        # The banked seconds have to survive a frame where the detector nudges
        # the box over the line, so leaving decays rather than discards.
        z = zone()
        t = park(z, seconds=20)
        self.assertGreater(z.states[5].score, 0)
        z.update([(5, ABOVE, "car", 0.9)], t, SHAPE)
        self.assertIn(5, z.states)
        self.assertFalse(z.states[5].inside)

    def test_a_moving_vehicle_accrues_at_the_reduced_rate(self):
        # Traffic crawling through must not reach the threshold at the same
        # rate as something actually parked.
        z, t, x = zone(), 0.0, 100
        while x < 400:
            z.update([(7, passing(x), "car", 0.9)], t, SHAPE)
            t += 0.5
            x += 20
        moving = z.states[7].score
        self.assertGreater(moving, 0)
        self.assertLess(moving, t * 0.5, "moving vehicle accrued near the stopped rate")

    def test_leaving_the_zone_decays_the_score(self):
        z = zone()
        t = park(z, seconds=20)
        before = z.states[5].score
        for _ in range(10):
            z.update([(5, ABOVE, "car", 0.9)], t, SHAPE)
            t += 0.5
        self.assertLess(z.states[5].score, before - 5)

    def test_it_alerts_once_at_the_threshold(self):
        z = zone(alert_score=5)
        fired = []
        t = 0.0
        while t < 20:
            fired += z.update([(5, PARKED, "car", 0.9)], t, SHAPE)
            t += 0.5
        self.assertEqual(len(fired), 1, "alerted more than once for one stay")
        self.assertEqual(fired[0]["label"], "car")
        self.assertGreaterEqual(fired[0]["score"], 5)


class IdentityChurnTests(SimpleTestCase):
    """The reported bug: traffic going past reset the obstruction timer."""

    def _occlude_then_return(self, z, t, new_id=9):
        """One frame where the parked car is hidden by a passer, then it comes
        back under `new_id` while the passer drives on."""
        z.update([(12, passing(180), "car", 0.9)], t, SHAPE)
        t += 0.5
        x = 260
        for _ in range(8):
            dets = [(new_id, PARKED, "car", 0.9)]
            if x < 560:
                dets.append((12, passing(x), "car", 0.9))
                x += 60
            z.update(dets, t, SHAPE)
            t += 0.5
        return t

    def test_a_passing_vehicle_does_not_steal_the_banked_seconds(self):
        # This is the regression. The passer used to inherit the whole score
        # simply by being the first new id near a momentarily-unseen one.
        z = zone()
        t = park(z, seconds=40)
        z.update([(12, passing(180), "car", 0.9)], t, SHAPE)
        self.assertEqual(z.states[12].score, 0.0, "the passer inherited the parked car's time")

    def test_the_parked_vehicle_keeps_its_time_under_a_new_id(self):
        z = zone()
        t = park(z, seconds=40)
        banked = z.states[5].score
        self._occlude_then_return(z, t)
        self.assertGreaterEqual(z.states[9].score, banked - 1,
                                "the obstruction restarted from zero")

    def test_the_passer_keeps_only_what_it_earned(self):
        z = zone()
        t = park(z, seconds=40)
        self._occlude_then_return(z, t)
        self.assertLess(z.states[12].score, 2)

    def test_the_handover_costs_about_a_second(self):
        # The claimant must hold still before it can absorb, so a little credit
        # is lost. It has to stay well inside PRESENCE_GRACE, or the lost score
        # would start decaying before anyone could claim it.
        z = zone()
        t = park(z, seconds=40)
        banked = z.states[5].score
        self._occlude_then_return(z, t)
        self.assertLess(banked - z.states[9].score, 2.0)

    def test_a_track_absorbs_at_most_one_lost_score(self):
        # Otherwise one long-parked vehicle sitting where several tracks have
        # come and gone could collect all of them and alert far too early.
        z = zone()
        t = park(z, seconds=40)
        t = self._occlude_then_return(z, t)
        claimant = z.states[9]
        self.assertTrue(claimant.adopted)
        taken = claimant.score

        # Offer it a second lost track, right on the same spot.
        ghost = VehicleState(77, t)
        ghost.box, ghost.score, ghost.last_seen = PARKED, 500.0, t
        z.states[77] = ghost
        z._absorb_lost(claimant, t, current_ids={9})

        self.assertEqual(claimant.score, taken, "absorbed a second lost score")
        self.assertIn(77, z.states, "consumed a lost track without taking it")

    def test_an_already_alerted_vehicle_does_not_alert_again_under_a_new_id(self):
        # The spatial cooldown covers this too, but only for 120s — the latch
        # has to travel with the score or a long stay would re-report forever.
        z = zone(alert_score=5)
        t = 0.0
        fired = []
        while t < 20:
            fired += z.update([(5, PARKED, "car", 0.9)], t, SHAPE)
            t += 0.5
        self.assertEqual(len(fired), 1)
        self._occlude_then_return(z, t)
        self.assertTrue(z.states[9].alerted, "the alerted latch did not transfer")

    def test_a_vehicle_parking_somewhere_else_does_not_inherit(self):
        # Near enough by centre distance, but not sitting ON the old box.
        z = zone()
        t = park(z, seconds=40)
        far = (330, 200, 450, 300)           # adjacent bay, no real overlap
        for _ in range(10):
            z.update([(9, far, "car", 0.9)], t, SHAPE)
            t += 0.5
        self.assertLess(z.states[9].score, 10, "inherited a neighbour's time")
        self.assertFalse(z.states[9].adopted)
