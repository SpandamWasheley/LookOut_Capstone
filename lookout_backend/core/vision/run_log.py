"""Per-run evidence log for Run Detection (alpha testing). LOGGING ONLY: nothing here is read by detection or scoring.

One JSON file per run, alpha_logs/<clip>_<timestamp>.json (override the folder with LOOKOUT_ALPHA_LOGS, switch off with
LOOKOUT_RUN_LOG=0): a header (clip, commit, model, the settings that decide a status), about one sample per second per
person/group for smoking, drinking and knife, and the alerts the run filed. Every hook is a no-op until start() is called,
and start() is only called for a recorded clip, never a live camera.
"""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

from django.conf import settings

KINDS = {"smoking": "smoking", "drinking": "drinking", "weapon": "knife", "holdup": "knife"}   # Score.kind -> log section

_run = None


def _folder():
    return Path(os.environ.get("LOOKOUT_ALPHA_LOGS") or (Path(settings.BASE_DIR) / "alpha_logs"))


def _commit():
    try:
        root = Path(settings.BASE_DIR)
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "core/vision", "core/management"], cwd=root,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return h + ("+uncommitted" if dirty else "")
    except Exception:
        return "unknown"


def _header(cmd, source):
    from core.models import SystemSettings
    from core.vision import location_momentum as lm, recognition, scoring
    from core.management.commands import watch_thief
    cfg = SystemSettings.load()
    return {
        "clip": Path(source).name, "camera": getattr(getattr(cmd, "camera", None), "code", None), "commit": _commit(),
        "model_path": str(getattr(recognition, "MERGED_MODEL_PATH", recognition.MODEL_PATH)),
        "settings": {
            "possible_score": round(scoring.SCORE_WARNING * 100), "likely_enabled": scoring.LIKELY_ENABLED,
            "drinking_group_duration_s": cfg.drinking_group_duration, "drinking_min_group": cfg.drinking_min_group,
            "drinking_passerby_filter": cfg.drinking_passerby_filter,
            "cigarette_min_conf_for_possible": lm.POSSIBLE_MIN_CONF, "bottle_overlap_veto": lm.BOTTLE_OVERLAP_VETO,
            "smoking_confidence_pct": cfg.smoking_confidence, "drinking_confidence_pct": cfg.drinking_confidence,
            "knife_confidence_pct": cfg.thief_confidence, "knife_max_wrist_dist_frac": watch_thief.KNIFE_MAX_WRIST_DIST_FRAC,
        },
    }


class _Run:
    def __init__(self, cmd, source):
        self.cmd, self.source = cmd, source
        self.t0 = datetime.datetime.now()
        self.header = _header(cmd, source)
        self.samples, self.alerts = [], []
        self.last_sec = {}          # key -> last whole second sampled
        self.wrist = {}             # track id -> last wrist distance / person height
        self.puffs = {}             # track id -> puffs inside the Likely window (location_momentum)
        self.passers = 0            # people the drinking passer-by filter left out of group formation, this frame


def start(cmd, source):
    """Begin logging this run (recorded clips only)."""
    global _run
    _run = None
    if os.environ.get("LOOKOUT_RUN_LOG", "1") == "0" or source.isdigit() or "://" in source:
        return
    try:
        _run = _Run(cmd, source)
    except Exception:
        _run = None


def note_wrist(track_id, frac):
    if _run is not None:
        _run.wrist[track_id] = round(float(frac), 3)


def note_puffs(track_id, n):
    if _run is not None:
        _run.puffs[track_id] = int(n)


def note_passers(n):
    if _run is not None:
        _run.passers = int(n)


def _num(v, nd=2):
    return None if v is None else round(float(v), nd)


def note_score(cmd, key, score, now, extra_cues=None):
    """One call per scored person/group per frame (from IncidentMixin._incident_sync); keeps one sample a second."""
    run = _run
    if run is None or now is None:
        return
    try:
        section = KINDS.get(score.kind)
        if section is None:
            return
        extra = extra_cues or {}
        conf = extra.get("object_confidence")
        cues = sorted(score.cues)
        if not cues and not score.score and conf is None:
            return                                              # nothing to say about this person this frame
        sec = int(now)
        if run.last_sec.get(key) == sec:
            return
        run.last_sec[key] = sec
        tid = key[1]
        row = {"t": sec, "type": section, "id": str(key[0]) + ":" + str(tid), "score": _num(score.score * 100, 0),
               "status": score.level, "cues": cues, "cap": getattr(score, "capped", "") or ""}
        if section == "smoking":
            row.update(cigarette_conf=_num(conf), cigarette_cue="cigarette" in cues, gesture_count=extra.get("puffs_seen"),
                       puff_count=run.puffs.get(tid), near_mouth="near_mouth" in cues, cap_active=bool(row["cap"]))
        elif section == "drinking":
            row.update(group_size=extra.get("gathering_size"), passersby_excluded=run.passers,
                       stay_seconds=extra.get("gathering_seconds"), bottle_linked="bottle" in cues,
                       bottle_conf=_num(conf))
        else:
            row.update(knife_conf=_num(conf), wrist_dist=run.wrist.get(tid), momentum_on="momentum" in cues)
        run.samples.append(row)
    except Exception:
        pass


def note_alert(cmd, key, alert, now, level):
    run = _run
    if run is None or alert is None:
        return
    try:
        run.alerts.append({"code": alert.code, "pk": alert.pk, "type": getattr(alert.type, "code", None),
                           "key": str(key), "track_id": key[1], "first_alert_t": _num(now, 1), "first_status": level})
    except Exception:
        pass


def finish():
    """Write the file. Peak status and the AI verdict are read back from the alert rows (the AI may still be running;
    scripts/export_alpha.py refreshes them later)."""
    global _run
    run, _run = _run, None
    if run is None:
        return None
    try:
        from core.models import Alert
        alerts = []
        for a in run.alerts:
            row = dict(a)
            obj = Alert.objects.filter(pk=a["pk"]).first()
            if obj is not None:
                row["peak_status"] = obj.peak_level
                ai = obj.ai or {}
                row["ai_state"] = ai.get("state")
                reply = ai.get("reply") or {}
                row["ai_verdict"] = {k: v for k, v in reply.items() if k != "observations"} or None
            alerts.append(row)
        out = {"header": run.header, "samples": run.samples, "alerts": alerts,
               "run_seconds": round((datetime.datetime.now() - run.t0).total_seconds(), 1)}
        folder = _folder()
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w.-]+", "_", Path(run.source).stem)
        path = folder / f"{stem}_{run.t0.strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
        return path
    except Exception:
        return None
