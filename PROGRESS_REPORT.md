# LOOKOUT — Development Progress Report

Work completed from the previous reporting week through the current one.

---

## At a Glance

| Feature | What it does | Status |
|---------|--------------|--------|
| Public drinking detection | Flags drinking in public, with posture and ordinance-hour checks | Done |
| CCTV recording + video evidence | Records continuously; saves a short clip with every alert | Done |
| Combined watcher | Runs all four violation types on one camera feed | Done |
| **Theft & robbery detection** | Recognises snatching, holdup, carnapping and stolen property by movement | Built and tested |
| Night-time image enhancement | Brightens and cleans dark frames before detection | Built |
| Sidewalk blocking measurement | Measures in metres how much walking space a parked vehicle leaves | Prototype |

Roughly **4,600 lines** were added in the completed work, plus about **2,750 lines** of new theft-detection code, tests and tools currently in progress.

---

## Public Drinking Detection

A new detector for drinking in public. The difficulty is that seeing a bottle is not the same as seeing a violation — people carry bottles home, set them on tables, hold them for someone else. So the detector adds three checks on top of simply spotting a bottle:

- **Posture** — the bottle must be near the person's face, not merely somewhere inside their outline.
- **Time** — it can be limited to the hours the local ordinance actually covers, the same way curfew is.
- **Duration** — the bottle must stay with the person for several seconds before it counts.

## Recording and Video Evidence

Two things were added so that an alert can be verified afterwards instead of taken on trust:

- **Continuous CCTV recording** that starts when an admin logs in and stops on logout, saved in segments with automatic reconnection if the camera drops.
- **A short evidence clip** saved with every alert, showing the detection boxes and a burned-in timestamp. The dashboard now plays this clip inside the violation card.

Live camera feeds were also added to the dashboard. Because browsers cannot play a camera's RTSP stream directly, the server fetches each image and passes it on — which also means camera passwords stay on the server and are never sent to the browser.

## Theft and Robbery Detection

This is the largest addition, and the most technically difficult problem in the system.

**The problem.** Every other violation can be seen in a single photograph — a cigarette, a bottle, a badly parked car. Theft cannot. A snatcher looks exactly like an ordinary pedestrian. What separates them is not appearance but **movement over several seconds**.

**The approach.** The system now watches how people move and recognises four patterns:

| Pattern | What the camera sees |
|---------|----------------------|
| Snatching | Two people briefly touch, then one suddenly runs while the other stays put |
| Holdup | Someone loiters, closes in quickly, then both freeze at arm's length |
| Carnapping | A parked motorcycle is left alone, then someone tampers with it or pushes it away |
| Stolen property | A bag is left unattended, then leaves with someone who is not its owner |

**Measuring in body-heights, not pixels.** A person 3 metres from the camera and one 30 metres away produce wildly different pixel measurements for the same action. So every distance and speed is measured relative to the person's own height in the frame. "Arm's reach" becomes a fixed number that works at any distance, on any camera — meaning the system does not have to be re-tuned for each new camera installation.

**Judging speed against the scene.** "Running" cannot be a fixed number either, because it depends on distance and lens. The system continuously measures what normal movement looks like in that scene at that moment, and compares each person against it.

**Adding up evidence instead of demanding certainty.** Requiring every clue to appear at once would mean the system never fires in real conditions. Instead each clue carries a weight — a visible weapon counts most, a bag changing hands next, and so on — and the total decides one of three outcomes:

- **Ignore** — not enough evidence.
- **Watch** — a near miss. Logged with all its details, but no alert raised.
- **Alert** — enough evidence; a real alert goes to the dispatcher.

The **Watch** category is deliberate. These borderline cases are saved so the alert threshold can later be set using real footage from the actual camera, rather than by guesswork. Every threshold in the system is currently a reasoned starting value, not yet a measured one — this is stated openly in the code.

**Avoiding false alarms.** Several rules exist purely to prevent embarrassing mistakes: a friendly greeting is not counted as a snatch, an owner picking up their own bag is not theft, someone riding a motorcycle away is not pushing it, and in a dense crowd the system declines to judge rather than guessing. When a person is half-hidden or standing at the edge of the frame, the system abstains instead of using measurements it knows are unreliable.

**Cost.** All of this reuses the person-detection pass that already runs every frame, so the new capability adds **no extra processing load**.

## Night-Time Image Enhancement

Low-light enhancement — brightening, noise removal and contrast correction — previously existed only in the smoking detector. It is now available to every detector through one shared component. Daytime frames skip it automatically, so nothing is slowed down unnecessarily.

This matters most for curfew detection, which by definition runs at night. The image is now cleaned up *before* faces are cut out for recognition, so face matching benefits from it too.

## Sidewalk Blocking Measurement

A prototype addressing a weakness in the parking detector: it currently reports pixel overlap, which means nothing to a barangay official and differs on every camera.

The new method asks the user to click the four corners of the sidewalk once and enter its real width with a tape measure. From that, the system converts the camera's angled view into a flat top-down map and measures the actual walking space left in metres.

The result is a statement someone can act on — *"north footpath: 0.42m of 1.50m left passable"* — instead of an abstract number. A browser tool was built so this can be tested on real uploaded footage.

## Testing

A test suite of **27 automated tests** was written for the theft detection rules. Because every rule depends on movement over time, the tests simulate people walking, running, meeting and separating on a virtual clock — the only way to test this logic without going out and filming staged crimes.

The tests cover both the patterns that *should* trigger an alert and, just as importantly, the everyday situations that must *not*: greetings, someone reclaiming their own bag, riding a motorcycle away, and crowded scenes.

**All 27 tests pass**, in under a second.

---

## Current Status

**Completed and merged:** drinking detection, CCTV recording, video evidence, live camera feeds, night enhancement, and the improved long-distance detection.

**Built and tested, pending final integration:** the theft and robbery pattern detection with its full test suite.

**Prototype:** the sidewalk measurement tool, working but not yet connected to the main parking detector.

## Known Limitations and Next Steps

- **The thresholds are not yet calibrated.** All values in the theft detector are reasoned starting points, not measured results. Calibrating them requires filming staged scenarios with the actual mounted camera. The logging and testing tools needed for that calibration are already built, so it will be a measurement exercise rather than guesswork.
- **Carnapping covers motorcycles and bicycles only.** At the planned camera height and lens, a car does not fit usefully in the frame at the distance where detection would need to happen. This is a deliberate, stated limit rather than an oversight.
- **Suspect re-identification is not yet implemented.** One rule that would confirm whether the person leaving a vehicle is the same one who arrived on it is written but currently inactive.
- **The sidewalk tool is not yet wired into the live parking detector.**
- **Noise and garbage detection** remain planned, with settings already in place, but no detector has been written yet.
