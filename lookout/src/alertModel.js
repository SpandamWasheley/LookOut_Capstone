// One place that turns an API alert into what the web UI uses, so the Violations tab, the
// Records page, the Overview watchlist and the alert modal can never disagree.
//
// Spec v6: the official status (Monitoring / Possible / Likely) comes only from the system
// indicators; the AI checker's context and suggested status are shown beside it and never
// change it. `aiContext` is computed by the backend on every read.

export function mapAlert(raw) {
  return {
    id: raw.code,
    dbId: raw.id,
    type: raw.type,
    status: raw.status,
    camera: raw.camera,
    cameraZone: raw.camera_zone,
    // Where the camera is (typed in Live Feeds).
    cameraAddress: raw.camera_address ?? "",
    timestamp: raw.timestamp,
    lastSeenAt: raw.last_seen_at,
    description: raw.description,
    imageUrl: raw.image_url,
    videoUrl: raw.video_url,
    rawVideoUrl: raw.raw_video_url,
    officersAssignedIds: raw.officers_assigned ?? [],
    officersAssignedNames: raw.officers_assigned_names ?? [],
    suspect: raw.suspect,
    notes: raw.notes,
    // The official status: stored level + its display name (Monitoring / Possible / Likely).
    level: raw.level,
    levelLabel: raw.level_label || "",
    // What the OBJECT DETECTOR was sure of (not a violation likelihood).
    objectConfidence: raw.object_confidence,
    // The evidence the tanod reads instead of a score.
    checklist: raw.cues?.checklist ?? null,
    cues: raw.cues,
    // The AI checker's cards: badge, observations, checklist, suggested status.
    aiContext: raw.ai_context ?? null,
    reviewedValid: raw.reviewed_valid,
    reviewedBy: raw.reviewed_by_name,
    reviewedAt: raw.reviewed_at,
  };
}

// The header tag that replaces "Active": set by the tanod.
//   reviewed_valid null -> Pending, true -> Verified, false -> Dismissed
export function reviewTag(alert) {
  if (alert.reviewedValid === true) return { key: "verified", label: "Verified", color: "#10b981", bg: "rgba(16,185,129,0.12)" };
  if (alert.reviewedValid === false) return { key: "dismissed", label: "Dismissed", color: "#64748b", bg: "rgba(100,116,139,0.14)" };
  return { key: "pending", label: "Pending", color: "#f59e0b", bg: "rgba(245,158,11,0.14)" };
}

// Status colours: amber for Possible, red for Likely, a quiet blue-grey for Monitoring.
export const LEVEL_COLORS = {
  Likely: "#dc2626",
  Possible: "#f59e0b",
  Monitoring: "#64748b",
};

export function levelColor(label) {
  return LEVEL_COLORS[label] || "var(--muted-foreground)";
}
