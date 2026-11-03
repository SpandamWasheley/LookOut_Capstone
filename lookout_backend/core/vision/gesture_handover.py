"""Lets a person who is given a new track ID keep the hand-to-mouth draws their old ID had counted.

Pure bookkeeping, no Django, no models. The tracker re-issues IDs to a person who walks, turns or is briefly hidden, and the
smoking gesture counter lives on the track, so each new ID started from zero: a woman who made one draw under ID 1 and one under
ID 15 never reached the two draws (or three cycles) the puff-only path needs, although the same person made both.

A new track with no counted draws adopts them from an old track only when the old track has vanished (not seen for MIN_GAP_S, so a
live track is never robbed), was seen at most MAX_GAP_S ago, and the new box's centre lies inside the old track's last box (grown by MARGIN_X / MARGIN_Y old-box heights, because a walker moves on while the old ID is being dropped). Each old
track is adopted at most once. The caller decides whether the person may adopt at all (watch_smoking refuses anyone a Bottle is
associated with). With stable IDs `adopt` never finds anything.
"""
GESTURE_HANDOVER = True        # False = every new track ID starts its draw count from zero (the old behaviour)
MAX_GAP_S = 1.5
MIN_GAP_S = 0.25
MARGIN_X = 0.5               # the new box's centre may lie this many old-box heights outside the old box sideways...
MARGIN_Y = 0.25              # ...and this many above / below it
YOUNG_TRACK_S = 3.0           # a track older than this never adopts: a person who has been tracked this long keeps their own count


class GestureHandover:
    def __init__(self):
        self._seen = {}                 # track id -> [box, last_seen, track]

    def note(self, track, box, now):
        """`now` is when the track was last MATCHED to a detection (Track.last_seen), not the current frame: the tracker keeps a lost
        track in its list for a while with its stale box, and that must not look like a person who is still there."""
        self._seen[track.id] = [tuple(box), now, track]

    def adopt(self, track, box, now):
        """The old track whose counted draws `track` should inherit, or None. Pops it, so it is adopted once."""
        if not GESTURE_HANDOVER:
            return None
        for tid in [t for t, e in self._seen.items() if now - e[1] > MAX_GAP_S]:
            del self._seen[tid]
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        best, best_d = None, None
        for tid, (old_box, seen, old) in self._seen.items():
            age = now - seen
            if tid == track.id or age < MIN_GAP_S or age > MAX_GAP_S:
                continue
            if not (old.gestures or old.strong_draws):
                continue                                        # nothing worth carrying
            oh = old_box[3] - old_box[1]                            # a walker moves on while the old ID is being dropped
            if not (old_box[0] - MARGIN_X * oh <= cx <= old_box[2] + MARGIN_X * oh
                    and old_box[1] - MARGIN_Y * oh <= cy <= old_box[3] + MARGIN_Y * oh):
                continue
            d = abs(cx - (old_box[0] + old_box[2]) / 2) + abs(cy - (old_box[1] + old_box[3]) / 2)
            if best is None or d < best_d:
                best, best_d = tid, d
        return self._seen.pop(best)[2] if best is not None else None

    @staticmethod
    def carry(old, new):
        """Copy the counted draws (not the hand-in-progress) from `old` to `new`."""
        new.gestures.extend(old.gestures)
        new.gestures_still.extend(old.gestures_still)
        new.strong_draws.extend(old.strong_draws)
        new.strong_draw_holds.extend(old.strong_draw_holds)
        new.object_puffs.update(old.object_puffs)
