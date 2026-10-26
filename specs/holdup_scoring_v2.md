# LookOut Holdup Scoring v2

Status: implemented. Supersedes section 7 (Holdup) of `LookOut_Scoring_Spec_v6.md` for the
Holdup violation only. Smoking, Drinking and Parking scoring are unchanged.

Code: `core/vision/theft.py` (Evidence, PairState, TheftEngine), `core/vision/scoring.py` (E20),
`core/vision/momentum.py` (knife hold), `core/management/commands/watch_thief.py`
(`_knife_evidence`, `_process_track`), `core/management/commands/watch_merged.py` (Layer E wiring).
Tests: `core/tests_holdup_v2.py`.

## 1. What a holdup is, to the system

One person holding a **knife** near a second person, with at least one further sign of a
confrontation. The system never decides this alone: it proposes a status, and an officer reviews
every alert. The AI checker only adds context and a suggested status. It never changes the score
or the level.

The detector (`merged_v2`) has no gun class. Only a knife is ever scored.

## 2. Indicators

| Code | Indicator | Points | True when |
|---|---|---|---|
| **E14** | Knife near a hand (the gate) | 45 | A knife is detected on the person, **within 0.22 body heights of one of that person's wrist keypoints** (pose), **and** in at least **2 of the last 5** processed frames for that person (a burst that meets this counts for 3 s). The momentum cue must also be ON for at least **0.5 s** (knife only). A knife with no wrist nearby (on a table or stall) never counts. |
| **E12** | Pair frozen close together | 20 | Two people within arm's length and nearly still (speed under **0.3**) for **2 s or more**, **and** a knife was seen on the holder during the freeze, up to **3 s before** it began, or up to **3 s after** it ended. Held for the cue-hold time (5 s) after the freeze breaks. |
| **E11→E12** | Approach, then freeze | +10 | A rapid approach (E11: from over 3 body heights apart to under 1 within 3 s) between the pair, with the freeze (E12) beginning no more than **5 s** after the approach landed. Shown as its own line. **E11 on its own is worth 0.** |
| **E10** | Loitering (remembered) | 10 | The knife holder qualified as a loiterer **within the last 60 s**. A track qualifies when it has been on screen for at least the *Lingering* setting **and** its path efficiency over the last 20 s is below 0.3. Each qualifying frame stamps the track (`loiter_last_seen`); the stamp is what E10 reads. |
| **E20** | Time of day | +4 to +10 | Added only once E10 or E12 is present, and capped (section 4). |

E15 (arm extended toward the second person) is **planned and not enabled**. It needs a database
setting and was left out for now. The existing `E15` name in `theft.py` belongs to carnapping
(an internal anchor state with no weight) and is unrelated.

### E10 memory and re-linking

A person who loiters, then walks up to someone and draws a knife, has moved a lot in the last
20 s, so a plain "moving little right now" test would miss them. The stamp makes loitering a
60-second memory.

If a stamped track vanishes and a new track appears within **3 s** and **0.5 body heights** of
where it was last seen, the stamp is copied to the new track. This is isolated in
`TheftEngine._relink_loiter_stamps` and disabled by setting `theft.LOITER_RELINK = False`. A track
whose external ID is merely renamed keeps its whole history (`PersonTracker._take_over_lost_id`).

### Knife persistence and the momentum hold

The knife's object cue is the existing momentum cue (ON at 1.5, OFF below 0.4, decay 0.9). For the
knife class only:

- momentum is **held for 1.0 s** after the last detection instead of decaying immediately, so a
  hand turning or an arm blocking the knife for a moment does not cost the momentum it took to
  turn the cue ON;
- the cue must stay ON for **0.5 s** (`momentum.KNIFE_CONFIRM_SECONDS`) instead of the 2 s
  object-confirmation setting. A holdup is brief and the blade can be visible for under a second,
  so a 2 s wait confirmed the cue after the confrontation was over. The setting itself is
  untouched: smoking and drinking still wait their own 2 s.

The 2-of-5 persistence rule sits on top of this: momentum decides when the cue is ON, persistence
decides whether the knife is solid enough to be scored. A burst that satisfies persistence counts
for 3 s (`KNIFE_PERSIST_MEMORY_SECONDS`), so it is not thrown away at the instant the cue confirms.

### Why the freeze is 2 s and 0.3 (spec 3 s and 0.2)

On a recorded staged holdup the pair stood at arm's length for about five seconds, but small
movements (hands, sway) pushed one person above 0.2 for a moment, so the strictly-still part lasted
only 1.7 s. E12 never completed. At 2 s and 0.3 it does, and a knife is still required for any
alert, so the looser freeze cannot raise one on its own.

## 3. Rules to leave Monitoring

All three must hold:

1. **E14** is present.
2. A **second person** is within the *How close the second person must be* setting
   (default 1.75 body heights of the knife holder).
3. At least one **behavioural cue**: **E10** or **E12**.

Otherwise the status stays **Monitoring**, whatever the score would be. E20 is not counted
without a behavioural cue.

## 4. Levels and the E20 cap

Levels (unchanged): **Monitoring** below 55, **Possible** 55 to 74, **Likely** 75 and above.

The level comes from the **base score**, the sum of E14, E12, E11→E12 and E10, **without E20**.
E20 may then add points but never move the score into a higher band:

```
if base >= 75:      final = base + E20
elif base >= 55:    final = min(base + E20, 74)
else:               final = min(base + E20, 54)
```

The breakdown shows the points E20 actually added, so the lines always add up to the total.

### E20 source and points

Robielos & Duran (2020), IEOM: City of Manila robbery and theft records, the probability of an
incident in each three-hour block of the day (`scoring.MANILA_HOUR_BLOCKS`).

`points = round(10 x p_block / p_max)`, with `p_max = 0.170`:

| Block | p | E20 points |
|---|---|---|
| 00:00-03:00 | 0.095 | 6 |
| 03:00-06:00 | 0.100 | 6 |
| 06:00-09:00 | 0.070 | 4 |
| 09:00-12:00 | 0.156 | 9 |
| 12:00-15:00 | 0.163 | 10 |
| 15:00-18:00 | 0.170 | 10 |
| 18:00-21:00 | 0.136 | 8 |
| 21:00-24:00 | 0.109 | 6 |

The hour only ever **adds**. No time of day can hide a holdup.

Time is read in **Asia/Manila**. A live run uses the wall clock in Manila time. An uploaded clip
uses its "Recorded at" time (entered as Manila time) plus the position in the clip. A timezone-aware
time is converted to Manila first. Django's own `TIME_ZONE` (UTC) is not changed.

**Honest caveats.** The data is from a different city (Manila), combines robbery with theft, and
counts only cases that reached conviction. It disagrees with wider literature that associates
darkness with street robbery. E20 is therefore a small prior, capped so it cannot change a status
and never counted without evidence.

## 5. Expected base scores

| Evidence | Base | Level |
|---|---|---|
| E14 only | 45 | Monitoring |
| E14 + E10 | 55 | Possible |
| E14 + E12 | 65 | Possible |
| E14 + E12 + E10 | 75 | Likely |
| E14 + E12 + E11→E12 | 75 | Likely |
| E14 + E12 (65) + E20 10 | 74 (capped) | Possible |

## 6. Settings used

| Setting | Used for |
|---|---|
| Lingering before the incident (`holdup_loiter_seconds`, default 20 s) | The on-screen time a track needs before it can be stamped as a loiterer (E10). |
| How close the second person must be (`holdup_near_person_heights`, default 1.75) | Rule 2 in section 3. |
| Detection confidence (`thief_confidence`) | The knife detector's minimum confidence. |

No database migration was needed.

## 7. Cost and limits

- Pose runs only on frames where a knife is detected (about 40 ms), as before.
- Loiter stamping runs for every tracked person each frame and is cheap.
- E14's wrist distance stays at 0.22 body heights (`KNIFE_MAX_WRIST_DIST_FRAC`).
- The knife's momentum hold is `momentum.KNIFE_HOLD_SECONDS` (1.0 s).
- Only the knife is detected by `merged_v2`. The standalone `thief.pt` model has a gun class that
  the scoring no longer reads.
- In `watch_merged` the Layer E engine is created in `_setup_engine`. Without it E12 and E10 can
  never fire, and a knife can only ever be E14 alone.
