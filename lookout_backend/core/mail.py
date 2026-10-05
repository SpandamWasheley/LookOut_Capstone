"""Brevo's transactional email API, as a Django email backend.

Why the HTTP API rather than SMTP, when both reach the same service:

* Managed hosts commonly block outbound SMTP ports (25/465/587) to stop
  spam from compromised apps. Nothing in the app can work around that, and
  the failure looks like a hang followed by a timeout. Port 443 is never
  blocked.
* An SMTP rejection is a three-digit code and a sentence -- "535 5.7.8
  Authentication failed" does not say whether the key is wrong, the login is
  wrong, or the IP is unauthorised. The API answers JSON that names the cause.
* One credential instead of two: the API key, with no separate SMTP key.

This is a backend rather than direct calls at the send site, so `send_mail()`
in views.py is unchanged and the test suite keeps working through Django's
locmem backend as before.

It does NOT bypass Brevo -> Security -> Authorised IPs. That restriction gates
the API exactly as it gates SMTP; an unlisted sender gets 401 here instead of
535 there.
"""
from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

SEND_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoAPIError(Exception):
    """Brevo accepted the request but refused the message."""


class BrevoAPIBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, api_key=None, timeout=None, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = api_key if api_key is not None else getattr(settings, "BREVO_API_KEY", "")
        self.timeout = timeout if timeout is not None else getattr(settings, "BREVO_TIMEOUT", 15)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if not self.api_key:
            if self.fail_silently:
                return 0
            raise BrevoAPIError(
                "BREVO_API_KEY is empty. Set it in the environment, or point "
                "EMAIL_BACKEND at another backend."
            )
        sent = 0
        # One session for the batch: these sends are TLS handshakes to the same
        # host, and the handshake costs more than the request.
        with requests.Session() as session:
            for message in email_messages:
                if self._send(session, message):
                    sent += 1
        return sent

    def _send(self, session, message):
        payload = self._payload(message)
        if payload is None:            # nothing to send to
            return False
        try:
            response = session.post(
                SEND_URL, json=payload, timeout=self.timeout,
                headers={"api-key": self.api_key, "accept": "application/json",
                         "content-type": "application/json"},
            )
        except requests.RequestException:
            if self.fail_silently:
                return False
            raise

        if response.status_code in (200, 201, 202):
            return True
        if self.fail_silently:
            return False
        # The JSON body is the whole reason for preferring this over SMTP --
        # surface it rather than just the status code.
        try:
            detail = response.json().get("message") or response.text
        except ValueError:
            detail = response.text
        raise BrevoAPIError(f"Brevo rejected the message (HTTP {response.status_code}): {detail}")

    def _payload(self, message):
        recipients = [{"email": a} for a in message.to if a]
        if not recipients:
            return None

        name, address = parseaddr(message.from_email or settings.DEFAULT_FROM_EMAIL or "")
        if not address:
            raise BrevoAPIError(
                "No From address. Set DEFAULT_FROM_EMAIL to an address verified "
                "under Brevo -> Senders."
            )
        sender = {"email": address}
        if name:
            sender["name"] = name

        payload = {"sender": sender, "to": recipients, "subject": message.subject or ""}

        # Brevo needs at least one of textContent/htmlContent, so a plain
        # message whose body happens to be empty still has to carry the key.
        if getattr(message, "content_subtype", "plain") == "html":
            payload["htmlContent"] = message.body or ""
        else:
            payload["textContent"] = message.body or ""
        for content, mimetype in getattr(message, "alternatives", []) or []:
            if mimetype == "text/html":
                payload["htmlContent"] = content

        if message.cc:
            payload["cc"] = [{"email": a} for a in message.cc if a]
        if message.bcc:
            payload["bcc"] = [{"email": a} for a in message.bcc if a]
        if message.reply_to:
            reply_name, reply_addr = parseaddr(message.reply_to[0])
            if reply_addr:
                payload["replyTo"] = {"email": reply_addr}
                if reply_name:
                    payload["replyTo"]["name"] = reply_name
        return payload
