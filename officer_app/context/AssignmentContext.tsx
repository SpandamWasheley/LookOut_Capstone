import React, { createContext, useCallback, useContext, useEffect, useState } from "react";

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
  assignedOfficerName: string | null;
  status: "active" | "dispatched" | "acknowledged" | "resolved";
  notes: string;
  suspect: string;
  imageUrl: string;
}

const DEFAULT_TYPE: ViolationTypeMeta = { code: "unknown", label: "Violation", color: "#f59e0b", icon: "⚠️" };

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
    assignedOfficerName: raw.officer_assigned,
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
  acceptAssignment: (id: string, officerName: string) => Promise<void>;
  resolveAssignment: (id: string, notes?: string) => Promise<void>;
  dismissAssignment: (id: string, reason: string) => Promise<void>;
  saveNote: (id: string, note: string) => Promise<void>;
}

const AssignmentContext = createContext<AssignmentContextType | null>(null);

export function AssignmentProvider({ children }: { children: React.ReactNode }) {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refreshAssignments = useCallback(async () => {
    setError("");
    try {
      const [alertsRes, typesRes] = await Promise.all([api.getAlerts(), api.getViolationTypes()]);
      const alerts = Array.isArray(alertsRes) ? alertsRes : alertsRes.results;
      const types = Array.isArray(typesRes) ? typesRes : typesRes.results;
      const typesByCode = Object.fromEntries(
        types.map((t) => [t.code, { code: t.code, label: t.label, color: t.color, icon: t.icon }])
      );
      setAssignments(alerts.map((a) => mapAlert(a, typesByCode)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load assignments.");
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    refreshAssignments().finally(() => setLoading(false));
  }, [refreshAssignments]);

  const getAssignment = useCallback((id: string) => assignments.find((a) => a.id === id), [assignments]);

  const acceptAssignment = useCallback(
    async (id: string, officerName: string) => {
      const a = assignments.find((x) => x.id === id);
      if (!a) return;
      await api.updateAlert(a.dbId, { status: "dispatched", officer_assigned: officerName });
      await refreshAssignments();
    },
    [assignments, refreshAssignments]
  );

  const resolveAssignment = useCallback(
    async (id: string, notes?: string) => {
      const a = assignments.find((x) => x.id === id);
      if (!a) return;
      await api.updateAlert(a.dbId, { status: "resolved", ...(notes !== undefined ? { notes } : {}) });
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

  const saveNote = useCallback(
    async (id: string, note: string) => {
      const a = assignments.find((x) => x.id === id);
      if (!a) return;
      await api.updateAlert(a.dbId, { notes: note });
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
        saveNote,
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
