import { useEffect, useState } from "react";
import {
  Search, UserPlus, CheckCircle, Clock, AlertTriangle,
  User, ScanFace, Loader2, Trash2,
} from "lucide-react";
import { getPersons, deletePerson } from "./api";
import { EnrollModal } from "./EnrollModal";

function mapPerson(raw) {
  return {
    id: raw.person_code,
    dbId: raw.id,
    name: raw.full_name,
    status: raw.status,
    enrolledAt: raw.enrolled_at,
    embeddings: raw.embeddings ?? [],
  };
}

const statusConfig = {
  enrolled: { label: "Enrolled", color: "#10b981", bg: "rgba(16,185,129,0.1)", icon: CheckCircle },
  pending:  { label: "Pending",  color: "#f59e0b", bg: "rgba(245,158,11,0.1)", icon: Clock },
};

const ANGLES = ["front", "right", "left"];

function angleLabel(angle) {
  return angle === "front" ? "Front" : angle === "right" ? "Right" : "Left";
}

// ── Remove confirmation ─────────────────────────────────────────────────────────
function RemovePersonModal({ person, onConfirm, onClose }) {
  const [removing, setRemoving] = useState(false);

  const confirm = async () => {
    setRemoving(true);
    try {
      await onConfirm();
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget && !removing) onClose(); }}
    >
      <div className="w-full max-w-sm rounded-2xl overflow-hidden shadow-2xl" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
        <div className="px-5 pt-5 pb-4 flex flex-col items-center text-center">
          <div className="w-11 h-11 rounded-full flex items-center justify-center mb-3" style={{ background: "rgba(239,68,68,0.12)" }}>
            <Trash2 size={18} style={{ color: "#ef4444" }} />
          </div>
          <div className="text-sm font-semibold mb-1" style={{ color: "var(--foreground)" }}>Remove from registry?</div>
          <div className="text-[12px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
            Are you sure you want to delete{" "}
            <span className="font-semibold" style={{ color: "var(--foreground)" }}>{person.name}</span>{" "}
            and their enrolled face photos? This cannot be undone.
          </div>
        </div>
        <div className="flex items-center gap-2 px-5 pb-5">
          <button
            onClick={onClose}
            disabled={removing}
            className="flex-1 px-4 py-2 rounded-xl text-sm font-medium"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)", cursor: removing ? "not-allowed" : "pointer" }}
          >
            Cancel
          </button>
          <button
            onClick={confirm}
            disabled={removing}
            className="flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all"
            style={{ background: "#ef4444", color: "#fff", cursor: removing ? "not-allowed" : "pointer" }}
          >
            {removing ? <><Loader2 size={13} className="animate-spin" /> Removing…</> : <><Trash2 size={13} /> Remove</>}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Person card ──────────────────────────────────────────────────────────────
function PersonCard({ person, onEnroll, onRemove }) {
  const st = statusConfig[person.status] ?? statusConfig.pending;
  const StatusIcon = st.icon;
  const embeddingByAngle = Object.fromEntries(person.embeddings.map((e) => [e.angle, e]));

  return (
    <div className="rounded-xl p-4 flex flex-col gap-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold truncate" style={{ color: "var(--foreground)" }}>{person.name}</div>
          <div className="text-[11px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>{person.id}</div>
        </div>
        <span className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium flex-shrink-0" style={{ background: st.bg, color: st.color }}>
          <StatusIcon size={10} /> {st.label}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {ANGLES.map((angle) => {
          const embedding = embeddingByAngle[angle];
          return (
            <div key={angle} className="flex flex-col gap-1">
              <div className="rounded-lg overflow-hidden flex items-center justify-center" style={{ aspectRatio: "3/4", background: "var(--secondary)", border: "1px solid var(--border)" }}>
                {embedding
                  ? <img src={embedding.image} alt={`${person.name} ${angle}`} className="w-full h-full object-cover" />
                  : <ScanFace size={16} style={{ color: "var(--muted-foreground)", opacity: 0.35 }} />
                }
              </div>
              <div className="text-[9px] text-center uppercase tracking-wide" style={{ color: "var(--muted-foreground)" }}>{angleLabel(angle)}</div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2 pt-1">
        <button
          onClick={onEnroll}
          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium transition-all"
          style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
        >
          <ScanFace size={11} /> Enroll faces
        </button>
        <button
          onClick={onRemove}
          className="flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium transition-all"
          style={{ background: "rgba(239,68,68,0.1)", color: "#ef4444" }}
        >
          <Trash2 size={11} /> Remove
        </button>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export function ResidentDatabase() {
  const [persons,     setPersons]     = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [loadError,   setLoadError]   = useState("");
  const [search,      setSearch]      = useState("");
  const [filter,      setFilter]      = useState("all");
  const [enrollTarget, setEnrollTarget] = useState(undefined); // undefined = closed, null = "add person", person = "enroll faces"
  const [removeTarget, setRemoveTarget] = useState(null);

  const refreshPersons = async () => {
    const data = await getPersons();
    setPersons((data.results ?? data).map(mapPerson));
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError("");
      try {
        await refreshPersons();
      } catch (err) {
        if (!cancelled) setLoadError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const registeredCount = persons.length;
  const enrolledCount = persons.filter((p) => p.embeddings.length === 3).length;
  const pendingCount = persons.filter((p) => p.embeddings.length === 0).length;

  const filteredPersons = persons.filter((p) => {
    const matchSearch = !search ||
      p.name.toLowerCase().includes(search.toLowerCase()) ||
      p.id.toLowerCase().includes(search.toLowerCase());
    const matchFilter = filter === "all" ? true : filter === "enrolled" ? p.status === "enrolled" : p.status === "pending";
    return matchSearch && matchFilter;
  });

  const handleRemove = async (person) => {
    try {
      await deletePerson(person.dbId);
      await refreshPersons();
      setRemoveTarget(null);
    } catch (err) {
      alert(`Failed to remove person: ${err.message}`);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Face Registry</h1>
          </div>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
            ArcFace biometrics ·{" "}
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{ background: "rgba(16,185,129,0.1)", color: "#10b981" }}>
              RA 10173 compliant
            </span>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setEnrollTarget(null)}
            className="flex items-center gap-2.5 px-4 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}>
            <UserPlus size={12} /> Add person
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col px-6 py-4 gap-4">
        {/* Stats */}
        <div className="grid grid-cols-3 gap-3 flex-shrink-0">
          {[
            { label: "Registered", value: registeredCount, color: "var(--muted-foreground)" },
            { label: "Enrolled",   value: enrolledCount,    color: "#10b981" },
            { label: "Pending",    value: pendingCount,     color: "#f59e0b" },
          ].map((s) => (
            <div key={s.label} className="rounded-xl p-4 text-center" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
              <div className="text-2xl font-semibold leading-none" style={{ color: s.color }}>{s.value}</div>
              <div className="text-[11px] mt-1.5" style={{ color: "var(--muted-foreground)" }}>{s.label}</div>
            </div>
          ))}
        </div>

        {/* Controls */}
        <div className="flex items-center gap-3 flex-shrink-0">
          <div className="flex-1 relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or person ID…"
              className="w-full pl-9 pr-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
          </div>
          <div className="flex gap-1">
            {["all", "enrolled", "pending"].map((f) => (
              <button key={f} onClick={() => setFilter(f)}
                className="px-3 py-2 text-xs font-medium rounded-lg transition-all capitalize"
                style={{
                  background: filter === f ? "var(--primary)" : "var(--secondary)",
                  color: filter === f ? "var(--primary-foreground)" : "var(--muted-foreground)",
                  border: `1px solid ${filter === f ? "var(--primary)" : "var(--border)"}`,
                }}>
                {f === "all" ? "All" : f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2">
              <Loader2 size={24} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Loading registry…</div>
            </div>
          ) : loadError ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2">
              <AlertTriangle size={24} style={{ color: "#ef4444" }} />
              <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Failed to load data</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
            </div>
          ) : filteredPersons.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2">
              <User size={28} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>No one in the registry yet</div>
            </div>
          ) : (
            <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
              {filteredPersons.map((p) => (
                <PersonCard
                  key={p.id}
                  person={p}
                  onEnroll={() => setEnrollTarget(p)}
                  onRemove={() => setRemoveTarget(p)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {enrollTarget !== undefined && (
        <EnrollModal
          person={enrollTarget ? { dbId: enrollTarget.dbId, id: enrollTarget.id, full_name: enrollTarget.name } : null}
          onClose={() => setEnrollTarget(undefined)}
          onEnrolled={refreshPersons}
        />
      )}
      {removeTarget && (
        <RemovePersonModal
          person={removeTarget}
          onConfirm={() => handleRemove(removeTarget)}
          onClose={() => setRemoveTarget(null)}
        />
      )}
    </div>
  );
}
