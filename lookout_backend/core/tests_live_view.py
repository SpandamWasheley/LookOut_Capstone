import json
import os
import tempfile

import numpy as np
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core import debug_state
from core.models import Camera
from core.monitor import has_road_edge, monitor
from core.vision import debug_view, scoring


class PublisherTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pub = debug_view.DebugPublisher(self.tmp.name, interval=0.0)
        self.frame = np.full((720, 1280, 3), 40, np.uint8)

    def test_publishes_a_clean_frame_and_every_tracked_subject_including_below_monitoring(self):
        s = scoring.Score("smoking", scoring.SMOKING_WEIGHTS, {"cigarette", "gesture"}, object_on=True)
        self.pub.stash(self.frame)
        self.pub.note(("smoke", 1), "Smoking", 1, (10, 20, 110, 220), debug_view.status_for(s), 60,
                      debug_view.indicator_list(s), {}, 2.4)
        self.pub.note(("smoke", 2), "Smoking", 2, (300, 20, 400, 220), debug_view.status_for(None), 0, [], {}, 0.6)
        self.pub.commit(12.0)
        state = json.load(open(os.path.join(self.tmp.name, "state.json")))
        self.assertEqual({e["status"] for e in state["subjects"]}, {"Possible", "Below Monitoring"})
        first = [e for e in state["subjects"] if e["id"] == 1][0]
        self.assertEqual(first["score"], 60)
        self.assertEqual(first["indicators"][0], {"name": "cigarette", "points": 40})
        self.assertEqual((state["frame_w"], state["frame_h"]), (1280, 720))
        self.assertTrue(os.path.getsize(os.path.join(self.tmp.name, "frame.jpg")) > 1000)

    def test_status_change_time_only_moves_when_the_status_changes(self):
        self.pub.note(("k", 1), "Holdup", 1, None, "Monitoring", 45, [], {}, 1.6)
        t1 = self.pub._entries[("k", 1)]["changed"]
        self.pub.note(("k", 1), "Holdup", 1, None, "Monitoring", 45, [], {}, 1.7)
        self.assertEqual(self.pub._entries[("k", 1)]["changed"], t1)
        self.pub.note(("k", 1), "Holdup", 1, None, "Possible", 65, [], {}, 1.9)
        self.assertGreaterEqual(self.pub._entries[("k", 1)]["changed"], t1)

    def test_no_frame_is_copied_between_publishes(self):
        pub = debug_view.DebugPublisher(self.tmp.name, interval=60.0)
        pub._last_pub = __import__("time").monotonic()
        pub.stash(self.frame)
        self.assertIsNone(pub._clean)

    def test_payload_skips_a_frame_the_page_already_has(self):
        self.pub.stash(self.frame)
        self.pub.commit(1.0)
        first = debug_state.payload(self.tmp.name)
        self.assertIn("frame", first)
        again = debug_state.payload(self.tmp.name, since=first["seq"])
        self.assertNotIn("frame", again)
        self.assertEqual(debug_state.payload(os.path.join(self.tmp.name, "nope")), {"available": False})


class MonitorTests(TestCase):
    def test_a_stopped_monitor_says_so_and_asks_for_the_road_edge(self):
        Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision", stream_url="rtsp://x")
        st = monitor.status()
        self.assertEqual(st["state"], "stopped")
        self.assertEqual(st["label"], "Detection stopped")
        self.assertIn("Draw the road edge for parking", st["notice"])

    def test_road_edge_detection(self):
        # The shapes EdgeCanvas actually saves: {"points": [...], "side": n} for
        # a kerb line, and {"type": "zone", "points": [...]} for a polygon. This
        # used to assert on a BARE list of points, a shape nothing writes --
        # _load_edges would raise AttributeError calling .get on it -- which the
        # old truthiness test happened to accept. See core/tests_has_road_edge.py
        # for the full matrix.
        cam = Camera(code="C", name="c", edges={})
        self.assertFalse(has_road_edge(cam))
        cam.edges = {"left": {"points": [[0, 0], [10, 10]], "side": 1}, "right": {}}
        self.assertTrue(has_road_edge(cam))
        cam.edges = {"road": {"type": "zone", "points": [[0, 0], [10, 0], [10, 10]]}}
        self.assertTrue(has_road_edge(cam))

    def test_monitor_endpoints_are_admin_only(self):
        officer = get_user_model().objects.create_user(username="o1", password="x", role="officer")
        c = APIClient()
        c.force_authenticate(officer)
        for url in ("/api/monitor/", "/api/monitor/state/"):
            self.assertEqual(c.get(url).status_code, 403)
        admin = get_user_model().objects.create_user(username="a1", password="x", role="admin")
        c.force_authenticate(admin)
        self.assertEqual(c.get("/api/monitor/").json()["state"], "stopped")
        self.assertEqual(c.get("/api/monitor/state/").json()["available"], False)
