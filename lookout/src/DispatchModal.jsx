import { useState } from "react";
import { Radio, X, Check, MapPin, AlertTriangle, Search } from "lucide-react";
import { violationDisplay } from "./constants/violationTypes";
import { RecordingPlayer, QuietCard } from "./ViolationModal";

const officerStatusColor = {
  "on-duty":    "#10b981",
  "off-duty":   "#4a5568",
  "responding": "#f59e0b",
};

const loadLabel = (count) =>
  count === 0 ? "free" : count === 1 ? "moderate" : "heavy";
const loadColor = (count) =>
  count === 0 ? "#10b981" : count === 1 ? "#f59e0b" : "#ef4444";

function OfficerRow({ officer, count, isChecked, onToggle }) {
  const sColor = officerStatusColor[officer.status];
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center gap-3 px-3.5 py-3 rounded-xl text-left transition-all"
      style={{
        background: isChecked ? "rgba(245,158,11,0.08)" : "var(--secondary)",
        border: `1px solid ${isChecked ? "rgba(245,158,11,0.3)" : "var(--border)"}`,
      }}
    >
      {/* Checkbox */}
      <div
        className="w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0 transition-all"
        style={{
          background: isChecked ? "#f59e0b" : "var(--card)",
          border: `2px solid ${isChecked ? "#f59e0b" : "var(--muted-foreground)"}`,
        }}
      >
        {isChecked && <Check size={11} color="#0c0f16" strokeWidth={2.5} />}
      </div>

      {/* Avatar */}
      <div
        className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
        style={{ background: `${sColor}22`, color: sColor }}
      >
        {officer.name.split(" ")[1]?.[0] ?? "O"}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium truncate" style={{ color: "var(--foreground)" }}>{officer.name}</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium flex-shrink-0"
            style={{ background: `${sColor}22`, color: sColor }}>
            {officer.status.replace("-", " ")}
          </span>
        </div>
        <div className="flex items-center gap-3 mt-0.5">
          <span className="text-[10px] flex items-center gap-1" style={{ color: "var(--muted-foreground)" }}>
            <MapPin size={9} /> {officer.location}
          </span>
          <span className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            {officer.badge}
          </span>
        </div>
      </div>

      {/* Workload */}
      <div className="text-right flex-shrink-0">
        <div className="text-sm font-bold leading-none" style={{ color: loadColor(count) }}>{count}</div>
        <div className="text-[10px] mt-0.5" style={{ color: loadColor(count), opacity: 0.7 }}>
          {loadLabel(count)}
        </div>
      </div>
    </button>
  );
}

export function DispatchModal({ alert, officers, alerts, onAssign, onClose }) {
  // Same icon/color source as ViolationModal (violationTypes.js) — see
  // violationDisplay's own doc comment for why this never shows a raw db
  // code even for curfew/waste/noise (out of violationTypes.js's scope).
  const vcfg = violationDisplay(alert.type);
  const VIcon = vcfg.icon;

  const [selected, setSelected] = useState(alert.officersAssignedIds ?? []);
  const [confirming, setConfirming] = useState(false);
  const [search, setSearch] = useState("");

  // Saving with zero officers selected is allowed — it clears any assignment
  // and returns the alert to "active". Editing an already-dispatched alert is
  // also a plain "Save" (no dispatch confirmation needed).
  const alreadyDispatched = alert.status === "dispatched";
  const isSave = selected.length === 0 || alreadyDispatched;

  const toggle = (id) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const assignmentCount = (officerId) =>
    alerts.filter(
      (a) =>
        a.officersAssignedIds.includes(officerId) &&
        a.id !== alert.id &&
        (a.status === "active" || a.status === "dispatched")
    ).length;

  const matchesSearch = (o) => {
    if (!search.trim()) return true;
    const q = search.trim().toLowerCase();
    return o.name.toLowerCase().includes(q)
      || o.location.toLowerCase().includes(q)
      || o.badge.toLowerCase().includes(q);
  };

  const available = officers.filter((o) => o.status !== "off-duty" && matchesSearch(o));
  const offline   = officers.filter((o) => o.status === "off-duty" && matchesSearch(o));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="w-full max-w-6xl rounded-2xl overflow-hidden shadow-2xl relative flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}
      >
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-start gap-3">
            <span className="mt-0.5"><VIcon size={20} style={{ color: vcfg.color }} /></span>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Assign Officers</div>
              <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                {vcfg.label} · {alert.cameraZone} · {alert.id}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg flex-shrink-0"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Tip */}
        <div className="px-6 pt-3 pb-1 flex-shrink-0">
          <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
            <AlertTriangle size={10} style={{ color: "#ef4444" }} />
            You can select multiple officers to respond to this violation.
          </div>
        </div>

        {/* Body — evidence left (60%), officer picker right (40%), matching
            the Violation modal's layout. The officer list is the primary
            task here, so it keeps its own scroll and stays reachable
            alongside the header/tip/footer rather than scrolling with them. */}
        <div className="grid grid-cols-[3fr_2fr] gap-5 px-6 py-4 flex-1 min-h-0">
          {/* Left: evidence clip + reference details */}
          <div className="flex flex-col gap-3 min-w-0 overflow-y-auto">
            <RecordingPlayer alert={alert} />
            <QuietCard label="Confidence" value={`${(alert.confidence * 100).toFixed(0)}%`} valueColor={vcfg.color} />
            <QuietCard label="What was detected" value={alert.description || "—"} />
          </div>

          {/* Right: search + officer list */}
          <div className="flex flex-col gap-2 min-w-0 min-h-0">
            <div className="text-[11px] font-semibold" style={{ color: "var(--muted-foreground)" }}>
              Available officers
            </div>
            <div className="relative flex-shrink-0">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search officers by name, zone or badge…"
                className="w-full pl-9 pr-3 py-2 rounded-xl text-[13px] outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
              />
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-0.5">
              {available.length === 0 && offline.length === 0 ? (
                <div className="py-8 text-center text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                  No officers match "{search}".
                </div>
              ) : (
                <>
                  {available.map((officer) => (
                    <OfficerRow
                      key={officer.id}
                      officer={officer}
                      count={assignmentCount(officer.id)}
                      isChecked={selected.includes(officer.id)}
                      onToggle={() => toggle(officer.id)}
                    />
                  ))}

                  {offline.length > 0 && (<>
                    <div className="text-[11px] font-semibold pt-2 pb-1" style={{ color: "var(--muted-foreground)" }}>Off duty</div>
                    {offline.map((officer) => (
                      <div key={officer.id} className="flex items-center gap-3 px-3.5 py-2.5 rounded-xl opacity-35"
                        style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                        <div className="w-5 h-5 rounded-md flex-shrink-0"
                          style={{ background: "transparent", border: "1px solid rgba(255,255,255,0.08)" }} />
                        <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                          style={{ background: "rgba(71,85,104,0.3)", color: "#64748b" }}>
                          {officer.name.split(" ")[1]?.[0] ?? "O"}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-[13px] font-medium truncate" style={{ color: "var(--foreground)" }}>{officer.name}</div>
                          <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Off duty · unavailable</div>
                        </div>
                      </div>
                    ))}
                  </>)}
                </>
              )}
            </div>
          </div>
        </div>

        {/* Confirmation overlay */}
        {confirming && (
          <div className="absolute inset-0 z-10 flex items-end rounded-2xl overflow-hidden"
            style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}>
            <div className="w-full px-5 py-5 space-y-3"
              style={{ background: "var(--card)", borderTop: "1px solid var(--border)" }}>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
                Confirm assignment
              </div>
              <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                Assign {selected.length} officer{selected.length !== 1 ? "s" : ""} to <span style={{ color: "var(--foreground)" }}>{alert.cameraZone}</span>?
                This will set the violation to <span style={{ color: "#3b82f6" }}>Assigned</span> and notify them on the app.
              </p>
              <div className="flex gap-2 justify-end">
                <button onClick={() => setConfirming(false)} className="px-4 py-2 rounded-xl text-sm font-medium"
                  style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                  Back
                </button>
                <button onClick={() => onAssign(selected)} className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium"
                  style={{ background: "rgba(245,158,11,0.2)", color: "#f59e0b" }}>
                  <Radio size={13} /> Confirm assignment
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-6 py-4 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Cancel
            </button>
          </div>

          <div className="flex items-center gap-3">
            {selected.length > 0 && (
              <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                {selected.length} officer{selected.length > 1 ? "s" : ""} selected
              </span>
            )}
            <button
              onClick={() => {
                if (isSave) onAssign(selected);
                else setConfirming(true);
              }}
              className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
              style={{
                background: "rgba(245,158,11,0.2)",
                color: "#f59e0b",
                cursor: "pointer",
              }}
            >
              {isSave ? <Check size={13} /> : <Radio size={13} />}
              {isSave
                ? "Save"
                : selected.length === 1
                ? "Assign officer"
                : `Assign ${selected.length} officers`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
