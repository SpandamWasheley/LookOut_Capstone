"""alpha_eval detection worker.

  python worker.py run    --run-id R --video V --parking-threshold N --drinking-duration N --clock "YYYY-MM-DD HH:MM"
  python worker.py daemon                      (queue runner: picks queued runs from eval.db, one at a time)

`run` is the blind detection process. Its ONLY inputs are the video path, the Set's two test thresholds, the
footage start clock and the run id. It never opens eval.db, so it has no access to the expected results.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config  # noqa: E402


def sha256_file(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(block)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def git_info():
    def git(*args):
        return subprocess.run(["git", "--no-optional-locks", "-C", str(config.BACKEND), *args],
                              capture_output=True, text=True, timeout=30).stdout
    try:
        commit = git("rev-parse", "HEAD").strip()
        dirty_backend = bool(git("status", "--porcelain", "--", ".").strip())
        dirty_repo = bool(git("status", "--porcelain").strip())
        return {"commit": commit, "dirty": dirty_backend, "dirty_repo": dirty_repo}
    except Exception as exc:                         # noqa: BLE001
        return {"commit": "unknown", "dirty": None, "error": str(exc)}


def settings_dump(cfg):
    out = {}
    for f in cfg._meta.concrete_fields:
        v = getattr(cfg, f.attname)
        out[f.name] = v if isinstance(v, (int, float, bool, str, type(None))) else str(v)
    return out


def prepare_work_dir():
    """A neutral working directory holding COPIES of the two small YOLO weights LookOut loads by bare file name
    (so nothing is downloaded and nothing is written inside lookout_backend)."""
    import shutil
    work = config.HERE / "work"
    work.mkdir(exist_ok=True)
    for name in ("yolov8n.pt", "yolov8n-pose.pt"):
        src, dst = config.BACKEND / name, work / name
        if src.exists() and (not dst.exists() or sha256_file(src) != sha256_file(dst)):
            shutil.copyfile(src, dst)
    return work


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def run_detection(a):
    run_dir = Path(a.run_dir) if a.run_dir else config.RUNS_DIR / a.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sandbox = run_dir / "sandbox_media"
    sandbox.mkdir(exist_ok=True)
    log_path = run_dir / "run.log"
    log = open(log_path, "w", encoding="utf-8", buffering=1)
    result = {"run_id": a.run_id, "status": "failed", "error": None}
    t_start = time.time()
    try:
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        os.environ["DJANGO_SETTINGS_MODULE"] = config.SETTINGS_MODULE
        os.environ["ALPHA_SANDBOX_MEDIA"] = str(sandbox)
        os.environ.setdefault("PYTHONUTF8", "1")
        for p in (str(config.BACKEND), str(HERE)):
            if p not in sys.path:
                sys.path.insert(0, p)
        work = prepare_work_dir()
        os.chdir(work)                      # LookOut loads yolov8n*.pt by bare name from the working directory

        import django
        django.setup()
        import dbguard
        guard = dbguard.install(run_dir / "blocked_writes.log")

        import videoclock
        patched = videoclock.install()

        import capture
        recorder, patched_alert = capture.install_alert_store(run_dir)
        run_due = capture.install_hooks()

        from core.models import SystemSettings
        orig_load = SystemSettings.load.__func__
        drink = int(round(float(a.drinking_duration)))

        def load(cls):                                # in-memory override; never saved
            obj = orig_load(cls)
            obj.drinking_group_duration = drink
            return obj
        SystemSettings.load = classmethod(load)

        from core.vision import recognition
        cfg = SystemSettings.load()
        model_path = Path(getattr(recognition, "MERGED_MODEL_PATH", recognition.MODEL_PATH))
        snapshot = {
            "alpha_eval_version": config.TOOL_VERSION,
            "scoring_spec": config.SCORING_SPEC,
            "model_file": model_path.name,
            "model_path": str(model_path),
            "model_sha256": sha256_file(model_path),
            "lookout_git": git_info(),
            "ai_models": {"vlm_model": cfg.vlm_model, "vlm_model_holdup": getattr(cfg, "vlm_model_holdup", ""),
                          "vlm_enabled": cfg.vlm_enabled, "vlm_async": cfg.vlm_async},
            "overrides_in_memory": {"parking_threshold_sec": float(a.parking_threshold),
                                    "drinking_group_duration_sec": drink},
            "clock_start": a.clock or None,
            "system_settings_used": settings_dump(cfg),
            "lookout_env": {k: v for k, v in os.environ.items() if k.startswith("LOOKOUT_")},
            "video_clock_patched": patched,
            "alert_store_patched": patched_alert,
            "zone_file": str(config.ZONE_FILE),
            "zone_sha256": sha256_file(config.ZONE_FILE) if config.ZONE_FILE.exists() else None,
            "aux_weights_sha256": {n: sha256_file(config.HERE / "work" / n)
                                   for n in ("yolov8n.pt", "yolov8n-pose.pt") if (config.HERE / "work" / n).exists()},
            "tracker": "bytetrack", "mode": "far 2x2 + cascade (LookOut defaults)",
            "worker_inputs": {"video": a.video, "parking_threshold": a.parking_threshold,
                              "drinking_duration": a.drinking_duration, "clock": a.clock, "run_id": a.run_id},
            "blocked_writes_active": guard.active,
        }

        import cv2
        cap = cv2.VideoCapture(a.video)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        duration = total_frames / fps if fps else 0.0
        cap.release()
        snapshot["video"] = {"path": a.video, "frames": total_frames, "fps": fps, "duration_sec": duration}
        write_json(run_dir / "snapshot.json", snapshot)

        state = {"frames": 0, "t": 0.0}
        t0 = time.time()

        def progress(frame_idx, now):
            state["frames"], state["t"] = frame_idx, now
            if frame_idx % 10 == 0:
                write_json(run_dir / "progress.json", {"frames": frame_idx, "total_frames": total_frames,
                                                       "video_time": now, "duration": duration,
                                                       "events": recorder.n_events, "elapsed": time.time() - t0})

        cancel_flag = run_dir / "cancel.flag"
        Command = capture.build_command_class(run_due, slow_sec=float(a.slow or 0), progress_cb=progress,
                                              cancel_cb=lambda: cancel_flag.exists())
        from django.core.management import call_command
        args = ["--source", a.video, "--camera", config.CAMERA_CODE, "--zone", str(config.ZONE_FILE),
                "--alert-score", str(float(a.parking_threshold))]
        if a.clock:
            args += ["--clock", a.clock]
        call_command(Command(), *args, stdout=log, stderr=log)

        recorder.finalize()
        recorder.close()
        elapsed = time.time() - t0
        cancelled = cancel_flag.exists()
        result.update(status="cancelled" if cancelled else "done", frames_processed=state["frames"],
                      processing_time_sec=elapsed, processing_fps=(state["frames"] / elapsed if elapsed else None),
                      blocked_writes=len(guard.blocked), error=None)
        if state["frames"] == 0 and not cancelled:
            result.update(status="failed", error="no frames were processed (see run.log)")
        write_json(run_dir / "progress.json", {"frames": state["frames"], "total_frames": total_frames,
                                               "video_time": state["t"], "duration": duration,
                                               "events": recorder.n_events, "elapsed": elapsed, "finished": True})
    except BaseException as exc:                      # noqa: BLE001
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        log.write(traceback.format_exc())
    finally:
        # proof that this process never loaded the eval database / answer-key code (blind detection)
        result["blind_check"] = {m: (m in sys.modules) for m in ("db", "matching", "scoring", "queue_runner", "ui_common", "exporter")}
        result["wall_total_sec"] = time.time() - t_start
        write_json(run_dir / "result.json", result)
        log.close()
    return 0 if result["status"] in ("done", "cancelled") else 1


def run_check():
    """Setup self-test (used by the Setup check page): prints one JSON object. Never writes anything."""
    out = {}
    try:
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
        os.environ["DJANGO_SETTINGS_MODULE"] = config.SETTINGS_MODULE
        os.environ["ALPHA_SANDBOX_MEDIA"] = str(config.HERE / "work" / "check_media")
        for p in (str(config.BACKEND), str(HERE)):
            if p not in sys.path:
                sys.path.insert(0, p)
        import django
        django.setup()
        out["django_import"] = True
        import dbguard
        import tempfile
        guard = dbguard.install(Path(tempfile.gettempdir()) / "alpha_eval_check_blocked.log")
        from django.db import connection
        blocked = False
        try:
            with connection.cursor() as cur:
                cur.execute("INSERT INTO core_systemsettings (id) VALUES (999999)")
        except dbguard.BlockedWrite:
            blocked = True
        except Exception:                                   # noqa: BLE001
            blocked = False
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM core_systemsettings")
            out["lookout_db_readable"] = cur.fetchone()[0] >= 1
        out["db_write_block"] = bool(blocked and guard.active)
        import videoclock
        patched = videoclock.install()
        videoclock.CLOCK.set(12.5)
        import core.management.commands.watch_merged as wm
        out["video_clock"] = bool(patched) and wm.time.time() == 12.5
        out["video_clock_patched"] = patched
        import capture
        capture.install_hooks()
        from core.vision import recognition
        mp = Path(getattr(recognition, "MERGED_MODEL_PATH", recognition.MODEL_PATH))
        out["model_file"] = mp.name
        out["model_sha256"] = sha256_file(mp)
        import torch
        out["gpu"] = bool(torch.cuda.is_available())
        out["gpu_name"] = torch.cuda.get_device_name(0) if out["gpu"] else None
        from core.models import SystemSettings, Camera
        cfg = SystemSettings.load()
        out["vlm_models"] = [m for m in {cfg.vlm_model, getattr(cfg, "vlm_model_holdup", "")} if m]
        out["vlm_enabled"] = bool(cfg.vlm_enabled)
        out["vlm_endpoint"] = cfg.resolved_vlm_endpoint
        out["camera_zone_in_db"] = bool(Camera.objects.filter(code=config.CAMERA_CODE).values_list("edges", flat=True).first())
    except BaseException as exc:                            # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        out.setdefault("django_import", False)
    out["git"] = git_info()
    print("ALPHA_CHECK_JSON " + json.dumps(out, default=str))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("--run-id", required=True)
    r.add_argument("--video", required=True)
    r.add_argument("--parking-threshold", type=float, required=True)
    r.add_argument("--drinking-duration", type=float, required=True)
    r.add_argument("--clock", default="")
    r.add_argument("--run-dir", default="")
    r.add_argument("--slow", type=float, default=0.0, help="TEST ONLY: sleep this long per frame")
    sub.add_parser("daemon")
    sub.add_parser("check")
    a = ap.parse_args()
    if a.mode == "run":
        sys.exit(run_detection(a))
    if a.mode == "check":
        run_check()
        return
    from queue_runner import daemon_loop          # imported lazily: needs eval.db, not Django
    daemon_loop()


if __name__ == "__main__":
    main()
