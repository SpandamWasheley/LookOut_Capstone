import { useEffect, useState } from "react";
import { Clock, AlertTriangle, Radio, ArrowRight } from "lucide-react";
import { VIOLATION_CONFIG } from "../data/mockData";
import { ViolationModal } from "./ViolationModal";
import { DispatchModal } from "./DispatchModal";
import { getAlerts, getOfficers, updateAlert } from "./api";

function mapAlert(raw) {
  return {
    id: raw.code,
    dbId: raw.id,
    type: raw.type,
    status: raw.status,
    camera: raw.camera,
    cameraZone: raw.camera_zone,
    timestamp: raw.timestamp,
    confidence: raw.confidence,
    description: raw.description,
    imageUrl: raw.image_url,
    officersAssignedIds: raw.officers_assigned ?? [],
    officersAssignedNames: raw.officers_assigned_names ?? [],
    suspect: raw.suspect,
    notes: raw.notes,
  };
}

function mapOfficer(raw) {
  return {
    id: raw.id,
    name: raw.name,
    status: raw.status,
    location: raw.location,
    badge: raw.badge,
  };
}

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString("en-PH", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function AlertCard({ alert, onView }) {
  const vcfg = VIOLATION_CONFIG[alert.type] ?? { label: alert.type, color: "#f59e0b", icon: AlertTriangle };

  return (
    <div className="rounded-2xl p-3 mb-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      {/* Top row: icon, title, time/confidence */}
      <div className="flex items-center gap-2.5">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ background: `${vcfg.color}22` }}>
          <vcfg.icon size={18} color={vcfg.color} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold text-white truncate">{vcfg.label}</div>
          <div className="flex items-center gap-1.5 text-[10px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
            <Clock size={10} className="flex-shrink-0" /> {formatTime(alert.timestamp)}
            <span className="font-semibold" style={{ color: vcfg.color }}>· {(alert.confidence * 100).toFixed(0)}%</span>
          </div>
        </div>

        <div className="flex flex-col items-end gap-1 flex-shrink-0">
          {alert.status === "active" && (
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap"
              style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444" }}>
              Please Assign Agent
            </span>
          )}
          {alert.status === "dispatched" && (
            <>
              <span className="text-[10px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap"
                style={{ background: "rgba(59,130,246,0.15)", color: "#3b82f6" }}>
                Assigned
              </span>
              <span className="text-[10px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap"
                style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
                Pending
              </span>
            </>
          )}
        </div>
      </div>

      {/* Location */}
      <div className="flex items-center gap-1.5 mt-2 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
        <span className="w-1 h-1 rounded-full flex-shrink-0" style={{ background: "var(--muted-foreground)" }} />
        <span className="truncate">{alert.cameraZone}</span>
      </div>

      {/* Officer assignment */}
      {alert.officersAssignedNames.length > 0 && (
        <div className="flex items-center gap-1.5 mt-1 text-[11px]" style={{ color: "#3b82f6" }}>
          <Radio size={10} className="flex-shrink-0" /> {alert.officersAssignedNames.join(", ")}
        </div>
      )}

      {/* View button */}
      <button
        onClick={onView}
        className="w-full flex items-center justify-center gap-1.5 mt-2.5 px-3 py-1.5 rounded-lg text-[12px] font-medium transition-all cursor-pointer hover:brightness-125 active:scale-95"
        style={{ background: `${vcfg.color}18`, color: vcfg.color, border: `1px solid ${vcfg.color}30` }}
      >
        View <ArrowRight size={12} />
      </button>
    </div>
  );
}

const ASSIGN_FILTERS = [
  { id: "all", label: "All" },
  { id: "unassigned", label: "Needs Agent" },
  { id: "assigned", label: "Assigned" },
];

export function AlertFeed({ showFilters = false }) {
  const [alerts, setAlerts] = useState([]);
  const [officers, setOfficers] = useState([]);
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [dispatchingAlert, setDispatchingAlert] = useState(null);
  const [typeFilter, setTypeFilter] = useState("all");
  const [assignFilter, setAssignFilter] = useState("all");

  const [actionError, setActionError] = useState("");

  const active = alerts.filter((a) => a.status === "active" || a.status === "dispatched");
  const visible = active.filter((a) => {
    const matchType = typeFilter === "all" || a.type === typeFilter;
    const matchAssign =
      assignFilter === "all" ||
      (assignFilter === "unassigned" ? a.officersAssignedIds.length === 0 : a.officersAssignedIds.length > 0);
    return matchType && matchAssign;
  });

  const refresh = async () => {
    const [alertsRes, officersRes] = await Promise.all([getAlerts(), getOfficers()]);
    setAlerts((alertsRes.results ?? alertsRes).map(mapAlert));
    setOfficers((officersRes.results ?? officersRes).map(mapOfficer));
  };

  useEffect(() => {
    refresh().catch(() => {});
    const interval = setInterval(() => refresh().catch(() => {}), 4000);
    return () => clearInterval(interval);
  }, []);

  const assignedOfficerNames = (alertId) => {
    const a = alerts.find((x) => x.id === alertId);
    return a?.officersAssignedNames ?? [];
  };

  const updateStatus = async (id, status) => {
    const a = alerts.find((x) => x.id === id);
    if (!a) return;
    setActionError("");
    try {
      await updateAlert(a.dbId, { status });
      await refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to update violation status.");
    }
  };

  const handleAssign = async (officerIds) => {
    setActionError("");
    try {
      await updateAlert(dispatchingAlert.dbId, { status: "dispatched", officers_assigned: officerIds });
      setDispatchingAlert(null);
      await refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to assign officers.");
    }
  };

  return (
    <div className="rounded-2xl p-5" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <AlertTriangle size={18} style={{ color: "var(--muted-foreground)" }} />
          <span className="text-lg font-semibold text-white">Recent Violations</span>
        </div>
        {active.length > 0 && (
          <span className="text-[12px] font-medium px-2.5 py-1 rounded-full"
            style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444" }}>
            {active.length} active
          </span>
        )}
      </div>

      {actionError && (
        <div className="mb-4 px-3 py-2 rounded-lg text-[12px] flex items-center justify-between gap-2"
          style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.3)" }}>
          <span>{actionError}</span>
          <button onClick={() => setActionError("")} className="font-semibold cursor-pointer flex-shrink-0">✕</button>
        </div>
      )}

      {/* Filters */}
      {showFilters && (
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="text-[12px] font-medium px-3 py-1.5 rounded-lg outline-none"
            style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }}
          >
            <option value="all">All types</option>
            {Object.entries(VIOLATION_CONFIG).map(([key, cfg]) => (
              <option key={key} value={key}>{cfg.label}</option>
            ))}
          </select>

          <div className="flex gap-1.5">
            {ASSIGN_FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setAssignFilter(f.id)}
                className="px-3 py-1.5 text-[12px] font-medium rounded-lg transition-all"
                style={{
                  background: assignFilter === f.id ? "rgba(245,158,11,0.15)" : "var(--secondary)",
                  color: assignFilter === f.id ? "#f59e0b" : "var(--muted-foreground)",
                  border: `1px solid ${assignFilter === f.id ? "rgba(245,158,11,0.4)" : "var(--border)"}`,
                }}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Cards */}
      {visible.length === 0 ? (
        <div className="text-center py-10 text-sm" style={{ color: "var(--muted-foreground)" }}>
          {active.length === 0 ? "No active violations" : "No violations match this filter"}
        </div>
      ) : (
        visible.map((alert) => (
          <AlertCard key={alert.id} alert={alert} onView={() => setSelectedAlert(alert)} />
        ))
      )}

      {selectedAlert && (
        <ViolationModal
          alert={selectedAlert}
          assignedOfficerNames={assignedOfficerNames(selectedAlert.id)}
          onClose={() => setSelectedAlert(null)}
          onDismiss={() => { updateStatus(selectedAlert.id, "acknowledged"); setSelectedAlert(null); }}
          onResolve={() => { updateStatus(selectedAlert.id, "resolved"); setSelectedAlert(null); }}
          onDispatch={() => { setDispatchingAlert(selectedAlert); setSelectedAlert(null); }}
        />
      )}

      {dispatchingAlert && (
        <DispatchModal
          alert={dispatchingAlert}
          officers={officers}
          alerts={alerts}
          onAssign={handleAssign}
          onClose={() => setDispatchingAlert(null)}
        />
      )}
    </div>
  );
}

export default AlertFeed;