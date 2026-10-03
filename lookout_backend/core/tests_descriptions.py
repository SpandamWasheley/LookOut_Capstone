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
