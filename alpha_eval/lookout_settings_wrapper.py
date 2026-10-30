"""Django settings for alpha_eval's detection worker.

Imports LookOut's real settings (read-only) and changes exactly two things:
  * MEDIA_ROOT  -> this run's sandbox folder, so evidence never lands in lookout_backend/media
  * DATABASES   -> LookOut's db.sqlite3 opened with mode=ro, so even a bug cannot write to it
"""
import os
import sys
from pathlib import Path

_BACKEND = Path(os.environ.get("LOOKOUT_BACKEND", Path(__file__).resolve().parent.parent / "lookout_backend"))
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lookout_backend.settings import *  # noqa: F401,F403,E402

MEDIA_ROOT = Path(os.environ["ALPHA_SANDBOX_MEDIA"])
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": f"file:{(_BACKEND / 'db.sqlite3').as_posix()}?mode=ro",
        "OPTIONS": {"uri": True},
    }
}
