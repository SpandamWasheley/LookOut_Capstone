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
import logging
import time
from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

SEND_URL = "https://api.brevo.com/v3/smtp/email"

logger = logging.getLogger(__name__)


def _mask(key):
    """Enough of the key to tell which one is loaded, not enough to use it.

    The two Brevo credentials differ in their prefix (`xkeysib-` for the API
    key, `xsmtpsib-` for the SMTP key) and using one in the other's place is
    the most common misconfiguration here -- so the prefix is the part worth
    printing.
    """
    if not key:
        return "<empty>"
    return "{}...{} (len {})".format(key[:12], key[-4:], len(key))


BODY_KEYS = ("textContent", "htmlContent")


def _redacted(payload):
    """The payload with the message body replaced by its length.

    Everything that is useful for diagnosing a rejection -- sender, recipients,
    subject, cc/bcc, replyTo, whether a body key is present at all -- is
    structure, not content. The content is a one-time code.
    """
    safe = {}
    for key, value in payload.items():
        if key in BODY_KEYS:
            safe[key] = f"<redacted, {len(value)} chars>"
        else:
            safe[key] = value
    return safe


class BrevoAPIError(Exception):
    """Brevo accepted the request but refused the message."""


class BrevoAPIBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, api_key=None, timeout=None, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = api_key if api_key is not None else getattr(settings, "BREVO_API_KEY", "")
        self.timeout = timeout if timeout is not None else getattr(settings, "BREVO_TIMEOUT", 15)

    def send_messages(self, email_messages):
        if not email_messages:
            logger.debug("send_messages called with nothing to send")
            return 0
        logger.info(
            "Brevo send: %d message(s) | api_key=%s | from=%s | timeout=%ss",
            len(email_messages), _mask(self.api_key),
            getattr(settings, "DEFAULT_FROM_EMAIL", "") or "<empty>", self.timeout,
        )
        if not self.api_key:
            logger.error(
                "BREVO_API_KEY is empty -- no send was attempted. Set it in "
                "lookout_backend/.env (an API key starts with 'xkeysib-')."
            )
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
        logger.info("Brevo send finished: %d/%d accepted", sent, len(email_messages))
        return sent

    def _send(self, session, message):
        try:
            payload = self._payload(message)
        except BrevoAPIError:
            logger.exception("Could not build the Brevo payload")
            raise
        if payload is None:            # nothing to send to
            logger.warning("Skipped a message with no recipients: subject=%r", message.subject)
            return False

        to = ", ".join(r["email"] for r in payload["to"])
        logger.info("POST %s -> to=%s subject=%r", SEND_URL, to, payload["subject"])
        # The body is REDACTED, not merely DEBUG-gated: on both paths that use
        # this backend the body is a verification code, and EMAIL_LOG_LEVEL=DEBUG
        # is precisely what gets set while debugging a failing code send -- so
        # the codes would end up in the terminal and in any captured log.
        #
        # Its *length* is kept, because an empty body is a real failure mode:
        # Brevo requires one of textContent/htmlContent to be non-empty.
        logger.debug("Brevo payload: %s", _redacted(payload))

        started = time.monotonic()
        try:
            response = session.post(
                SEND_URL, json=payload, timeout=self.timeout,
                headers={"api-key": self.api_key, "accept": "application/json",
                         "content-type": "application/json"},
            )
        except requests.Timeout:
            logger.error(
                "Brevo TIMED OUT after %.1fs (BREVO_TIMEOUT=%s) sending to %s. The "
                "request never got an answer -- check this machine can reach "
                "api.brevo.com, or raise BREVO_TIMEOUT.",
                time.monotonic() - started, self.timeout, to,
            )
            if self.fail_silently:
                return False
            raise
        except requests.RequestException:
            logger.exception(
                "Brevo request FAILED after %.1fs sending to %s (no HTTP response)",
                time.monotonic() - started, to,
            )
            if self.fail_silently:
                return False
            raise

        elapsed = time.monotonic() - started
        if response.status_code in (200, 201, 202):
            # Brevo echoes a messageId -- that is what you quote to their logs.
            try:
                message_id = response.json().get("messageId", "")
            except ValueError:
                message_id = ""
            logger.info(
                "Brevo ACCEPTED the message in %.2fs (HTTP %s) to=%s messageId=%s",
                elapsed, response.status_code, to, message_id or "<none>",
            )
            return True

        logger.error(
            "Brevo REJECTED the message in %.2fs: HTTP %s to=%s body=%s%s",
            elapsed, response.status_code, to, response.text[:500],
            self._hint(response.status_code),
        )
        if self.fail_silently:
            return False
        # The JSON body is the whole reason for preferring this over SMTP --
        # surface it rather than just the status code.
        try:
            detail = response.json().get("message") or response.text
        except ValueError:
            detail = response.text
        raise BrevoAPIError(f"Brevo rejected the message (HTTP {response.status_code}): {detail}")

    @staticmethod
    def _hint(status_code):
        """Map Brevo's status codes onto the thing to go and change.

        The JSON body names the cause in Brevo's vocabulary ("unrecognised IP
        address"); this says which setting or dashboard page that is.
        """
        hints = {
            400: ("Brevo understood the request but refused the content -- usually an "
                  "unverified sender. DEFAULT_FROM_EMAIL must be an address listed "
                  "under Brevo -> Senders."),
            401: ("Rejected credentials or source IP. Either BREVO_API_KEY is wrong "
                  "(it must be the API key, 'xkeysib-...', NOT the SMTP key "
                  "'xsmtpsib-...'), or this machine's IP is not listed under "
                  "Brevo -> Security -> Authorised IPs."),
            402: "The Brevo account is out of sending credits.",
            403: "The key is valid but lacks permission to send transactional email.",
            404: "Endpoint not found -- SEND_URL is wrong.",
            429: "Rate-limited by Brevo: too many sends too quickly.",
        }
        hint = hints.get(status_code)
        if hint is None and status_code >= 500:
            hint = "Brevo-side failure. Nothing to fix locally; retry."
        return "\n    -> " + hint if hint else ""

    def _payload(self, message):
        recipients = [{"email": a} for a in message.to if a]
        if not recipients:
            return None

        name, address = parseaddr(message.from_email or settings.DEFAULT_FROM_EMAIL or "")
        if not address:
            logger.error(
                "No From address: DEFAULT_FROM_EMAIL is empty (raw value %r). Set it "
                "in lookout_backend/.env to an address verified under Brevo -> Senders.",
                message.from_email or settings.DEFAULT_FROM_EMAIL,
            )
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
