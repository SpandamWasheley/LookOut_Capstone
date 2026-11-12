# Reminders for next week

Not done on purpose (to save usage credits). Nothing here is committed or scheduled.

## Regression replays that were skipped

The smoking changes of 2026-11-01 (side-on gesture ruler, bottle guard for it, cross-ID draw carry-over) were only replayed on:
SD 1 FarNear, SMOKE WALK vapenig, svAPE fARwALK, WALK FAR S, VAPE NEAR, S VapeNightNear.

Replay these next, current code vs. the changes switched off, and look for any NEW smoking alert (a new false alarm):

| Clip | Why it matters |
|---|---|
| Accuracy\Smoking Placeholder House\Done\SF LolliNear1.mkv | cigarette-class look-alike, near range |
| Accuracy\Smoking Placeholder House\Done\SD NearFar.mkv | drinker near + far person |
| Accuracy\Smoking Placeholder House\Done\SF FanFar.mkv | far range, hand-to-face |
| Accuracy\Smoking Placeholder House\HIde\S FarWalk.mkv | walker, tracker ID switches |
| Accuracy\Drinking\D Near 1.mkv | near drinker (sips look like draws) |
| Accuracy\Drinking\D Far.mkv | far drinkers, ID switches |
| Accuracy\Drinking\SD FarNear.mkv | drinker near + smoker across the street |
| Accuracy\Drinking\DSingle NearNight.mp4 | single drinker at night |
| Accuracy\Smoking Placeholder House\Done\S1 NearVape2.mp4 | vape; alert moved from 26 s to 56 s when the bottle guard went in |
| Accuracy\Smoking Placeholder House\Done\S 2NearFar.mkv | near girl alerts; far vaper (track 34) still does not |

How to switch the changes off for a baseline (the trace scripts in the session scratchpad used these env vars; the product code has no such switch except the first):
- `core/vision/gesture_handover.py`: `GESTURE_HANDOVER = False` (cross-ID draw carry-over)
- `core/vision/recognition.py`: `GESTURE_PROFILE_MIN_HEIGHT_PX` very large (side-on ruler off)

## Known open items

- **svAPE fARwALK.mkv**: no alert yet. The woman's draws are spread over three track IDs (16, 41, 46); the carry-over joins 16 -> 41 but the total stays at 1 strong draw / 2 cycles, short of 2 strong or 3 cycles. Her draws are weak (not held 1 s). Not loosened further.
- **VAPE NEAR.mkv**: with the carry-over there is a second Possible (track 16, 42.6 s) on the SAME woman who already alerted as track 4: she walks off and gets a new ID. Check in the live app that the same-spot cooldown merges it into the existing alert rather than filing a second one.

- **Vape Far Night.mkv** still does not alert. The vaper is ~195 px tall; the side-on ruler only applies from `GESTURE_PROFILE_MIN_HEIGHT_PX` = 250. Lowering it to 150 alerts (track 3, Possible at 23.7 s) but a far person on a phone call looks the same to the pose. Needs the replays above first.
- **S 2NearFar.mkv far vaper** (track 34, ~265 px, three-quarter turned, hand at the mouth): shoulders are 0.16-0.17 of height, just over `GESTURE_PROFILE_SHOULDER_FRAC` = 0.15. A value of 0.20 made it alert (Possible at 52.7 s) with the near girl unchanged; not adopted because it was not checked against the other clips.
- **SD 1 FarNear.mkv**: the true smoker across the street still gets no smoking alert (baseline and current). Its wrong alerts that are NOT smoking (group drinking that counts a motorcycle rider as an accomplice, a Holdup on the near drinker) were not touched and need their own look.
- **WALK FAR S.mkv**: the blue-striped-shirt walker gets a Possible from "cigarette + near mouth" only (ALT-0340, AI says no smoking item visible). The woman across the road is still not alerted: her cigarette is mostly rejected "off-anchor" / "no-kp" by the location gate (far, walking, pose anchors unreliable).
- The mouth/object path (`location_momentum.anchors_from_kpts`) also uses a shoulder-width radius, so side-on people have the same blind spot there. Not changed.
