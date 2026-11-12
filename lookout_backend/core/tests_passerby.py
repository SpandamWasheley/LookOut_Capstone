"""PasserbyFilter: riders and walkers are left out of gathering formation; seated members never are."""
from django.test import SimpleTestCase, TestCase

from core.models import SystemSettings
from core.vision import spec_settings
from core.vision.tracking import PasserbyFilter, PersonTracker


def box(cx, cy, w=50, h=200):
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, 0.9)


def run(fn, seconds, fps=10):
    """fn(t) -> (list of (ext_id, box), list of motorcycle boxes). -> {ext_id: set of reasons} over the run."""
    tracker, pf, seen = PersonTracker(), PasserbyFilter(), {}
    for i in range(int(seconds * fps) + 1):
        t = i / fps
        people, motos = fn(t)
        tracks = tracker.update([b for _, b in people], t, ids=[e for e, _ in people])
        live = [x for x in tracks if not x.is_scene and x.seen_at(t)]
        for tid, reason in pf.exclude(live, motos, t).items():
            ext = next(x.ext_id for x in live if x.id == tid)
            seen.setdefault(ext, set()).add(reason)
    return seen


class PasserbyFilterTests(SimpleTestCase):
    def test_rider_is_removed(self):
        def fn(t):
            ppl, motos = [(1, box(300, 400)), (2, box(380, 400))], []
            if 5 <= t <= 12:
                x = 100 + (t - 5) * 40
                ppl.append((3, box(x, 400)))
                motos.append((x - 60, 430, x + 70, 520, 0.8))
            return ppl, motos
        self.assertEqual(run(fn, 20), {3: {"rider"}})

    def test_seated_member_is_not_a_rider_when_a_motorcycle_passes_in_front(self):
        """The rider fix: a person still for 5 s is never removed as a rider (DRK_05 G1 / DRK_06 G1)."""
        def fn(t):
            ppl, motos = [(1, box(300, 400)), (2, box(380, 400))], []
            if 10 <= t <= 14:                                  # long after both have been still for 5 s
                x = 250 + (t - 10) * 40
                motos.append((x - 60, 430, x + 70, 520, 0.8))   # a bike sweeps across both seated people's boxes
            return ppl, motos
        self.assertEqual(run(fn, 20), {})

    def test_walker_is_removed_and_a_seated_member_who_stands_up_is_kept(self):
        def walker(t):
            ppl = [(1, box(300, 400)), (2, box(380, 400))]
            if 5 <= t <= 13:
                ppl.append((3, box(100 + (t - 5) * 100, 400)))
            return ppl, []
        self.assertEqual(run(walker, 20), {3: {"walker"}})

        def stands_up(t):
            x = 380
            if 20 <= t < 22:
                x = 380 + 90 * (t - 20)
            elif 22 <= t < 24:
                x = 560 - 90 * (t - 22)
            return [(1, box(300, 400)), (2, box(x, 400))], []
        self.assertEqual(run(stands_up, 40), {})

    def test_young_track_is_never_excluded(self):
        def fn(t):
            ppl = [(1, box(300, 400)), (2, box(380, 400))]
            if 5 <= t <= 5.6:
                ppl.append((3, box(100 + (t - 5) * 400, 400)))
            return ppl, []
        self.assertEqual(run(fn, 10), {})


class PasserbySettingTests(TestCase):
    def test_filter_is_on_by_default_and_exposed_as_a_setting(self):
        self.assertTrue(SystemSettings.objects.create().drinking_passerby_filter)
        self.assertIs(spec_settings.SPEC_DEFAULTS["drinking_passerby_filter"], True)
        self.assertIn("drinking_passerby_filter", spec_settings.GROUPS["drinking"])
        from core.serializers import SystemSettingsSerializer
        self.assertIn("drinking_passerby_filter", SystemSettingsSerializer.Meta.fields)

    def test_long_stay_default_is_60_in_model_and_spec(self):
        self.assertEqual(SystemSettings.objects.create().drinking_group_duration, 60)
        self.assertEqual(spec_settings.SPEC_DEFAULTS["drinking_group_duration"], 60)
