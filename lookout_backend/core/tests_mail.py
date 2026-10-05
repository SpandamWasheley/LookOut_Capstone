"""The Brevo HTTP API email backend (core/mail.py).

Every test mocks requests.post -- none of these touch the network.
"""
from unittest import mock

from django.core.mail import EmailMessage, EmailMultiAlternatives, send_mail
from django.test import SimpleTestCase, override_settings

from core.mail import BrevoAPIBackend, BrevoAPIError

BACKEND = "core.mail.BrevoAPIBackend"


def _ok(status=201):
    return mock.Mock(status_code=status, json=lambda: {"messageId": "<abc@brevo>"})


@override_settings(EMAIL_BACKEND=BACKEND, BREVO_API_KEY="xkeysib-test",
                   DEFAULT_FROM_EMAIL="noreply@example.com")
class BrevoBackendTests(SimpleTestCase):
    def _post(self, fn):
        with mock.patch("requests.Session.post", return_value=_ok()) as post:
            result = fn()
        return post, result

    def test_send_mail_posts_the_expected_payload(self):
        post, sent = self._post(lambda: send_mail(
            "Your LookOut password reset code", "Your code is 123456.",
            "noreply@example.com", ["officer@example.com"]))
        self.assertEqual(sent, 1)
        url = post.call_args[0][0]
        body = post.call_args[1]["json"]
        self.assertEqual(url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(body["sender"], {"email": "noreply@example.com"})
        self.assertEqual(body["to"], [{"email": "officer@example.com"}])
        self.assertEqual(body["subject"], "Your LookOut password reset code")
        self.assertEqual(body["textContent"], "Your code is 123456.")
        self.assertNotIn("htmlContent", body)

    def test_the_api_key_goes_in_the_header_never_the_body(self):
        post, _ = self._post(lambda: send_mail("s", "b", "noreply@example.com",
                                               ["a@example.com"]))
        self.assertEqual(post.call_args[1]["headers"]["api-key"], "xkeysib-test")
        self.assertNotIn("xkeysib-test", str(post.call_args[1]["json"]))

    def test_a_display_name_is_split_out_of_the_from_address(self):
        # Brevo wants {"name": ..., "email": ...}, not one RFC822 string.
        post, _ = self._post(lambda: send_mail("s", "b", "LookOut <noreply@example.com>",
                                               ["a@example.com"]))
        self.assertEqual(post.call_args[1]["json"]["sender"],
                         {"name": "LookOut", "email": "noreply@example.com"})

    def test_an_html_alternative_is_carried_alongside_the_text(self):
        def go():
            m = EmailMultiAlternatives("s", "plain", "noreply@example.com", ["a@example.com"])
            m.attach_alternative("<p>rich</p>", "text/html")
            return m.send()
        post, _ = self._post(go)
        body = post.call_args[1]["json"]
        self.assertEqual(body["textContent"], "plain")
        self.assertEqual(body["htmlContent"], "<p>rich</p>")

    def test_cc_bcc_and_reply_to_are_passed_through(self):
        def go():
            return EmailMessage("s", "b", "noreply@example.com", ["a@example.com"],
                                cc=["c@example.com"], bcc=["b@example.com"],
                                reply_to=["Desk <desk@example.com>"]).send()
        post, _ = self._post(go)
        body = post.call_args[1]["json"]
        self.assertEqual(body["cc"], [{"email": "c@example.com"}])
        self.assertEqual(body["bcc"], [{"email": "b@example.com"}])
        self.assertEqual(body["replyTo"], {"name": "Desk", "email": "desk@example.com"})

    def test_a_message_with_no_recipients_is_skipped_not_posted(self):
        with mock.patch("requests.Session.post") as post:
            sent = EmailMessage("s", "b", "noreply@example.com", []).send()
        self.assertEqual(sent, 0)
        post.assert_not_called()

    # --- failures -----------------------------------------------------------

    def test_an_api_rejection_surfaces_brevos_own_reason(self):
        # The whole point of preferring the API: a 535 says nothing, this says
        # exactly what is wrong.
        bad = mock.Mock(status_code=401,
                        json=lambda: {"message": "unrecognised IP address 1.2.3.4"})
        with mock.patch("requests.Session.post", return_value=bad):
            with self.assertRaises(BrevoAPIError) as caught:
                send_mail("s", "b", "noreply@example.com", ["a@example.com"])
        self.assertIn("unrecognised IP address 1.2.3.4", str(caught.exception))
        self.assertIn("401", str(caught.exception))

    def test_fail_silently_swallows_a_rejection(self):
        bad = mock.Mock(status_code=400, json=lambda: {"message": "nope"})
        with mock.patch("requests.Session.post", return_value=bad):
            sent = send_mail("s", "b", "noreply@example.com", ["a@example.com"],
                             fail_silently=True)
        self.assertEqual(sent, 0)

    def test_a_network_error_propagates_unless_fail_silently(self):
        import requests
        with mock.patch("requests.Session.post", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(requests.ConnectionError):
                send_mail("s", "b", "noreply@example.com", ["a@example.com"])
            self.assertEqual(
                send_mail("s", "b", "noreply@example.com", ["a@example.com"],
                          fail_silently=True), 0)

    @override_settings(BREVO_API_KEY="")
    def test_a_missing_key_fails_loudly_and_names_the_setting(self):
        with self.assertRaises(BrevoAPIError) as caught:
            send_mail("s", "b", "noreply@example.com", ["a@example.com"])
        self.assertIn("BREVO_API_KEY", str(caught.exception))

    def test_sending_nothing_makes_no_request(self):
        with mock.patch("requests.Session.post") as post:
            self.assertEqual(BrevoAPIBackend().send_messages([]), 0)
        post.assert_not_called()
