# Parking obstruction: defaults and lost-track handling

Parking has no points and no 55 / 75 scoring. A vehicle that is **stopped** with at least a set share
of itself inside the drawn no-parking area banks one second per second; when the banked time reaches
the limit, one alert is raised.

## Defaults (changed)

| Setting | Was | Now | Where it is set |
|---|---|---|---|
| Time limit ("for N minutes") | 5 minutes (300 s) | **2 minutes (120 s)** | Zone editor, stored per camera (`Camera.obstruction_minutes`) |
| Inside the road ("Inside the road %") | 50 % | **35 %** | Zone editor, stored per camera (`Camera.obstruction_pct`) |

Both values belong to the camera whose zone editor you used and are read when a parking run starts, so a
change needs a new run. Migration `0057_parking_defaults` changes only the model defaults (no schema
change). Existing cameras still at the old defaults were moved to the new ones; a camera with its own
value was left alone (apart from the time limit, which was at the old default).

### Why

- **2 minutes:** a 5-minute limit needs someone to stand there for five real minutes in a demo, and the
  recorded test clips are 2 minutes long, so they could never alert.
- **35 %:** in a recorded test the parked scooter sat at 48-52 % inside the area, on the knife edge of the
  50 % entry line. A tiny difference in the detector's box decided whether it counted at all (one run
  banked 97 s, another banked 0 s). A lower entry line makes a vehicle that is clearly parked on the area's
  edge count reliably. Redrawing the area to cover the whole kerb lane achieves the same thing.

### Departure from the ordinance rule

`LookOut_Scoring_Spec.md` describes the rule as "vehicle at least 50 % past the road edge for 5 minutes",
citing Ordinance No. 601. The defaults above deviate from that on purpose. To follow the ordinance exactly,
set the zone editor to **50 %** and **5 minutes**; nothing else needs to change.

## Lost tracks

The tracker does not always keep one ID on a parked vehicle. `ObstructionZone._absorb_lost` hands the banked
seconds of a lost ID to a new one, if the new vehicle is stopped, sits on the old box (overlap at least 55 %),
and the old ID was seen recently. A passing vehicle cannot claim them.

| Constant | Was | Now | Meaning |
|---|---|---|---|
| `FORGET_AFTER` | 10 s | **30 s** | How long a lost vehicle's state is kept, and how long its seconds can be handed to a new ID. |
| `MISSING_DECAY_PER_SEC` | (2.0, shared) | **0.5** | Time lost per second while a vehicle is simply **not detected** (after 2 s of grace). |
| `DECAY_PER_SEC` | 2.0 | 2.0 | Time lost per second while the vehicle is **detected but outside** the area. Unchanged. |

A vehicle hidden behind a passing truck has not left, so it loses time slowly. One that is seen driving off
still loses it fast. On the recorded clip, a 28 s gap with a new ID now alerts at 105 s instead of 127 s
(limit 60 s).

## Limits

- Time is counted in footage seconds for a file, so a clip shorter than the limit can never alert.
- A vehicle's time accrues only while it is **stopped** inside the area; moving traffic banks nothing.
- One alert per stay, with a 2-minute cooldown for the same spot.
