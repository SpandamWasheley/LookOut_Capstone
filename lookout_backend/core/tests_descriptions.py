import re

from django.test import SimpleTestCase

from core import descriptions as d


class DescriptionTests(SimpleTestCase):
    def test_wording(self):
        self.assertEqual(d.holdup("knife", True), "Knife detected on a person, with a second person nearby.")
        self.assertEqual(d.holdup("knife", False), "Knife detected on a person.")
        self.assertEqual(d.holdup("knife, Cigarette"), "Knife detected on a person.")
        self.assertEqual(d.smoking("Cigarette"), "Cigarette detected on a person.")
        self.assertEqual(d.smoking(None, 3), "Hand-to-mouth smoking movement seen on a person.")
        self.assertEqual(d.drinking("Bottle", True), "Bottle detected on a person, held up to the mouth.")
        self.assertEqual(d.gathering(4, 90), "Drinking gathering of 4 people, together for about 2 min.")
        self.assertEqual(d.parking("car", 0), "Car stopped in a no-parking area.")

    def test_no_internal_identifiers(self):
        samples = [d.smoking("Cigarette", 2), d.drinking("Bottle", False), d.gathering(3, 30),
                   d.holdup("knife", True), d.parking("truck", 130), d.road_edge("left", 0.4, 2.5)]
        for text in samples:
            self.assertNotRegex(text, r"#\d|CAM-|track|status|Status")


class ObservationTests(SimpleTestCase):
    REPLY = {"smoking_item_visible": True, "hand_to_mouth_activity": "smoking", "confidence": "high"}

    def _obs(self, text):
        from core.vision import ai_status
        out = ai_status.validate_reply("smoking", dict(self.REPLY, observations=text))
        return out["observations"] if out else None

    def test_short_sentence_is_kept_whole(self):
        self.assertEqual(self._obs("A man holds a cigarette near his mouth."), "A man holds a cigarette near his mouth.")

    def test_a_long_sentence_is_kept_whole(self):
        # It used to be cut to 20 words HERE, before store_ai_result wrote the
        # row — so the clipped version was the only copy kept. The observation
        # is the one thing the checker produces that a person reads; shortening
        # it is a decision for a card's CSS, not for the validator.
        long = " ".join(f"word{i}" for i in range(30))
        out = self._obs(long)
        self.assertEqual(len(out.split()), 30)
        self.assertTrue(out.endswith("word29"), out)
        self.assertNotIn("…", out)

    def test_wrapped_whitespace_is_collapsed(self):
        # The models return sentences wrapped across lines; a newline inside a
        # quoted sentence renders as a gap in the card.
        self.assertEqual(self._obs("A man\n  holds  a\tbottle."),
                         "A man holds a bottle.")
