import json

import numpy as np
from django.test import SimpleTestCase, TestCase

from core.vision import ai_checker as A


def _jpg(w=400, h=300):
    import cv2
    ok, buf = cv2.imencode(".jpg", np.full((h, w, 3), 90, np.uint8))
    return buf.tobytes()


class PromptTests(SimpleTestCase):
    def test_note_wording_matches_spec_examples(self):
        self.assertEqual(A.system_note("drinking", people=3, minutes=12, stationary=True),
                         "bottle detected; 3 people stationary together for 12 minutes")
        self.assertEqual(A.system_note("smoking", puffs=2), "smoking item detected; hand reached the mouth 2 times")
        self.assertEqual(A.system_note("smoking", puff_only=True, puffs=3),
                         "hand reached the mouth 3 times; no smoking item detected")
        self.assertEqual(A.system_note("holdup", people=2), "knife detected; two people standing still close together")

    def test_messages_fill_note_and_carry_images(self):
        m = A.build_messages("smoking", "smoking item detected", ["aaa", "bbb"])
        self.assertEqual(len(m), 1)
        self.assertIn("System detections (from the object and pose models): smoking item detected", m[0]["content"])
        self.assertIn("hand_to_mouth_activity", m[0]["content"])
        self.assertEqual(m[0]["images"], ["aaa", "bbb"])

    def test_schema_requires_exactly_the_validated_fields(self):
        from core.vision import ai_status
        for kind in A.KINDS:
            self.assertEqual(set(A.response_schema(kind)["required"]), set(ai_status.schema_fields(kind)))
        # v6.2.4: holdup is also asked for people_count (an integer), optional in a STORED reply so old alerts stay valid
        props = A.response_schema("holdup")["properties"]
        self.assertEqual(props["people_count"]["type"], "integer")
        self.assertNotIn("people_count", ai_status.REQUIRED_FIELDS["holdup"])

    def test_parse_reply_rejects_invalid(self):
        good = {"observations": "a man stands", "smoking_item_visible": False,
                "hand_to_mouth_activity": "unclear", "confidence": "low"}
        self.assertIsNotNone(A.parse_reply("smoking", json.dumps(good)))
        self.assertIsNone(A.parse_reply("smoking", "not json"))
        self.assertIsNone(A.parse_reply("smoking", json.dumps({**good, "hand_to_mouth_activity": "vaping"})))
        self.assertIsNone(A.parse_reply("smoking", json.dumps({k: v for k, v in good.items() if k != "confidence"})))

    def test_kind_mapping(self):
        self.assertEqual(A.kind_for("thief"), "holdup")
        self.assertEqual(A.kind_for("smoking"), "smoking")
        self.assertIsNone(A.kind_for("parking"))


class FramesTests(SimpleTestCase):
    def _window(self, step=0.2, span=6.0, objects=None):
        """Ring-like items every `step` s over `span` s; `objects` maps time -> object confidence."""
        out = []
        for i in range(int(span / step)):
            t = round(i * step, 3)
            note = {("obj", ("track", 1)): objects[t]} if objects and t in objects else {}
            out.append((t, b"", note))
        return out

    def test_selection_covers_whole_window_and_includes_trigger_frame(self):
        items = self._window()
        picked = A.select_frames(items, 5.0, n=8, keys=[("track", 1)])
        times = [p[0] for p in picked]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), 8)
        self.assertIn(5.0, times)
        self.assertGreater(times[-1] - times[0], 4.0)               # not bunched around t0
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertLess(max(gaps), 2.5)                              # and no big hole

    def test_object_frames_preferred_by_confidence_and_spaced(self):
        # cigarette seen 1.0-1.6 (dense, 0.2 s apart) and at 3.0; 1.2 is the most confident
        objs = {1.0: 0.4, 1.2: 0.9, 1.4: 0.5, 1.6: 0.45, 3.0: 0.6}
        picked = A.select_frames(self._window(objects=objs), 5.0, n=8, keys=[("track", 1)])
        times = [p[0] for p in picked]
        object_times = [t for t in times if t in objs]
        self.assertIn(1.2, times)
        self.assertIn(3.0, times)
        self.assertLessEqual(len(object_times), 4)                   # at most n/2
        for a, b in zip(object_times, object_times[1:]):
            self.assertGreaterEqual(b - a, A.OBJECT_FRAME_GAP - 1e-9)
        self.assertIn(5.0, times)

    def test_object_frames_of_other_subjects_do_not_count(self):
        items = [(t, b"", {("obj", ("track", 2)): 0.9} if 1.0 <= t <= 1.4 else {}) for t, _, _ in self._window()]
        times = [p[0] for p in A.select_frames(items, 5.0, n=8, keys=[("track", 1)])]
        self.assertEqual(times, [p[0] for p in A.select_frames(self._window(), 5.0, n=8, keys=[("track", 1)])])

    def test_few_frames_are_all_sent(self):
        items = self._window(step=0.5, span=2.0)                     # 4 frames
        self.assertEqual([p[0] for p in A.select_frames(items, 1.0, n=8)], [0.0, 0.5, 1.0, 1.5])
        self.assertEqual(A.select_frames([], 1.0), [])

    def test_fewer_object_frames_than_quota_fills_with_spread(self):
        picked = A.select_frames(self._window(objects={2.0: 0.7}), 5.0, n=8, keys=[("track", 1)])
        times = [p[0] for p in picked]
        self.assertEqual(len(times), 8)
        self.assertIn(2.0, times)
        self.assertIn(5.0, times)

    def test_switch_off_restores_nearest_to_trigger(self):
        old = A.AI_SPREAD_FRAMES
        A.AI_SPREAD_FRAMES = False
        try:
            times = [p[0] for p in A.select_frames(self._window(), 3.0, n=8)]
        finally:
            A.AI_SPREAD_FRAMES = old
        self.assertEqual(len(times), 8)
        self.assertTrue(all(abs(t - 3.0) <= 0.8 for t in times))

    def test_ring_records_object_confidence_per_frame(self):
        ring = A.FrameRing(seconds=7.0, min_gap=0.0)
        frame = np.zeros((300, 400, 3), np.uint8)
        ring.note_object(("track", 1), 0.62)
        ring.add(frame, 1.0)
        ring.note_object(("track", 1), 0.0)                          # not detected: nothing recorded
        ring.add(frame, 2.0)
        self.assertEqual(A.object_conf(ring._items[0][2], [("track", 1)]), 0.62)
        self.assertEqual(A.object_conf(ring._items[1][2], [("track", 1)]), 0.0)
        self.assertEqual(A.object_conf(ring._items[0][2], [("track", 9)]), 0.0)

    def test_window_covers_five_seconds_before_and_one_after(self):
        self.assertEqual((A.FRAMES_BEFORE, A.FRAMES_AFTER), (5.0, 1.0))
        self.assertGreater(A.RING_SECONDS, A.FRAMES_BEFORE + A.FRAMES_AFTER)

    def test_ring_stores_boxes_and_expires_old_frames(self):
        ring = A.FrameRing(seconds=2.0, min_gap=0.0)
        frame = np.zeros((300, 400, 3), np.uint8)
        for i in range(10):
            ring.note_box(("track", 1), (10, 10, 50, 100))
            ring.add(frame, i * 0.5)
        self.assertLessEqual(len(ring), 5)
        self.assertEqual(ring._items[-1][2][("track", 1)], (10, 10, 50, 100))

    def test_crop_shapes_by_violation(self):
        shape = (1440, 2560, 3)
        person = (1000, 400, 1100, 700)
        smoking = A.crop_box("smoking", [person], shape)
        holdup = A.crop_box("holdup", [person, (1150, 420, 1250, 700)], shape)
        self.assertLess(smoking[3] - smoking[1], (person[3] - person[1]) * 1.2)     # half body, not the legs
        self.assertGreater(holdup[2] - holdup[0], 250)                                # both people
        self.assertIsNone(A.crop_box("smoking", [], shape))

    def test_crop_is_cut_from_full_res_then_resized(self):
        img = A.crop_for("drinking", _jpg(2000, 1000), [(500, 300, 1500, 800)], edge=640)
        self.assertEqual(max(img.shape[:2]), 640)

    def test_prepare_images_holds_last_box_when_subject_missing(self):
        jpg = _jpg()
        items = [(0, jpg, {("p", 1): (50, 50, 150, 250)}), (1, jpg, {}), (2, jpg, {("p", 1): (60, 50, 160, 250)})]
        self.assertEqual(len(A.prepare_images("smoking", items, [("p", 1)])), 3)


class FakeClient:
    model = "fake"

    def __init__(self, text=None, exc=None):
        self.text, self.exc = text, exc

    def chat(self, kind, note, images):
        if self.exc:
            raise self.exc
        return self.text, 1.5, {}


class CheckTests(SimpleTestCase):
    def test_check_never_raises_and_reports_unavailable(self):
        r = A.check(FakeClient(exc=TimeoutError("slow")), "smoking", "n", [b"x"])
        self.assertFalse(r.ok)
        self.assertIn("TimeoutError", r.error)
        self.assertFalse(A.check(FakeClient(text="garbage"), "smoking", "n", [b"x"]).ok)
        self.assertFalse(A.check(FakeClient(text="{}"), "smoking", "n", []).ok)

    def test_check_valid(self):
        text = json.dumps({"observations": "one two", "smoking_item_visible": True,
                           "hand_to_mouth_activity": "smoking", "confidence": "high"})
        r = A.check(FakeClient(text=text), "smoking", "n", [b"x"])
        self.assertTrue(r.ok)
        self.assertEqual(r.as_dict()["reply"]["hand_to_mouth_activity"], "smoking")


class NoteHonestyTests(SimpleTestCase):
    def test_a_group_is_only_called_stationary_when_it_earned_the_gathering_cue(self):
        self.assertEqual(A.system_note("drinking", people=3, minutes=0.0),
                         "bottle detected; 3 people close together")
        self.assertEqual(A.system_note("drinking", people=3, minutes=0.5, stationary=True),
                         "bottle detected; 3 people stationary together")


class CardOmittedWhenThereIsNoCheckerTests(TestCase):
    """`ai_context` is None for a violation the checker does not cover.

    None is not the same as "unavailable". Unavailable means a check was
    expected and produced no answer — the reader is entitled to wonder where
    the AI's opinion went, so the card says so. Parking has no checker at all:
    its question is a measurement, already answered exactly by geometry. A card
    reading "AI context unavailable" on every parking alert advertises a
    missing feature that was never meant to be there, so the field is omitted
    and the clients draw nothing.
    """

    def setUp(self):
        from django.utils import timezone
        from core.models import Alert, Camera, ViolationType
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision")
        self.now = timezone.now()
        self.Alert = Alert
        self.vtype = lambda code: ViolationType.objects.get_or_create(
            code=code, defaults={"label": code.title(), "color": "#000", "icon": "x"})[0]

    def _context(self, code, cues):
        from core.serializers import AlertSerializer
        alert = self.Alert.objects.create(type=self.vtype(code), camera=self.cam,
                                          confidence=0.6, timestamp=self.now, cues=cues)
        return AlertSerializer(alert).data["ai_context"]

    def test_parking_gets_no_ai_card(self):
        # watch_parking writes no "kind" into cues, because it does no scoring.
        self.assertIsNone(self._context("parking", {}))

    def test_a_covered_violation_still_gets_the_card_even_with_no_check(self):
        # A smoking alert whose check failed or never ran MUST keep the card:
        # "unavailable" there is real information about a real gap.
        ctx = self._context("smoking", {"kind": "smoking"})
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["state"], "unavailable")

    def test_thief_is_recognised_as_holdup_and_keeps_its_card(self):
        # kind_for maps thief/knife/weapon onto "holdup"; a mismatch here would
        # silently strip the cards off every theft alert.
        self.assertIsNotNone(self._context("theft", {"kind": "thief"}))
