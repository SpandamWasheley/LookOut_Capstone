import { useState } from "react";
import { CheckCircle, X, MapPin, Clock, Shield, Search, FileText } from "lucide-react";
import { mockAlerts, VIOLATION_CONFIG } from "../data/mockData";
import { ViolationModal } from "./ViolationModal";

const outcomeConfig = {
  resolved:     { label: "Resolved",  color: "#10b981", bg: "rgba(16,185,129,0.1)",  icon: CheckCircle },
  acknowledged: { label: "Dismissed", color: "#64748b", bg: "rgba(100,116,139,0.1)", icon: X },
};

function formatFull(ts) {
  return new Date(ts).toLocaleString("en-PH", {
    month: "short", day: "numeric", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

const finishedAlerts = [
  ...mockAlerts.filter((a) => a.status === "acknowledged" || a.status === "resolved"),
  {
    id: "ALT-0035", type: "curfew", status: "resolved",
    camera: "CAM-01", cameraZone: "Tetuan Market Entrance",
    timestamp: "2025-06-11T23:10:00",
    confidence: 0.88, description: "Minor detained and escorted home by officer.",
    imageUrl: "https://images.unsplash.com/photo-1549402098-f4d9b419c5f8?w=400&h=300&fit=crop&auto=format",
    officerAssigned: "PO2 Mangubat, Lisa", suspect: "Reyes, Kristine Joy",
  },
  {
    id: "ALT-0034", type: "noise", status: "acknowledged",
    camera: "CAM-02", cameraZone: "Barangay Hall Plaza",
    timestamp: "2025-06-11T21:45:00",
    confidence: 0.74, description: "Noise complaint verified but source already dispersed.",
    imageUrl: "https://images.unsplash.com/photo-1512966885769-8207b47677df?w=400&h=300&fit=crop&auto=format",
    officerAssigned: "PO3 Cabrera, Dante",
  },
  {
    id: "ALT-0033", type: "waste", status: "acknowledged",
    camera: "CAM-04", cameraZone: "Purok 6 Alley",
    timestamp: "2025-06-11T18:30:00",
    confidence: 0.65, description: "Alert dismissed — confirmed not a gathering.",
    imageUrl: "https://images.unsplash.com/photo-1470420084874-431eb0a8d5b1?w=400&h=300&fit=crop&auto=format",
    notes: "Confirmed not a gathering.",
  },
  {
    id: "ALT-0032", type: "accident", status: "resolved",
    camera: "CAM-03", cameraZone: "R.T. Lim Blvd. Junction",
    timestamp: "2025-06-11T16:00:00",
    confidence: 0.91, description: "Traffic accident cleared. Vehicles towed.",
    imageUrl: "https://images.unsplash.com/photo-1525609004556-c46c7d6cf023?w=400&h=300&fit=crop&auto=format",
    officerAssigned: "PO1 Reyes, Marco",
  },
  {
    id: "ALT-0031", type: "indecency", status: "acknowledged",
    camera: "CAM-01", cameraZone: "Tetuan Market Entrance",
    timestamp: "2025-06-10T20:15:00",
    confidence: 0.60, description: "False positive — vendor unloading goods.",
    imageUrl: "https://images.unsplash.com/photo-1492724441997-5dc865305da7?w=400&h=300&fit=crop&auto=format",
    notes: "False positive confirmed.",
  },
  {
    id: "ALT-0030", type: "intrusion", status: "resolved",
    camera: "CAM-04", cameraZone: "Purok 6 Alley",
    timestamp: "2025-06-10T02:00:00",
    confidence: 0.95, description: "Unauthorized entry resolved. Suspect identified.",
    imageUrl: "https://images.unsplash.com/photo-1515601914948-8493e1a7cf6d?w=400&h=300&fit=crop&auto=format",
  },
];

export function RecordsPage() {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);

  const records = finishedAlerts.filter((a) => {
    const matchFilter = filter === "all" || a.status === filter;
    const matchSearch =
      !search ||
      VIOLATION_CONFIG[a.type].label.toLowerCase().includes(search.toLowerCase()) ||
      a.cameraZone.toLowerCase().includes(search.toLowerCase()) ||
      a.id.toLowerCase().includes(search.toLowerCase()) ||
      (a.officerAssigned ?? "").toLowerCase().includes(search.toLowerCase());
    return matchFilter && matchSearch;
  });

  const resolvedCount  = finishedAlerts.filter((a) => a.status === "resolved").length;
  const dismissedCount = finishedAlerts.filter((a) => a.status === "acknowledged").length;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <h1 className="text-[15px] font-semibold text-white">Records</h1>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>Completed violations archive</span>
        </div>
        <div className="flex items-center gap-4 text-xs" style={{ color: "var(--muted-foreground)" }}>
          <span style={{ color: "#10b981" }}>{resolvedCount} resolved</span>
          <span style={{ color: "#64748b" }}>{dismissedCount} dismissed</span>
        </div>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col px-6 py-5 gap-4">
        {/* KPIs */}
        <div className="grid grid-cols-3 gap-3 flex-shrink-0">
          {[
            { label: "Total Records", value: finishedAlerts.length, color: "#f59e0b" },
            { label: "Resolved",      value: resolvedCount,         color: "#10b981" },
            { label: "Dismissed",     value: dismissedCount,        color: "#64748b" },
          ].map((s) => (
            <div key={s.label} className="rounded-xl p-4 text-center"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              <div className="text-2xl font-semibold leading-none" style={{ color: s.color }}>{s.value}</div>
              <div className="text-[11px] mt-1.5" style={{ color: "var(--muted-foreground)" }}>{s.label}</div>
            </div>
          ))}
        </div>

        {/* Controls */}
        <div className="flex items-center gap-3 flex-shrink-0">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2"
              style={{ color: "var(--muted-foreground)" }} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by type, zone, officer, or ID…"
              className="w-full pl-9 pr-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }}
            />
          </div>
          <div className="flex gap-1.5">
            {["all", "resolved", "acknowledged"].map((f) => {
              const label = f === "all" ? "All" : f === "resolved" ? "Resolved" : "Dismissed";
              const color = f === "resolved" ? "#10b981" : f === "acknowledged" ? "#64748b" : "#0c0f16";
              const bg    = f === "resolved" ? "rgba(16,185,129,0.1)" : f === "acknowledged" ? "rgba(100,116,139,0.1)" : "#f59e0b";
              return (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className="px-3 py-1.5 text-xs font-medium rounded-full transition-all"
                  style={{
                    background: filter === f ? bg : "var(--secondary)",
                    color:      filter === f ? color : "var(--muted-foreground)",
                    border:     `1px solid ${filter === f ? (f === "all" ? "#f59e0b" : color + "40") : "var(--border)"}`,
                  }}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Table */}
        <div className="flex-1 overflow-y-auto rounded-xl" style={{ border: "1px solid var(--border)" }}>
          {/* Header */}
          <div
            className="grid px-4 py-2.5 text-[11px] font-semibold sticky top-0"
            style={{
              gridTemplateColumns: "2fr 2fr 1.5fr 1fr 0.5fr",
              color: "var(--muted-foreground)",
              background: "var(--card)",
              borderBottom: "1px solid var(--border)",
              zIndex: 1,
            }}
          >
            <span>Violation</span>
            <span>Zone · Timestamp</span>
            <span>Officer</span>
            <span>Outcome</span>
            <span />
          </div>

          {records.map((alert, idx) => {
            const vcfg = VIOLATION_CONFIG[alert.type];
            const oc = outcomeConfig[alert.status] ?? outcomeConfig["acknowledged"];
            const OcIcon = oc.icon;
            return (
              <div
                key={alert.id}
                onClick={() => setSelected(alert)}
                className="grid px-4 py-3 items-center cursor-pointer transition-colors group hover:bg-white/[0.02]"
                style={{
                  gridTemplateColumns: "2fr 2fr 1.5fr 1fr 0.5fr",
                  borderBottom: idx < records.length - 1 ? "1px solid var(--border)" : "none",
                  background: "var(--card)",
                }}
              >
                {/* Type */}
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
                    style={{ background: vcfg.bgColor ?? `${vcfg.color}22` }}>
                    <vcfg.icon size={14} color={vcfg.color} />
                  </div>
                  <div>
                    <div className="text-[12px] font-medium text-white leading-none">{vcfg.label}</div>
                    <div className="text-[10px] mt-0.5"
                      style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>{alert.id}</div>
                  </div>
                </div>

                {/* Zone + time */}
                <div>
                  <div className="text-[12px] text-white flex items-center gap-1">
                    <MapPin size={9} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                    {alert.cameraZone}
                  </div>
                  <div className="text-[10px] mt-0.5 flex items-center gap-1"
                    style={{ color: "var(--muted-foreground)" }}>
                    <Clock size={9} /> {formatFull(alert.timestamp)}
                  </div>
                </div>

                {/* Officer */}
                <div className="text-[12px] flex items-center gap-1.5" style={{ color: "#cbd5e1" }}>
                  {alert.officerAssigned
                    ? <><Shield size={10} style={{ color: "#3b82f6", flexShrink: 0 }} />{alert.officerAssigned.split(",")[0]}</>
                    : <span style={{ color: "var(--muted-foreground)" }}>—</span>
                  }
                </div>

                {/* Outcome */}
                <div>
                  <span className="flex items-center gap-1.5 w-fit text-[11px] font-medium px-2 py-1 rounded-md"
                    style={{ background: oc.bg, color: oc.color }}>
                    <OcIcon size={10} /> {oc.label}
                  </span>
                </div>

                {/* View */}
                <div className="flex justify-end">
                  <span className="text-[11px] opacity-0 group-hover:opacity-100 flex items-center gap-1 px-2 py-1 rounded-md"
                    style={{ color: "#f59e0b", background: "rgba(245,158,11,0.08)" }}>
                    <FileText size={10} /> View
                  </span>
                </div>
              </div>
            );
          })}

          {records.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 gap-2"
              style={{ background: "var(--card)" }}>
              <FileText size={28} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium text-white">No records found</div>
              <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>Try adjusting the filter or search</div>
            </div>
          )}
        </div>
      </div>

      {selected && (
        <ViolationModal
          alert={selected}
          assignedOfficerNames={selected.officerAssigned ? [selected.officerAssigned] : []}
          onDismiss={() => {}}
          onDispatch={() => {}}
          onResolve={() => {}}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}