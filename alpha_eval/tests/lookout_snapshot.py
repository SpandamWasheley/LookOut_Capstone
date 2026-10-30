"""Read-only fingerprint of LookOut's database, media folder and git state (verification 2).

  python tests/lookout_snapshot.py > before.json      (and again after the runs, then diff)
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snapshot():
    out = {"db_sha256": sha(config.LOOKOUT_DB), "db_size": config.LOOKOUT_DB.stat().st_size,
           "db_mtime": config.LOOKOUT_DB.stat().st_mtime}
    con = sqlite3.connect(f"file:{config.LOOKOUT_DB.as_posix()}?mode=ro", uri=True)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out["row_counts"] = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    out["systemsettings"] = [list(r) for r in con.execute("SELECT * FROM core_systemsettings")]
    con.close()
    files = [p for p in config.LOOKOUT_MEDIA.rglob("*") if p.is_file()]
    out["media_files"] = len(files)
    out["media_bytes"] = sum(p.stat().st_size for p in files)
    porcelain = subprocess.run(["git", "--no-optional-locks", "-C", str(config.BACKEND), "status", "--porcelain", "--", "."],
                               capture_output=True, text=True).stdout
    out["git_status_lines"] = len(porcelain.splitlines())
    out["git_status_sha256"] = hashlib.sha256(porcelain.encode()).hexdigest()
    out["git_head"] = subprocess.run(["git", "-C", str(config.BACKEND), "rev-parse", "HEAD"], capture_output=True,
                                     text=True).stdout.strip()
    out["backend_tracked_diff_sha256"] = hashlib.sha256(subprocess.run(
        ["git", "--no-optional-locks", "-C", str(config.BACKEND), "diff", "--", "."], capture_output=True).stdout).hexdigest()
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1, default=str))
