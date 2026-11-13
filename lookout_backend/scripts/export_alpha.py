"""alpha_logs/*.json -> alpha_logs/alpha_summary.csv: one row per alert, plus one row per clip that filed none.

    python scripts/export_alpha.py            (from lookout_backend)

Peak status and the AI verdict are refreshed from the database when the alert row still exists there; otherwise the values
the run wrote are kept. Read-only: it never writes to the database.
"""
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = Path(os.environ.get("LOOKOUT_ALPHA_LOGS") or ROOT / "alpha_logs")
FIELDS = ["clip", "log_file", "commit", "alert", "type", "track_id", "first_alert_t", "first_status", "peak_status",
          "ai_state", "ai_verdict", "peak_knife_conf", "peak_cigarette_conf", "max_group_size", "run_seconds"]


def _db():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lookout_backend.settings")
    try:
        import django
        django.setup()
        from core.models import Alert
        return Alert
    except Exception:
        return None


def _peak(samples, field):
    vals = [s.get(field) or 0 for s in samples]
    return max(vals) if vals and max(vals) else ""


def main():
    Alert = _db()
    rows = []
    for f in sorted(FOLDER.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        base = {"clip": d["header"]["clip"], "log_file": f.name, "commit": d["header"]["commit"],
                "run_seconds": d.get("run_seconds")}
        if not d["alerts"]:
            rows.append({**base, "alert": "(no alert)"})
            continue
        for a in d["alerts"]:
            sid = a["key"].strip("()").replace("'", "").replace(", ", ":")
            mine = [s for s in d["samples"] if s["id"] == sid]
            peak, ai_state, ai_verdict = a.get("peak_status"), a.get("ai_state"), a.get("ai_verdict")
            obj = None
            if Alert is not None:
                try:
                    obj = Alert.objects.filter(pk=a["pk"], code=a["code"]).first()
                except Exception:
                    obj = None
            if obj is not None:
                peak = obj.peak_level
                ai = obj.ai or {}
                ai_state = ai.get("state")
                ai_verdict = {k: v for k, v in (ai.get("reply") or {}).items() if k != "observations"} or None
            rows.append({**base, "alert": a["code"], "type": a["type"], "track_id": a["track_id"],
                         "first_alert_t": a["first_alert_t"], "first_status": a["first_status"], "peak_status": peak,
                         "ai_state": ai_state, "ai_verdict": json.dumps(ai_verdict) if ai_verdict else "",
                         "peak_knife_conf": _peak(mine, "knife_conf"), "peak_cigarette_conf": _peak(mine, "cigarette_conf"),
                         "max_group_size": _peak(mine, "group_size")})
    FOLDER.mkdir(parents=True, exist_ok=True)
    out = FOLDER / "alpha_summary.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
