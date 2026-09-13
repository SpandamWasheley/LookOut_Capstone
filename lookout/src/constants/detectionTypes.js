import { Cigarette, Beer, Car, ShieldAlert, Layers } from "lucide-react";

// The 5 detectors DetectionJobViewSet.DETECTION_COMMANDS knows how to run,
// shared by every upload-a-clip-and-detect entry point (UploadDetectionModal,
// RunDetectionPage) so the list is defined once. Deliberately NOT the same
// shape as VIOLATION_TYPES in constants/violationTypes.js — that file keys
// theft as "theft" (the backend's real ViolationType/Alert code) while the
// watch_thief command and DETECTION_COMMANDS use "thief", and "merged" isn't
// a ViolationType at all, just a 5th job type. See violationTypes.js's own
// note on this mismatch.
export const DETECTION_TYPES = [
  { key: "smoking", label: "Smoking", icon: Cigarette, color: "#f59e0b" },
  { key: "drinking", label: "Drinking", icon: Beer, color: "#8b5cf6" },
  { key: "thief", label: "Theft (Holdup)", icon: ShieldAlert, color: "#ef4444" },
  { key: "parking", label: "Parking", icon: Car, color: "#f97316" },
  { key: "merged", label: "Merged (All 3)", icon: Layers, color: "#22c55e" },
];

// Only parking judges vehicles against drawn road-edge lines (see
// obstruction_web.py / watch_parking.py) — every other detector runs on the
// clip alone, so the edge-drawing step is skipped for them.
export const TYPES_WITH_EDGES = new Set(["parking"]);
