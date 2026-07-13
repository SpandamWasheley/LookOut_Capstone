import React, { createContext, useCallback, useContext, useEffect, useState } from "react";

import { useAuth } from "@/context/AuthContext";
import * as api from "@/lib/api";

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
  confidence: number;
  dispatchedAt: string;
  assignedOfficerIds: number[];
  assignedOfficerNames: string[];
  status: "active" | "dispatched" | "acknowledged" | "resolved";
  notes: string;
  suspect: string;
  imageUrl: string;
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
    confidence: Math.round(raw.confidence * 100),
    dispatchedAt: raw.timestamp,
    assignedOfficerIds: raw.officers_assigned,
    assignedOfficerNames: raw.officers_assigned_names,
    status: raw.status,
    notes: raw.notes,
    suspect: raw.suspect,
    imageUrl: raw.image_url,
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
  resolveAssignment: (id: string, notes?: string, suspect?: string) => Promise<void>;
  dismissAssignment: (id: string, reason: string) => Promise<void>;
}

const AssignmentContext = createContext<AssignmentContextType | null>(null);

export function AssignmentProvider({ children }: { children: React.ReactNode }) {
  const { officer } = useAuth();
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchAssignments = useCallback(async (silent = false) => {
    if (!silent) setError("");
    try {
      const [alertsRes, typesRes] = await Promise.all([api.getAlerts(), api.getViolationTypes()]);
      const alerts = Array.isArray(alertsRes) ? alertsRes : alertsRes.results;
      const types = Array.isArray(typesRes) ? typesRes : typesRes.results;
      const typesByCode = Object.fromEntries(
        types.map((t) => [t.code, { code: t.code, label: t.label, color: t.color, icon: t.icon }])
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
      if (silent) setError("");
    } catch (err) {
      // Background polling fails silently (keeps showing the last good data);
      // only surface an error for the initial load / explicit manual refresh.
      if (!silent) setError(err instanceof Error ? err.message : "Failed to load assignments.");
    }
  }, []);

  const refreshAssignments = useCallback(() => fetchAssignments(false), [fetchAssignments]);

  useEffect(() => {
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
      // Atomic add on the server — safe if another officer accepts the same
      // alert at the same time, unlike a client-computed read-modify-write.
      await api.acceptAlert(a.dbId);
      await refreshAssignments();
    },
    [assignments, refreshAssignments]
  );

  const resolveAssignment = useCallback(
    async (id: string, notes?: string, suspect?: string) => {
      const a = assignments.find((x) => x.id === id);
      if (!a) return;
      await api.updateAlert(a.dbId, {
        status: "resolved",
        ...(notes !== undefined ? { notes } : {}),
        ...(suspect !== undefined ? { suspect } : {}),
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
