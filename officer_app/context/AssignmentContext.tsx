import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { useAuth } from "@/context/AuthContext";
import { announceNewAssignments } from "@/lib/alertSound";
import * as api from "@/lib/api";
import { displayLabel } from "@/lib/violationLabels";

export interface ViolationTypeMeta {
  code: string;
  label: string;
  color: string;
  icon: string;
}

export interface Assignment {
  id: string;
  dbId: number;
  code: string;
  violationType: ViolationTypeMeta;
  description: string;
  location: string;
  cameraCode: string | null;
  // What the officer reads: the Status, "Possible" or "Likely". There is deliberately no
  // score or percentage: it would read like a probability, which it is not.
  levelLabel: string;
  // What the object detector was sure of, separately from the score above.
  objectConfidence: number | null;
  // Plain-language evidence lines, and anything that adjusted the status.
  checklist: { found: string[]; adjusted_by: string[]; tag: string } | null;
  // The AI checker's context card and suggested status (display only).
  ai: api.ApiAiContext | null;
  // Closing details, for the Dismissed / Resolved banner.
  reviewedBy: string;
  reviewedAt: string | null;
  citationIssued: boolean;
  dispatchedAt: string;
  assignedOfficerIds: number[];
  assignedOfficerNames: string[];
  status: "active" | "dispatched" | "acknowledged" | "resolved";
  notes: string;
  imageUrl: string;
  videoUrl: string;
}

const DEFAULT_TYPE: ViolationTypeMeta = { code: "unknown", label: "Violation", color: "#f59e0b", icon: "alert" };

function mapAlert(raw: api.ApiAlert, typesByCode: Record<string, ViolationTypeMeta>): Assignment {
  return {
    id: String(raw.id),
    dbId: raw.id,
    code: raw.code,
    violationType: typesByCode[raw.type] ?? { ...DEFAULT_TYPE, code: raw.type },
    description: raw.description,
    location: raw.camera_zone || "Unknown location",
    cameraCode: raw.camera,
    levelLabel: raw.level_label || "",
    objectConfidence: raw.object_confidence ?? null,
    checklist: raw.cues?.checklist
      ? {
          found: raw.cues.checklist.found ?? [],
          adjusted_by: raw.cues.checklist.adjusted_by ?? [],
          tag: raw.cues.checklist.tag ?? "",
        }
      : null,
    ai: raw.ai_context ?? null,
    reviewedBy: raw.reviewed_by_name || "",
    reviewedAt: raw.reviewed_at ?? null,
    citationIssued: !!raw.citation_issued,
    dispatchedAt: raw.timestamp,
    assignedOfficerIds: raw.officers_assigned,
    assignedOfficerNames: raw.officers_assigned_names,
    status: raw.status,
    notes: raw.notes,
    imageUrl: raw.image_url,
    videoUrl: raw.video_url,
  };
}

interface AssignmentContextType {
  assignments: Assignment[];
  activeAssignments: Assignment[];
  historyAssignments: Assignment[];
  loading: boolean;
  error: string;
  refreshAssignments: () => Promise<void>;
  getAssignment: (id: string) => Assignment | undefined;
  acceptAssignment: (id: string) => Promise<void>;
  resolveAssignment: (id: string, notes?: string) => Promise<void>;
  dismissAssignment: (id: string, reason: string) => Promise<void>;
}

const AssignmentContext = createContext<AssignmentContextType | null>(null);

export function AssignmentProvider({ children }: { children: React.ReactNode }) {
  const { officer } = useAuth();
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Which open violations were already this officer's last time we looked.
  // `null` until the first list lands — that one is the backlog, not an
  // arrival, and announcing it would chime through everything an officer is
  // already carrying every time they sign in.
  const mineRef = useRef<Set<string> | null>(null);
  // Assignments this officer gave themselves by tapping Accept. They are
  // holding the phone and just pressed the button, so a notification about it
  // is noise — the same reason AlertViewSet.accept deliberately sends no
  // push. Consumed on the poll that first sees them, so an alert reassigned
  // to them later still announces.
  const selfClaimedRef = useRef<Set<string>>(new Set());

  // Mirrors core/push.py's rule: only assignments made to THIS officer, and
  // only ones that are new to them. Runs on the poll's own response, so the
  // comparison is against what the server just said rather than against a
  // re-render of the same array.
  const announce = useCallback((list: Assignment[]) => {
    const id = officer?.officerId;
    if (id == null) return;
    const mine = list.filter(
      (a) => (a.status === "active" || a.status === "dispatched") && a.assignedOfficerIds.includes(id),
    );
    const open = new Set(mine.map((a) => a.id));
    const seen = mineRef.current;
    mineRef.current = open;
    if (seen === null) return;

    const fresh = mine.filter((a) => {
      if (seen.has(a.id)) return false;
      if (selfClaimedRef.current.has(a.id)) {
        selfClaimedRef.current.delete(a.id);
        return false;
      }
      return true;
    });
    if (fresh.length === 0) return;
    announceNewAssignments({
      count: fresh.length,
      id: fresh[0].id,
      label: fresh[0].violationType.label,
      location: fresh[0].location,
    });
  }, [officer?.officerId]);

  const fetchAssignments = useCallback(async (silent = false) => {
    if (!silent) setError("");
    try {
      const [alertsRes, typesRes] = await Promise.all([api.getAlerts(), api.getViolationTypes()]);
      const alerts = Array.isArray(alertsRes) ? alertsRes : alertsRes.results;
      const types = Array.isArray(typesRes) ? typesRes : typesRes.results;
      const typesByCode = Object.fromEntries(
        // displayLabel here rather than at each render site: this is the single
        // place the app turns an API type into the shape every screen reads, so
        // the card, the detail header, the search filter and the local
        // notification cannot drift apart on what a violation is called.
        types.map((t) => [t.code, { code: t.code, label: displayLabel(t), color: t.color, icon: t.icon }])
      );
      const next = alerts.map((a) => mapAlert(a, typesByCode));
      // Only swap in a new array when the data actually changed. Every 4s poll
      // otherwise produced fresh object references even when nothing moved,
      // re-rendering every consumer and reloading the detail screen's evidence
      // image — which read as a constant flicker. Returning the previous
      // reference lets React bail out of the re-render entirely.
      setAssignments((prev) =>
        JSON.stringify(prev) === JSON.stringify(next) ? prev : next
      );
      // Driven by the RESPONSE, not by the state swap above: whether React
      // re-rendered says nothing about whether this officer was handed
      // something, and `announce` keeps its own record of what was already
      // theirs.
      announce(next);
      if (silent) setError("");
    } catch (err) {
      // Background polling fails silently (keeps showing the last good data);
      // only surface an error for the initial load / explicit manual refresh.
      if (!silent) setError(err instanceof Error ? err.message : "Failed to load assignments.");
    }
    // `announce` is rebuilt when the signed-in officer changes, which the
    // poll's own effect reacts to as well — without it here, polling would go
    // on calling the previous officer's version.
  }, [announce]);

  const refreshAssignments = useCallback(() => fetchAssignments(false), [fetchAssignments]);

  useEffect(() => {
    // A different account on the same handset must not inherit the last
    // officer's record of what was already theirs: reseed from their own
    // first list instead of announcing the difference between two people's
    // workloads.
    mineRef.current = null;
    selfClaimedRef.current.clear();
    if (!officer) {
      setAssignments([]);
      setError("");
      setLoading(false);
      return;
    }
    setLoading(true);
    fetchAssignments(false).finally(() => setLoading(false));

    const interval = setInterval(() => {
      fetchAssignments(true);
    }, 4000);
    return () => clearInterval(interval);
  }, [officer, fetchAssignments]);

  const getAssignment = useCallback((id: string) => assignments.find((a) => a.id === id), [assignments]);

  const acceptAssignment = useCallback(
    async (id: string) => {
      const a = assignments.find((x) => x.id === id);
      if (!a) return;
      // Recorded BEFORE the request, because the refresh below is what makes
      // it look like an arrival — and it is not one, the officer just pressed
      // Accept with the phone in their hand.
      selfClaimedRef.current.add(a.id);
      try {
        // Atomic add on the server — safe if another officer accepts the same
        // alert at the same time, unlike a client-computed read-modify-write.
        await api.acceptAlert(a.dbId);
      } catch (err) {
        // The accept never happened, so this is not theirs and the mark would
        // silently swallow the announcement if a dispatcher assigned it to
        // them later. The screen still reports the failure.
        selfClaimedRef.current.delete(a.id);
        throw err;
      }
      await refreshAssignments();
    },
    [assignments, refreshAssignments]
  );

  const resolveAssignment = useCallback(
    async (id: string, notes?: string) => {
      const a = assignments.find((x) => x.id === id);
      if (!a) return;
      await api.updateAlert(a.dbId, {
        status: "resolved",
        ...(notes !== undefined ? { notes } : {}),
      });
      await refreshAssignments();
    },
    [assignments, refreshAssignments]
  );

  const dismissAssignment = useCallback(
    async (id: string, reason: string) => {
      const a = assignments.find((x) => x.id === id);
      if (!a) return;
      await api.updateAlert(a.dbId, { status: "acknowledged", notes: reason });
      await refreshAssignments();
    },
    [assignments, refreshAssignments]
  );

  const activeAssignments = assignments.filter((a) => a.status === "active" || a.status === "dispatched");
  const historyAssignments = assignments.filter((a) => a.status === "acknowledged" || a.status === "resolved");

  return (
    <AssignmentContext.Provider
      value={{
        assignments,
        activeAssignments,
        historyAssignments,
        loading,
        error,
        refreshAssignments,
        getAssignment,
        acceptAssignment,
        resolveAssignment,
        dismissAssignment,
      }}
    >
      {children}
    </AssignmentContext.Provider>
  );
}

export function useAssignments() {
  const ctx = useContext(AssignmentContext);
  if (!ctx) throw new Error("useAssignments must be used within AssignmentProvider");
  return ctx;
}
