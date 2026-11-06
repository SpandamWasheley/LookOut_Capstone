"""Camera.edges holds two areas -- parking's road and disposal's rubbish area.

Three promises, each of which fails silently if broken:

  * a "disposal" entry is never read as a road (it would launch parking with
    nothing to judge against);
  * writing one area never deletes the other;
  * an area a person saved or edited is never replaced by a trace -- tracing
    runs only when no road is saved.
"""
from io import StringIO
from unittest import mock

import numpy as np
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core import zones
from core.management.commands.watch_parking import Command as ParkingCommand
from core.models import Camera
from core.monitor import CAMERA_CODE, has_road_edge

ROAD = {"road": {"type": "zone", "points": [[0, 0], [100, 0], [100, 50]]}}
BIN = {"disposal": {"type": "disposal", "points": [[10, 10], [50, 10], [50, 40]]}}


class GuardTests(TestCase):
    """A disposal entry is never treated as a road."""

    def test_disposal_alone_is_not_a_road(self):
        self.assertFalse(zones.has_road(BIN))
        self.assertTrue(zones.has_disposal(BIN))

    def test_has_road_edge_ignores_a_disposal_only_camera(self):
        # The live monitor launches parking only when this is True.
        self.assertFalse(has_road_edge(Camera(code=CAMERA_CODE, name="c", edges=BIN)))

    def test_road_plus_disposal_is_a_road(self):
        both = {**ROAD, **BIN}
        self.assertTrue(has_road_edge(Camera(code=CAMERA_CODE, name="c", edges=both)))

    def test_legacy_open_kerb_still_counts_as_a_road(self):
        kerb = {"left": {"points": [[0, 0], [10, 10]]}}
        self.assertTrue(zones.has_road(kerb))

    def test_empty_and_malformed_edges_are_no_road(self):
        for bad in (None, {}, [], "x"):
            self.assertFalse(zones.has_road(bad))

    def test_parking_zone_reader_ignores_the_disposal_entry(self):
        cam = Camera(code=CAMERA_CODE, name="c", edges=BIN, edges_width=100, edges_height=50)
        cmd = ParkingCommand()
        cmd.camera, cmd.stdout = cam, StringIO()
        self.assertIsNone(cmd._zone_points_from_camera())

    def test_with_road_keeps_disposal_and_with_disposal_keeps_road(self):
        both = {**ROAD, **BIN}
        newer = {"road": {"type": "zone", "points": [[1, 1], [2, 2], [3, 3]]}}
        self.assertEqual(zones.with_road(both, newer), {**newer, **BIN})
        self.assertEqual(zones.with_disposal(both, {}), ROAD)
        # A disposal entry smuggled in through the "road" argument is dropped.
        self.assertEqual(zones.with_road(ROAD, {**newer, **BIN}), newer)

    def test_rescaled_carries_the_other_area_to_a_new_frame_size(self):
        out = zones.rescaled(BIN, (100, 50), (200, 100))
        self.assertEqual(out["disposal"]["points"], [[20, 20], [100, 20], [100, 80]])
        self.assertEqual(BIN["disposal"]["points"][0], [10, 10])      # input untouched
        self.assertEqual(zones.rescaled(BIN, (None, None), (200, 100)), BIN)


def _admin():
    return get_user_model().objects.create_user(
        username="adm", email="a@x.ph", password="pw", role="admin")


class SavingOneAreaRestartsOnlyThatDetector(TestCase):
    def setUp(self):
        self.cam = Camera.objects.create(
            code=CAMERA_CODE, name="C", edges={**ROAD, **BIN}, edges_width=100, edges_height=50)
        self.client = APIClient()
        self.client.force_authenticate(_admin())

    def _patch(self, edges, **extra):
        with mock.patch("core.monitor.monitor.apply_zone", return_value="restarting") as pz, \
                mock.patch("core.monitor.monitor.apply_disposal_zone",
                           return_value="restarting") as pd:
            res = self.client.patch(f"/api/cameras/{self.cam.pk}/",
                                    {"edges": edges, "edges_width": 100, "edges_height": 50, **extra},
                                    format="json")
        self.assertEqual(res.status_code, 200, res.content)
        return res.data, pz, pd

    def test_editing_disposal_leaves_parking_alone(self):
        moved = {"disposal": {"type": "disposal", "points": [[20, 20], [60, 20], [60, 45]]}}
        data, pz, pd = self._patch({**ROAD, **moved})
        pd.assert_called_once()
        pz.assert_not_called()
        self.assertIn("disposal_zone_applied", data)
        self.assertNotIn("zone_applied", data)
        self.cam.refresh_from_db()
        self.assertEqual(zones.road_entries(self.cam.edges), ROAD)          # untouched
        self.assertEqual(zones.disposal_entries(self.cam.edges), moved)

    def test_editing_parking_leaves_disposal_alone(self):
        moved = {"road": {"type": "zone", "points": [[5, 5], [90, 5], [90, 45]]}}
        data, pz, pd = self._patch({**moved, **BIN})
        pz.assert_called_once()
        pd.assert_not_called()
        self.assertIn("zone_applied", data)
        self.assertNotIn("disposal_zone_applied", data)
        self.cam.refresh_from_db()
        self.assertEqual(zones.disposal_entries(self.cam.edges), BIN)

    def test_resaving_unchanged_areas_restarts_nothing(self):
        _data, pz, pd = self._patch({**ROAD, **BIN})
        pz.assert_not_called()
        pd.assert_not_called()

    def test_clearing_disposal_keeps_the_road(self):
        data, pz, pd = self._patch(ROAD)
        pd.assert_called_once()
        pz.assert_not_called()
        self.cam.refresh_from_db()
        self.assertEqual(self.cam.edges, ROAD)


TRACED = [[20, 10], [90, 10], [90, 45], [20, 45]]


def _options(cmd):
    return vars(cmd.create_parser("manage.py", "watch_parking").parse_args([]))


class EditedTraceSurvivesARerun(TestCase):
    """Trace -> person edits it -> the next run keeps the person's version."""

    def _run(self, camera):
        cmd = ParkingCommand()
        cmd.camera, cmd.stdout, cmd.auto_zone = camera, StringIO(), True
        options = {**_options(cmd), "source": "clip.mp4"}
        report = {"samples": 5, "detections": 9, "moving_tracks": 3, "tracks": 4,
                  "ground_points": 40}
        frame = np.zeros((50, 100, 3), dtype=np.uint8)
        with mock.patch("core.management.commands.watch_parking.autodetect.trace_clip",
                        return_value=(TRACED, report, frame)) as trace:
            zone, where = cmd._load_zone(options)
        return zone, where, trace

    def test_trace_saves_then_user_edit_then_rerun_keeps_the_edit(self):
        cam = Camera.objects.create(code=CAMERA_CODE, name="C")

        # 1. Nothing saved: the run traces one and stores it on the camera.
        _zone, where, trace = self._run(cam)
        trace.assert_called_once()
        self.assertIn("auto-traced", where)
        cam.refresh_from_db()
        self.assertEqual(cam.edges["road"]["points"], TRACED)

        # 2. The person does not like it and drags it elsewhere (the editor's save).
        mine = {"road": {"type": "zone", "points": [[1, 1], [60, 1], [60, 30]]}}
        client = APIClient()
        client.force_authenticate(_admin())
        res = client.patch(f"/api/cameras/{cam.pk}/",
                           {"edges": mine, "edges_width": 100, "edges_height": 50}, format="json")
        self.assertEqual(res.status_code, 200, res.content)

        # 3. Running again reads THEIR polygon and never calls the tracer.
        cam.refresh_from_db()
        _zone, where, trace = self._run(cam)
        trace.assert_not_called()
        self.assertIn(f"camera {CAMERA_CODE}", where)
        cam.refresh_from_db()
        self.assertEqual(cam.edges, mine)

    def test_a_disposal_only_camera_still_gets_a_road_traced_and_keeps_its_area(self):
        cam = Camera.objects.create(code=CAMERA_CODE, name="C", edges=BIN,
                                    edges_width=100, edges_height=50)
        _zone, _where, trace = self._run(cam)
        trace.assert_called_once()             # a disposal area is not a road
        cam.refresh_from_db()
        self.assertEqual(cam.edges["road"]["points"], TRACED)
        self.assertEqual(zones.disposal_entries(cam.edges), BIN)

    def test_a_trace_at_a_new_frame_size_rescales_the_disposal_area(self):
        cam = Camera.objects.create(code=CAMERA_CODE, name="C", edges=BIN,
                                    edges_width=50, edges_height=25)    # drawn at half size
        self._run(cam)                                                  # traced at 100x50
        cam.refresh_from_db()
        self.assertEqual((cam.edges_width, cam.edges_height), (100, 50))
        self.assertEqual(cam.edges["disposal"]["points"], [[20, 20], [100, 20], [100, 80]])

    def test_test_cameras_never_persist_a_trace(self):
        cam = Camera.objects.create(code="CAM-PARKING-TEST", name="T")
        self._run(cam)
        cam.refresh_from_db()
        self.assertEqual(cam.edges, {})


class DisposalEdgesMigrationTests(TestCase):
    """0075 must not lose an area drawn under 0066's dedicated columns."""

    def test_old_area_is_rekeyed_and_rescaled_to_the_roads_frame(self):
        import importlib
        mig = importlib.import_module("core.migrations.0075_remove_camera_disposal_edges")
        stub = mock.Mock(disposal_edges={"road": {"type": "zone", "points": [[10, 10], [50, 10], [50, 40]]}},
                         disposal_edges_width=100, disposal_edges_height=50,
                         edges=dict(ROAD), edges_width=200, edges_height=100)
        apps = mock.Mock()
        apps.get_model.return_value.objects.all.return_value = [stub]
        mig.disposal_edges_into_edges(apps, None)
        self.assertEqual(stub.edges["disposal"],
                         {"type": "disposal", "points": [[20, 20], [100, 20], [100, 80]]})
        self.assertEqual(zones.road_entries(stub.edges), ROAD)

    def test_a_camera_without_a_road_adopts_the_old_frame_size(self):
        import importlib
        mig = importlib.import_module("core.migrations.0075_remove_camera_disposal_edges")
        stub = mock.Mock(disposal_edges={"k": {"type": "zone", "points": [[1, 2], [3, 4], [5, 6]]}},
                         disposal_edges_width=640, disposal_edges_height=480,
                         edges={}, edges_width=None, edges_height=None)
        apps = mock.Mock()
        apps.get_model.return_value.objects.all.return_value = [stub]
        mig.disposal_edges_into_edges(apps, None)
        self.assertEqual((stub.edges_width, stub.edges_height), (640, 480))
        self.assertEqual(stub.edges["disposal"]["points"], [[1, 2], [3, 4], [5, 6]])

    def test_an_existing_disposal_entry_is_not_overwritten(self):
        import importlib
        mig = importlib.import_module("core.migrations.0075_remove_camera_disposal_edges")
        stub = mock.Mock(disposal_edges={"k": {"type": "zone", "points": [[1, 2], [3, 4], [5, 6]]}},
                         disposal_edges_width=10, disposal_edges_height=10,
                         edges=dict(BIN), edges_width=100, edges_height=50)
        apps = mock.Mock()
        apps.get_model.return_value.objects.all.return_value = [stub]
        mig.disposal_edges_into_edges(apps, None)
        self.assertEqual(stub.edges, BIN)

    def test_empty_old_columns_change_nothing(self):
        import importlib
        mig = importlib.import_module("core.migrations.0075_remove_camera_disposal_edges")
        stub = mock.Mock(disposal_edges={}, edges=dict(ROAD), edges_width=100, edges_height=50)
        apps = mock.Mock()
        apps.get_model.return_value.objects.all.return_value = [stub]
        mig.disposal_edges_into_edges(apps, None)
        self.assertEqual(stub.edges, ROAD)
        stub.save.assert_not_called()
