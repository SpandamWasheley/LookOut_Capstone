"""A single-page bench for testing the detector, the scoring and the checker.

The dashboard shows FINISHED alerts. To see why something did or did not alert
you have to run a watcher, read a stats table, and infer the rest -- which is a
slow loop when the question is "would this image alert, and why not?".

This answers that directly: drop in an image, see every detection with its
confidence, every cue that fired with its points, the total, the band, and
optionally what the AI context checker makes of it.

Deliberately NOT part of the dashboard. It is a developer tool: it runs models
synchronously, has no auth, and is registered only when DEBUG is on. Mounting it
beside the operational UI would invite someone to use it as one.
"""

import base64
import time

import numpy as np
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from core.models import SystemSettings
from core.vision import ai_checker, ai_status, recognition, scoring

# Which prompt spec and weight table belong to each violation.
KINDS = {
    "drinking": (scoring.DRINKING_WEIGHTS, "bottle"),
    "smoking": (scoring.SMOKING_WEIGHTS, "cigarette"),
    "holdup": (None, "E14"),
}

# The merged model's class names -> the gate cue each one opens. The model
# reports "Bottle"/"Cigarette"/"knife"; matching is case-insensitive because
# the three engines disagree on case and always have.
LABEL_TO_KIND = {
    "bottle": "drinking",
    "cigarette": "smoking",
    "knife": "holdup",
}


def page(request):
    cfg = SystemSettings.load()
    return render(request, "lab.html", {
        "model_path": recognition.MERGED_MODEL_PATH.name,
        "model_exists": recognition.MERGED_MODEL_PATH.exists(),
        "vlm_model": cfg.vlm_model,
        "vlm_enabled": cfg.vlm_enabled,
        "thresholds": {
            "drinking": cfg.drinking_confidence,
            "smoking": cfg.smoking_confidence,
            "holdup": cfg.thief_confidence,
        },
        "bands": {
            "possible": int(scoring.SCORE_WARNING * 100),
            "likely": int(scoring.SCORE_VIOLATION * 100),
        },
    })


# What the lab may change, and the bounds it may change it within.
#
# An allowlist rather than "any field on SystemSettings": this endpoint has no
# auth (it is DEBUG-only, see the module docstring), so it must not be a way to
# rewrite the email credentials or the VLM key. Bounds are here because a
# threshold of 900 or a negative dwell is not a configuration, it is a bug
# someone will spend an hour chasing.
EDITABLE = {
    "drinking_confidence": (1, 99),
    "smoking_confidence": (1, 99),
    "thief_confidence": (1, 99),
    "drinking_min_group": (1, 10),
    "drinking_group_duration": (1, 3600),
    "drinking_dwell": (1, 300),
    "smoking_dwell": (1, 300),
    "thief_dwell": (1, 300),
    "vlm_enabled": None,          # bool
    "vlm_async": None,            # bool
    "vlm_model": None,            # free text -- an ollama tag
    "vlm_max_edge": (128, 2048),
    "vlm_frames": (1, 12),
    "vlm_timeout": (5, 3600),
}


@csrf_exempt
def config(request):
    """Read or update the settings this bench is allowed to touch."""
    cfg = SystemSettings.load()

    if request.method == "POST":
        changed = {}
        for field, bounds in EDITABLE.items():
            if field not in request.POST:
                continue
            raw = request.POST[field]
            current = getattr(cfg, field)
            try:
                if isinstance(current, bool):
                    value = raw in ("1", "true", "True", "on")
                elif isinstance(current, (int, float)):
                    value = type(current)(raw)
                    lo, hi = bounds
                    if not (lo <= value <= hi):
                        return JsonResponse(
                            {"error": f"{field} must be between {lo} and {hi}."},
                            status=400)
                else:
                    value = raw.strip()
            except (TypeError, ValueError):
                return JsonResponse({"error": f"{field}: {raw!r} is not valid."},
                                    status=400)
            if value != current:
                setattr(cfg, field, value)
                changed[field] = value
        if changed:
            cfg.save(update_fields=list(changed))
        return JsonResponse({"saved": changed, "values": _values(cfg)})

    return JsonResponse({"values": _values(cfg), "editable": list(EDITABLE)})


def _values(cfg):
    return {f: getattr(cfg, f) for f in EDITABLE}


@csrf_exempt
def analyse(request):
    """Run the detector (and optionally the checker) over one uploaded image."""
    if request.method != "POST" or "image" not in request.FILES:
        return JsonResponse({"error": "POST an image."}, status=400)

    import cv2

    upload = request.FILES["image"]
    if _is_video(upload.name):
        return _analyse_video(request, upload)

    raw = np.frombuffer(upload.read(), np.uint8)
    frame = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if frame is None:
        return JsonResponse({"error": "That file is not a readable image."},
                            status=400)

    out = {"kind": "image", "size": f"{frame.shape[1]}x{frame.shape[0]}"}

    # --- the object detector -------------------------------------------------
    t = time.monotonic()
    try:
        # conf 0.05 on purpose: this bench exists to show what the model saw,
        # including the boxes the configured thresholds would discard. Those
        # near-misses are usually the answer to "why didn't this alert?".
        dets = recognition.detect_merged(frame, conf=0.05)
    except Exception as exc:                        # noqa: BLE001
        return JsonResponse({"error": f"{type(exc).__name__}: {exc}"}, status=500)
    out["detect_seconds"] = round(time.monotonic() - t, 2)

    cfg = SystemSettings.load()
    thresholds = {"drinking": cfg.drinking_confidence / 100,
                  "smoking": cfg.smoking_confidence / 100,
                  "holdup": cfg.thief_confidence / 100}

    detections = []
    for x1, y1, x2, y2, conf, label in dets:
        kind = LABEL_TO_KIND.get(label.lower())
        floor = thresholds.get(kind, 1.0)
        detections.append({
            "label": label,
            "kind": kind,
            "confidence": round(conf * 100, 1),
            "threshold": round(floor * 100, 1),
            # The distinction that matters: a box the model found but the
            # configured threshold throws away produces no alert at all, and
            # looks identical to "nothing was detected" from the dashboard.
            "passes": conf >= floor,
            "box": [int(x1), int(y1), int(x2), int(y2)],
        })
    detections.sort(key=lambda d: -d["confidence"])
    out["detections"] = detections

    # --- people, because most cues depend on how many there are -------------
    try:
        persons = recognition.detect_persons(frame, conf=0.4)
    except Exception:
        persons = []
    out["persons"] = len(persons)

    # --- the score, per violation -------------------------------------------
    out["scores"] = [_score_for(kind, detections, len(persons), request)
                     for kind in ("drinking", "smoking", "holdup")]

    # --- the checker, only when asked ---------------------------------------
    if request.POST.get("ask_vlm") == "1":
        out["vlm"] = _ask(frame, request.POST.get("vlm_kind", "smoking"), cfg)

    # The annotated frame, so the boxes are visible rather than described.
    out["image"] = _annotated(frame, detections)
    return JsonResponse(out)


VIDEO_SUFFIXES = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v")

# How often to look. Detection costs ~1-8s per frame on CPU, so every frame of
# a 30s clip is not an option; roughly two looks per second is enough to see an
# object appear, persist and go, which is all the temporal cues need.
SAMPLE_FPS = 2.0

# A hard ceiling regardless of length, so a long clip cannot hang the request.
MAX_SAMPLES = 60

# Longest edge fed to the detector. These clips are 2560x1440; at that size each
# frame is 10.5 MB and the pass is slow, for no accuracy gain the models were
# trained at 640.
WORK_EDGE = 960


def _is_video(name):
    return str(name).lower().endswith(VIDEO_SUFFIXES)


def _analyse_video(request, upload):
    """Sample a clip, run the detector over each sample, and score the whole."""
    import os
    import tempfile

    import cv2

    # OpenCV needs a real path; an InMemoryUploadedFile has none.
    suffix = os.path.splitext(upload.name)[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        for chunk in upload.chunks():
            tmp.write(chunk)
        tmp.close()

        cap = cv2.VideoCapture(tmp.name)
        if not cap.isOpened():
            return JsonResponse({"error": "Could not open that video."},
                                status=400)

        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(int(round(src_fps / SAMPLE_FPS)), 1)
        duration = total / src_fps if total else 0.0

        cfg = SystemSettings.load()
        thresholds = {"drinking": cfg.drinking_confidence / 100,
                      "smoking": cfg.smoking_confidence / 100,
                      "holdup": cfg.thief_confidence / 100}

        timeline, best_shot, best_conf = [], None, -1.0
        max_persons = 0
        idx = taken = 0
        started = time.time()

        while taken < MAX_SAMPLES:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step:
                idx += 1
                continue
            idx += 1
            taken += 1

            work = _fit(frame, WORK_EDGE)
            try:
                dets = recognition.detect_merged(work, conf=0.05)
            except Exception as exc:                 # noqa: BLE001
                cap.release()
                return JsonResponse({"error": f"{type(exc).__name__}: {exc}"},
                                    status=500)
            try:
                persons = len(recognition.detect_persons(work, conf=0.4))
            except Exception:
                persons = 0
            max_persons = max(max_persons, persons)

            rows = []
            for x1, y1, x2, y2, conf, label in dets:
                kind = LABEL_TO_KIND.get(label.lower())
                rows.append({
                    "label": label, "kind": kind,
                    "confidence": round(conf * 100, 1),
                    "threshold": round(thresholds.get(kind, 1.0) * 100, 1),
                    "passes": conf >= thresholds.get(kind, 1.0),
                    "box": [int(x1), int(y1), int(x2), int(y2)],
                })
            timeline.append({
                "t": round((idx - 1) / src_fps, 1),
                "persons": persons,
                "detections": rows,
            })

            # Keep the single most confident frame to show, so the picture is
            # the clip's best evidence rather than whichever frame came first.
            top = max((r["confidence"] for r in rows), default=-1)
            if top > best_conf:
                best_conf, best_shot = top, (work, rows)

        cap.release()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if not timeline:
        return JsonResponse({"error": "No frames could be read from that video."},
                            status=400)

    # --- what persisted, which is the thing a still could never show --------
    per_kind = {}
    for kind in ("drinking", "smoking", "holdup"):
        hits = [f for f in timeline
                if any(r["passes"] and r["kind"] == kind for r in f["detections"])]
        best = max((r["confidence"] for f in timeline for r in f["detections"]
                    if r["kind"] == kind), default=0.0)
        per_kind[kind] = {
            "frames_seen": len(hits),
            "best_confidence": best,
            # Seconds between the first and last sample that carried the object.
            # Not the same as continuous presence, and labelled as such in the
            # UI -- a gap in the middle still counts here.
            "span_seconds": round(hits[-1]["t"] - hits[0]["t"], 1) if len(hits) > 1 else 0.0,
        }

    best_frame, best_rows = best_shot if best_shot else (None, [])
    out = {
        "kind": "video",
        "size": f"{best_frame.shape[1]}x{best_frame.shape[0]}" if best_frame is not None else "-",
        "duration": round(duration, 1),
        "sampled": len(timeline),
        "sample_fps": SAMPLE_FPS,
        "detect_seconds": round(time.time() - started, 1),
        "persons": max_persons,
        "per_kind": per_kind,
        "timeline": timeline,
        "detections": sorted(best_rows, key=lambda r: -r["confidence"]),
        "image": _annotated(best_frame, best_rows) if best_frame is not None else "",
        "scores": [_score_video(k, per_kind[k], max_persons) for k in
                   ("drinking", "smoking", "holdup")],
    }
    if request.POST.get("ask_vlm") == "1" and best_frame is not None:
        out["vlm"] = _ask(best_frame, request.POST.get("vlm_kind", "smoking"),
                          SystemSettings.load())
    return JsonResponse(out)


def _score_video(kind, summary, person_count):
    """Score a clip, using the temporal cues a still cannot supply."""
    cfg = SystemSettings.load()
    seen = summary["frames_seen"] > 0
    span = summary["span_seconds"]

    if kind == "holdup":
        weights = {"E14": 0.45, "E12": 0.20, "E10": 0.10}
        cues = {"E14"} if seen else set()
        # E12/E10 are motion patterns across tracked identities, which this
        # bench does not reproduce -- it samples frames, it does not track.
        missing = ["E12 confrontation freeze", "E10 loitering"]
        if person_count >= 2:
            missing = ["E12 confrontation freeze (2+ people present)"] + missing[1:]
    elif kind == "drinking":
        weights = scoring.DRINKING_WEIGHTS
        cues = {"bottle"} if seen else set()
        if person_count >= 2:
            cues.add("gathering")
        if span >= cfg.drinking_group_duration:
            cues.add("gathering_duration")
        missing = ["at_mouth (needs pose)", "time_band (needs the clock)"]
    else:
        weights = scoring.SMOKING_WEIGHTS
        cues = {"cigarette"} if seen else set()
        missing = ["near_mouth, gesture, puffs (need pose)"]

    score = scoring.Score(kind, weights, cues)
    return {
        "kind": kind,
        "gate_open": score.gate_open,
        "gate_cue": KINDS[kind][1],
        "points": int(round(score.score * 100)),
        "level": scoring.label_of(score.level),
        "visible": score.visible,
        "fired": [{"cue": n, "points": int(round(w * 100))}
                  for n, w in sorted(score.cues.items(), key=lambda kv: -kv[1])],
        "needs_video": missing,
    }


def _fit(frame, edge):
    import cv2

    h, w = frame.shape[:2]
    if max(h, w) <= edge:
        return frame
    sc = edge / float(max(h, w))
    return cv2.resize(frame, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_AREA)


def _score_for(kind, detections, person_count, request):
    """What this violation would score, and what is missing if it would not."""
    gate = KINDS[kind][1]
    hits = [d for d in detections if d["kind"] == kind and d["passes"]]

    if kind == "holdup":
        weights = {"E14": 0.45, "E12": 0.20, "E10": 0.10}
        cues = {"E14"} if hits else set()
        # Everything else needs motion across frames, which one still cannot
        # provide. Shown as unavailable rather than silently scored as absent.
        unavailable = ["E12 confrontation freeze", "E10 loitering"]
    else:
        weights = KINDS[kind][0]
        cues = {gate} if hits else set()
        if kind == "drinking" and person_count >= 2:
            cues.add("gathering")
        unavailable = (["gathering_duration", "at_mouth", "time_band"]
                       if kind == "drinking"
                       else ["near_mouth", "gesture", "puffs"])

    score = scoring.Score(kind, weights, cues)
    return {
        "kind": kind,
        "gate_open": score.gate_open,
        "gate_cue": gate,
        "points": int(round(score.score * 100)),
        "level": scoring.label_of(score.level),
        "visible": score.visible,
        "fired": [{"cue": n, "points": int(round(w * 100))}
                  for n, w in sorted(score.cues.items(), key=lambda kv: -kv[1])],
        # Named explicitly: a single image genuinely cannot show duration,
        # repetition or a freeze, so their absence is a limit of the test and
        # not evidence about the scene.
        "needs_video": unavailable,
    }


def _ask(frame, kind, cfg):
    """One checker call on a single frame (a still has no motion, so the same frame is
    sent a few times), timed, with every failure reported rather than hidden."""
    import cv2

    kind = ai_checker.kind_for(kind) or "smoking"
    client = ai_checker.OllamaClient(model=cfg.vlm_model, endpoint=cfg.resolved_vlm_endpoint,
                                     timeout=max(cfg.vlm_timeout, 600))
    ok, why = client.available()
    if not ok:
        return {"ok": False, "error": why}
    h, w = frame.shape[:2]
    scale = min(1.0, cfg.vlm_max_edge / float(max(h, w)))
    img = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
    data = ai_checker.encode(img)
    result = ai_checker.check(client, kind, ai_checker.system_note(kind), [data] * 2)
    ctx = ai_status.ai_context(kind, {"state": "done", "reply": result.reply, "model": result.model,
                                      "seconds": result.seconds}, "warning")
    return {
        "ok": result.ok,
        "error": result.error,
        "reply": result.reply,
        "badge": ctx["badge"]["text"],
        "checklist": ctx["checklist"],
        "seconds": round(result.seconds or 0, 1),
        "model": result.model,
    }


def _annotated(frame, detections):
    """The frame with boxes drawn, as a data: URI."""
    import cv2

    img = frame.copy()
    for d in detections:
        x1, y1, x2, y2 = d["box"]
        # Green passed the threshold and can open a gate; grey was found and
        # discarded. The difference is the whole point of looking.
        colour = (80, 200, 80) if d["passes"] else (140, 140, 140)
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, 2)
        cv2.putText(img, f'{d["label"]} {d["confidence"]:.0f}%',
                    (x1, max(y1 - 6, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    colour, 2)

    h, w = img.shape[:2]
    if max(h, w) > 900:
        s = 900 / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)))
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
