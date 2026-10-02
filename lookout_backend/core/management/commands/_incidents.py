"""One Alert row per incident, moving through Monitoring -> Possible -> Likely.

Spec v6 sections 2-3: as soon as the object cue is ON the event is stored as
Monitoring (a quiet watchlist, never notified). The SAME row is updated as the
score rises or falls (hysteresis lives in scoring.Score), and it stops being
"active" when the object goes away. A clip is written only once the event
reaches Possible or Likely, and refreshed while it continues; a Monitoring
event that never rises gets one image and no clip.

The three watchers (smoking, drinking, thief) mix this in and supply two small
callables: how to create the row, and how to describe it.
"""

from django.utils import timezone

from collections import namedtuple

import cv2

from core.models import Alert
from core.vision import momentum, recognition, scoring

CLIP_REFRESH_SECONDS = 15.0     # while Possible / Likely continues
ROW_UPDATE_SECONDS = 5.0        # throttle for score / last_seen writes
END_GRACE_SECONDS = 3.0         # no update for this long -> the incident has ended


class Incident:
    """In-memory state for one tracked incident (never persisted)."""

    def __init__(self, now):
        self.started = now
        self.last_active = now
        self.level = scoring.NONE
        self.alert = None
        self.clip_base = None
        self.last_clip_at = None
        self.last_row_write = None
        self.peak = scoring.NONE
        self.announced = False      # the first row (or its dry-run log line) was handled


ObjectCue = namedtuple("ObjectCue", "on label conf momentum snapshot")
NO_CUE = ObjectCue(False, None, None, 0.0, None)


class IncidentMixin:
    """Mix into a watch_* Command. Needs: self.dry_run, self.stdout, self.style,
    self.stats, self._alert_log, and the watcher's own _save_clips()."""

    # ---- object cue: momentum per (track, class) ---------------------------

    def _momentum_book(self):
        book = self.__dict__.get("_momentum")
        if book is None:
            book = self._momentum = momentum.MomentumBook(momentum.DEFAULT_CONFIG)
            self._seen_track_ids = set()
        return book

    def _object_cue(self, track, dets, now_ts):
        """Advance this track's momentum slots by one processed frame and report
        the object cue (docs/specs/LookOut_Object_Cue_Momentum_Spec.md).

        Replaces the old vote + dwell gate. `dets` are this frame's detections
        for the track; a class that is absent this frame simply decays.
        """
        book = self._momentum_book()
        self._seen_track_ids.add(track.id)
        confs, names = {}, {}
        for d in dets:
            cls = str(d[5]).lower()
            if d[4] > confs.get(cls, 0.0):
                confs[cls] = float(d[4])
            names.setdefault(cls, d[5])
        slots = book.step(track.id, confs)
        # Optional run log ($LOOKOUT_MOUTH_LOG): the raw per-frame input of every slot,
        # so momentum settings can be replayed offline without re-running the models.
        if confs or slots:
            recognition.log_mouth(
                kind="mom", t=round(now_ts, 3), track=track.id, confs={c: round(v, 3) for c, v in confs.items()},
                box=[int(v) for v in track.box] if track.box is not None else None,
                on=[c for c, sl in slots.items() if sl.cue_on])
        for cls in slots:
            names.setdefault(cls, cls)
        on = [c for c, sl in slots.items() if sl.cue_on]
        if not on:
            top = max(slots.values(), key=lambda sl: sl.momentum, default=None)
            return ObjectCue(False, None, None, top.momentum if top else 0.0, None)
        cls = max(on, key=lambda c: slots[c].momentum)
        conf = confs.get(cls, slots[cls].last_conf) or slots[cls].peak / 3.0
        return ObjectCue(True, names[cls], float(conf), slots[cls].momentum,
                         book.snapshot(track.id, cls))

    def _draw_object_cue(self, frame, track, dets, cue, color_on, color_building=(0, 200, 0)):
        """Draw the violation boxes ALWAYS (green while momentum builds, `color_on`
        once the cue is ON). With no detection this frame the track's last known
        boxes are drawn dashed and dimmed."""
        draw_dets = dets if dets else (track.dets if cue.on else [])
        is_historical = not dets and bool(draw_dets)
        for (x1, y1, x2, y2, score, label) in draw_dets:
            color = color_on if cue.on else color_building
            text = f"{label} {score * 100:.0f}% m={cue.momentum:.1f}{' ON' if cue.on else ''}"
            if is_historical:
                color = tuple(c // 2 for c in color)
                recognition.draw_dashed_rect(frame, (x1, y1), (x2, y2), color, 2)
                text += " (last seen)"
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            recognition.draw_label(frame, text, x1, max(y1 - 8, 0), color)

    def _incident_book(self):
        book = self.__dict__.get("_incidents")
        if book is None:
            book = self._incidents = {}
        return book

    def _incident_level(self, key):
        """The incident's last status, for Score(previous_level=...) hysteresis."""
        inc = self._incident_book().get(key)
        return inc.level if inc is not None else None

    def _incident_sync(self, key, score, now, *, create, describe=None, frame=None,
                       blocked=None, box=None, extra_cues=None):
        """Apply `score` (a scoring.Score) to the incident `key`.

        create(level, with_clip) -> Alert | None    builds the first row
        describe(score) -> str                      refreshed description
        blocked() -> bool                           cooldown check for a NEW row
        extra_cues                                  merged into Alert.cues (audit data)
        Returns the Alert (None in --dry-run or when nothing is shown).
        """
        book = self._incident_book()
        inc = book.get(key)
        level = score.level

        if level == scoring.NONE:
            # Behaviour points are still logged; the incident (if any) is no
            # longer active and will be closed by _incident_gc after the grace.
            if inc is not None:
                inc.level = scoring.NONE
            return inc.alert if inc is not None else None

        if inc is None:
            inc = Incident(now)
            book[key] = inc
        inc.last_active = now
        previous = inc.level
        rose = scoring.LEVEL_ORDER[level] > scoring.LEVEL_ORDER[previous]

        if not inc.announced:
            if blocked is not None and blocked():
                self.stats["suppressed: recent incident at same spot"] += 1
                book.pop(key, None)
                return None
            inc.announced = True
            if self.dry_run:
                inc.level = level
                recognition.log_mouth(kind="incident", event="start", level=level, key=str(key),
                                      score=round(score.score, 3), cues=sorted(score.cues))
                self._alert_log.append((tuple(box) if box is not None else None, now))
                self.stats[f"incident:{level}"] += 1
                return None
            alert = create(level, score.alerting)
            inc.alert = alert
            inc.clip_base = getattr(self, "_last_clip_base", None)
            if score.alerting:
                inc.last_clip_at = now
            inc.last_row_write = now
            if box is not None:
                self._alert_log.append((tuple(box), now))
            self.stats[f"incident:{level}"] += 1
        elif self.dry_run:
            if rose:
                recognition.log_mouth(kind="incident", event="rise", level=level, key=str(key),
                                      score=round(score.score, 3), cues=sorted(score.cues))
                self.stats[f"incident:{level}"] += 1
        else:
            alert = inc.alert
            due = (level != previous) or (inc.last_row_write is None
                                           or now - inc.last_row_write >= ROW_UPDATE_SECONDS)
            if alert is not None and due:
                self._incident_write(inc, score, level, describe, extra_cues)
                inc.last_row_write = now
            if (alert is not None and score.alerting and frame is not None
                    and (previous not in scoring.NOTIFY_LEVELS
                         or inc.last_clip_at is None
                         or now - inc.last_clip_at >= CLIP_REFRESH_SECONDS)):
                self._incident_clip(inc, frame, now)
                inc.last_clip_at = now

        if rose:
            self.stats[f"status: {scoring.label_of(level)}"] += 1
        inc.level = level
        if scoring.LEVEL_ORDER[level] > scoring.LEVEL_ORDER[inc.peak]:
            inc.peak = level
        return inc.alert

    def _incident_write(self, inc, score, level, describe, extra_cues):
        """Update the stored row to the current status, score and cue vector."""
        alert = inc.alert
        fields = {
            "level": level,
            "confidence": score.score,
            "last_seen_at": timezone.now(),
        }
        if describe is not None:
            fields["description"] = describe(score)
        cues = dict(Alert.objects.filter(pk=alert.pk).values_list("cues", flat=True).first() or {})
        cues.update(score.as_dict())
        if extra_cues:
            cues.update(extra_cues)
        fields["cues"] = cues
        Alert.objects.filter(pk=alert.pk).update(**fields)
        for name, value in fields.items():
            setattr(alert, name, value)

    def _incident_clip(self, inc, frame, now):
        """(Re)write the evidence clip for a Possible / Likely incident."""
        alert = inc.alert
        if inc.clip_base is None:
            return
        video_url, raw_video_url = self._save_clips(inc.clip_base, frame, now)
        update = {}
        if video_url:
            update["video_url"] = video_url
        if raw_video_url:
            update["raw_video_url"] = raw_video_url
        if update:
            Alert.objects.filter(pk=alert.pk).update(**update)
            for name, value in update.items():
                setattr(alert, name, value)

    def _incident_gc(self, now, frame=None):
        """Close incidents that have not been updated for END_GRACE_SECONDS.

        The row stays (it is the record); it simply stops being active:
        `last_seen_at` is the last moment the object cue was ON.
        """
        # Momentum slots of tracks that were not processed this frame are lost
        # with their track (no leak, no carry-over to a new id).
        mbook = self._momentum_book()
        dropped = mbook.drop_missing(self._seen_track_ids)
        if dropped:
            self.stats["momentum slots cleaned"] += dropped
        self._seen_track_ids = set()
        book = self._incident_book()
        for key in [k for k, inc in book.items() if now - inc.last_active > END_GRACE_SECONDS]:
            inc = book.pop(key)
            if inc.alert is not None and not self.dry_run:
                Alert.objects.filter(pk=inc.alert.pk).update(
                    last_seen_at=timezone.now() - timezone.timedelta(seconds=END_GRACE_SECONDS))
                if inc.peak in scoring.NOTIFY_LEVELS and frame is not None:
                    self._incident_clip(inc, frame, now)
            self.stats["incident ended"] += 1
