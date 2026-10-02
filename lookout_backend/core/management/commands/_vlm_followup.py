"""Write an asynchronous checker answer back onto an alert.

Shared by every watcher so the three of them cannot drift on what "the context
arrived" means, and so the rules about what an async answer may and may not
change live in one place.
"""

import logging

from core.models import Alert
from core.vision import scoring

logger = logging.getLogger(__name__)


def attach_verdict(alert_id, verdict, kind):
    """Store a late-arriving reading on an already-published alert.

    The SCORE is deliberately left alone.

    The alert was filed, shown, and possibly already dispatched against. Moving
    its score minutes later would mean a tanod who looked at 10:01 and one who
    looked at 10:09 saw different numbers for the same event, with nothing on
    screen to explain why. The context is additive: it tells a reviewer what the
    checker made of the scene, and the card marks an ordinary-activity reading
    clearly enough to act on.

    The live path still scores the checker in full. This is only for answers
    that arrive after publication.
    """
    if verdict is None or not verdict.ok:
        return

    try:
        alert = Alert.objects.get(pk=alert_id)
    except Alert.DoesNotExist:
        return                      # deleted while the checker was thinking

    alert.vlm_verdict = verdict.verdict
    alert.vlm_confidence = verdict.confidence
    alert.vlm_reason = verdict.reason
    cues = dict(alert.cues or {})
    cues["vlm"] = verdict.as_dict()
    cues["vlm"]["source"] = "arrived after the alert was published"
    # Surfaced on the card as the amber badge. Recorded explicitly rather than
    # re-derived in the client, so a change to the multiplier table cannot make
    # old alerts describe themselves differently.
    cues["vlm"]["reads_as_ordinary"] = bool(verdict.enum_multipliers())
    alert.cues = cues
    alert.save(update_fields=["vlm_verdict", "vlm_confidence", "vlm_reason",
                              "cues"])
    logger.info("VLM context attached to alert %s (%s): %s",
                alert.code, kind, verdict.verdict)
