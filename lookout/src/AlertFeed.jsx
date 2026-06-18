import { useState } from "react";
import { Clock, AlertTriangle, Radio, ArrowRight } from "lucide-react";
import { mockAlerts, mockOfficers, VIOLATION_CONFIG } from "../data/mockData";
import { ViolationModal } from "./ViolationModal";
import { DispatchModal } from "./DispatchModal";

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString("en-PH", {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function AlertCard({ alert, onView }) {
  const vcfg = VIOLATION_CONFIG[alert.type] ?? { label: alert.type, color: "#f59e0b", icon: "⚠️" };

  return (
    <div className="rounded-2xl p-4 mb-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      <div className="flex items-start gap-4">
        {/* Icon tile */}
        <div className="w-12 h-12 rounded-xl flex items-center justify-center text-xl flex-shrink-0"
          style={{ background: `${vcfg.color}22` }}>
          {vcfg.icon}
        </div>

        {/* Middle content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[15px] font-semibold text-white">{vcfg.label}</span>
            {alert.status === "active" && (
              <span className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444" }}>
                Active
              </span>
            )}
          </div>

          {/* Officer assignment */}
          {alert.officerAssigned && (
            <div className="flex items-center gap-1.5 mt-1 text-[12px]" style={{ color: "#3b82f6" }}>
              <Radio size={12} /> 1 officer
            </div>
          )}

          {/* Location */}
          <div className="flex items-center gap-1.5 mt-1 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
            <span className="w-1 h-1 rounded-full flex-shrink-0" style={{ background: "var(--muted-foreground)" }} />
            {alert.cameraZone}
          </div>
        </div>

        {/* Time + confidence */}
        <div className="flex flex-col items-end gap-2 flex-shrink-0">
          <div className="flex items-center gap-1.5 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
            <Clock size={13} /> {formatTime(alert.timestamp)}
          </div>
          <div className="text-[15px] font-bold leading-tight text-right" style={{ color: vcfg.color }}>
            {(alert.confidence * 100).toFixed(0)}%<br />
            <span className="text-[11px] font-normal">conf</span>
          </div>
        </div>

        {/* View button */}
        <div className="flex-shrink-0 self-center">
          <button
            onClick={onView}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-[13px] font-medium transition-all cursor-pointer hover:brightness-125 active:scale-95"
            style={{ background: `${vcfg.color}18`, color: vcfg.color, border: `1px solid ${vcfg.color}30` }}
          >
            View <ArrowRight size={13} />
          </button>
        </div>
      </div>
    </div>
  );
}

export function AlertFeed() {
  const [alerts, setAlerts] = useState(mockAlerts);
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [dispatchingAlert, setDispatchingAlert] = useState(null);
  const [assignments, setAssignments] = useState([]);
  const active = alerts.filter((a) => a.status === "active");

  const updateStatus = (id, status) => {
    setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, status } : a)));
  };

  const assignedOfficerNames = (alertId) =>
    assignments
      .filter((a) => a.alertId === alertId)
      .map((a) => mockOfficers.find((o) => o.id === a.officerId)?.name)
      .filter(Boolean);

  const handleAssign = (officerIds) => {
    setAssignments((prev) => [
      ...prev.filter((a) => a.alertId !== dispatchingAlert.id),
      ...officerIds.map((officerId) => ({ alertId: dispatchingAlert.id, officerId })),
    ]);
    updateStatus(dispatchingAlert.id, "dispatched");
    setDispatchingAlert(null);
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

      {/* Cards */}
      {active.length === 0 ? (
        <div className="text-center py-10 text-sm" style={{ color: "var(--muted-foreground)" }}>
          No active violations
        </div>
      ) : (
        active.map((alert) => (
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
          officers={mockOfficers}
          assignments={assignments}
          onAssign={handleAssign}
          onClose={() => setDispatchingAlert(null)}
        />
      )}
    </div>
  );
}

export default AlertFeed;