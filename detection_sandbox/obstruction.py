"""Alias for the canonical rule in lookout_backend/core/vision/obstruction.py.

The backend file is EXECUTED INTO THIS MODULE'S NAMESPACE rather than imported
and re-exported. That distinction matters: every definition then lives in this
module's globals, so tuning a constant here - `obstruction.ENTER_FRACTION = 0.6`,
as the web tester does per request - is seen by the functions that read it. A
plain `from ... import *` would copy the values and the tuning would silently do
nothing.

Loading by path also avoids importing the `core` package, so the sandbox never
pulls Django in.
"""

import importlib.util
import sys
from pathlib import Path

_PATH = (Path(__file__).resolve().parents[1] / "lookout_backend" / "core"
         / "vision" / "obstruction.py")
if not _PATH.exists():           # pragma: no cover - checkout without the backend
    raise ImportError(f"canonical rule not found at {_PATH}")

_spec = importlib.util.spec_from_file_location(__name__, _PATH)
_spec.loader.exec_module(sys.modules[__name__])
