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
    // This is what every badge shows — the live truth, which can fall as well as rise.
    level: raw.level,
    levelLabel: raw.level_label || "",
    // The HIGHEST status this event ever reached. Decides WHERE it is listed, never
    // what it is badged as: an event that reached Possible and faded back to
    // Monitoring stays in Potential Violations, still showing "Monitoring".
    peakLevel: raw.peak_level || "",
    peakLevelLabel: raw.peak_level_label || "",
    // What the OBJECT DETECTOR was sure of (not a violation likelihood).
    objectConfidence: raw.object_confidence,
    // The evidence the tanod reads instead of a score.
    checklist: raw.cues?.checklist ?? null,
    cues: raw.cues,
    // The AI checker's cards: badge, observations, checklist, suggested status.
    aiContext: raw.ai_context ?? null,
    // Closing details (dismissed / resolved banner) and the event timeline.
    citationIssued: !!raw.citation_issued,
    timeline: raw.timeline ?? [],
    // "live" | "recorded" (uploaded clip with a Recorded-at time) | "processed" (uploaded clip, no time given)
    timeSource: raw.time_source ?? "live",
    reviewedBy: raw.reviewed_by_name,
    reviewedAt: raw.reviewed_at,
  };
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
