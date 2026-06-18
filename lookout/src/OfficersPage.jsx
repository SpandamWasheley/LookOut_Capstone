import { useState } from "react";
import { UserPlus, Radio, CheckCircle, Clock, X, Shield, MapPin, Edit2, Trash2 } from "lucide-react";

const initialOfficers = [
  { id: "OFC-01", name: "PO1 Reyes, Marco",   badge: "B-058", status: "on-duty",    location: "Zone 3", phone: "+63 934 882 1175", shift: "14:00–22:00", assignedAlerts: 1, joinedDate: "2023-01-15" },
  { id: "OFC-02", name: "PO2 Mangubat, Lisa",  badge: "B-091", status: "responding", location: "Zone 1", phone: "+63 917 555 6921", shift: "18:00–02:00", assignedAlerts: 2, joinedDate: "2022-04-11" },
  { id: "OFC-03", name: "PO3 Cabrera, Dante",  badge: "B-073", status: "on-duty",    location: "Zone 4", phone: "+63 927 221 4845", shift: "06:00–14:00", assignedAlerts: 0, joinedDate: "2021-10-02" },
  { id: "OFC-04", name: "PO2 Santos, Ben",     badge: "B-044", status: "off-duty",   location: "Zone 2", phone: "+63 918 876 3308", shift: "22:00–06:00", assignedAlerts: 0, joinedDate: "2024-02-20" },
];

const statusConfig = {
  "on-duty":    { label: "On duty",    color: "#10b981", bg: "rgba(16,185,129,0.1)",  icon: CheckCircle },
  "off-duty":   { label: "Off duty",   color: "#64748b", bg: "rgba(100,116,139,0.1)", icon: Clock },
  "responding": { label: "Responding", color: "#f59e0b", bg: "rgba(245,158,11,0.1)",  icon: Radio },
};

const emptyForm = { name: "", phone: "", shift: "06:00–14:00" };

function AddOfficerModal({ onAdd, onClose }) {
  const [form, setForm] = useState(emptyForm);
  const valid = form.name.trim() && form.phone.trim();
  const set = (k, v) => setForm((p) => ({ ...p, [k]: v }));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-sm rounded-2xl overflow-hidden shadow-2xl"
        style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
        <div className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold text-white">Add Officer</div>
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>Register a new field officer</div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          {[
            { label: "Full name", key: "name", placeholder: "e.g. PO1 Dela Cruz, Juan" },
            { label: "Phone",     key: "phone", placeholder: "+63 9XX XXX XXXX" },
          ].map(({ label, key, placeholder }) => (
            <div key={key}>
              <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>{label}</label>
              <input
                value={form[key]}
                onChange={(e) => set(key, e.target.value)}
                placeholder={placeholder}
                className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }}
              />
            </div>
          ))}

          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Shift hours</label>
            <select
              value={form.shift}
              onChange={(e) => set("shift", e.target.value)}
              className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9", colorScheme: "dark" }}
            >
              <option>06:00–14:00</option>
              <option>14:00–22:00</option>
              <option>18:00–02:00</option>
              <option>22:00–06:00</option>
            </select>
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 px-5 py-4"
          style={{ borderTop: "1px solid var(--border)" }}>
          <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
            Cancel
          </button>
          <button
            disabled={!valid}
            onClick={() => valid && onAdd(form)}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
            style={{
              background: valid ? "rgba(245,158,11,0.2)" : "rgba(245,158,11,0.05)",
              color: valid ? "#f59e0b" : "rgba(245,158,11,0.4)",
              cursor: valid ? "pointer" : "not-allowed",
            }}
          >
            <UserPlus size={13} /> Add officer
          </button>
        </div>
      </div>
    </div>
  );
}

export function OfficersPage() {
  const [officers, setOfficers] = useState(initialOfficers);
  const [showAdd, setShowAdd] = useState(false);
  const [filterStatus, setFilterStatus] = useState("all");
  const [deleteTarget, setDeleteTarget] = useState(null);

  const filtered = officers.filter((o) => filterStatus === "all" || o.status === filterStatus);

  const handleAdd = (form) => {
    const newOfficer = {
      id: `OFC-${String(officers.length + 1).padStart(2, "0")}`,
      name: form.name,
      badge: `B-${String(Math.floor(Math.random() * 900) + 100)}`,
      phone: form.phone,
      shift: form.shift,
      location: "Zone 1",
      status: "on-duty",
      assignedAlerts: 0,
      joinedDate: new Date().toISOString().slice(0, 10),
    };
    setOfficers((prev) => [...prev, newOfficer]);
    setShowAdd(false);
  };

  const handleDelete = (id) => {
    setOfficers((prev) => prev.filter((o) => o.id !== id));
    setDeleteTarget(null);
  };

  const toggleStatus = (id) => {
    setOfficers((prev) => prev.map((o) => {
      if (o.id !== id) return o;
      const next = o.status === "off-duty" ? "on-duty" : "off-duty";
      return { ...o, status: next };
    }));
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <h1 className="text-[15px] font-semibold text-white">Officers</h1>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
            {officers.filter((o) => o.status !== "off-duty").length} on duty · {officers.length} total
          </span>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
          style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}
        >
          <UserPlus size={12} /> Add officer
        </button>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col px-6 py-5 gap-4">
        {/* KPIs */}
        <div className="grid grid-cols-4 gap-3 flex-shrink-0">
          {[
            { label: "Total",      value: officers.length,                                        color: "#f59e0b" },
            { label: "On Duty",    value: officers.filter((o) => o.status === "on-duty").length,    color: "#10b981" },
            { label: "Responding", value: officers.filter((o) => o.status === "responding").length, color: "#f59e0b" },
            { label: "Off Duty",   value: officers.filter((o) => o.status === "off-duty").length,   color: "#64748b" },
          ].map((s) => (
            <div key={s.label} className="rounded-xl p-4 text-center"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              <div className="text-2xl font-semibold leading-none" style={{ color: s.color }}>{s.value}</div>
              <div className="text-[11px] mt-1.5" style={{ color: "var(--muted-foreground)" }}>{s.label}</div>
            </div>
          ))}
        </div>

        {/* Filter tabs */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {["all", "on-duty", "responding", "off-duty"].map((s) => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className="px-3 py-1 text-xs font-medium rounded-full transition-all capitalize"
              style={{
                background: filterStatus === s ? (s === "all" ? "#f59e0b" : statusConfig[s]?.bg ?? "rgba(245,158,11,0.1)") : "var(--secondary)",
                color:      filterStatus === s ? (s === "all" ? "#0c0f16" : statusConfig[s]?.color ?? "#f59e0b") : "var(--muted-foreground)",
                border:     `1px solid ${filterStatus === s ? (s === "all" ? "#f59e0b" : (statusConfig[s]?.color ?? "#f59e0b") + "40") : "var(--border)"}`,
              }}
            >
              {s === "all" ? "All" : s.replace("-", " ")}
            </button>
          ))}
        </div>

        {/* Table */}
        <div className="flex-1 overflow-y-auto rounded-xl" style={{ border: "1px solid var(--border)" }}>
          {/* Header */}
          <div
            className="grid px-4 py-2.5 text-[11px] font-semibold sticky top-0"
            style={{
              gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr 0.5fr",
              color: "var(--muted-foreground)",
              background: "var(--card)",
              borderBottom: "1px solid var(--border)",
            }}
          >
            <span>Officer</span>
            <span>Status</span>
            <span>Location</span>
            <span>Shift</span>
            <span>Assignments</span>
            <span />
          </div>

          {filtered.map((officer, idx) => {
            const scfg = statusConfig[officer.status];
            const StatusIcon = scfg.icon;
            return (
              <div
                key={officer.id}
                className="grid px-4 py-3 items-center transition-colors hover:bg-white/[0.02] group"
                style={{
                  gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr 0.5fr",
                  borderBottom: idx < filtered.length - 1 ? "1px solid var(--border)" : "none",
                  background: "var(--card)",
                }}
              >
                {/* Name + badge */}
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                    style={{ background: `${scfg.color}22`, color: scfg.color }}>
                    {officer.name.split(" ")[1]?.[0] ?? "O"}
                  </div>
                  <div>
                    <div className="text-[13px] font-medium text-white">{officer.name}</div>
                    <div className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                      {officer.badge} · {officer.phone}
                    </div>
                  </div>
                </div>

                {/* Status */}
                <button
                  onClick={() => toggleStatus(officer.id)}
                  className="flex items-center gap-1.5 px-2 py-1 rounded-md w-fit text-[11px] font-medium transition-all"
                  style={{ background: scfg.bg, color: scfg.color }}
                  title="Click to toggle on-duty / off-duty"
                >
                  <StatusIcon size={10} /> {scfg.label}
                </button>

                {/* Location */}
                <div className="text-[12px] flex items-center gap-1" style={{ color: "#cbd5e1" }}>
                  <MapPin size={10} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                  {officer.location}
                </div>

                {/* Shift */}
                <div className="text-[11px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                  {officer.shift}
                </div>

                {/* Assignments */}
                <div>
                  <span className="text-sm font-semibold" style={{
                    color: officer.assignedAlerts === 0 ? "#10b981" : officer.assignedAlerts === 1 ? "#f59e0b" : "#ef4444",
                  }}>
                    {officer.assignedAlerts}
                  </span>
                  <span className="text-[11px] ml-1" style={{ color: "var(--muted-foreground)" }}>
                    {officer.assignedAlerts === 1 ? "case" : "cases"}
                  </span>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1 justify-end">
                  <button className="p-1.5 rounded-lg transition-all" style={{ color: "var(--muted-foreground)" }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = "#f1f5f9"; e.currentTarget.style.background = "var(--secondary)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}>
                    <Edit2 size={13} />
                  </button>
                  <button onClick={() => setDeleteTarget(officer.id)}
                    className="p-1.5 rounded-lg transition-all" style={{ color: "var(--muted-foreground)" }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = "#ef4444"; e.currentTarget.style.background = "rgba(239,68,68,0.08)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}>
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            );
          })}

          {filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 gap-2"
              style={{ background: "var(--card)" }}>
              <Shield size={28} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium text-white">No officers found</div>
            </div>
          )}
        </div>
      </div>

      {/* Confirm delete */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(6px)" }}>
          <div className="w-full max-w-xs rounded-2xl overflow-hidden"
            style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
            <div className="px-5 py-5 text-center">
              <div className="w-10 h-10 rounded-full flex items-center justify-center mx-auto mb-3"
                style={{ background: "rgba(239,68,68,0.1)" }}>
                <Trash2 size={18} style={{ color: "#ef4444" }} />
              </div>
              <div className="text-sm font-semibold text-white">Remove officer?</div>
              <p className="text-[12px] mt-1" style={{ color: "var(--muted-foreground)" }}>
                {officers.find((o) => o.id === deleteTarget)?.name} will be removed from the roster.
              </p>
            </div>
            <div className="flex gap-2 px-5 pb-5">
              <button onClick={() => setDeleteTarget(null)}
                className="flex-1 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
              <button onClick={() => handleDelete(deleteTarget)}
                className="flex-1 py-2 rounded-xl text-sm font-medium"
                style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.25)" }}>
                Remove
              </button>
            </div>
          </div>
        </div>
      )}

      {showAdd && <AddOfficerModal onAdd={handleAdd} onClose={() => setShowAdd(false)} />}
    </div>
  );
}