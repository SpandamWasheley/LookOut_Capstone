"""Which entry of Camera.edges belongs to which rule.

Camera.edges is one JSON object that holds TWO independent areas, told apart by
each entry's "type":

  "zone"      parking's no-parking road polygon (key "road" when drawn here)
  "disposal"  where improper disposal watches for dumped rubbish

Anything else (the legacy open kerb lines, keyed left/right with no type) is a
road-side leftover and counts with the road.

This module is the one place that knows that. Every reader that asks "is there
a road?" and every writer that replaces one area must go through it, because the
failure modes are silent: a disposal entry mistaken for a road launches parking
with nothing to judge against, and a writer that assigns `camera.edges = ...`
wholesale deletes whichever area it was not editing.

Pure dict-in / dict-out, no Django, so it is unit-tested on its own.
"""

DISPOSAL_TYPE = "disposal"


def _specs(edges):
    return edges if isinstance(edges, dict) else {}


def is_disposal(spec):
    return isinstance(spec, dict) and spec.get("type") == DISPOSAL_TYPE


def road_entries(edges):
    """The parking side of `edges`: every entry that is not a disposal area."""
    return {k: v for k, v in _specs(edges).items() if not is_disposal(v)}


def disposal_entries(edges):
    """The disposal side of `edges`."""
    return {k: v for k, v in _specs(edges).items() if is_disposal(v)}


def has_road(edges):
    """True when a usable no-parking area is saved.

    Both shapes count: a closed ZONE (type "zone", >= 3 points) and a legacy
    open EDGE (>= 2 points). A disposal polygon never counts -- it has no
    bearing on whether parking has anything to judge a vehicle against.
    """
    for spec in road_entries(edges).values():
        if not isinstance(spec, dict):
            continue
        points = spec.get("points") or []
        minimum = 3 if spec.get("type") == "zone" else 2
        if len(points) >= minimum:
            return True
    return False


def has_disposal(edges):
    return any(len(spec.get("points") or []) >= 3
               for spec in disposal_entries(edges).values())


def with_road(edges, road):
    """`edges` with its road side replaced by `road`; the disposal area is kept."""
    return {**road_entries(road), **disposal_entries(edges)}


def with_disposal(edges, disposal):
    """`edges` with its disposal side replaced by `disposal`; the road is kept."""
    return {**road_entries(edges), **disposal_entries(disposal)}
