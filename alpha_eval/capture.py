"""Event capture for the alpha_eval worker.

Runs LookOut's real `watch_merged_all` engine (all four violation types) and, instead of letting it write
Alert rows, records every event with its VIDEO time.

How (all inside the worker process; nothing in lookout_backend is edited):
  * `Alert` is replaced, in the five modules that use it, by an in-memory store (`FakeAlert`). The engine's
    create / update / delete calls land there, and each one is turned into a line in events.jsonl.
  * `IncidentMixin._incident_sync` is wrapped only to remember which track / box the write belongs to.
  * `ai_checker.check_async` is deferred: the check is held until the VIDEO reaches trigger + FRAMES_AFTER
    (or the end of the clip) and is then run synchronously by the original function. No wall-clock sleep,
    so the frames the AI sees do not depend on processing speed.
  * Evidence clips (ffmpeg / mp4 writing) are skipped: alpha_eval only needs the event times and one frame.
"""
import json
import time as _real_time
from collections import deque
from pathlib import Path

import cv2

from videoclock import CLOCK, ClockedCapture

LEVEL_LABEL = {"monitoring": "Monitoring", "warning": "Possible", "violation": "Likely"}
LEVEL_RANK = {"": 0, "none": 0, "monitoring": 1, "warning": 2, "violation": 3}
KIND_FOR_AI = {"smoking": "smoking", "drinking": "drinking", "theft": "holdup"}


class Context:
    """What the engine is doing right now (set by the wrappers, read by the fake Alert store)."""
    def __init__(self):
        self.key = None
        self.box = None
        self.frame = None              # clean copy of the current frame
        self.parking_hits = deque()
        self.pending_ai = []           # deferred AI checks


CTX = Context()


class Recorder:
    """Owns events.jsonl, the event frames and the in-memory alert records."""

    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.frames_dir = self.run_dir / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.fh = open(self.events_path, "w", encoding="utf-8")
        self.records = {}
        self.next_pk = 1
        self.meta = {}                 # pk -> {violation_type, track_id, bbox, status, frames_saved:set()}
        self.n_events = 0

    # ---- low level -------------------------------------------------------
    def emit(self, **ev):
        ev.setdefault("video_time_sec", round(CLOCK.now, 3))
        self.fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        self.fh.flush()
        self.n_events += 1

    def close(self):
        try:
            self.fh.close()
        except OSError:
            pass

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _type_code(rec):
        t = getattr(rec, "type", None)
        return getattr(t, "code", None) or "unknown"

    @staticmethod
    def _box(box):
        if box is None:
            return None
        return [int(round(float(v))) for v in box][:4]

    def _save_frame(self, pk, status_label, final_alias=False):
        m = self.meta[pk]
        frame = CTX.frame
        if frame is None or status_label in m["frames_saved"]:
            return
        img = frame.copy()
        bb = m["bbox"]
        label = f"{m['event_id']} {m['violation_type']} {status_label} @ {CLOCK.now:.1f}s"
        if bb:
            cv2.rectangle(img, (bb[0], bb[1]), (bb[2], bb[3]), (0, 0, 255), 4)
            cv2.putText(img, label, (bb[0], max(30, bb[1] - 12)), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                        (0, 0, 255), 3, cv2.LINE_AA)
        else:
            cv2.putText(img, label, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 255), 3, cv2.LINE_AA)
        # keep the files small: longest edge 1280
        h, w = img.shape[:2]
        scale = 1280.0 / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        name = f"{m['event_id']}_{status_label.lower()}.jpg"
        cv2.imwrite(str(self.frames_dir / name), img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        m["frames_saved"].add(status_label)
        # <event_id>.jpg = the frame where it first reached a counting status (Possible/Likely)
        if status_label in ("Possible", "Likely") and not (self.frames_dir / f"{m['event_id']}.jpg").exists():
            cv2.imwrite(str(self.frames_dir / f"{m['event_id']}.jpg"), img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])

    # ---- alert lifecycle (called from the fake store) --------------------
    def created(self, rec):
        pk = rec.pk
        code = self._type_code(rec)
        level = (rec.level or "").lower() or "violation"      # parking has no stages: it fires once, at the threshold
        hit = None
        if code == "parking" and CTX.parking_hits:
            hit = CTX.parking_hits.popleft()
        track_id = hit["track_id"] if hit else (CTX.key[1] if CTX.key else None)
        box = hit["box"] if hit else CTX.box
        event_id = f"E{pk:04d}"
        self.meta[pk] = {"event_id": event_id, "violation_type": code, "track_id": track_id,
                         "bbox": self._box(box), "status": level, "frames_saved": set(),
                         "kind": CTX.key[0] if CTX.key else "parking", "peak": level}
        self.emit(event_id=event_id, track_id=track_id, violation_type=code, kind="alert_created",
                  status=LEVEL_LABEL[level], confidence=_num(rec.confidence), bbox=self._box(box),
                  ai_verdict=None, ai_suggested_status=None, description=str(rec.description)[:200])
        self._save_frame(pk, LEVEL_LABEL[level])

    def updated(self, rec, fields):
        pk = rec.pk
        m = self.meta.get(pk)
        if m is None:
            return
        if CTX.box is not None and CTX.key is not None:
            m["bbox"] = self._box(CTX.box)
        if "level" in fields and fields["level"] and fields["level"] != m["status"]:
            lvl = fields["level"]
            m["status"] = lvl
            if LEVEL_RANK.get(lvl, 0) > LEVEL_RANK.get(m["peak"], 0):
                m["peak"] = lvl
            self.emit(event_id=m["event_id"], track_id=m["track_id"], violation_type=m["violation_type"],
                      kind="status_change", status=LEVEL_LABEL.get(lvl, str(lvl)),
                      confidence=_num(fields.get("confidence", rec.confidence)), bbox=m["bbox"],
                      ai_verdict=None, ai_suggested_status=None)
            self._save_frame(pk, LEVEL_LABEL.get(lvl, str(lvl)))
        if "ai" in fields:
            self._ai(rec, m, fields["ai"])

    def _ai(self, rec, m, ai):
        state = (ai or {}).get("state")
        if state not in ("done", "unavailable"):
            return                                              # "pending": nothing to record yet
        verdict, badge, suggested = self._ai_judgement(m, ai, m["status"])
        m["ai"] = ai
        self.emit(event_id=m["event_id"], track_id=m["track_id"], violation_type=m["violation_type"],
                  kind="ai_verdict", status=LEVEL_LABEL.get(m["status"], m["status"]), confidence=None,
                  bbox=m["bbox"], ai_verdict=verdict, ai_suggested_status=suggested, ai_badge=badge,
                  ai_reply=(ai or {}).get("reply"), ai_error=(ai or {}).get("error"),
                  ai_model=(ai or {}).get("model"), ai_seconds=(ai or {}).get("seconds"),
                  ai_trigger_level=(ai or {}).get("trigger_level"), final=False)

    @staticmethod
    def _ai_judgement(m, ai, level):
        """(Agrees|Disagrees|Not run, badge code, suggested status) for a stored ai dict (DECISIONS D9)."""
        from core.vision import ai_status
        kind = KIND_FOR_AI.get(m["violation_type"])
        if not kind or not ai or ai.get("state") != "done" or not ai.get("reply"):
            return "Not run", "unavailable", None
        ctx = ai_status.ai_context(kind, ai, level)
        badge = ctx["badge"]["code"]
        verdict = "Agrees" if badge == "supports" else ("Disagrees" if badge in ("ordinary", "unclear") else "Not run")
        return verdict, badge, ctx["suggestion"].get("suggested")

    def deleted(self, pk):
        m = self.meta.get(pk)
        if m is not None:
            self.emit(event_id=m["event_id"], track_id=m["track_id"], violation_type=m["violation_type"],
                      kind="discarded", status=LEVEL_LABEL.get(m["status"], m["status"]), confidence=None,
                      bbox=m["bbox"], ai_verdict=None, ai_suggested_status=None)
        self.records.pop(pk, None)

    def finalize(self):
        """End of clip: re-judge each AI answer against the event's PEAK status (the suggested status
        follows the official status), and mark checks that never came back as Not run."""
        for pk, m in self.meta.items():
            ai = m.get("ai")
            if ai is None:
                st = (self.records.get(pk).ai or {}) if pk in self.records else {}
                if st.get("state") == "pending":
                    self.emit(event_id=m["event_id"], track_id=m["track_id"], violation_type=m["violation_type"],
                              kind="ai_verdict", status=LEVEL_LABEL.get(m["peak"], m["peak"]), confidence=None,
                              bbox=m["bbox"], ai_verdict="Not run", ai_suggested_status=None,
                              ai_badge="pending", final=True)
                continue
            verdict, badge, suggested = self._ai_judgement(m, ai, m["peak"])
            self.emit(event_id=m["event_id"], track_id=m["track_id"], violation_type=m["violation_type"],
                      kind="ai_verdict", status=LEVEL_LABEL.get(m["peak"], m["peak"]), confidence=None,
                      bbox=m["bbox"], ai_verdict=verdict, ai_suggested_status=suggested, ai_badge=badge,
                      ai_reply=ai.get("reply"), final=True)


def _num(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


# ---- the in-memory Alert replacement --------------------------------------------------------

RECORDER = None      # set by install()


class _Rows(list):
    def first(self):
        return self[0] if self else None


class _QuerySet:
    def __init__(self, pks):
        self.pks = pks

    def update(self, **fields):
        for pk in self.pks:
            rec = RECORDER.records.get(pk)
            if rec is None:
                continue
            for k, v in fields.items():
                setattr(rec, k, v)
            RECORDER.updated(rec, fields)
        return len(self.pks)

    def values(self, *names):
        return _Rows({n: getattr(RECORDER.records[pk], n, None) for n in names}
                     for pk in self.pks if pk in RECORDER.records)

    def values_list(self, name, flat=False):
        return _Rows(getattr(RECORDER.records[pk], name, None) for pk in self.pks if pk in RECORDER.records)

    def delete(self):
        for pk in self.pks:
            RECORDER.deleted(pk)
        return len(self.pks)

    def first(self):
        return RECORDER.records.get(self.pks[0]) if self.pks else None

    def exists(self):
        return bool(self.pks)


class _Manager:
    def create(self, **kw):
        rec = FakeAlert(**kw)
        rec.pk = rec.id = RECORDER.next_pk
        RECORDER.next_pk += 1
        rec.code = f"ALT-{rec.pk:04d}"
        RECORDER.records[rec.pk] = rec
        RECORDER.created(rec)
        return rec

    def filter(self, pk=None, **kw):
        if pk is None or kw:
            raise NotImplementedError(f"FakeAlert.filter({pk!r}, {kw!r})")
        return _QuerySet([pk] if pk in RECORDER.records else [])


def _make_fake_alert():
    from core.models import Alert as RealAlert

    class FakeAlert:
        Status = RealAlert.Status
        objects = _Manager()

        def __init__(self, **kw):
            self.level = ""
            self.peak_level = ""
            self.cues = {}
            self.timeline = []
            self.ai = {}
            self.image_url = ""
            self.description = ""
            self.confidence = 0.0
            self.__dict__.update(kw)

    return FakeAlert


FakeAlert = None

ALERT_MODULES = [
    "core.management.commands._incidents",
    "core.management.commands.watch_smoking",
    "core.management.commands.watch_drinking",
    "core.management.commands.watch_thief",
    "core.management.commands.watch_parking",
    "core.timeline",
]


def install_alert_store(run_dir):
    """Swap `Alert` for the in-memory store in every module that writes alerts. Returns the Recorder."""
    global RECORDER, FakeAlert
    import importlib
    RECORDER = Recorder(run_dir)
    FakeAlert = _make_fake_alert()
    patched = []
    for name in ALERT_MODULES:
        mod = importlib.import_module(name)
        if hasattr(mod, "Alert"):
            mod.Alert = FakeAlert
            patched.append(name)
    return RECORDER, patched


def install_hooks():
    """Wrappers that tell the recorder which track/box a write belongs to; defer the AI checker; skip evidence IO."""
    from core.management.commands._incidents import IncidentMixin
    from core.vision import ai_checker, recognition

    orig_sync = IncidentMixin._incident_sync

    def sync(self, key, score, now, **kw):
        CTX.key, CTX.box = key, kw.get("box")
        try:
            return orig_sync(self, key, score, now, **kw)
        finally:
            CTX.key, CTX.box = None, None

    IncidentMixin._incident_sync = sync

    orig_check = ai_checker.check_async

    def deferred(client, ring, kind, keys, t0, note, on_done, **kw):
        CTX.pending_ai.append((t0 + ai_checker.FRAMES_AFTER, (client, ring, kind, keys, t0, note, on_done), kw))
        return None

    ai_checker.check_async = deferred

    def run_due(video_now):
        """Run every held AI check whose trigger + FRAMES_AFTER has been reached in VIDEO time."""
        still = []
        for due, args, kw in CTX.pending_ai:
            if due <= video_now:
                orig_check(*args, **{**kw, "wait": True})
            else:
                still.append((due, args, kw))
        CTX.pending_ai[:] = still

    # evidence IO: not needed for scoring, expensive; LookOut's detection decisions do not depend on it
    recognition.ClipRecorder.save = lambda self, path: False
    recognition.cut_raw_clip = lambda *a, **k: False
    return run_due


def build_command_class(run_due, slow_sec=0.0, progress_cb=None, cancel_cb=None):
    """Subclass of watch_merged_all.Command that clocks frames, records the clean frame and drives the AI queue."""
    from core.management.commands.watch_merged_all import Command as MergedAll

    class AlphaCommand(MergedAll):
        def _open(self, source):
            cap = super()._open(source)
            return cap if (source.isdigit() or "://" in source) else ClockedCapture(cap)

        def _setup_extra(self, options):
            err = super()._setup_extra(options)
            if err is None:
                zone = self.parking.zone
                orig = zone.update

                def upd(dets, t, shape):
                    hits = orig(dets, t, shape)
                    CTX.parking_hits.extend(hits)
                    return hits
                zone.update = upd
            return err

        def _process_frame(self, frame, now, cfg, active, debug, frame_idx=None, timestamp=None, extra=()):
            CTX.frame = frame.copy()
            if slow_sec:
                _real_time.sleep(slow_sec)              # test hook only (verification 11.4)
            super()._process_frame(frame, now, cfg, active, debug, frame_idx, timestamp, extra)
            run_due(now)
            if progress_cb:
                progress_cb(frame_idx or 0, now)
            if cancel_cb and cancel_cb():
                raise KeyboardInterrupt()

        def _run(self, source, debug):
            super()._run(source, debug)
            run_due(float("inf"))                      # clip over: finish every pending AI check

    return AlphaCommand
