import { Cigarette, Beer, Car, ShieldAlert, Trash2, Layers, Layers3, SquareStack } from "lucide-react";

// The 8 detectors DetectionJobViewSet.DETECTION_COMMANDS knows how to run,
// shared by every upload-a-clip-and-detect entry point (UploadDetectionModal,
// RunDetectionPage) so the list is defined once. Deliberately NOT the same
// shape as VIOLATION_TYPES in constants/violationTypes.js — that file keys
// theft as "theft" (the backend's real ViolationType/Alert code) while the
// watch_thief command and DETECTION_COMMANDS use "thief", and "merged" isn't
// a ViolationType at all, just a job type. See violationTypes.js's own note.
export const DETECTION_TYPES = [
  { key: "smoking",    label: "Gray",   icon: Cigarette,   color: "#9ca3af" },
  { key: "drinking",   label: "Violet", icon: Beer,        color: "#8b5cf6" },
  // The KEY stays "thief" -- it is the watch_thief command's own name, which
  // DETECTION_COMMANDS matches on. Only the label an operator reads changes.
  { key: "thief",      label: "Red",    icon: ShieldAlert, color: "#ef4444" },
  { key: "parking",    label: "Orange", icon: Car,         color: "#f97316" },
  { key: "disposal",   label: "Green",  icon: Trash2,      color: "#22c55e" },
  { key: "merged",     label: "Pink",   icon: Layers,      color: "#ec4899" },
  // Gray + Violet + Red (merged model) + Green (disposal, separate process).
  { key: "merged4",    label: "Cyan",   icon: Layers3,     color: "#06b6d4" },
  // All five: Gray + Violet + Red + Green + Orange.
  // Worth knowing: disposal (Green) runs no trained violation class — dual
  // background subtraction + zero-shot crop — so it cannot share the
  // merged_v2.pt pass and is launched as its own process beside it.
  { key: "everything", label: "Black",  icon: SquareStack, color: "#18181b" },
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
export const TYPES_WITH_EDGES = new Set(["parking", "merged4", "disposal", "everything"]);
// Empty: both detectors that use an area can now work one out from the
// footage. Kept because it is what UploadDetectionModal filters on — an entry
// point with no drawing step at all still needs a way to ask.
export const TYPES_REQUIRING_EDGES = new Set([]);
export const TYPES_WITH_OPTIONAL_EDGES = new Set(["disposal", "parking", "merged4", "everything"]);

// Which detectors own the "inside the road %" and "for N minutes" boxes.
//
// Deliberately NOT the inverse of TYPES_WITH_OPTIONAL_EDGES, which it used to
// be read as. The two questions came apart when parking's area became
// optional: parking's thresholds ARE its rule and apply to an auto-traced area
// exactly as to a drawn one, while disposal draws an area and has no
// thresholds at all. Tying them together hid parking's only real controls the
// moment its polygon stopped being compulsory.
export const TYPES_WITH_AREA_THRESHOLDS = new Set(["parking", "merged4", "everything"]);

// Which rule a drawn polygon belongs to, which is NOT always the detector you
// picked. On an All 5 run parking traces its own no-parking area from the
// traffic, so the one canvas on screen is free to mean the thing that cannot
// be traced as well: where to watch for rubbish. Drawing it must therefore
// write a "disposal" area and leave parking's alone — before this, one shape
// was read as both, and a tight no-parking polygon silently confined the
// rubbish search to the carriageway.
export const edgeKind = (key) =>
  (key === "disposal" || key === "everything") ? "disposal" : "zone";

// What the polygon MEANS, which is not the same sentence for every detector:
// parking judges vehicles against a no-parking area, disposal just confines
// where it looks. Calling disposal's a "no-parking area" would describe a rule
// that does not exist.
export const edgeHeading = (key, label) =>
  key === "disposal"
    ? "Draw the area to watch for dumped rubbish (optional)"
    : key === "everything"
      // Parking traces its own from the traffic on this run, so this canvas
      // is for the thing tracing does worst: where rubbish gets left, which
      // is off the carriageway by definition.
      ? "Area to watch for dumped rubbish (optional — the no-parking area is worked out from the footage)"
      : `No-parking area for ${label} (optional — worked out from the footage if you skip it)`;

export const edgeCameraNote = (key, label) =>
  key === "everything"
    ? `${label} runs the merged four plus disposal as a second process. Parking works its no-parking area out from the footage; a rubbish area drawn here is used by disposal only.`
    : key === "disposal"
    ? `${label} uses the area saved on the camera (Feeds → Edge Zones). Without one it watches the whole frame.`
    : `${label} uses the no-parking area saved on the camera (Feeds → Edge Zones). Without one it works the road out from the first half-minute of footage and saves what it finds.`;

// A job row carries the raw DETECTION_COMMANDS key ("thief", "merged4"), which
// is not what the operator picked it by. Falls back to the key itself so an
// older job whose detector has since been removed still renders.
export const detectionLabel = (key) =>
  DETECTION_TYPES.find((t) => t.key === key)?.label ?? key;
