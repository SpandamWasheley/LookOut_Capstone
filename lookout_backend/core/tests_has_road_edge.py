"""has_road_edge: does this camera have an area worth running parking against?

The live monitor launches watch_parking alongside watch_merged only when this
is true. It used to test the left/right keys alone — correct while the editor
drew kerb lines, and silently wrong once the editor became zone-only: a camera
with a perfectly good polygon answered False and parking was quietly dropped
from the run. No error, no log line, just three violations where four were
configured.
"""
from django.test import TestCase

from core.models import Camera
from core.monitor import has_road_edge


def camera(edges):
    return Camera(code="CAM-X", name="X", edges=edges)


class HasRoadEdgeTests(TestCase):
    def test_a_road_zone_counts(self):
        self.assertTrue(has_road_edge(camera({
            "road": {"type": "zone", "points": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        })))

    def test_a_zone_needs_three_points(self):
        # Two points is a line, not an area; cv2.pointPolygonTest would accept
        # it and answer nonsense.
        self.assertFalse(has_road_edge(camera({
            "road": {"type": "zone", "points": [[0, 0], [10, 0]]},
        })))

    def test_legacy_kerb_lines_still_count(self):
        # Cameras configured before the editor became zone-only must keep
        # working — watch_merged_all still drives the edge rule.
        self.assertTrue(has_road_edge(camera({
            "left": {"points": [[0, 0], [10, 10]], "side": 1},
        })))

    def test_an_edge_needs_two_points(self):
        self.assertFalse(has_road_edge(camera({"left": {"points": [[0, 0]], "side": 1}})))

    def test_a_zone_under_any_key_name_counts(self):
        # The shape is identified by "type", not by the key, so a spec saved
        # under some other name still works.
        self.assertTrue(has_road_edge(camera({
            "whatever": {"type": "zone", "points": [[0, 0], [5, 0], [5, 5]]},
        })))

    def test_nothing_drawn_is_false(self):
        self.assertFalse(has_road_edge(camera({})))
        self.assertFalse(has_road_edge(camera(None)))

    def test_junk_does_not_raise(self):
        # edges is a free-form JSONField; anything could be in there.
        self.assertFalse(has_road_edge(camera("not a dict")))
        self.assertFalse(has_road_edge(camera({"road": "not a dict"})))
        self.assertFalse(has_road_edge(camera({"road": {"points": None}})))

    def test_one_usable_shape_among_junk_is_enough(self):
        self.assertTrue(has_road_edge(camera({
            "left": {"points": []},
            "road": {"type": "zone", "points": [[0, 0], [5, 0], [5, 5]]},
        })))
