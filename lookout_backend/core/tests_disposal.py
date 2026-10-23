"""Improper disposal — the static-object rule and the zero-shot confirmation.

Stage 1 is tested on synthetic frames rather than footage, so these run
without a GPU, a camera or the YOLO-World weights: the thing under test is the
dual-background RULE (does a moving object get ignored and a dumped one
survive?), and a drawn rectangle exercises that honestly.

Stage 2 is tested against a fake model object, because what matters is the
DECISION — garbage must outscore every reject class — not ultralytics' own
inference, which is not ours to test.
"""

import inspect
import pathlib
import shutil
import sys
import tempfile
import types
from unittest import mock

import numpy as np
from django.test import TestCase

from core import descriptions
from core.models import SystemSettings
from core.vision import disposal


def scene(width=640, height=360, shade=90):
    """A plain, slightly textured background. Pure flat grey makes MOG2's
    variance degenerate, so a little noise keeps it realistic."""
    rng = np.random.default_rng(0)
    frame = np.full((height, width, 3), shade, dtype=np.uint8)
    noise = rng.integers(-4, 5, size=frame.shape, dtype=np.int16)
    return np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def with_object(frame, x, y, w=60, h=45, shade=235):
    out = frame.copy()
    out[y:y + h, x:x + w] = shade
    return out


def warm_up(detector, frames=80, step=0.1, start=0.0):
    """Feed the empty scene until the background is learned."""
    now = start
    for _ in range(frames):
        detector.update(scene(), now)
        now += step
    return now


class StaticObjectDetectorTests(TestCase):
    def test_nothing_is_reported_while_the_background_is_still_being_learned(self):
        det = disposal.StaticObjectDetector()
        out = det.update(scene(), 0.0)
        self.assertTrue(det.warming)
        self.assertEqual(out, [])

    def test_warming_ends_after_the_warmup_window(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        self.assertGreater(now, disposal.WARMUP_SECONDS)
        self.assertFalse(det.warming)

    def test_an_object_that_appears_and_stays_becomes_a_candidate(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        self.assertTrue(det.candidates, "a dumped object should be tracked")

    def test_a_candidate_accrues_still_time_across_frames(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        first = now
        for _ in range(20):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.2
        cand = det.candidates[0]
        # Measured from when it was FIRST seen, not from the latest frame.
        self.assertGreater(cand.still_for(now), 1.0)
        self.assertGreaterEqual(cand.first, first)

    def test_a_candidate_is_dropped_once_it_goes_unseen_past_the_grace(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        self.assertTrue(det.candidates)
        # The object is gone (picked up); nothing matches, so after the grace
        # window the candidate must not linger accruing still-time forever.
        now += disposal.CANDIDATE_GRACE + 1.0
        det.update(scene(), now)
        self.assertEqual(det.candidates, [])

    def test_due_for_check_waits_for_the_static_requirement(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        self.assertTrue(det.candidates)
        # Nothing has been still for 60s, so nothing is due.
        self.assertEqual(det.due_for_check(now, static_seconds=60.0), [])
        # With a requirement it HAS met, it is due.
        self.assertTrue(det.due_for_check(now, static_seconds=0.2))

    def test_a_confirmed_candidate_is_never_offered_again(self):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        cand = det.candidates[0]
        cand.confirmed = True
        self.assertEqual(det.due_for_check(now, static_seconds=0.2), [])

    def test_a_rejected_candidate_is_re_offered_only_after_the_recheck_window(self):
        """A rejected candidate gets another look — stage 2 saw one crop at one
        moment, and a bag half-occluded by a passer-by deserves a second."""
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(12):
            det.update(with_object(scene(), 300, 180), now)
            now += 0.1
        cand = det.candidates[0]
        cand.rejected, cand.last_check = True, now
        self.assertEqual(det.due_for_check(now, static_seconds=0.2), [])
        later = now + disposal.RECHECK_SECONDS + 0.1
        cand.last = later          # keep it alive past the grace window
        self.assertIn(cand, det.due_for_check(later, static_seconds=0.2))


class CropTests(TestCase):
    def test_a_box_is_mapped_back_to_full_resolution_and_padded(self):
        frame = scene(1920, 1080)
        # A box found at PROC_WIDTH 960 means scale 0.5 against a 1920 frame.
        crop = disposal.crop_full_res(frame, (100, 100, 60, 40), scale=0.5)
        # Padding makes the crop strictly larger than the mapped 120x80 box.
        self.assertGreater(crop.shape[1], 120)
        self.assertGreater(crop.shape[0], 80)

    def test_a_tiny_box_still_yields_at_least_the_minimum_crop(self):
        frame = scene(1920, 1080)
        crop = disposal.crop_full_res(frame, (500, 500, 4, 4), scale=1.0)
        self.assertGreaterEqual(min(crop.shape[:2]), disposal.MIN_CROP - 1)

    def test_a_box_at_the_frame_edge_is_clamped_not_wrapped(self):
        frame = scene(640, 360)
        crop = disposal.crop_full_res(frame, (0, 0, 40, 30), scale=1.0)
        self.assertGreater(crop.size, 0)


class FakeBoxes:
    def __init__(self, pairs):
        self.cls = [float(c) for c, _ in pairs]
        self.conf = [float(s) for _, s in pairs]
        self.cls = _Listy(self.cls)
        self.conf = _Listy(self.conf)


class _Listy(list):
    def tolist(self):
        return list(self)


class FakeResult:
    def __init__(self, pairs, names):
        self.boxes = FakeBoxes(pairs)
        self.names = names


class FakeWorld:
    """Stands in for YOLOWorld. `pairs` are (class_id, score)."""

    def __init__(self, pairs):
        self.pairs = pairs
        names = {i: n for i, n in enumerate(disposal.PROMPTS + disposal.REJECT)}
        self.names = names

    def predict(self, crop, **kwargs):
        return [FakeResult(self.pairs, self.names)]


class ConfirmTests(TestCase):
    """The decision, not the inference: garbage must BEAT every reject class."""

    def setUp(self):
        self.crop = scene(128, 128)
        self.garbage_ids = disposal.garbage_class_ids()
        # ids 0..4 are PROMPTS; REJECT starts at len(PROMPTS).
        self.sack = len(disposal.PROMPTS) - 1
        self.person = len(disposal.PROMPTS)
        self.dog = len(disposal.PROMPTS) + 1

    def test_a_bag_alone_confirms(self):
        ok, (name, score), _ = disposal.confirm(
            FakeWorld([(0, 0.31)]), self.crop, self.garbage_ids)
        self.assertTrue(ok)
        self.assertEqual(name, "plastic bag")
        self.assertAlmostEqual(score, 0.31, places=5)

    def test_a_dog_scoring_higher_than_the_bag_is_rejected(self):
        """The whole point of stage 2. A bare confidence floor would pass this."""
        ok, _g, (r_name, _r) = disposal.confirm(
            FakeWorld([(0, 0.22), (self.dog, 0.55)]), self.crop, self.garbage_ids)
        self.assertFalse(ok)
        self.assertEqual(r_name, "dog")

    def test_a_bag_outscoring_a_person_confirms(self):
        ok, (name, _s), _r = disposal.confirm(
            FakeWorld([(self.sack, 0.48), (self.person, 0.20)]),
            self.crop, self.garbage_ids)
        self.assertTrue(ok)
        self.assertEqual(name, "sack")

    def test_a_tie_counts_as_garbage(self):
        """Stage 1 already established something was dumped and left, so a crop
        the model cannot separate is resolved in favour of the evidence."""
        ok, _g, _r = disposal.confirm(
            FakeWorld([(0, 0.30), (self.dog, 0.30)]), self.crop, self.garbage_ids)
        self.assertTrue(ok)

    def test_nothing_detected_is_not_garbage(self):
        ok, (name, score), _ = disposal.confirm(
            FakeWorld([]), self.crop, self.garbage_ids)
        self.assertFalse(ok)
        self.assertEqual((name, score), ("", 0.0))

    def test_only_reject_classes_is_not_garbage(self):
        ok, _g, (r_name, _s) = disposal.confirm(
            FakeWorld([(self.person, 0.9)]), self.crop, self.garbage_ids)
        self.assertFalse(ok)
        self.assertEqual(r_name, "person")

    def test_an_empty_crop_is_handled_without_calling_the_model(self):
        empty = np.zeros((0, 0, 3), dtype=np.uint8)
        ok, _g, _r = disposal.confirm(FakeWorld([(0, 0.9)]), empty, self.garbage_ids)
        self.assertFalse(ok)

    def test_garbage_ids_cover_exactly_the_prompt_classes(self):
        self.assertEqual(disposal.garbage_class_ids(), set(range(len(disposal.PROMPTS))))
        self.assertNotIn(self.person, disposal.garbage_class_ids())


class SpatialCooldownTests(TestCase):
    """Place-keyed, not id-keyed — the D4 lesson applied to this detector."""

    def test_the_same_spot_is_blocked_inside_the_window(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0)
        self.assertTrue(cd.blocks((110.0, 105.0), 30.0))

    def test_a_different_spot_is_not_blocked(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0)
        self.assertFalse(cd.blocks((400.0, 300.0), 30.0))

    def test_the_same_spot_is_allowed_once_the_window_lapses(self):
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((100.0, 100.0), 0.0)
        self.assertFalse(cd.blocks((100.0, 100.0), 121.0))

    def test_a_new_candidate_id_at_the_same_place_is_still_blocked(self):
        """The failure this exists to prevent: stage 1 drops a candidate after a
        couple of unseen seconds, and the replacement starts with a clean
        timer. An id-keyed cooldown would let one bag re-alert indefinitely."""
        cd = disposal.SpatialCooldown(seconds=120, distance=60)
        cd.remember((250.0, 180.0), 10.0)
        for t in (12.0, 20.0, 60.0, 119.0):
            self.assertTrue(cd.blocks((252.0, 181.0), t), f"should block at {t}s")


class DisposalSettingsTests(TestCase):
    def test_the_defaults_are_the_documented_ones(self):
        cfg = SystemSettings.load()
        self.assertTrue(cfg.disposal_enabled)
        self.assertEqual(cfg.disposal_confidence, 15)
        self.assertEqual(cfg.disposal_static_seconds, 5)
        self.assertEqual(cfg.disposal_cooldown, 120)

    def test_the_confidence_default_matches_the_vision_modules_floor(self):
        """Settings store a percent; the CV module stores a 0-1 fraction. If
        these drift, the dashboard and the detector disagree about the floor."""
        cfg = SystemSettings.load()
        self.assertAlmostEqual(cfg.disposal_confidence / 100.0,
                               disposal.CONFIRM_CONF, places=5)

    def test_the_static_default_matches_the_vision_module(self):
        cfg = SystemSettings.load()
        self.assertEqual(float(cfg.disposal_static_seconds), disposal.STATIC_SECONDS)

    def test_the_settings_endpoint_publishes_the_disposal_fields(self):
        from core.serializers import SystemSettingsSerializer
        data = SystemSettingsSerializer(SystemSettings.load()).data
        for field in ("disposal_enabled", "disposal_confidence",
                      "disposal_static_seconds", "disposal_cooldown"):
            self.assertIn(field, data)


class DisposalDescriptionTests(TestCase):
    """The officer-facing wording leads with LEFT BEHIND, not 'bag seen' — that
    is what the ordinance is about and what the static rule established."""

    def test_seconds_are_reported_in_seconds(self):
        text = descriptions.disposal("plastic bag", 8)
        self.assertIn("left behind", text)
        self.assertIn("8 s", text)

    def test_long_dwells_are_reported_in_minutes(self):
        text = descriptions.disposal("sack", 180)
        self.assertIn("3 min", text)

    def test_no_duration_still_reads_as_a_sentence(self):
        text = descriptions.disposal("garbage bag", 0)
        self.assertEqual(text, "Garbage bag left behind and unattended.")

    def test_a_missing_object_name_falls_back_to_garbage(self):
        self.assertTrue(descriptions.disposal(None, 0).startswith("Garbage"))

    def test_no_track_ids_or_scores_leak_into_the_wording(self):
        text = descriptions.disposal("plastic bag", 42)
        for leak in ("#", "track", "conf", "0."):
            self.assertNotIn(leak, text.lower())


class DisposalWiringTests(TestCase):
    def test_the_violation_type_is_seeded_green(self):
        from core.models import ViolationType
        from django.core.management import call_command
        call_command("seed_core", verbosity=0)
        vt = ViolationType.objects.get(code="disposal")
        self.assertEqual(vt.label, "Improper Disposal")
        self.assertEqual(vt.color.lower(), "#22c55e")

    def test_the_command_and_seed_core_agree_on_label_colour_and_icon(self):
        """get_or_create matches on CODE alone, so if watch_disposal's defaults
        and seed_core's row disagree, whichever ran first wins forever and the
        other's label/colour silently never applies."""
        from core.management.commands.seed_core import VIOLATION_TYPES
        seeded = {code: (label, color, icon)
                  for code, label, color, icon in VIOLATION_TYPES}
        self.assertEqual(seeded["disposal"], ("Improper Disposal", "#22c55e", "trash"))

        from core.management.commands import watch_disposal
        source = inspect.getsource(watch_disposal.Command.handle)
        for value in ("Improper Disposal", "#22c55e", "trash"):
            self.assertIn(value, source)

    def test_disposal_is_a_runnable_detection_type(self):
        from core.views import DETECTION_COMMANDS
        self.assertEqual(DETECTION_COMMANDS["disposal"], "watch_disposal")

    def test_disposal_is_never_gated_on_a_drawn_area(self):
        """An area NARROWS where rubbish is watched for; it is never required.

        This originally asserted disposal was not edge-capable at all. An area
        was added because on a 4MP frame the whole-frame rule raises a flood of
        noise blobs, and confining it to the spot that actually gets dumped on
        is the cheapest fix. The half that still matters is EDGE_REQUIRED: a
        run with no area drawn must keep working and watch everywhere, which is
        what disposal did before areas existed.
        """
        from core.views import EDGE_CAPABLE, EDGE_REQUIRED
        self.assertIn("disposal", EDGE_CAPABLE)
        self.assertNotIn("disposal", EDGE_REQUIRED)

    def test_the_command_is_importable_and_declares_its_flags(self):
        from core.management.commands import watch_disposal
        parser = watch_disposal.Command().create_parser("manage.py", "watch_disposal")
        flags = {a.dest for a in parser._actions}
        for expected in ("source", "camera", "confidence", "static_seconds",
                         "dry_run", "stride"):
            self.assertIn(expected, flags)

    def test_the_command_accepts_an_rtsp_source(self):
        """The stale limitation recorded against parking (webcam-only, no
        --source) must not be repeated here."""
        from core.management.commands import watch_disposal
        parser = watch_disposal.Command().create_parser("manage.py", "watch_disposal")
        opts = parser.parse_args(["--source", "rtsp://user:pass@10.0.0.2:554/Streaming/Channels/102"])
        self.assertTrue(opts.source.startswith("rtsp://"))


class PromptCacheTests(TestCase):
    """The prompt cache is purely a speed optimisation, so these assert the two
    things that make it SAFE rather than fast: that editing the prompts cannot
    silently keep using embeddings computed from the old wording, and that a bad
    cache file degrades to the slow path instead of taking the detector down.

    No YOLO-World here — `load_world_model` is driven against a stub ultralytics
    module, because the thing under test is the CACHE DECISION, not inference.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # ---- fingerprinting ----

    def test_the_fingerprint_is_stable_across_calls(self):
        self.assertEqual(disposal.prompt_fingerprint(), disposal.prompt_fingerprint())

    def test_editing_the_prompts_changes_the_cache_file(self):
        """The whole point of keying the filename on the prompts."""
        before = disposal.prompt_cache_path(cache_dir=self.tmp)
        with mock.patch.object(disposal, "PROMPTS", disposal.PROMPTS + ["bin bag"]):
            after = disposal.prompt_cache_path(cache_dir=self.tmp)
        self.assertNotEqual(before.name, after.name)

    def test_editing_the_reject_list_also_changes_the_cache_file(self):
        before = disposal.prompt_cache_path(cache_dir=self.tmp)
        with mock.patch.object(disposal, "REJECT", disposal.REJECT + ["traffic cone"]):
            after = disposal.prompt_cache_path(cache_dir=self.tmp)
        self.assertNotEqual(before.name, after.name)

    def test_different_base_weights_get_different_caches(self):
        a = disposal.prompt_cache_path("yolov8s-worldv2.pt", cache_dir=self.tmp)
        b = disposal.prompt_cache_path("yolov8l-worldv2.pt", cache_dir=self.tmp)
        self.assertNotEqual(a.name, b.name)

    def test_the_cache_path_honours_an_explicit_directory(self):
        path = disposal.prompt_cache_path(cache_dir=self.tmp)
        self.assertEqual(path.parent, pathlib.Path(self.tmp))
        self.assertTrue(path.name.endswith(".pt"))

    def test_the_cache_directory_defaults_under_the_vision_package(self):
        self.assertEqual(disposal.PROMPT_CACHE_DIR.name, "world_cache")
        self.assertEqual(disposal.PROMPT_CACHE_DIR.parent, disposal.VISION_DIR)

    # ---- load_world_model ----

    def _stub_ultralytics(self, loaded):
        """Install a fake `ultralytics.YOLOWorld` that records how it was built.

        `loaded` collects one entry per construction, so a test can tell a cache
        hit (built from the cache path, no set_classes) from a miss (built from
        base weights, then prompted).
        """
        class FakeWorldModel:
            def __init__(self):
                self.clip_model = object()      # the 354 MB CLIP stands in here

        class FakeYOLOWorld:
            def __init__(self, weights):
                self.weights = str(weights)
                self.classes = None
                self.model = FakeWorldModel()
                loaded.append(self)

            def set_classes(self, classes):
                self.classes = list(classes)

            def save(self, path):
                pathlib.Path(path).write_bytes(b"baked")

        module = types.ModuleType("ultralytics")
        module.YOLOWorld = FakeYOLOWorld
        return mock.patch.dict(sys.modules, {"ultralytics": module})

    def test_a_cold_load_prompts_the_model_and_writes_the_cache(self):
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertTrue(disposal.prompt_cache_path("base.pt", self.tmp).exists())
        self.assertEqual(loaded[0].weights, "base.pt")
        self.assertEqual(loaded[0].classes, disposal.PROMPTS + disposal.REJECT)

    def test_clip_is_stripped_before_the_cache_is_written(self):
        """354 MB that only set_classes needs, and the cached path never calls it."""
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertIsNone(loaded[0].model.clip_model)

    def test_a_warm_load_skips_set_classes_entirely(self):
        """The whole saving: no set_classes on a later launch means no CLIP."""
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
            loaded.clear()
            model = disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertEqual(len(loaded), 1)
        self.assertIsNone(model.classes)
        self.assertEqual(pathlib.Path(model.weights),
                         disposal.prompt_cache_path("base.pt", self.tmp))

    def test_an_edited_prompt_list_does_not_reuse_the_old_cache(self):
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
            loaded.clear()
            with mock.patch.object(disposal, "PROMPTS", disposal.PROMPTS + ["bin bag"]):
                expected = disposal.PROMPTS + disposal.REJECT
                disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertEqual(loaded[0].classes, expected)

    def test_an_unreadable_cache_falls_back_and_is_rebuilt(self):
        """A truncated .pt from a process killed mid-save must not be fatal."""
        path = disposal.prompt_cache_path("base.pt", self.tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a torch checkpoint")
        loaded, notes = [], []

        class Boom:
            def __init__(self, weights):
                if str(weights) == str(path):
                    raise RuntimeError("corrupt checkpoint")
                self.weights, self.classes = str(weights), None
                self.model = types.SimpleNamespace(clip_model=object())
                loaded.append(self)

            def set_classes(self, classes):
                self.classes = list(classes)

            def save(self, p):
                pathlib.Path(p).write_bytes(b"baked")

        module = types.ModuleType("ultralytics")
        module.YOLOWorld = Boom
        with mock.patch.dict(sys.modules, {"ultralytics": module}):
            model = disposal.load_world_model("base.pt", cache_dir=self.tmp,
                                              log=notes.append)
        self.assertEqual(model.weights, "base.pt")
        self.assertEqual(model.classes, disposal.PROMPTS + disposal.REJECT)
        self.assertTrue(any("unreadable" in n for n in notes), notes)

    def test_a_cache_that_cannot_be_written_still_returns_a_working_model(self):
        """Read-only media or a full disk: the detector must still run."""
        loaded, notes = [], []
        stub = self._stub_ultralytics(loaded)
        with stub, mock.patch.object(disposal.Path, "mkdir",
                                     side_effect=OSError("read-only")):
            model = disposal.load_world_model("base.pt", cache_dir=self.tmp,
                                              log=notes.append)
        self.assertEqual(model.classes, disposal.PROMPTS + disposal.REJECT)
        self.assertTrue(any("uncached" in n for n in notes), notes)

    def test_the_cache_is_written_atomically(self):
        """No .tmp left behind, so a later launch never reads a partial file."""
        loaded = []
        with self._stub_ultralytics(loaded):
            disposal.load_world_model("base.pt", cache_dir=self.tmp)
        self.assertEqual(list(pathlib.Path(self.tmp).glob("*.tmp")), [])


class AreaTests(TestCase):
    """The watched-area polygon. Geometry only — no frames, no model.

    The anchor is the blob's BOTTOM-CENTRE (where it rests on the ground), so
    these pin that down: judging a bag by its centre would place one at the far
    kerb inside an area drawn on the near one.
    """

    # left half of the frame, full height
    LEFT_HALF = [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]
    SHAPE = (400, 1000, 3)        # h, w, c

    def test_a_polygon_needs_at_least_three_points(self):
        for bad in ([], [(0.0, 0.0)], [(0.0, 0.0), (1.0, 1.0)]):
            with self.assertRaises(ValueError):
                disposal.Area(bad)

    def test_a_blob_inside_the_area_is_contained(self):
        area = disposal.Area(self.LEFT_HALF)
        self.assertTrue(area.contains_box((100, 100, 40, 40), self.SHAPE))

    def test_a_blob_outside_the_area_is_not(self):
        area = disposal.Area(self.LEFT_HALF)
        self.assertFalse(area.contains_box((800, 100, 40, 40), self.SHAPE))

    def test_the_ground_point_decides_not_the_centre(self):
        """A tall blob whose centre sits outside but whose base is inside."""
        area = disposal.Area([(0.0, 0.5), (1.0, 0.5), (1.0, 1.0), (0.0, 1.0)])
        # box top 100, height 140 -> centre y=170 (above the line at y=200),
        # base y=240 (below it). The base is what counts.
        self.assertTrue(area.contains_box((500, 100, 40, 140), self.SHAPE))

    def test_the_polygon_rescales_when_the_frame_size_changes(self):
        """One drawn area has to serve PROC_WIDTH, full res and the live view."""
        area = disposal.Area(self.LEFT_HALF)
        self.assertTrue(area.contains_box((100, 100, 40, 40), (400, 1000, 3)))
        # same relative position in a frame half the size
        self.assertTrue(area.contains_box((50, 50, 20, 20), (200, 500, 3)))
        self.assertFalse(area.contains_box((400, 50, 20, 20), (200, 500, 3)))


class AreaFiltersCandidatesTests(TestCase):
    """Stage 1 with an area: an out-of-area object must never become a
    candidate at all, rather than being created and discarded later."""

    def _run(self, x, area=None):
        det = disposal.StaticObjectDetector(area=area)
        now = warm_up(det)
        for _ in range(40):
            det.update(with_object(scene(), x, 150), now)
            now += 0.1
        return det

    def test_without_an_area_an_object_anywhere_is_a_candidate(self):
        self.assertTrue(self._run(500).candidates)

    def test_an_object_inside_the_area_is_still_found(self):
        area = disposal.Area([(0.0, 0.0), (0.4, 0.0), (0.4, 1.0), (0.0, 1.0)])
        self.assertTrue(self._run(40, area).candidates)

    def test_an_object_outside_the_area_never_becomes_a_candidate(self):
        """Not merely unconfirmed — never created, so it mints no id, accrues
        no still-time and never reaches the zero-shot check."""
        area = disposal.Area([(0.0, 0.0), (0.4, 0.0), (0.4, 1.0), (0.0, 1.0)])
        det = self._run(500, area)
        self.assertEqual(det.candidates, [])
        self.assertEqual(det.due_for_check(999.0, 1.0), [])

    def test_the_detector_defaults_to_no_area(self):
        """No area drawn must keep the pre-area behaviour: watch everything."""
        self.assertIsNone(disposal.StaticObjectDetector().area)


class CoverageGeometryTests(TestCase):
    """How much of a blob sits inside a person/vehicle box."""

    def test_a_blob_fully_inside_is_fully_covered(self):
        self.assertAlmostEqual(
            disposal._coverage((10, 10, 20, 20), (0, 0, 100, 100)), 1.0)

    def test_a_blob_entirely_outside_is_not_covered(self):
        self.assertAlmostEqual(
            disposal._coverage((200, 200, 20, 20), (0, 0, 100, 100)), 0.0)

    def test_a_half_overlapping_blob_is_half_covered(self):
        # blob x 90..110, box ends at 100 -> half its width inside
        self.assertAlmostEqual(
            disposal._coverage((90, 10, 20, 20), (0, 0, 100, 100)), 0.5)

    def test_a_zero_area_blob_does_not_divide_by_zero(self):
        self.assertEqual(disposal._coverage((10, 10, 0, 0), (0, 0, 100, 100)), 0.0)

    def test_padding_grows_the_box_around_its_centre(self):
        x, y, w, h = disposal._padded((100, 100, 100, 100), pad=0.1)
        self.assertEqual((x, y), (90, 90))
        self.assertEqual((w, h), (120, 120))


class IgnoreMoversTests(TestCase):
    """A person who stands still must never get a candidate id.

    Stage 1 asks "did something arrive and stop moving?", and a stationary
    person answers yes — measured at 19 of 37 candidates on real footage. The
    suppression therefore has to happen at CREATION, not at confirmation.
    """

    OBJ = dict(x=300, y=180, w=60, h=45)

    def _run(self, ignore_boxes=None, frames=14):
        det = disposal.StaticObjectDetector()
        now = warm_up(det)
        for _ in range(frames):
            det.update(with_object(scene(), self.OBJ["x"], self.OBJ["y"]),
                       now, ignore_boxes=ignore_boxes)
            now += 0.1
        return det

    def test_without_ignore_boxes_the_object_is_a_candidate(self):
        """The baseline this must not disturb."""
        self.assertTrue(self._run().candidates)

    def test_a_blob_inside_a_person_box_never_becomes_a_candidate(self):
        box = (self.OBJ["x"] - 20, self.OBJ["y"] - 20,
               self.OBJ["w"] + 40, self.OBJ["h"] + 40)
        det = self._run(ignore_boxes=[box])
        self.assertEqual(det.candidates, [])

    def test_a_suppressed_blob_is_recorded_for_the_live_view(self):
        """Seen and passed over, not invisible — the Testing view shows these
        so a filter over the wrong thing cannot look like a blind detector."""
        box = (self.OBJ["x"] - 20, self.OBJ["y"] - 20,
               self.OBJ["w"] + 40, self.OBJ["h"] + 40)
        det = self._run(ignore_boxes=[box])
        self.assertTrue(det.ignored)

    def test_a_box_somewhere_else_suppresses_nothing(self):
        det = self._run(ignore_boxes=[(10, 10, 40, 40)])
        self.assertTrue(det.candidates)
        self.assertEqual(det.ignored, [])

    def test_an_empty_box_list_behaves_like_no_filter(self):
        self.assertTrue(self._run(ignore_boxes=[]).candidates)

    def test_ignored_resets_when_the_filter_is_switched_off(self):
        """Toggling disposal_ignore_movers off mid-run must not leave a stale
        suppression list feeding the live view."""
        box = (self.OBJ["x"] - 20, self.OBJ["y"] - 20,
               self.OBJ["w"] + 40, self.OBJ["h"] + 40)
        det = self._run(ignore_boxes=[box])
        self.assertTrue(det.ignored)
        det.update(with_object(scene(), self.OBJ["x"], self.OBJ["y"]), 99.0)
        self.assertEqual(det.ignored, [])


class DisposalSettingsTests(TestCase):
    """The tuning knobs are editable in Settings rather than hardcoded."""

    KEYS = [
        "disposal_enabled", "disposal_confidence", "disposal_static_seconds",
        "disposal_cooldown", "disposal_cooldown_dist", "disposal_min_area",
        "disposal_max_area", "disposal_warmup_seconds",
        "disposal_recheck_seconds", "disposal_ignore_movers",
    ]

    def test_every_knob_exists_on_the_settings_row(self):
        cfg = SystemSettings.load()
        for key in self.KEYS:
            self.assertTrue(hasattr(cfg, key), key)

    def test_the_defaults_match_the_module_constants(self):
        """A default that drifts from the CV module's constant means the
        dashboard and a bare CLI run quietly disagree."""
        cfg = SystemSettings.load()
        self.assertAlmostEqual(cfg.disposal_min_area / 100.0, disposal.MIN_AREA_FRAC)
        self.assertAlmostEqual(cfg.disposal_max_area / 100.0, disposal.MAX_AREA_FRAC)
        self.assertEqual(float(cfg.disposal_warmup_seconds), disposal.WARMUP_SECONDS)
        self.assertEqual(float(cfg.disposal_recheck_seconds), disposal.RECHECK_SECONDS)
        self.assertEqual(float(cfg.disposal_cooldown_dist), disposal.COOLDOWN_DIST)
        self.assertEqual(float(cfg.disposal_static_seconds), disposal.STATIC_SECONDS)
        self.assertEqual(float(cfg.disposal_cooldown), disposal.COOLDOWN_SECONDS)

    def test_the_api_exposes_every_knob(self):
        from core.serializers import SystemSettingsSerializer
        fields = set(SystemSettingsSerializer.Meta.fields)
        for key in self.KEYS:
            self.assertIn(key, fields, key)

    def test_the_size_bounds_are_a_percent_not_a_fraction(self):
        """0.08 means 0.08% of the view. Storing the fraction instead would be
        a hundredfold change that still looks plausible in the box."""
        cfg = SystemSettings.load()
        self.assertEqual(cfg.disposal_min_area, 0.08)
        self.assertEqual(cfg.disposal_max_area, 8.0)

    def test_the_detector_accepts_the_configured_bounds(self):
        det = disposal.StaticObjectDetector(min_area_frac=0.002, max_area_frac=0.5,
                                            warmup_seconds=9.0)
        self.assertEqual(det.min_area_frac, 0.002)
        self.assertEqual(det.max_area_frac, 0.5)
        self.assertEqual(det.warmup_seconds, 9.0)
