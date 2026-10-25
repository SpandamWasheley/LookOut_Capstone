import { Cigarette, Beer, Car, ShieldAlert, Trash2, Layers, Layers3 } from "lucide-react";

// The 7 detectors DetectionJobViewSet.DETECTION_COMMANDS knows how to run,
// shared by every upload-a-clip-and-detect entry point (UploadDetectionModal,
// RunDetectionPage) so the list is defined once. Deliberately NOT the same
// shape as VIOLATION_TYPES in constants/violationTypes.js — that file keys
// theft as "theft" (the backend's real ViolationType/Alert code) while the
// watch_thief command and DETECTION_COMMANDS use "thief", and "merged" isn't
// a ViolationType at all, just a job type. See violationTypes.js's own note.
export const DETECTION_TYPES = [
  { key: "smoking", label: "Smoking", icon: Cigarette, color: "#f59e0b" },
  { key: "drinking", label: "Drinking", icon: Beer, color: "#8b5cf6" },
  { key: "thief", label: "Holdup", icon: ShieldAlert, color: "#ef4444" },
  { key: "parking", label: "Parking", icon: Car, color: "#f97316" },
  // green-600, not the green-500 the violation chip uses: "Merged (All 3)"
  // below already owns #22c55e, and these two appear in the SAME picker list.
  { key: "disposal", label: "Disposal", icon: Trash2, color: "#16a34a" },
  { key: "merged", label: "Merged (All 3)", icon: Layers, color: "#22c55e" },
  // Smoking + drinking + theft off one merged-model pass, plus road-edge
  // obstruction running its own vehicle model beside them (watch_merged_all).
  { key: "merged4", label: "Merged (All 4)", icon: Layers3, color: "#14b8a6" },
];

// Detectors that use a drawn area, in three tiers that mirror the backend's
// EDGE_CAPABLE / EDGE_REQUIRED in views.py:
//
//   TYPES_WITH_EDGES      show the drawing step.
//   TYPES_REQUIRING_EDGES cannot start until something is drawn.
//   TYPES_WITH_OPTIONAL_EDGES  offer the step, but start happily without it.
//
// Parking no longer needs anything drawn: with no area saved, watch_parking
// works one out from the footage itself — it watches where traffic drives and
// hulls those ground contacts into the no-parking area (--no-auto-zone turns
// that off). The canvas is still offered, because a traced area is a reading of
// the footage and an operator who knows the street may want to correct it, but
// it is an override now rather than a prerequisite. Merged (All 4) has no such
// fallback and refuses to start without an area, so it is also hidden from
// entry points that have no drawing step at all.
//
// Disposal is the one that must stay genuinely optional in the UI as well as
// the API. It is not a vehicle rule at all: the area only narrows WHERE
// dumped rubbish is watched for, and watch_disposal watches the whole frame
// without one. Leaving it out of this third set would make a drawn polygon a
// precondition for starting a run that worked fine without one before.
export const TYPES_WITH_EDGES = new Set(["parking", "merged4", "disposal"]);
export const TYPES_REQUIRING_EDGES = new Set(["merged4"]);
export const TYPES_WITH_OPTIONAL_EDGES = new Set(["disposal", "parking"]);

// Which detectors own the "inside the road %" and "for N minutes" boxes.
//
// Deliberately NOT the inverse of TYPES_WITH_OPTIONAL_EDGES, which it used to
// be read as. The two questions came apart when parking's area became
// optional: parking's thresholds ARE its rule and apply to an auto-traced area
// exactly as to a drawn one, while disposal draws an area and has no
// thresholds at all. Tying them together hid parking's only real controls the
// moment its polygon stopped being compulsory.
export const TYPES_WITH_AREA_THRESHOLDS = new Set(["parking", "merged4"]);

// What the polygon MEANS, which is not the same sentence for every detector:
// parking judges vehicles against a no-parking area, disposal just confines
// where it looks. Calling disposal's a "no-parking area" would describe a rule
// that does not exist.
export const edgeHeading = (key, label) =>
  key === "disposal"
    ? "Draw the area to watch for dumped rubbish (optional)"
    : `No-parking area for ${label} (optional — worked out from the footage if you skip it)`;

export const edgeCameraNote = (key, label) =>
  key === "disposal"
    ? `${label} uses the area saved on the camera (Feeds → Edge Zones). Without one it watches the whole frame.`
    : `${label} uses the no-parking area saved on the camera (Feeds → Edge Zones). Without one it works the road out from the first half-minute of footage and saves what it finds.`;

// A job row carries the raw DETECTION_COMMANDS key ("thief", "merged4"), which
// is not what the operator picked it by. Falls back to the key itself so an
// older job whose detector has since been removed still renders.
export const detectionLabel = (key) =>
  DETECTION_TYPES.find((t) => t.key === key)?.label ?? key;
