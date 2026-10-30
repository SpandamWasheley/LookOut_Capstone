"""Queue runner: the background process that executes queued runs, one at a time (single GPU).

All state lives in eval.db, so closing the browser, restarting Streamlit or restarting this runner never loses
or duplicates a run:
  * a run that was 'running' when the runner died is ADOPTED if its worker process is still alive, otherwise
    re-queued (same run row, attempt + 1) - never a second row.
  * only one runner may be active (heartbeat in the meta table).
"""
import json
import os
import subprocess
import sys
import time

import psutil

import config
import db

HEARTBEAT_STALE = 15.0


def _meta_get(conn, key):
    r = db.one(conn, "SELECT value FROM meta WHERE key=?", key)
    return r["value"] if r else None


def _meta_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()


def pid_alive(pid):
    try:
        return bool(pid) and psutil.pid_exists(int(pid)) and psutil.Process(int(pid)).status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, ValueError):
        return False


def runner_alive(conn):
    hb, pid = _meta_get(conn, "runner_heartbeat"), _meta_get(conn, "runner_pid")
    return bool(hb) and (time.time() - float(hb) < HEARTBEAT_STALE) and pid_alive(pid)


def finalize_run(conn, run_id, adopted_status=None):
    """Read the worker's result files into eval.db."""
    rd = db.run_dir(run_id)
    result = {}
    if (rd / "result.json").exists():
        result = json.loads((rd / "result.json").read_text(encoding="utf-8"))
    snapshot = (rd / "snapshot.json").read_text(encoding="utf-8") if (rd / "snapshot.json").exists() else None
    status = result.get("status") or "failed"
    error = result.get("error") or (None if result else "worker ended without a result (see run.log)")
    cur = db.get_run(conn, run_id)
    if cur and cur["cancel_requested"] and status != "done":
        status = "cancelled"
    conn.execute(
        "UPDATE runs SET status=?, finished_at=?, frames_processed=?, processing_fps=?, processing_time_sec=?,"
        " error=?, snapshot_json=?, blocked_writes=?, worker_pid=NULL WHERE run_id=?",
        (status, db.now_iso(), result.get("frames_processed"), result.get("processing_fps"),
         result.get("processing_time_sec"), error, snapshot, result.get("blocked_writes", 0), run_id))
    conn.commit()
    if status == "done":
        db.ingest_events(conn, run_id)
        import matching
        matching.compute_run(conn, run_id, persist=True)


def recover(conn):
    for r in db.rows(conn, "SELECT * FROM runs WHERE status='running'"):
        if pid_alive(r["worker_pid"]):
            continue                                   # still running: adopted by the main loop
        if (db.run_dir(r["run_id"]) / "result.json").exists():
            finalize_run(conn, r["run_id"])
        elif (r["attempt"] or 0) >= 2:
            conn.execute("UPDATE runs SET status='failed', error='worker died repeatedly', worker_pid=NULL WHERE run_id=?",
                         (r["run_id"],))
        else:
            conn.execute("UPDATE runs SET status='queued', worker_pid=NULL, attempt=COALESCE(attempt,0)+1 WHERE run_id=?",
                         (r["run_id"],))
    conn.commit()


def _beat(conn):
    _meta_set(conn, "runner_heartbeat", time.time())
    _meta_set(conn, "runner_pid", os.getpid())


def _watch(conn, run_id, proc_pid, proc=None):
    """Block until the worker process ends, honouring cancel requests."""
    rd = db.run_dir(run_id)
    while True:
        alive = (proc.poll() is None) if proc is not None else pid_alive(proc_pid)
        if not alive:
            return
        _beat(conn)
        cur = db.get_run(conn, run_id)
        if cur and cur["cancel_requested"]:
            (rd / "cancel.flag").write_text("cancel", encoding="utf-8")
        time.sleep(1.0)


def start_worker(conn, run):
    rd = db.run_dir(run["run_id"])
    rd.mkdir(parents=True, exist_ok=True)
    clip = db.get_clip(conn, run["clip_id"])
    cmd = [sys.executable, str(config.HERE / "worker.py"), "run", "--run-id", run["run_id"],
           "--video", clip["video_path"], "--parking-threshold", str(run["parking_threshold"]),
           "--drinking-duration", str(run["drinking_duration"])]
    if run.get("clock"):
        cmd += ["--clock", run["clock"]]
    slow = os.environ.get("ALPHA_EVAL_TEST_SLOW")
    if slow:
        cmd += ["--slow", slow]
    env = dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", PYTHONUTF8="1")
    out = open(rd / "worker_stdout.log", "ab")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, env=env, cwd=str(config.HERE), creationflags=flags)
    conn.execute("UPDATE runs SET status='running', started_at=COALESCE(started_at, ?), worker_pid=? WHERE run_id=?",
                 (db.now_iso(), proc.pid, run["run_id"]))
    conn.commit()
    return proc


def daemon_loop(once=False):
    conn = db.connect()
    if runner_alive(conn) and str(_meta_get(conn, "runner_pid")) != str(os.getpid()):
        print("A queue runner is already active - not starting a second one.")
        return
    _beat(conn)
    print(f"alpha_eval queue runner started (pid {os.getpid()})")
    while True:
        _beat(conn)
        recover(conn)
        running = db.one(conn, "SELECT * FROM runs WHERE status='running' ORDER BY started_at LIMIT 1")
        if running and pid_alive(running["worker_pid"]):
            print(f"adopting run {running['run_id']} (worker {running['worker_pid']})")
            _watch(conn, running["run_id"], running["worker_pid"])
            finalize_run(conn, running["run_id"])
            continue
        nxt = db.one(conn, "SELECT * FROM runs WHERE status='queued' ORDER BY queued_at, run_id LIMIT 1")
        if nxt is None:
            if once:
                return
            time.sleep(2.0)
            continue
        if nxt["cancel_requested"]:
            conn.execute("UPDATE runs SET status='cancelled', finished_at=? WHERE run_id=?", (db.now_iso(), nxt["run_id"]))
            conn.commit()
            continue
        print(f"running {nxt['run_id']} ({nxt['clip_id']})")
        proc = start_worker(conn, nxt)
        _watch(conn, nxt["run_id"], proc.pid, proc)
        finalize_run(conn, nxt["run_id"])
        print(f"finished {nxt['run_id']}")
        if once and not db.one(conn, "SELECT 1 AS x FROM runs WHERE status='queued'"):
            return
