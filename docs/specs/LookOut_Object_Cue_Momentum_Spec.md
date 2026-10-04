**LookOut — Object Cue Accumulation ("Momentum")**

*How a flickering detection becomes a stable ON/OFF cue — algorithm, placement, and build checklist*

A standalone spec for one mechanism: how an object detection (bottle, smoking item, knife) is turned into a stable ON/OFF cue that survives brief flicker, occlusion, and missed frames — instead of resetting every time YOLO misses one frame. This supplements the main scoring spec; it does not replace it.

1\. What problem this solves

**The naive rule** (what a "2 seconds straight" gate looks like):

if object detected in every frame for the last 2.0s -\> cue ON

if object not detected for 3.0s -\> cue OFF

**Why this breaks in practice:** YOLO confidence on a small object (bottle, cigarette, knife) is not stable frame to frame, even when the object never left the scene:

frame conf: 0.71 0.68 0.22 0.74 0.09 0.70 0.66 0.71 0.18 0.69

The dips (0.22, 0.09, 0.18) are not the object disappearing — they're angle changes, partial occlusion (a hand, a passing person), motion blur, or lighting. Under the naive rule, any one of those dips below the confidence floor resets the "detected continuously" counter to zero, so the cue never accumulates enough continuous frames to turn ON — or it turns ON, then OFF, then has to build back up from zero, even though the bottle was on the table the entire time.

This is a known behaviour in detection and tracking systems, and the standard fix is evidence accumulation with decay instead of a hard streak requirement. Fernandez-Testa and Salcedo (2024) use this exact approach for weapon detection — summing confidence across recent frames with decaying weights into a "momentum" score, alerting only once the accumulated total clears a threshold (https://arxiv.org/html/2410.09731v1).

2\. What it is

**Accumulation ("momentum") is a running score per tracked object-slot** (one per track_id + object_class pair) that goes up when the object is seen and decays — but does not instantly reset — when it isn't.

Instead of asking "was this detected every frame for N seconds," it asks "has enough evidence piled up recently." A few missed frames lower the score a little; they don't zero it out.

momentum_t = momentum\_(t-1) \* DECAY + confidence_t

- confidence_t = YOLO's confidence for that object class in the current frame (0 if not detected at all this frame)

- DECAY = a constant between 0 and 1 (e.g. 0.90) that determines how fast old evidence fades

- momentum_t = the running total after this frame

**Two thresholds, not one (hysteresis):**

cue turns ON when momentum \>= ON_THRESHOLD

cue turns OFF when momentum \< OFF_THRESHOLD (OFF_THRESHOLD \< ON_THRESHOLD)

The gap between the two thresholds is what makes the cue flicker-resistant. A momentum dip that doesn't fall all the way to OFF_THRESHOLD never actually turns the cue off — it's a case of accumulated evidence briefly sagging, not the object leaving.

3\. The algorithm

Run once per frame, per tracked object-slot (a track_id carrying a candidate bottle, cigarette, or knife).

\# Constants — starting values, tune against real footage

DECAY = 0.90 \# momentum retained per frame from the previous value

ON_THRESHOLD = 1.5 \# momentum needed to turn the cue ON

OFF_THRESHOLD = 0.4 \# momentum must fall below this to turn the cue OFF

MAX_MOMENTUM = 3.0 \# hard cap, prevents unbounded growth on long detections

def update_momentum(state, confidence_this_frame):

"""

state: { "momentum": float, "cue_on": bool }

confidence_this_frame: float in \[0, 1\], 0.0 if no detection this frame

Returns the updated state.

"""

state\["momentum"\] = state\["momentum"\] \* DECAY + confidence_this_frame

state\["momentum"\] = min(state\["momentum"\], MAX_MOMENTUM)

if not state\["cue_on"\] and state\["momentum"\] \>= ON_THRESHOLD:

state\["cue_on"\] = True

elif state\["cue_on"\] and state\["momentum"\] \< OFF_THRESHOLD:

state\["cue_on"\] = False

return state

**Starting state** for a new object-slot: {"momentum": 0.0, "cue_on": False}.

**Tuning note:** DECAY = 0.90 means momentum from 10 frames ago is worth 0.90^10 ≈ 0.35 of its original value, and from 20 frames ago ≈ 0.12 — so the cue has roughly a half-second to one-second memory depending on frame rate. Raise DECAY toward 0.95+ for more flicker tolerance (longer memory); lower it for a more responsive, less forgiving cue. These four constants should be exposed as config, not hardcoded, so they can be tuned per violation and per deployment without a code change.

4\. What this is, and is NOT, a fix for

| **Situation**                                                                               | **Does momentum help?**                                                                                                                                             |
|---------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Object flickers out for 1-2 frames (bad angle, motion blur, brief hand occlusion)           | Yes — this is exactly what it's for                                                                                                                                 |
| Object briefly fully blocked for under ~1s (someone walks past)                             | Yes, if DECAY is tuned generously enough                                                                                                                            |
| Object genuinely leaves the scene, or is occluded for 10s+                                  | No — momentum correctly decays to 0 and the cue turns OFF. This is correct behaviour, not a bug                                                                     |
| Object was never detected in the first place (too small, wrong angle, lighting)             | No — momentum only works with detections it's given. If YOLO never fires, there's no confidence value to accumulate                                                 |
| Object detected once, then the track_id is lost and a new ID is assigned to the same person | No, not by itself — momentum is keyed to the track_id. If tracking identity breaks, the object-slot and its accumulated momentum are lost and a new one starts at 0 |

For the "never detected in the first place" case — e.g. a bottle at a non-training angle or height — see the separate note on the viewing-angle / training-height mismatch. That is a model and dataset issue, not something this mechanism can solve.

5\. Where this sits in the pipeline, and when it runs

EVERY FRAME

\|

v

\[1\] YOLO detection (unchanged)

\| -\> boxes + confidences for bottle / smoking item / knife

v

\[2\] ByteTrack (unchanged)

\| -\> each person gets a track_id

v

\[3\] Object-slot lookup

\| -\> for each tracked person, find (or create) their

\| object-slot state for each relevant class

v

\[4\] \*\*\* ACCUMULATION RUNS HERE \*\*\*

\| -\> update_momentum() called once per object-slot, per frame

\| -\> this is a NEW step, inserted between detection/tracking

\| and the scoring layer

v

\[5\] Object cue (ON/OFF) feeds the heuristic indicators

\| -\> "bottle seen" / "smoking item detected" / "knife seen"

\| in the scoring spec now reads this cue,

\| not a raw per-frame detection

v

\[6\] Violation score (unchanged)

**In short:** accumulation is a small new stage that sits directly after detection and tracking and directly before the object cue is read by the scoring layer. It replaces the old "was it detected 2s straight" check at exactly the one spot where that check currently lives — nothing else in the pipeline needs to change.

**Frequency:** runs every frame, for every tracked object-slot currently being watched — every person-track that YOLO has returned a bottle, cigarette, or knife detection for at least once recently, or that still has non-zero momentum decaying. This is cheap: a multiply and an add per object-slot, not a model inference pass, so it adds negligible cost next to YOLO and pose.

Object-slot lifecycle

- Created the first time a given track_id gets any confidence above 0 for that object class

- Destroyed when the track_id itself is lost — person leaves, or the tracker drops them. Momentum is not carried across track IDs

- Multiple object-slots can exist per track_id if a person is a candidate for more than one object class (rare, but the data structure should allow it)

6\. Interaction with the rest of the scoring design

- The object cue is still the gate: nothing becomes visible in the UI, and the VLM is not called, until the cue is ON. Accumulation changes how reliably the cue turns ON and stays ON — it does not change the gate's role.

- The VLM trigger (object cue turns ON, call the VLM) is unaffected in design, just now driven by a more stable cue.

- Behaviour indicators that run in the background (group, duration, loitering) are unrelated to this mechanism; they use track position history, not object confidence, and already have their own continuity logic via the tracker.

7\. Limitations paragraph (ready to paste)

*Object detection confidence is accumulated across recent frames with a decaying weight, so that momentary drops in detection confidence (due to partial occlusion, motion blur, or viewing angle) do not immediately reset the system's evidence for an object's presence. This improves tolerance to brief flicker but does not compensate for extended occlusion, a genuinely absent object, or an object the detector was never able to recognize in the first place, such as due to a camera angle not represented in the training data. The accumulation parameters — decay rate and ON/OFF thresholds — were tuned empirically on the project's own footage rather than derived analytically, and their values are reported in the methodology.*

8\. Build checklist for the dev

- Add momentum + cue_on state per (track_id, object_class) pair — likely a dict keyed by (track_id, class_name), held in memory per camera/session (not the database — this is high-frequency, ephemeral state)

- Call update_momentum() once per frame, per active object-slot, right after detection and tracking, before the scoring layer reads the object cue

- Expose DECAY, ON_THRESHOLD, OFF_THRESHOLD, MAX_MOMENTUM as config (per violation, if the three object classes end up needing different tuning — cigarette and knife being smaller objects than bottle may warrant different decay)

- Clean up object-slots when their track_id is dropped by the tracker, to avoid a slow memory leak from accumulating dead slots

- Log the momentum value alongside each alert's score breakdown — useful for tuning and for the evaluation chapter, since it shows how close or far an event was from the ON/OFF boundary

- Test on a clip where the known issue occurs (object flickers, cue currently drops) and confirm the cue now holds through the flicker

- Separately, test on a clip where the object genuinely leaves — confirm the cue still correctly turns OFF, not just "never turns off"
