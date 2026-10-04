import { Cigarette, Beer, Car, ShieldAlert, Layers, Layers3 } from "lucide-react";

// The 6 detectors DetectionJobViewSet.DETECTION_COMMANDS knows how to run,
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
  { key: "merged", label: "Merged (All 3)", icon: Layers, color: "#22c55e" },
  // Smoking + drinking + theft off one merged-model pass, plus road-edge
  // obstruction running its own vehicle model beside them (watch_merged_all).
  { key: "merged4", label: "Merged (All 4)", icon: Layers3, color: "#14b8a6" },
];

// Detectors that judge vehicles against a drawn no-parking area, in two tiers
// that mirror the backend's EDGE_CAPABLE / EDGE_REQUIRED in views.py:
//
//   TYPES_WITH_EDGES      show the drawing step.
//   TYPES_REQUIRING_EDGES cannot start until something is drawn.
//
// Parking is capable but not required — with nothing drawn, watch_parking
// falls back to its plain dwell rule and the run is still valid. Merged (All 4)
// has no such fallback and refuses to start without an area, so it is also
// hidden from entry points that have no drawing step at all.
export const TYPES_WITH_EDGES = new Set(["parking", "merged4"]);
export const TYPES_REQUIRING_EDGES = new Set(["merged4"]);

// A job row carries the raw DETECTION_COMMANDS key ("thief", "merged4"), which
// is not what the operator picked it by. Falls back to the key itself so an
// older job whose detector has since been removed still renders.
export const detectionLabel = (key) =>
  DETECTION_TYPES.find((t) => t.key === key)?.label ?? key;
