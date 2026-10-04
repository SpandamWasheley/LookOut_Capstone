"""Reading the live processing view's files for the web API (see core/vision/debug_view.py)."""
import base64
import os
import time

from core.vision import debug_view


def state_age(directory):
    """Seconds since the detector last published, or None if it never has."""
    try:
        return time.time() - os.path.getmtime(os.path.join(str(directory), "state.json"))
    except OSError:
        return None


def payload(directory, since=None):
    """The JSON the web page polls: subjects, plus the latest clean frame as a data URL when it
    has changed since `since`. {"available": False} until the detector has published once."""
    state, jpeg = debug_view.read_state(str(directory), since)
    if state is None:
        return {"available": False}
    out = dict(state)
    out["available"] = True
    out["age"] = round(state_age(directory) or 0, 1)
    if jpeg is not None:
        out["frame"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    return out
