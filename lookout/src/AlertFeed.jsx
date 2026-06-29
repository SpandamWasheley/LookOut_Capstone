import { useEffect, useState } from "react";
import { Bell, MapPin, Clock, AlertTriangle, Radio, CheckCircle, X, Camera as CameraIcon, Moon, Sun, Sunset } from "lucide-react";
import { VIOLATION_CONFIG } from "../data/mockData";
import { ViolationModal } from "./ViolationModal";
import { DispatchModal } from "./DispatchModal";
import { getAlerts, getOfficers, getCameras, getHouseholds, getResidents, updateAlert } from "./api";

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
  return { id: raw.id, name: raw.name, status: raw.status, location: raw.location, badge: raw.badge };
}

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", hour12: false });
}

const statusConfig = {
  active:       { label: "Active",     color: "#ef4444", bg: "rgba(239,68,68,0.1)"   },
  acknowledged: { label: "Dismissed",  color: "#64748b", bg: "rgba(100,116,139,0.1)" },
  dispatched:   { label: "Dispatched", color: "#3b82f6", bg: "rgba(59,130,246,0.1)"  },
  resolved:     { label: "Resolved",   color: "#10b981", bg: "rgba(16,185,129,0.1)"  },
};

const dismissReasons = [
  "False positive — no violation present",
  "Duplicate alert — already handled",
  "Outside barangay jurisdiction",
  "Manually handled before dispatch",
  "Technical glitch / sensor error",
  "Other",
];

// ── Dismiss modal ─────────────────────────────────────────────────────────────
function DismissModal({ alert, onConfirm, onClose }) {
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const vcfg = VIOLATION_CONFIG[alert.type] ?? { label: alert.type, color: "#f59e0b", icon: AlertTriangle };
  const VIcon = vcfg.icon;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.8)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-sm rounded-2xl overflow-hidden shadow-2xl"
        style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-md flex items-center justify-center"
              style={{ background: `${vcfg.color}18` }}>
              <VIcon size={14} color={vcfg.color} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Dismiss Alert</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{alert.id} · {vcfg.label}</div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs font-medium mb-2" style={{ color: "var(--muted-foreground)" }}>
              Reason for dismissal <span style={{ color: "#ef4444" }}>*</span>
            </label>
            <div className="space-y-1.5">
              {dismissReasons.map((r) => (
                <label
                  key={r}
                  className="flex items-center gap-2.5 px-3 py-2 rounded-lg cursor-pointer transition-all"
                  style={{
                    background: reason === r ? "rgba(245,158,11,0.08)" : "var(--secondary)",
                    border: `1px solid ${reason === r ? "rgba(245,158,11,0.25)" : "var(--border)"}`,
                  }}
                >
                  <input
                    type="radio"
                    name="dismiss-reason"
                    value={r}
                    checked={reason === r}
                    onChange={() => setReason(r)}
                    style={{ accentColor: "#f59e0b" }}
                  />
                  <span className="text-[12px]"
                    style={{ color: reason === r ? "var(--foreground)" : "var(--muted-foreground)" }}>
                    {r}
                  </span>
                </label>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Additional notes <span style={{ color: "var(--muted-foreground)", fontWeight: 400 }}>(optional)</span>
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="Any additional context…"
              className="w-full px-3 py-2.5 rounded-xl text-sm outline-none resize-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4"
          style={{ borderTop: "1px solid var(--border)" }}>
          <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
            Cancel
          </button>
          <button
            disabled={!reason}
            onClick={() => reason && onConfirm(reason, notes)}
            className="px-5 py-2 rounded-xl text-sm font-medium transition-all"
            style={{
              background: reason ? "rgba(100,116,139,0.2)" : "rgba(100,116,139,0.08)",
              color: "var(--muted-foreground)",
              border: `1px solid ${reason ? "rgba(100,116,139,0.3)" : "var(--border)"}`,
              cursor: reason ? "pointer" : "not-allowed",
            }}
          >
            Confirm dismissal
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Alert card ────────────────────────────────────────────────────────────────
function AlertCard({ alert, onView }) {
  const vcfg = VIOLATION_CONFIG[alert.type] ?? { label: alert.type, color: "#f59e0b", icon: AlertTriangle };
  const officerLabel = alert.officersAssignedNames.length > 0
    ? alert.officersAssignedNames[0]
    : "Unassigned";

  return (
    <div
      onClick={onView}
      className="rounded-xl overflow-hidden cursor-pointer transition-all duration-150 mb-2"
      style={{
        background: "var(--secondary)",
        border: "1px solid rgba(239,68,68,0.2)",
        borderLeft: `3px solid ${vcfg.color}`,
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = "rgba(239,68,68,0.4)")}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = "rgba(239,68,68,0.2)")}
    >
      <div className="flex items-center gap-3 px-3.5 py-2.5">
        <span className="text-[12px] font-semibold flex-shrink-0"
          style={{ color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }}>
          {formatTime(alert.timestamp)}
        </span>
        <span className="text-[11px] font-medium flex-shrink-0" style={{ color: vcfg.color }}>
          {(alert.confidence * 100).toFixed(0)}% conf
        </span>
        <span className="text-[11px] ml-auto truncate text-right"
          style={{ color: alert.officersAssignedNames.length > 0 ? "#3b82f6" : "var(--muted-foreground)" }}>
          {officerLabel}
        </span>
      </div>
    </div>
  );
}

// ── Shift Info ────────────────────────────────────────────────────────────────
const SHIFTS = [
  { name: "Day",     start: 6,  end: 14, label: "6:00 AM - 2:00 PM",  Icon: Sun,    color: "#f59e0b" },
  { name: "Evening", start: 14, end: 22, label: "2:00 PM - 10:00 PM", Icon: Sunset, color: "#f97316" },
  { name: "Night",   start: 22, end: 6,  label: "10:00 PM - 6:00 AM", Icon: Moon,   color: "#3b82f6" },
];

function getCurrentShift() {
  const h = new Date().getHours();
  return (
    SHIFTS.find((s) => s.start < s.end ? h >= s.start && h < s.end : h >= s.start || h < s.end)
    ?? SHIFTS[2]
  );
}

function ShiftInfo() {
  const shift = getCurrentShift();
  const ShiftIcon = shift.Icon;
  return (
    <div className="px-4 py-4">
      <div className="text-[10px] font-semibold uppercase mb-3"
        style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace", letterSpacing: "0.1em" }}>
        Shift Info
      </div>
      <div className="space-y-2.5">
        <div className="flex items-center justify-between text-[12px]">
          <span className="flex items-center gap-2" style={{ color: "var(--muted-foreground)" }}>
            <ShiftIcon size={12} /> Current shift
          </span>
          <span className="font-semibold" style={{ color: shift.color }}>{shift.name}</span>
        </div>
        <div className="flex items-center justify-between text-[12px]">
          <span className="flex items-center gap-2" style={{ color: "var(--muted-foreground)" }}>
            <Clock size={12} /> Hours
          </span>
          <span className="font-semibold" style={{ color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }}>
            {shift.label}
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Right panel ───────────────────────────────────────────────────────────────
function RightPanel({ alerts, cameras }) {
  const onlineCount = cameras.filter((c) => c.status === "online").length;
  const activeCount = alerts.filter((a) => a.status === "active").length;
  const dispatchedCount = alerts.filter((a) => a.status === "dispatched").length;
  const pendingReview = alerts.filter((a) => a.status === "active" && a.officersAssignedIds.length === 0).length;
  const totalToday = alerts.length;

  const latestAlert = [...alerts]
    .filter((a) => a.status === "active" || a.status === "dispatched")
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))[0];

  const lastDetected = alerts.length > 0
    ? formatTime([...alerts].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))[0].timestamp)
    : "--:--";

  const sectionLabel = (text) => (
    <div className="text-[10px] font-semibold uppercase mb-3"
      style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace", letterSpacing: "0.1em" }}>
      {text}
    </div>
  );

  return (
    <div className="rounded-xl overflow-hidden" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      {/* System Status */}
      <div className="px-4 pt-4 pb-4" style={{ borderBottom: "1px solid var(--border)" }}>
        {sectionLabel("System Status")}
        <div className="space-y-3">
          <div className="flex items-center justify-between text-[12px]">
            <span className="flex items-center gap-2" style={{ color: "var(--muted-foreground)" }}>
              <CameraIcon size={12} /> Cameras online
            </span>
            <span className="font-semibold" style={{ color: "#f59e0b", fontFamily: "'DM Mono', monospace" }}>
              {onlineCount} / {cameras.length || "—"}
            </span>
          </div>
          <div className="flex items-center justify-between text-[12px]">
            <span className="flex items-center gap-2" style={{ color: "var(--muted-foreground)" }}>
              <Clock size={12} /> Last detection
            </span>
            <span className="font-semibold" style={{ color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }}>
              {lastDetected}
            </span>
          </div>
        </div>
      </div>

      {/* Today's Violations */}
      <div className="px-4 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
        {sectionLabel("Today's Violations")}
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: "Total today",    value: totalToday,      color: "#f97316" },
            { label: "Pending review", value: pendingReview,   color: "#f59e0b" },
            { label: "Dispatched",     value: dispatchedCount, color: "#f97316" },
            { label: "Active",         value: activeCount,     color: "#ef4444" },
          ].map((s) => (
            <div key={s.label} className="rounded-lg p-2.5"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              <div className="text-xl font-bold leading-none" style={{ color: s.color }}>{s.value}</div>
              <div className="text-[10px] mt-1" style={{ color: "var(--muted-foreground)" }}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Latest Alert */}
      {latestAlert && (() => {
        const vcfg = VIOLATION_CONFIG[latestAlert.type] ?? { label: latestAlert.type, color: "#ef4444", icon: AlertTriangle };
        return (
          <div className="px-4 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
            {sectionLabel("Latest Alert")}
            <div className="rounded-lg p-3"
              style={{ background: "var(--secondary)", borderLeft: `3px solid ${vcfg.color}`, border: "1px solid var(--border)" }}>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[13px] font-semibold" style={{ color: "var(--foreground)" }}>{vcfg.label}</span>
                <span className="text-[10px] font-medium px-2 py-0.5 rounded"
                  style={{ background: "rgba(239,68,68,0.2)", color: "#ef4444" }}>
                  {(latestAlert.confidence * 100).toFixed(0)}% conf
                </span>
              </div>
              <div className="flex items-center gap-3 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                <span className="flex items-center gap-1"><AlertTriangle size={9} /> {latestAlert.cameraZone}</span>
                <span className="flex items-center gap-1"><Clock size={9} /> {formatTime(latestAlert.timestamp)}</span>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Shift Info */}
      <ShiftInfo />
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────
export function AlertFeed({ showFilters = false }) {
  const [alerts, setAlerts] = useState([]);
  const [officers, setOfficers] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [households, setHouseholds] = useState([]);
  const [residents, setResidents] = useState([]);
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [dispatchingAlert, setDispatchingAlert] = useState(null);
  const [dismissTarget, setDismissTarget] = useState(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [actionError, setActionError] = useState("");
  const [toast, setToast] = useState(null);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const ongoing = alerts.filter((a) => a.status === "active");
  const visible = ongoing.filter((a) => {
    if (statusFilter === "active")     return a.status === "active";
    if (statusFilter === "dispatched") return a.status === "dispatched";
    return true;
  });

  const refresh = async () => {
    const [alertsRes, officersRes, camerasRes] = await Promise.all([
      getAlerts(), getOfficers(), getCameras(),
    ]);
    setAlerts((alertsRes.results ?? alertsRes).map(mapAlert));
    setOfficers((officersRes.results ?? officersRes).map(mapOfficer));
    setCameras(camerasRes.results ?? camerasRes);
  };

  useEffect(() => {
    refresh().catch(() => {});
    const id = setInterval(() => refresh().catch(() => {}), 4000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    getHouseholds().then((data) => setHouseholds(data.results ?? data)).catch(() => {});
    getResidents().then((data) => setResidents(data.results ?? data)).catch(() => {});
  }, []);

  const assignedOfficerNames = (alertId) =>
    alerts.find((x) => x.id === alertId)?.officersAssignedNames ?? [];

  const handleDismiss = async (reason, notes) => {
    if (!dismissTarget) return;
    setActionError("");
    try {
      await updateAlert(dismissTarget.dbId, {
        status: "acknowledged",
        notes: notes ? `${reason}: ${notes}` : reason,
      });
      await refresh();
      showToast(`Alert ${dismissTarget.id} dismissed`);
      setSelectedAlert(null);
    } catch (err) {
      setActionError(err.message || "Failed to dismiss alert.");
    }
    setDismissTarget(null);
  };

  const handleAssign = async (officerIds) => {
    setActionError("");
    try {
      await updateAlert(dispatchingAlert.dbId, { status: "dispatched", officers_assigned: officerIds });
      setDispatchingAlert(null);
      await refresh();
      showToast(`Officers dispatched to ${dispatchingAlert.cameraZone}`);
    } catch (err) {
      setActionError(err.message || "Failed to assign officers.");
    }
  };

  const handleResolve = async (alertId, suspectNames) => {
    const a = alerts.find((x) => x.id === alertId);
    if (!a) return;
    setActionError("");
    try {
      const payload = { status: "resolved" };
      if (suspectNames) payload.suspect = suspectNames;
      await updateAlert(a.dbId, payload);
      await refresh();
      showToast("Violation marked resolved");
      setSelectedAlert(null);
    } catch (err) {
      setActionError(err.message || "Failed to resolve.");
    }
  };

  const errorBanner = actionError && (
    <div className="mb-3 px-3 py-2 rounded-lg text-[12px] flex items-center justify-between gap-2"
      style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.3)" }}>
      <span>{actionError}</span>
      <button onClick={() => setActionError("")} className="font-semibold cursor-pointer flex-shrink-0">✕</button>
    </div>
  );

  const modals = (
    <>
      {selectedAlert && (
        <ViolationModal
          alert={selectedAlert}
          assignedOfficerNames={assignedOfficerNames(selectedAlert.id)}
          households={households}
          residents={residents}
          onClose={() => setSelectedAlert(null)}
          onDismiss={() => setDismissTarget(selectedAlert)}
          onResolve={(suspectNames) => handleResolve(selectedAlert.id, suspectNames)}
          onDispatch={() => { setDispatchingAlert(selectedAlert); setSelectedAlert(null); }}
        />
      )}
      {dismissTarget && (
        <DismissModal
          alert={dismissTarget}
          onConfirm={(r, n) => handleDismiss(r, n)}
          onClose={() => setDismissTarget(null)}
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
    </>
  );

  // ── Compact (dashboard embed) ──────────────────────────────────────────────
  if (!showFilters) {
    return (
      <div className="relative">
        {toast && (
          <div className="fixed top-5 left-1/2 -translate-x-1/2 z-[70] flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium shadow-xl"
            style={{ background: "rgba(16,185,129,0.15)", border: "1px solid rgba(16,185,129,0.25)", color: "#10b981", backdropFilter: "blur(8px)" }}>
            <CheckCircle size={13} /> {toast}
          </div>
        )}
        {errorBanner}
        {visible.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2">
            <CheckCircle size={28} style={{ color: "#10b981" }} />
            <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>No active violations</div>
            <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>All zones clear</div>
          </div>
        ) : (
          visible.map((a) => <AlertCard key={a.id} alert={a} onView={() => setSelectedAlert(a)} />)
        )}
        {modals}
      </div>
    );
  }

  // ── Full page ──────────────────────────────────────────────────────────────
  const activeCount = alerts.filter((a) => a.status === "active").length;

  return (
    <div className="flex flex-col h-full relative" style={{ background: "var(--background)" }}>
      {/* Toast */}
      {toast && (
        <div className="fixed top-5 left-1/2 -translate-x-1/2 z-[70] flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium shadow-xl"
          style={{ background: "rgba(16,185,129,0.15)", border: "1px solid rgba(16,185,129,0.25)", color: "#10b981", backdropFilter: "blur(8px)" }}>
          <CheckCircle size={13} /> {toast}
        </div>
      )}

      {/* Page header */}
      {(() => {
        const headerColor = statusFilter === "active" ? "#ef4444" : statusFilter === "dispatched" ? "#3b82f6" : null;
        return (
          <div className="flex items-center justify-between px-6 py-4 flex-shrink-0 transition-colors duration-200"
            style={{
              borderBottom: `1px solid ${headerColor ? headerColor + "40" : "var(--border)"}`,
              background: headerColor ? headerColor + "12" : "transparent",
            }}>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-bold transition-colors duration-200"
                style={{ color: headerColor ?? "var(--foreground)" }}>
                Violations
              </h1>
              {activeCount > 0 && (
                <span className="flex items-center gap-1.5 text-[12px] font-medium px-2.5 py-1 rounded-full"
                  style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444" }}>
                  <Bell size={11} /> {activeCount} active
                </span>
              )}
            </div>
            <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
              AI-detected · evidence captured · human review required
            </span>
          </div>
        );
      })()}

      {/* Filter row */}
      <div className="flex items-center justify-between px-6 py-3 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-1.5">
          {(["all", "active", "dispatched"]).map((s) => {
            const isActive = statusFilter === s;
            const scfg = statusConfig[s];
            return (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className="px-3 py-1 text-xs font-medium rounded-full transition-all capitalize"
                style={{
                  background: isActive ? (s === "all" ? "var(--primary)" : scfg.bg) : "var(--secondary)",
                  color: isActive ? (s === "all" ? "var(--primary-foreground)" : scfg.color) : "var(--muted-foreground)",
                  border: `1px solid ${isActive ? (s === "all" ? "var(--primary)" : scfg.color + "40") : "var(--border)"}`,
                }}
              >
                {s === "all" ? "All active" : scfg?.label ?? s}
              </button>
            );
          })}
        </div>
        <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
          {visible.length} record{visible.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Content grid */}
      <div className="flex-1 overflow-hidden grid gap-4 px-6 py-4"
        style={{ gridTemplateColumns: "1fr 280px" }}>
        {/* Alert list */}
        <div className="overflow-y-auto">
          {errorBanner}
          {visible.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2">
              <CheckCircle size={28} style={{ color: "#10b981" }} />
              <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
                {ongoing.length === 0 ? "No active violations" : "No violations match this filter"}
              </div>
              <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>All zones clear</div>
            </div>
          ) : (
            visible.map((a) => <AlertCard key={a.id} alert={a} onView={() => setSelectedAlert(a)} />)
          )}
        </div>

        {/* Right panel */}
        <div className="overflow-y-auto">
          <RightPanel alerts={alerts} cameras={cameras} />
        </div>
      </div>

      {modals}
    </div>
  );
}

export default AlertFeed;
