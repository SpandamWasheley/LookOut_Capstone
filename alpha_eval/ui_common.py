"""Shared Streamlit helpers."""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import config
import db
import scoring

_player = components.declare_component("alpha_player", path=str(config.HERE / "components" / "player"))


@st.cache_resource
def get_conn():
    return db.connect()


def player(clip, markers=None, mark=False, seek=None, seek_nonce=0, key="player"):
    """Inline video player. `markers` are clickable jump points; `mark=True` shows 'Use current time'
    and returns {'t': seconds, 'n': counter} when it is pressed."""
    url = static_url(clip)
    if not url:
        st.info("No video uploaded for this clip yet.")
        return None
    return _player(src=url, markers=markers or [], mark=mark, seek=seek, seek_nonce=seek_nonce, key=key, default=None)


@st.cache_resource
def _media_port():
    import mediaserver
    return mediaserver.start(config.CLIPS_DIR)


def static_url(clip):
    """URL of the clip on the local preview server (only files inside alpha_eval/clips are served)."""
    from urllib.parse import quote
    p = clip.get("video_path") if clip else None
    if not p or not Path(p).exists() or Path(p).resolve().parent != config.CLIPS_DIR.resolve():
        return None
    return f"http://localhost:{_media_port()}/{quote(Path(p).name)}"


def sha256_stream(fileobj, block=1 << 20):
    h = hashlib.sha256()
    while True:
        b = fileobj.read(block)
        if not b:
            break
        h.update(b)
    return h.hexdigest()


def video_props(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return dict(duration_sec=round(n / fps, 2) if fps else None, fps=round(fps, 3), resolution=f"{w}x{h}")


# ---- setup checks --------------------------------------------------------------------------------

def _ollama_models():
    try:
        with urllib.request.urlopen(f"{config.OLLAMA}/api/tags", timeout=3) as r:
            return [m.get("name", "") for m in json.loads(r.read().decode()).get("models", [])]
    except Exception:                                    # noqa: BLE001
        return None


def run_setup_checks():
    """Returns a list of (name, ok, detail) plus the raw self-test dict."""
    env = dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", PYTHONUTF8="1")
    raw = {}
    try:
        p = subprocess.run([sys.executable, str(config.HERE / "worker.py"), "check"], capture_output=True, text=True,
                           timeout=240, env=env, cwd=str(config.HERE), encoding="utf-8", errors="replace")
        for line in p.stdout.splitlines():
            if line.startswith("ALPHA_CHECK_JSON "):
                raw = json.loads(line[len("ALPHA_CHECK_JSON "):])
        if not raw:
            raw = {"error": (p.stderr or p.stdout)[-400:]}
    except Exception as exc:                             # noqa: BLE001
        raw = {"error": str(exc)}
    models = _ollama_models()
    wanted = raw.get("vlm_models") or []
    missing = [m for m in wanted if models is not None and m not in models]
    checks = [
        ("LookOut path", (config.BACKEND / "core" / "management" / "commands" / "watch_merged.py").exists(),
         str(config.BACKEND)),
        ("Django import (LookOut settings, read-only DB)", bool(raw.get("django_import")) and bool(raw.get("lookout_db_readable")),
         raw.get("error", "ok")),
        ("DB write-block active", bool(raw.get("db_write_block")), "an INSERT probe was blocked" if raw.get("db_write_block") else "probe was NOT blocked"),
        ("Video clock active", bool(raw.get("video_clock")), f"{len(raw.get('video_clock_patched', []))} modules patched"),
        ("Ollama reachable", models is not None, config.OLLAMA if models is not None else "start Ollama (ollama serve)"),
        ("Qwen models present", models is not None and bool(wanted) and not missing,
         ", ".join(wanted) if not missing else "missing: " + ", ".join(missing)),
        ("GPU visible", bool(raw.get("gpu")), raw.get("gpu_name") or "no CUDA GPU"),
        ("Template found", config.TEMPLATE.exists(), str(config.TEMPLATE)),
        ("Parking zone file", config.ZONE_FILE.exists(), str(config.ZONE_FILE)),
        ("Model SHA-256", bool(raw.get("model_sha256")), f"{raw.get('model_file')}  {raw.get('model_sha256', '')[:16]}…"),
    ]
    return checks, raw


def setup_ok():
    s = st.session_state.get("setup")
    return bool(s) and all(ok for _, ok, _ in s[0])


def ensure_setup():
    if "setup" not in st.session_state:
        with st.spinner("Running setup checks (about 15 s)…"):
            st.session_state["setup"] = run_setup_checks()
    return setup_ok()


def runner_alive():
    import queue_runner
    return queue_runner.runner_alive(get_conn())


def start_runner():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([sys.executable, str(config.HERE / "worker.py"), "daemon"], cwd=str(config.HERE),
                     env=dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", PYTHONUTF8="1"), creationflags=flags,
                     stdout=open(config.HERE / "runner.log", "ab"), stderr=subprocess.STDOUT)


def clip_status(conn, clip_id):
    """(run status text for the template, latest run, latest done run)"""
    latest = db.latest_run(conn, clip_id)
    done = db.latest_run(conn, clip_id, "done")
    if latest is None:
        return "Not run", None, None
    m = {"queued": "Queued", "running": "Running", "done": "Done", "failed": "Failed", "cancelled": "Not run"}
    return m.get(latest["status"], "Not run"), latest, done


def fmt_t(sec):
    return scoring.fmt_time(sec)
