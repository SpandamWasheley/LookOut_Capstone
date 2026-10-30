"""Paths and constants shared by every alpha_eval module. No imports from LookOut here."""
import os
from pathlib import Path

TOOL_VERSION = "1.0.0"
SCORING_SPEC = "v6.1"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND = Path(os.environ.get("LOOKOUT_BACKEND", ROOT / "lookout_backend"))   # READ-ONLY, never written
LOOKOUT_DB = BACKEND / "db.sqlite3"                                           # opened mode=ro only
LOOKOUT_MEDIA = BACKEND / "media"
SETTINGS_MODULE = "lookout_settings_wrapper"

EVAL_DB = Path(os.environ.get("ALPHA_EVAL_DB", HERE / "eval.db"))        # env overrides are for the test harness only
CLIPS_DIR = HERE / "clips"
RUNS_DIR = Path(os.environ.get("ALPHA_EVAL_RUNS", HERE / "runs"))
EXPORTS_DIR = HERE / "exports"
TEMPLATE = HERE / "templates" / "LookOut_Alpha_Testing_Template.xlsx"
ZONE_FILE = HERE / "parking_zone.json"          # parking polygon of the installed camera (see DECISIONS D8)
ZONE_SEED = BACKEND / "zone_mine.json"          # copied (read) into ZONE_FILE the first time
CAMERA_CODE = "CAM-SMOKE-01"
OLLAMA = "http://localhost:11434"

# Template geometry
PLANNED_ROWS = (5, 59)
SPARE_ROWS = (60, 154)
LOG_ROWS = (5, 404)

# Names, in the template's exact spelling
TYPES = ["Smoking", "Drinking", "Parking Obstruction", "Holdup"]
KINDS = ["Violation", "Look-alike", "No violation"]
SETS = ["Main", "Validation"]
DISTANCES = ["Near", "Mid", "Far"]
TIMES_OF_DAY = ["Day", "Afternoon", "Night"]
TAGS = ["Duplicate", "False alarm", "Unscripted – false alarm", "Unscripted – real violation", "Late (real act)"]
MISS_REASONS = ["Distance / too small", "Occlusion", "Lighting / glare", "Track ID switch",
                "Alert too late", "Wrong type", "Look-alike object", "Crowd", "Other"]
STATUSES = ["Monitoring", "Possible", "Likely"]

# internal LookOut names -> template names (the ONE place this mapping lives)
INTERNAL_TO_TYPE = {"smoking": "Smoking", "drinking": "Drinking", "theft": "Holdup", "thief": "Holdup",
                    "holdup": "Holdup", "parking": "Parking Obstruction"}
LEVEL_TO_STATUS = {"monitoring": "Monitoring", "warning": "Possible", "violation": "Likely"}

# Time of day -> clock time used as the footage start when the clip has no explicit recorded time (DECISIONS D5)
DEFAULT_CLOCK = {"Day": "10:00", "Afternoon": "17:00", "Night": "23:00"}

DEFAULT_EVAL_SETTINGS = {
    "alert_window_sec": 60.0, "start_tolerance_sec": 5.0, "monitoring_counts_as_alert": 0,
    "parking_threshold_main_sec": 120.0, "parking_threshold_validation_sec": 300.0,
    "drinking_group_duration_main_sec": 60.0, "drinking_group_duration_validation_sec": 600.0,
}
