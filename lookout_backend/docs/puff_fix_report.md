# Smoking puff fix — proposal (NOT committed)

Status: **the edit is in the working tree of `core/vision/location_momentum.py` only; nothing is committed. Waiting for your OK.**
Base: commit `716fa9a`, plus other people's uncommitted work (left untouched). No existing numeric value was changed. One number the brief itself gave (2 s) is a new named constant, and one reading of "same confidence" needs your confirmation (see Questions).

## Result in one paragraph

The fix stops the Aug7 smoker (ID 1, the man on his phone) from reaching Possible or Likely, and changes nothing on Holdup 2 or FH. **It does not give the expected "no smoking alert in Aug7"**: the same clip still raises a Smoking Possible → Likely at 46.3–46.4 s for the *other* man (ID 11, seated, drinking from a bottle), through two paths the brief leaves as they are (Questions 1 and 2). Switching the first path off (test only) makes Aug7 clean but leaves the real smoker in Aug18 at Monitoring, because he has no strict puff either.

## Changes (one line each)

| # | Change | Reason |
|---|---|---|
| 1 | `update_puffs`: every pose reach now waits for a completed hand-to-mouth cycle (`PUFF_NEEDS_COMPLETED_GESTURE`, new boolean). | A puff is a completed gesture: enter ≤ 0.9 SW held 0.7 s, exit ≥ 1.0 SW (`tracking.HAND_RAISED_RATIO`, `GESTURE_APEX_HOLD_SECONDS`, `HAND_LOWERED_RATIO`). Those values live in `Track.update_gesture`, not in `update_puffs` (which used 0.7 SW, no hold, no exit), so I reuse its cycle instead of copying numbers. Before, only a zoomed-pose reach had to wait. |
| 2 | One puff per person per moment: a puff within `PUFF_ONE_PER_MOMENT_SECONDS = 2.0` of the last one is dropped (both wrists, and the object path). | Left and right wrist both counted at 12.1 s on Aug7. The 2.0 is the number in your brief. |
| 3 | Boxes that confirm a puff (and "cigarette at hand or mouth now") must be ≥ `POSSIBLE_MIN_CONF` (0.45). | A 0.36 box confirmed a puff at 4.6 s. I read "same as the object gate" as the 0.45 that gates Possible (the detector's own floor is 0.30). |
| 4 | `puff_cap`: a puff inside the 10 s window only raises the status while a cigarette is at a wrist or the mouth within `MOUTH_ACCEPT_FRESH_SECONDS` (1.0 s, existing). New `cig_in_hand_or_mouth_now` and `CAP_NOT_NOW_TEXT`. Older puffs still count in `puffs_within` (totals, the 30 s Likely count). | The status must follow the present: the Aug7 Likely used puffs banked at 12.1 s and a box that left at 20.3 s. |
| – | Puff-only path: **not touched.** It is off (`PUFF_ONLY_ENABLED = False`) and counts the pose counter's completed cycles, which already use the strict definition. | As asked. |

**Tests:** `tests_far_puff`, `tests_location_momentum`, `tests_false_alarm_fixes`, `tests_incidents`, `tests_v6210`, `tests_puff_only_off`, `tests_scoring_v6`, `tests_vape_size_gate`: 292 run, **6 fail**, all because they assert the old behaviour:
`tests_far_puff…two_make_likely_possible`, `tests_location_momentum.PuffTests.test_cap_rules`, `…test_sticky_possible_never_falls_back_to_monitoring_when_the_window_expires`, `ZoomCycleTests.test_switch_off`, `ZoomCycleTests.test_whole_frame_reach_needs_no_cycle`, `tests_false_alarm_fixes…test_a_puff_is_enough_without_the_mouth`.
Two of those files carry other people's uncommitted edits, so I did not touch any of them; I will update them with your OK.

## Dry runs

"Before" = the committed `location_momentum.py` loaded into the same runner, on the same working tree. Aug7 alert rows come from a real (non-dry) run on a scratch DB copy, 0–60 s, because dry-run mode holds the spot cooldown differently (it also does it for Monitoring rows). Times are seconds into the clip.

| Clip | Before | After |
|---|---|---|
| **Aug7_16 Trim** | Smoking row on ID 1: Monitoring 10.7, Possible 11.0, **Likely 21.9**. Puffs on ID 1: 4.6 (L, box 0.36), 12.1 (R, 0.47), 12.1 (L, 0.47), 24.3 (L, 0.47), 24.8 (L, 0.38); then ID 11 at 50.4 and 60.9. | ID 1: **Monitoring only** (capped "no cigarette at the hand or mouth now" at 12.1 and 21.7), no row. Puffs: 4.6 (L, 0.49), 12.1 (R, 0.47), 24.3 (L, 0.47). **But ID 11 (bottle at the mouth): Possible 46.3, Likely 46.4, a Smoking Likely row.** Other rows unchanged: Drinking Possible, Holdup Likely 44.5, Holdup Possible 52.6. |
| **Aug18_18 NightNear** | Earlier single-process run: Monitoring 16.6, Possible 16.7, Likely 42.3. Same-tree dry run (2–4 jobs in parallel): Monitoring 16.6, Possible 16.7, no Likely. | Identical to the same-tree "before": Possible 16.7, no Likely. The smoker has **no strict puff at all** (the only puff in the clip is ID 12, object path, 51.2); Possible comes from "cigarette at the mouth with momentum ≥ 1.0", unchanged. |
| **Holdup 2** | Holdup Monitoring 40.6 → Likely 41.7. Smoking Monitoring 7.4 (capped, no puff). No puffs. | Identical. |
| **FH FriendHug** | No smoking events. 11 object-path puffs. Knife Monitoring ×4, Drinking Monitoring 93.1. | Identical (same 11 puffs, same incidents). |

Variant for the Questions below (test only: the "cigarette at the mouth" route switched off, so Possible needs a strict puff): Aug7 → ID 1 and ID 11 stay Monitoring (clean); Aug18 → the smoker stays **Monitoring** (a hidden row, no Possible or Likely).

**Caveat on Aug18:** the Likely at 42.3 in my first sanity run did not reappear in either the "before" or "after" dry run on this tree. The earlier run was single-process; these ran 2–4 at once, the pose pass may be load-sensitive, and the tree also changed (passer-by filter). Before vs after is the same either way; I did not isolate the cause.

## Pose component test (pose_eval, saved detections, 5 POSE clips)

`pose_eval` scores `Track.update_gesture` cycles. It does not call `update_puffs`, `puff_cap` or anything else in this diff, and it saves no cigarette boxes. Re-running `score` in a copy (`scratch_sanity/pose_eval_copy`, so your `results/` is untouched) with the new code gives **exactly the saved numbers**, as expected. Its read-only guard also flags "tree changed" because the backend tree changed.

| clip | real | sys | TP before → after | FN | FP | precision | recall |
|---|---|---|---|---|---|---|---|
| POSE_01 near day | 7 | 10 | 7 → 7 | 0 → 0 | 3 → 3 | 0.70 → 0.70 | 1.00 → 1.00 |
| POSE_02 far day | 7 | 6 | 5 → 5 | 2 → 2 | 1 → 1 | 0.83 → 0.83 | 0.71 → 0.71 |
| POSE_03 far day | 5 | 6 | 2 → 2 | 3 → 3 | 4 → 4 | 0.33 → 0.33 | 0.40 → 0.40 |
| POSE_04 near night | 6 | 6 | 6 → 6 | 0 → 0 | 0 → 0 | 1.00 → 1.00 | 1.00 → 1.00 |
| POSE_05 far night | 6 | 1 | 0 → 0 | 6 → 6 | 1 → 1 | 0.00 → 0.00 | 0.00 → 0.00 |
| **Pooled** | 31 | 29 | 20 → 20 | 11 → 11 | 9 → 9 | 0.69 → 0.69 | 0.65 → 0.65 |

To test the new `update_puffs` on the POSE clips I would need cigarette detections saved per frame; `pose_eval` does not keep them. Say so if you want that added (it means re-running detection).

## Questions (need your decision; I stopped here)

1. **The "cigarette at the mouth" route to Possible** (`puff_cap`: momentum ≥ 1.0 at the mouth anchor, no puff needed). It is what lets ID 11's bottle read as a cigarette, and it is also the only reason the Aug18 smoker reaches Possible. Your fix 4 says Possible needs a puff; applied literally this route goes and Aug18's smoker stays Monitoring. Keep it, remove it, or tighten it?
2. **Sticky status.** Once an incident is Possible, `puff_cap` lifts every cap (documented design, asserted by a test in `tests_location_momentum`), so banked points take it to Likely with no present evidence (ID 11, 46.3 → 46.4). "Banked puffs can't raise the status on their own" needs that changed.
3. **"Same confidence as the cigarette object gate":** 0.45 (`POSSIBLE_MIN_CONF`, what I used) or the detector floor 0.30 (`smoking_confidence`)? At 0.30 change 3 does nothing.
4. The new 2.0 s constant: fine as a named constant?

## Diff (`core/vision/location_momentum.py`)

```diff
diff --git a/lookout_backend/core/vision/location_momentum.py b/lookout_backend/core/vision/location_momentum.py
index ff9dfbc..1e36514 100644
--- a/lookout_backend/core/vision/location_momentum.py
+++ b/lookout_backend/core/vision/location_momentum.py
@@ -67,6 +67,14 @@ WALK_SPEED_HEIGHTS_PER_SEC = 0.3
 # zoomed pose therefore also needs a cycle completed at or after its reach (within the pair window, which is how long a reach waits).
 PUFF_ZOOM_NEEDS_GESTURE_CYCLE = True
 PUFF_GESTURE_SLACK_SECONDS = 0.5    # the cycle may complete this long before the reach was registered (frame / keypoint jitter)
+# Puff fix (manuscript definition): a puff is a COMPLETED hand-to-mouth gesture -- the pose counter's cycle (wrist at the face
+# <= tracking.HAND_RAISED_RATIO shoulder-widths held tracking.GESTURE_APEX_HOLD_SECONDS, then out past tracking.HAND_LOWERED_RATIO).
+# Until now only a zoomed-pose reach had to be followed by one; a reach from the whole-frame pose counted the moment it touched the
+# zone. True = every pose reach needs the completed cycle (the far-range object puff has no wrist and is unchanged).
+PUFF_NEEDS_COMPLETED_GESTURE = True
+# One puff per person per moment: a second puff (the other wrist, or the object path) within this long of the last one is the same
+# gesture seen twice and is not counted again.
+PUFF_ONE_PER_MOMENT_SECONDS = 2.0
 FAR_PUFF_BELOW_FRAC = 0.25          # legacy size trigger: NOT used since v6.2.11 (the object puff needs pose to be unable to measure)
 FAR_PUFF_HEAD_ZONE = 0.22           # head zone = top this fraction of the person box (0 = top, 1 = bottom)
 FAR_PUFF_BODY_ZONE = (0.30, 0.70)   # chest / hand zone
@@ -107,6 +115,7 @@ MOUTH_ACCEPT_FRESH_SECONDS = 1.0    # "accepted at the mouth" must be this recen
 CAP_NO_PUFF_TEXT = "capped: no puff"
 CAP_LOW_CONF_TEXT = "capped: cigarette confidence under 0.45"
 CAP_FEW_PUFFS_TEXT = "capped: needs 2 puffs in 30 s for Likely"
+CAP_NOT_NOW_TEXT = "capped: no cigarette at the hand or mouth now"
 
 # holdup (v6.2.1, reworked in v6.2.5). True = knife stability rules, E12 as "stayed close" with pass-through rejection, the
 # "knife + E12" gate (capped at Monitoring without it) and the sticky holdup status. False = the pre-v6.2 (v6) holdup rules.
@@ -578,7 +587,10 @@ def update_puffs(cmd, track, dets, now):
                    if anchors.get("has_shoulders") else anchors["close"])
     mouth = anchors.get(MOUTH)
     shoulder_w = anchors["close"] / CLOSE_SHOULDER_FRAC if anchors.get("has_shoulders") else 0.0
+    # A box that confirms a puff (or shows a cigarette at the hand / mouth now) must reach the same confidence the cigarette needs
+    # for Possible (POSSIBLE_MIN_CONF); a weaker box is not evidence of a puff.
     cig_dets = [(tuple(d[:4]), float(d[4])) for d in dets if str(d[5]).lower() == "cigarette"
+                and float(d[4]) >= POSSIBLE_MIN_CONF
                 and not class_flipped(now, tuple(d[:4]), "cigarette")]
     cigs = [b for b, _c in cig_dets]
 
@@ -620,9 +632,13 @@ def update_puffs(cmd, track, dets, now):
     for w, t_reach in list(loc.reaches.items()):
         seen = {"wrist": loc.cig_at_wrist.get(w), "mouth": loc.cig_at_mouth}
         ok = {k: t for k, t in seen.items() if t is not None and abs(t - t_reach) <= PUFF_PAIR_WINDOW_SECONDS}
-        if ok and PUFF_ZOOM_NEEDS_GESTURE_CYCLE and loc.reach_src.get(w) == "zoom" and not _cycle_after(track, t_reach):
-            continue                                # zoomed-pose reach with no completed hand-to-mouth cycle yet: keep waiting (the reach expires)
+        needs_cycle = PUFF_NEEDS_COMPLETED_GESTURE or (PUFF_ZOOM_NEEDS_GESTURE_CYCLE and loc.reach_src.get(w) == "zoom")
+        if ok and needs_cycle and not _cycle_after(track, t_reach):
+            continue                                # no completed hand-to-mouth cycle yet (still at the mouth): keep waiting (the reach expires)
         if not walking and ok:
+            if _puffed_this_moment(loc, now):
+                del loc.reaches[w]                  # the other wrist of the same gesture: one puff per person per moment
+                continue
             loc.puffs.append(now)
             loc.n_pose_puffs += 1
             where = min(ok, key=lambda k: abs(ok[k] - t_reach))
@@ -677,6 +693,8 @@ def _update_object_puff(loc, track, dets, now):
         return
     if loc.last_object_puff is not None and now - loc.last_object_puff < FAR_PUFF_MIN_GAP_SECONDS:
         return
+    if _puffed_this_moment(loc, now):
+        return
     loc.puffs.append(now)
     loc.n_object_puffs += 1
     loc.last_object_puff = now
@@ -685,6 +703,25 @@ def _update_object_puff(loc, track, dets, now):
     loc.head_seen = loc.body_seen = None                 # consumed: the next puff needs fresh sightings of both
 
 
+def _puffed_this_moment(loc, now):
+    """True when this person already counted a puff within PUFF_ONE_PER_MOMENT_SECONDS."""
+    return bool(loc.puffs) and now - loc.puffs[-1] < PUFF_ONE_PER_MOMENT_SECONDS
+
+
+def cig_in_hand_or_mouth_now(track, now):
+    """True when a cigarette (at POSSIBLE_MIN_CONF or better) was seen at one of this person's wrists or at their mouth within
+    MOUTH_ACCEPT_FRESH_SECONDS, or the location gate accepted a cigarette hit on one of those anchors that recently. A box
+    merely somewhere on the body box does not count."""
+    loc = getattr(track, "loc", None)
+    if loc is None:
+        return False
+    seen = [t for t in list(loc.cig_at_wrist.values()) + [loc.cig_at_mouth] if t is not None]
+    if seen and now - max(seen) <= MOUTH_ACCEPT_FRESH_SECONDS:
+        return True
+    last = loc.last_hit.get("cigarette")
+    return last is not None and bool(last[2]) and now - last[3] <= MOUTH_ACCEPT_FRESH_SECONDS
+
+
 def _expire_reaches(loc, now):
     for w, t_reach in list(loc.reaches.items()):
         if now - t_reach > PUFF_PAIR_WINDOW_SECONDS:
@@ -713,11 +750,15 @@ def puff_cap(track, now, previous_level=None, conf=None, momentum=None):
 
     `conf` (the Cigarette detection confidence) and `momentum` switch on the Possible evidence rule (see POSSIBLE_MIN_CONF);
     left None, only the puff rule applies."""
+    # Tied to the present: a puff in the last POSSIBLE_PUFF_WINDOW_SECONDS only raises the status while a cigarette is at the hand
+    # or mouth NOW. Older puffs still count toward the totals (and the Likely count below) but cannot raise it on their own.
     puffed = puffs_within(track, now, POSSIBLE_PUFF_WINDOW_SECONDS) >= POSSIBLE_MIN_PUFFS
+    banked_only = puffed and not cig_in_hand_or_mouth_now(track, now)
+    puffed = puffed and not banked_only
     if not puffed and (conf is None or not (accepted_at_mouth(track, now) and (momentum or 0.0) >= POSSIBLE_MOUTH_MOMENTUM)):
         if previous_level in ("warning", "violation"):
             return None, ""
-        return "monitoring", CAP_NO_PUFF_TEXT
+        return "monitoring", (CAP_NOT_NOW_TEXT if banked_only else CAP_NO_PUFF_TEXT)
     if conf is not None and conf < POSSIBLE_MIN_CONF:
         if previous_level in ("warning", "violation"):
             return None, ""
```
