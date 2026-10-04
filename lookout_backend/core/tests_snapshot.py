"""The camera snapshot proxy, and the two topologies it has to serve.

Browsers cannot play RTSP and the camera may sit on a subnet the browser
cannot route to, so the still is fetched server-side with the camera's own
credentials and streamed back as JPEG.

Where "server-side" is decides the scheme. On the LAN the API and the camera
are on the same network and ISAPI stills are plain http. In the split
deployment the API is in the cloud and the camera is behind an ngrok tunnel,
which only speaks TLS — so the scheme has to come from stream_url rather than
being hardcoded.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


class SnapshotUrlTests(TestCase):
    def setUp(self):
        from core.models import Camera
        User = get_user_model()
        self.admin = User.objects.create_user(username="adm", password="x", role="admin")
        self.c = APIClient()
        self.c.force_authenticate(self.admin)
        self.Camera = Camera

    _n = 0

    def _fetch(self, stream_url):
        """Returns the URL the view tried to GET."""
        type(self)._n += 1
        cam = self.Camera.objects.create(code=f"CAM-{self._n:02d}", name="Gate",
                                         stream_url=stream_url)
        with mock.patch("requests.get") as get:
            get.return_value = mock.Mock(status_code=200, content=b"\xff\xd8jpg",
                                         headers={"Content-Type": "image/jpeg"})
            r = self.c.get(f"/api/cameras/{cam.pk}/snapshot/")
        self.assertEqual(r.status_code, 200, r.content)
        return get.call_args[0][0], get.call_args[1]

    def test_an_rtsp_lan_camera_still_uses_plain_http(self):
        # The long-standing behaviour: ISAPI stills on a LAN camera are http
        # on port 80, whatever port RTSP itself is on.
        url, _ = self._fetch("rtsp://admin:pass@192.168.1.64:554/Streaming/Channels/102")
        self.assertEqual(url, "http://192.168.1.64/ISAPI/Streaming/channels/102/picture")

    def test_an_https_tunnel_is_fetched_over_https(self):
        # Forcing http:// at an ngrok host fails outright — its edge only
        # speaks TLS — and reports a bare "Camera unreachable" that says
        # nothing about the scheme.
        url, _ = self._fetch("https://admin:pass@abc.ngrok-free.app/Streaming/Channels/101")
        self.assertEqual(
            url, "https://abc.ngrok-free.app/ISAPI/Streaming/channels/101/picture")

    def test_an_explicit_http_tunnel_is_honoured_too(self):
        url, _ = self._fetch("http://admin:pass@abc.ngrok-free.app/Streaming/Channels/102")
        self.assertEqual(
            url, "http://abc.ngrok-free.app/ISAPI/Streaming/channels/102/picture")

    def test_an_rtsp_port_is_dropped(self):
        # 554 is the RTSP port; ISAPI is not served there, so reusing it gives
        # http://camera:554/ISAPI/... which never answers.
        url, _ = self._fetch("rtsp://a:b@10.0.0.7:554/Streaming/Channels/102")
        self.assertEqual(url, "http://10.0.0.7/ISAPI/Streaming/channels/102/picture")

    def test_a_non_default_http_port_is_kept(self):
        # A tunnel or a camera on a non-standard port was silently dropped
        # before, because only parsed.hostname was used.
        url, _ = self._fetch("http://admin:pass@10.0.0.5:8080/Streaming/Channels/102")
        self.assertEqual(url, "http://10.0.0.5:8080/ISAPI/Streaming/channels/102/picture")

    def test_the_channel_comes_from_the_path_and_defaults_to_the_substream(self):
        url, _ = self._fetch("rtsp://a:b@10.0.0.5:554/Streaming/Channels/101")
        self.assertIn("/channels/101/", url)
        url, _ = self._fetch("rtsp://a:b@10.0.0.5:554/live")
        self.assertIn("/channels/102/", url)

    def test_credentials_are_taken_from_the_url_and_sent_as_digest(self):
        from requests.auth import HTTPDigestAuth
        _, kwargs = self._fetch("rtsp://admin:p%40ss@192.168.1.64:554/Streaming/Channels/102")
        auth = kwargs["auth"]
        self.assertIsInstance(auth, HTTPDigestAuth)
        self.assertEqual((auth.username, auth.password), ("admin", "p@ss"))

    def test_a_camera_with_no_stream_url_404s(self):
        cam = self.Camera.objects.create(code="CAM-UNSET", name="Unset")
        r = self.c.get(f"/api/cameras/{cam.pk}/snapshot/")
        self.assertEqual(r.status_code, 404)

    def test_an_unreachable_camera_reports_502_not_500(self):
        import requests
        cam = self.Camera.objects.create(code="CAM-GONE", name="Gone",
                                         stream_url="rtsp://a:b@10.0.0.9:554/x")
        with mock.patch("requests.get", side_effect=requests.ConnectionError("refused")):
            r = self.c.get(f"/api/cameras/{cam.pk}/snapshot/")
        self.assertEqual(r.status_code, 502)
        self.assertIn("unreachable", r.json()["detail"])
