import { useState, useEffect } from "react";
import {
  Search, AlertTriangle, CheckCircle,
  User, Play, X,
} from "lucide-react";
import { getResidents, getAlerts, getHouseholds } from "./api";
import { VIOLATION_CONFIG } from "../data/mockData";

// ── Helpers ───────────────────────────────────────────────────────────────────
function computeAge(birthdate) {
  if (!birthdate) return null;
  const today = new Date();
  const dob = new Date(birthdate);
  let age = today.getFullYear() - dob.getFullYear();
  const m = today.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
  return age;
}

function fmtDate(ts) {
  return new Date(ts).toLocaleDateString("en-PH", { month: "short", day: "numeric", year: "numeric" });
}

function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function matchesSuspect(suspect, name) {
  if (!suspect || !name) return false;
  const s = suspect.toLowerCase();
  const parts = name.toLowerCase().split(/[\s,]+/).filter((p) => p.length > 2);
  return parts.some((p) => s.includes(p));
}

function getViolationsFor(person, alerts) {
  return alerts.filter((a) => matchesSuspect(a.suspect, person.name));
}

// Build flat list from households + individual residents, deduped by barangayId
function buildTrackedPersons(households, residents) {
  const seen = new Set();
  const people = [];

  // From households
  for (const hh of households) {
    for (const m of hh.members) {
      if (!m.barangayId) continue;
      seen.add(m.barangayId);
      const age = computeAge(m.birthdate);
      people.push({
        id: m.id,
        name: `${m.lastName}, ${m.firstName}`,
        age,
        isMinor: age != null && age < 18,
        householdName: `${hh.familyName} household`,
        barangayId: m.barangayId,
        status: m.status ?? "pending",
        imageUrl: m.imageUrl,
      });
    }
  }

  // Individual residents not already covered by a household
  for (const r of residents) {
    if (r.barangayId && seen.has(r.barangayId)) continue;
    people.push({
      id: r.id ?? r.code,
      name: r.name,
      age: r.age,
      isMinor: r.age != null && r.age < 18,
      householdName: r.guardianName ? `${r.guardianName} household` : null,
      barangayId: r.barangayId ?? r.id,
      status: r.status ?? "pending",
      imageUrl: r.imageUrl,
    });
  }

  return people;
}

const STATUS_COLOR = {
  verified: "#34a86b",
  pending:  "#e0972a",
  flagged:  "#ef4e32",
  active:   "#ef4e32",
};

// ── Avatar ────────────────────────────────────────────────────────────────────
function Avatar({ src, name, size = 36, isMinor, status, rounded = "xl" }) {
  const initials = name ? name[0].toUpperCase() : "?";
  const borderColor = `${STATUS_COLOR[status] ?? "#6e7a78"}40`;
  const cls = rounded === "full" ? "rounded-full" : "rounded-xl";

  return (
    <div className="relative flex-shrink-0" style={{ width: size, height: size }}>
      {src ? (
        <img
          src={src} alt={name}
          className={`w-full h-full object-cover ${cls}`}
          style={{ border: `1.5px solid ${borderColor}` }}
          onError={(e) => {
            e.currentTarget.style.display = "none";
            e.currentTarget.nextSibling.style.display = "flex";
          }}
        />
      ) : null}
      <div
        className={`w-full h-full flex items-center justify-center text-xs font-bold ${cls}`}
        style={{
          display: src ? "none" : "flex",
          background: "var(--secondary)",
          color: "var(--muted-foreground)",
          border: `1.5px solid ${borderColor}`,
        }}
      >
        {initials}
      </div>
      {isMinor && (
        <span
          className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full text-[7px] font-bold flex items-center justify-center"
          style={{ background: "#e0972a", color: "#fff" }}
        >
          M
        </span>
      )}
    </div>
  );
}

// ── Person row ────────────────────────────────────────────────────────────────
function PersonRow({ person, violations, onSelect, selected }) {
  const count = violations.length;
  const latest = [...violations].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))[0];
  const countColor = count === 0 ? "#34a86b" : count === 1 ? "#e0972a" : "#ef4e32";
  const countBg    = count === 0 ? "rgba(52,168,107,.10)" : count === 1 ? "rgba(224,151,42,.12)" : "rgba(239,78,50,.12)";

  const latestTypeCode = latest ? (typeof latest.type === "object" ? latest.type?.code : latest.type) : null;
  const latestVcfg = latestTypeCode ? (VIOLATION_CONFIG[latestTypeCode] ?? null) : null;
  const LatestIcon = latestVcfg?.icon;

  return (
    <div
      onClick={() => onSelect(person)}
      className="flex items-center gap-4 px-4 py-3 cursor-pointer transition-all rounded-xl"
      style={{
        background: selected ? "rgba(14,124,134,0.07)" : "var(--card)",
        border: `1px solid ${selected ? "rgba(14,124,134,0.3)" : "var(--border)"}`,
      }}
    >
      <Avatar src={person.imageUrl} name={person.name} size={36} isMinor={person.isMinor} status={person.status} />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13px] font-medium" style={{ color: "var(--foreground)" }}>{person.name}</span>
          {person.isMinor && (
            <span className="text-[10px] px-1.5 py-0.5 rounded font-medium"
              style={{ background: "rgba(224,151,42,0.10)", color: "#e0972a" }}>
              Minor
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 mt-0.5 text-[10px]" style={{ color: "var(--muted-foreground)" }}>
          <span style={{ fontFamily: "'DM Mono', monospace" }}>{person.barangayId}</span>
          {person.age != null && <><span>·</span><span>Age {person.age}</span></>}
          {person.householdName && <><span>·</span><span className="truncate">{person.householdName}</span></>}
        </div>
      </div>

      <div className="flex-shrink-0 text-right min-w-0 max-w-[160px]">
        {latestVcfg ? (
          <>
            <div className="flex items-center justify-end gap-1 text-[11px] font-medium"
              style={{ color: "var(--foreground)" }}>
              {LatestIcon && <LatestIcon size={11} style={{ color: latestVcfg.color, flexShrink: 0 }} />}
              <span className="truncate">{latestVcfg.label}</span>
            </div>
            <div className="text-[10px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
              {fmtDate(latest.timestamp)}
            </div>
          </>
        ) : (
          <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>No record</div>
        )}
      </div>

      <div
        className="w-9 h-9 rounded-xl flex items-center justify-center text-sm font-bold flex-shrink-0"
        style={{ background: countBg, color: countColor }}
      >
        {count}
      </div>
    </div>
  );
}

// ── Violation clip (simulated CCTV player over the evidence still) ─────────────
function ClipPlayer({ src, camera, timestamp }) {
  const [playing, setPlaying] = useState(false);

  if (!src) {
    return (
      <div className="flex flex-col items-center justify-center gap-1 py-8"
        style={{ borderTop: "1px solid var(--border)", background: "#000" }}>
        <span className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
          No clip available
        </span>
      </div>
    );
  }

  return (
    <div className="relative w-full aspect-video bg-black overflow-hidden"
      style={{ borderTop: "1px solid var(--border)" }}>
      <img src={src} alt="Violation clip"
        className="absolute inset-0 w-full h-full object-cover transition-all duration-300"
        style={{ opacity: playing ? 0.85 : 0.55, filter: playing ? "none" : "grayscale(25%)" }} />
      {/* scanlines */}
      <div className="absolute inset-0 pointer-events-none" style={{
        backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.06) 3px, rgba(0,0,0,0.06) 4px)",
      }} />
      {/* top overlay */}
      <div className="absolute top-0 left-0 right-0 px-2 py-1.5 flex items-center justify-between"
        style={{ background: "linear-gradient(to bottom, rgba(0,0,0,0.72), transparent)" }}>
        <span className="text-[9px] font-medium px-1.5 py-0.5 rounded"
          style={{ background: "rgba(239,68,68,0.85)", color: "#fff", fontFamily: "'DM Mono', monospace" }}>
          ● REC
        </span>
        {camera && (
          <span className="text-[9px]" style={{ color: "rgba(255,255,255,0.75)", fontFamily: "'DM Mono', monospace" }}>
            {camera}
          </span>
        )}
      </div>
      {/* timestamp */}
      <span className="absolute bottom-1.5 left-2 text-[9px]"
        style={{ color: "rgba(255,255,255,0.75)", fontFamily: "'DM Mono', monospace" }}>
        {fmtDate(timestamp)} {fmtTime(timestamp)}
      </span>
      {/* play / pause */}
      <button onClick={() => setPlaying((p) => !p)}
        className="absolute inset-0 flex items-center justify-center group">
        {!playing && (
          <div className="w-11 h-11 rounded-full flex items-center justify-center transition-all group-hover:scale-110"
            style={{ background: "rgba(245,158,11,0.9)", boxShadow: "0 0 22px rgba(245,158,11,0.35)" }}>
            <Play size={18} color="#0c0f16" fill="#0c0f16" style={{ marginLeft: 2 }} />
          </div>
        )}
      </button>
    </div>
  );
}

// ── Full violation history modal (opened from "View all") ─────────────────────
const CLIP_RETENTION_DAYS = 30;

function ViolationHistoryModal({ person, violations, onClose }) {
  const [openClipId, setOpenClipId] = useState(null);
  const sorted = [...violations].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  const [now] = useState(() => Date.now());
  const isExpired = (ts) => (now - new Date(ts).getTime()) > CLIP_RETENTION_DAYS * 86400000;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "85vh" }}>
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
              Violation History — {person.name}
            </div>
            <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
              {violations.length} record{violations.length !== 1 ? "s" : ""} · Clips retained for {CLIP_RETENTION_DAYS} days
            </div>
          </div>
          <button onClick={onClose}
            className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto scrollbar-visible">
          {sorted.map((v) => {
            const code = typeof v.type === "object" ? v.type?.code : v.type;
            const vcfg = VIOLATION_CONFIG[code] ?? { label: code ?? "Violation", color: "#6e7a78", icon: AlertTriangle };
            const vid = v.code ?? v.id;
            const expired = isExpired(v.timestamp);
            const isOpen = openClipId === vid;
            const cameraName = v.camera
              ? (typeof v.camera === "object" ? v.camera.name : v.camera)
              : v.cameraZone ?? null;
            return (
              <div key={vid} style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="flex items-center gap-3 px-5 py-3">
                  {/* Type-colored initial */}
                  <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold flex-shrink-0"
                    style={{ background: `${vcfg.color}22`, color: vcfg.color }}>
                    {vcfg.label[0]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-semibold truncate" style={{ color: "var(--foreground)" }}>{vcfg.label}</div>
                    <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                      {fmtDate(v.timestamp)} · {fmtTime(v.timestamp)}
                    </div>
                  </div>
                  {expired ? (
                    <span className="text-[11px] flex-shrink-0" style={{ color: "var(--muted-foreground)" }}>Clip expired</span>
                  ) : (
                    <button
                      onClick={() => setOpenClipId(isOpen ? null : vid)}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold flex-shrink-0 transition-all"
                      style={{ background: "var(--sidebar)", color: "#fff" }}
                    >
                      <Play size={11} color="#fff" fill="#fff" /> {isOpen ? "Hide clip" : "Play clip"}
                    </button>
                  )}
                </div>
                {isOpen && !expired && <ClipPlayer src={v.image_url} camera={cameraName} timestamp={v.timestamp} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Violation detail panel ────────────────────────────────────────────────────
function ViolationPanel({ person, violations }) {
  const [openClipId, setOpenClipId] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const totalCount = violations.length;

  const VISIBLE_LIMIT = 4;
  const sortedViolations = [...violations].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  const shownViolations = sortedViolations.slice(0, VISIBLE_LIMIT);

  return (
    <div className="flex flex-col h-full">
      {/* Profile */}
      <div className="flex items-center gap-3 pb-4 mb-4" style={{ borderBottom: "1px solid var(--border)" }}>
        <Avatar src={person.imageUrl} name={person.name} size={48} isMinor={person.isMinor} status={person.status} />
        <div>
          <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>{person.name}</div>
          <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            {person.barangayId}
          </div>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            {person.age != null && (
              <span className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Age {person.age}</span>
            )}
            {person.isMinor && (
              <span className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                style={{ background: "rgba(224,151,42,0.10)", color: "#e0972a" }}>Minor</span>
            )}
            <span className="text-[10px] font-medium capitalize"
              style={{ color: STATUS_COLOR[person.status] ?? "#6e7a78" }}>
              {person.status}
            </span>
          </div>
        </div>
      </div>

      {/* Total violations */}
      <div className="rounded-lg p-3 text-center mb-4"
        style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
        <div className="text-xl font-bold leading-none" style={{ color: totalCount === 0 ? "#34a86b" : "#ef4e32" }}>
          {totalCount}
        </div>
        <div className="text-[10px] mt-1" style={{ color: "var(--muted-foreground)" }}>Total violations</div>
      </div>

      {/* Violation history */}
      <div className="flex-1 overflow-y-auto scrollbar-visible pr-2">
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] font-semibold tracking-widest uppercase"
            style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            Violation history
          </div>
          {violations.length > VISIBLE_LIMIT && (
            <button
              onClick={() => setShowModal(true)}
              className="text-[10px] font-semibold transition-colors flex-shrink-0"
              style={{ color: "var(--primary)" }}
            >
              View all ({violations.length})
            </button>
          )}
        </div>
        {violations.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 gap-2">
            <CheckCircle size={24} style={{ color: "#34a86b" }} />
            <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>No violations</div>
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>Clean record</div>
          </div>
        ) : (
          <div className="space-y-2">
            {shownViolations.map((v) => {
                const code = typeof v.type === "object" ? v.type?.code : v.type;
                const vcfg = VIOLATION_CONFIG[code] ?? { label: code ?? "Violation", color: "#6e7a78", icon: AlertTriangle };
                const VIcon = vcfg.icon;
                const vid = v.code ?? v.id;
                const isOpen = openClipId === vid;
                const cameraName = v.camera
                  ? (typeof v.camera === "object" ? v.camera.name : v.camera)
                  : v.cameraZone ?? null;
                return (
                  <div key={vid} className="rounded-xl overflow-hidden"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                    {/* Title — click to reveal the violation clip */}
                    <button
                      onClick={() => setOpenClipId(isOpen ? null : vid)}
                      className="w-full flex items-center gap-2 px-3 py-2.5 text-left transition-colors"
                    >
                      <VIcon size={13} style={{ color: vcfg.color, flexShrink: 0 }} />
                      <span className="text-[12px] font-medium flex-1 min-w-0 truncate" style={{ color: "var(--foreground)" }}>
                        {vcfg.label}
                      </span>
                      <span className="text-[10px] flex-shrink-0" style={{ color: "var(--muted-foreground)" }}>
                        {fmtDate(v.timestamp)}
                      </span>
                    </button>

                    {/* Only the video clip is shown when expanded */}
                    {isOpen && <ClipPlayer src={v.image_url} camera={cameraName} timestamp={v.timestamp} />}
                  </div>
                );
              })}
          </div>
        )}
      </div>

      {showModal && (
        <ViolationHistoryModal person={person} violations={violations} onClose={() => setShowModal(false)} />
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export function ResidentLog() {
  const [households, setHouseholds] = useState([]);
  const [residents, setResidents] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    Promise.all([
      getHouseholds().then((r) => r.results ?? r).catch(() => []),
      getResidents().then((r) => r.results ?? r).catch(() => []),
      getAlerts().then((r) => r.results ?? r).catch(() => []),
    ]).then(([hh, res, al]) => {
      // Map household API shape to expected shape
      const mappedHH = hh.map((h) => ({
        familyName: h.family_name ?? h.familyName,
        members: (h.members ?? []).map((m) => ({
          id: m.code ?? m.id,
          firstName: m.first_name ?? m.firstName,
          lastName: m.last_name ?? m.lastName,
          birthdate: m.birthdate,
          barangayId: m.barangay_id ?? m.barangayId,
          status: m.status ?? "pending",
          imageUrl: m.image_url ?? m.imageUrl,
        })),
      }));

      // Map resident API shape
      const mappedRes = res.map((r) => ({
        id: r.code ?? r.id,
        name: r.name,
        age: r.age,
        barangayId: r.barangay_id ?? r.barangayId,
        status: r.status ?? "pending",
        imageUrl: r.image_url ?? r.imageUrl,
        guardianName: r.guardian_name ?? r.guardianName,
      }));

      setHouseholds(mappedHH);
      setResidents(mappedRes);
      setAlerts(al);
      setLoading(false);
    });
  }, []);

  const allPeople = buildTrackedPersons(households, residents);

  const withCounts = allPeople.map((p) => ({
    person: p,
    violations: getViolationsFor(p, alerts),
  }));

  const filtered = withCounts
    .filter(({ person, violations }) => {
      const q = search.toLowerCase();
      const matchSearch =
        !search ||
        person.name.toLowerCase().includes(q) ||
        person.barangayId.toLowerCase().includes(q) ||
        (person.householdName ?? "").toLowerCase().includes(q);
      const matchFilter =
        filter === "all" ||
        (filter === "with-violations" && violations.length > 0) ||
        (filter === "minor" && person.isMinor);
      return matchSearch && matchFilter;
    })
    .sort((a, b) => b.violations.length - a.violations.length || a.person.name.localeCompare(b.person.name));

  const selectedViolations = selected ? getViolationsFor(selected, alerts) : [];

  const totalWithViolations = withCounts.filter((w) => w.violations.length > 0).length;
  const totalViolations = withCounts.reduce((acc, w) => acc + w.violations.length, 0);

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Header */}
      <div
        className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Resident Violations</h1>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>Linked by suspect identification</span>
        </div>
        <div className="flex items-center gap-4 text-xs" style={{ color: "var(--muted-foreground)" }}>
          <span>
            <span style={{ color: "#ef4e32", fontWeight: 600 }}>{totalWithViolations}</span> residents with violations
          </span>
          <span>
            <span style={{ color: "var(--primary)", fontWeight: 600 }}>{totalViolations}</span> total records
          </span>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">

        {/* ── Left: list ───────────────────────────────────────────────── */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden px-6 py-4 gap-3">

          {/* Search + filters */}
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="flex-1 relative">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2"
                style={{ color: "var(--muted-foreground)" }} />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by name, BRG ID, or household…"
                className="w-full pl-9 pr-3 py-2 rounded-lg text-sm outline-none"
                style={{
                  background: "var(--secondary)",
                  border: "1px solid var(--border)",
                  color: "var(--foreground)",
                }}
              />
            </div>
            <div className="flex gap-1">
              {[
                { k: "all",             label: "All" },
                { k: "with-violations", label: "With violations" },
                { k: "minor",           label: "Minors" },
              ].map(({ k, label }) => (
                <button
                  key={k}
                  onClick={() => setFilter(k)}
                  className="px-3 py-2 text-xs font-medium rounded-lg transition-all"
                  style={{
                    background: filter === k ? "var(--primary)" : "var(--secondary)",
                    color: filter === k ? "var(--primary-foreground)" : "var(--muted-foreground)",
                    border: `1px solid ${filter === k ? "var(--primary)" : "var(--border)"}`,
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Count */}
          <div className="text-[11px] flex-shrink-0" style={{ color: "var(--muted-foreground)" }}>
            {loading ? "Loading…" : `${filtered.length} resident${filtered.length !== 1 ? "s" : ""}`}
          </div>

          {/* List */}
          <div className="flex-1 overflow-y-auto space-y-2 pr-1">
            {filtered.map(({ person, violations }) => (
              <PersonRow
                key={person.id}
                person={person}
                violations={violations}
                onSelect={setSelected}
                selected={selected?.id === person.id}
              />
            ))}
            {!loading && filtered.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 gap-2">
                <User size={28} style={{ color: "var(--muted-foreground)" }} />
                <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>No residents found</div>
              </div>
            )}
          </div>
        </div>

        {/* ── Right: detail panel ──────────────────────────────────────── */}
        <div
          className="w-80 flex-shrink-0 overflow-y-auto px-5 py-5"
          style={{ borderLeft: "1px solid var(--border)" }}
        >
          {selected ? (
            <ViolationPanel key={selected.id} person={selected} violations={selectedViolations} />
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
              <AlertTriangle size={28} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Select a resident</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                View their full violation history and breakdown
              </div>
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
