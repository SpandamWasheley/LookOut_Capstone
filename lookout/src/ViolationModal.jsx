import { useRef, useEffect, useState, useMemo } from "react";
import {
  X, MapPin, Clock, User, Shield, Play, Pause,
  SkipBack, Volume2, VolumeX, Download, Radio, CheckCircle, AlertTriangle,
  MessageSquare, Phone, ChevronDown, ChevronRight, Home, Loader2, Search, Send, Info,
} from "lucide-react";
import { VIOLATION_CONFIG } from "../data/mockData";
import { sendSms } from "./api";

const statusConfig = {
  active:       { label: "Active",     color: "#ef4444", bg: "rgba(239,68,68,0.1)" },
  acknowledged: { label: "Dismissed",  color: "#64748b", bg: "rgba(100,116,139,0.1)" },
  dispatched:   { label: "Dispatched", color: "#3b82f6", bg: "rgba(59,130,246,0.1)" },
  resolved:     { label: "Resolved",   color: "#10b981", bg: "rgba(16,185,129,0.1)" },
};

function formatFull(ts) {
  return new Date(ts).toLocaleString("en-PH", {
    month: "short", day: "numeric", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function formatShort(ts) {
  return new Date(ts).toLocaleString("en-PH", {
    month: "short", day: "numeric", year: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: true,
  });
}

// ── Recording player ──────────────────────────────────────────────────────────
function RecordingPlayer({ alert }) {
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [elapsed, setElapsed] = useState(18);
  const duration = 45;
  const ivRef = useRef(null);

  useEffect(() => {
    if (playing) {
      ivRef.current = window.setInterval(() => {
        setElapsed((p) => {
          if (p >= duration) { setPlaying(false); return duration; }
          return p + 0.5;
        });
      }, 500);
    } else {
      if (ivRef.current !== null) { window.clearInterval(ivRef.current); ivRef.current = null; }
    }
    return () => { if (ivRef.current !== null) { window.clearInterval(ivRef.current); ivRef.current = null; } };
  }, [playing]);

  const fmtSec = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const pct = (elapsed / duration) * 100;

  return (
    <div className="rounded-xl overflow-hidden" style={{ background: "#000", border: "1px solid var(--border)" }}>
      <div className="relative" style={{ paddingTop: "56.25%" }}>
        <img
          src={alert.imageUrl}
          alt="Recording"
          className="absolute inset-0 w-full h-full object-cover transition-all duration-300"
          style={{ opacity: playing ? 0.82 : 0.55, filter: playing ? "none" : "grayscale(25%)" }}
        />
        <div className="absolute inset-0 pointer-events-none" style={{
          backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.06) 3px, rgba(0,0,0,0.06) 4px)",
        }} />
        <div className="absolute top-0 left-0 right-0 px-3 py-2 flex items-center justify-between"
          style={{ background: "linear-gradient(to bottom, rgba(0,0,0,0.72), transparent)" }}>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded"
              style={{ background: "rgba(239,68,68,0.85)", color: "#fff", fontFamily: "'DM Mono', monospace" }}>
              ● REC
            </span>
            <span className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              {alert.camera}
            </span>
          </div>
          <span className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            {formatFull(alert.timestamp)}
          </span>
        </div>
        <div className="absolute bottom-10 left-3 text-[10px]"
          style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
          {alert.cameraZone}
        </div>
        {!playing && (
          <button onClick={() => setPlaying(true)} className="absolute inset-0 flex items-center justify-center group">
            <div className="w-14 h-14 rounded-full flex items-center justify-center transition-all group-hover:scale-110"
              style={{ background: "rgba(245,158,11,0.9)", boxShadow: "0 0 28px rgba(245,158,11,0.35)" }}>
              <Play size={22} color="#0c0f16" fill="#0c0f16" style={{ marginLeft: 2 }} />
            </div>
          </button>
        )}
      </div>
      <div className="px-4 py-3" style={{ background: "var(--sidebar)" }}>
        <div
          className="relative h-1 rounded-full mb-3 cursor-pointer"
          style={{ background: "var(--border)" }}
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const t = ((e.clientX - r.left) / r.width) * duration;
            setElapsed(Math.max(0, Math.min(t, duration)));
          }}
        >
          <div className="absolute left-0 top-0 h-full rounded-full transition-all"
            style={{ width: `${pct}%`, background: "var(--primary)" }} />
          <div className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full border-2"
            style={{ left: `${(18 / duration) * 100}%`, background: "#ef4444", borderColor: "var(--sidebar)" }}
            title="Violation detected" />
        </div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <button
              onClick={() => { setElapsed(0); setPlaying(false); }}
              className="p-1 rounded transition-colors"
              style={{ color: "var(--muted-foreground)" }}
              onMouseEnter={(e) => (e.currentTarget.style.color = "var(--foreground)")}
              onMouseLeave={(e) => (e.currentTarget.style.color = "var(--muted-foreground)")}
            >
              <SkipBack size={13} />
            </button>
            <button
              onClick={() => setPlaying(!playing)}
              className="w-8 h-8 rounded-full flex items-center justify-center"
              style={{ background: "var(--primary)" }}
            >
              {playing
                ? <Pause size={13} color="#0c0f16" fill="#0c0f16" />
                : <Play  size={13} color="#0c0f16" fill="#0c0f16" style={{ marginLeft: 1 }} />}
            </button>
            <button
              onClick={() => setMuted(!muted)}
              className="p-1 rounded"
              style={{ color: "var(--muted-foreground)" }}
              onMouseEnter={(e) => (e.currentTarget.style.color = "var(--foreground)")}
              onMouseLeave={(e) => (e.currentTarget.style.color = "var(--muted-foreground)")}
            >
              {muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
            </button>
            <span className="text-[11px] tabular-nums"
              style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              {fmtSec(elapsed)} / {fmtSec(duration)}
            </span>
          </div>
          <button
            className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-md"
            style={{ color: "var(--muted-foreground)" }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "var(--foreground)"; e.currentTarget.style.background = "var(--border)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}
          >
            <Download size={11} /> Save clip
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Phone row inside ContactGuardianModal ─────────────────────────────────────
function PhoneRow({ label, sublabel, phone, isSelected, onToggle, accent }) {
  return (
    <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg"
      style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      <div className="flex-1 min-w-0">
        <div className="text-[12px] font-medium truncate" style={{ color: "var(--foreground)" }}>{label}</div>
        {sublabel && (
          <div className="text-[10px] capitalize" style={{ color: "var(--muted-foreground)" }}>{sublabel}</div>
        )}
      </div>
      {phone ? (
        <button
          onClick={onToggle}
          className="flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded-lg transition-all flex-shrink-0"
          style={{
            background: isSelected ? "rgba(16,185,129,0.15)" : accent ? "rgba(245,158,11,0.1)" : "var(--secondary)",
            color: isSelected ? "#10b981" : accent ? "#f59e0b" : "var(--muted-foreground)",
            border: `1px solid ${isSelected ? "rgba(16,185,129,0.3)" : "var(--border)"}`,
            fontFamily: "'DM Mono', monospace",
          }}>
          <Phone size={10} style={{ flexShrink: 0 }} />
          {phone}
        </button>
      ) : (
        <span className="text-[11px] italic flex-shrink-0" style={{ color: "var(--muted-foreground)" }}>
          No number
        </span>
      )}
    </div>
  );
}

// ── Resolve checklist helpers ─────────────────────────────────────────────────
function calcAge(birthdate) {
  if (!birthdate) return null;
  const dob = new Date(birthdate), today = new Date();
  let a = today.getFullYear() - dob.getFullYear();
  const m = today.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) a--;
  return a;
}

function suspectMatches(suspect, fullName) {
  if (!suspect || !fullName) return false;
  const s = suspect.toLowerCase();
  return fullName.toLowerCase().split(/[\s,]+/).filter((p) => p.length > 2).some((p) => s.includes(p));
}

function buildCandidates(rawHouseholds, rawResidents) {
  const seen = new Set();
  const people = [];
  for (const hh of (rawHouseholds ?? [])) {
    const familyName = hh.family_name ?? "";
    for (const m of (hh.members ?? [])) {
      const bid = m.barangay_id ?? m.code;
      if (!bid || seen.has(bid)) continue;
      seen.add(bid);
      const age = calcAge(m.birthdate);
      people.push({
        id: m.code,
        name: `${m.last_name ?? ""}, ${m.first_name ?? ""}`.trim().replace(/^,\s*/, ""),
        fullName: `${m.first_name ?? ""} ${m.last_name ?? ""}`.trim(),
        age, isMinor: age != null && age < 18,
        household: `${familyName} household`,
        barangayId: bid, status: m.status ?? "pending", imageUrl: m.image_url ?? "",
      });
    }
  }
  for (const r of (rawResidents ?? [])) {
    const bid = r.barangay_id ?? r.code;
    if (!bid || seen.has(bid)) continue;
    seen.add(bid);
    people.push({
      id: r.code, name: r.name ?? "", fullName: r.name ?? "",
      age: r.age, isMinor: r.age != null && r.age < 18,
      household: null, barangayId: bid, status: r.status ?? "pending", imageUrl: r.image_url ?? "",
    });
  }
  return people;
}

// ── Resolve checklist modal ───────────────────────────────────────────────────
function ResolveChecklistModal({ alert, vcfg, households: rawHH, residents: rawRes, onConfirm, onClose }) {
  const [search, setSearch] = useState("");
  const [checkedIds, setCheckedIds] = useState(new Set());
  const [resolving, setResolving] = useState(false);

  const candidates = useMemo(() => buildCandidates(rawHH, rawRes), [rawHH, rawRes]);

  useEffect(() => {
    if (!alert.suspect || candidates.length === 0) return;
    setCheckedIds(new Set(candidates.filter((c) => suspectMatches(alert.suspect, c.fullName)).map((c) => c.id)));
  }, [candidates, alert.suspect]);

  const isCurfew = alert.type === "curfew";

  const filtered = candidates.filter((c) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return c.name.toLowerCase().includes(q) || c.barangayId.toLowerCase().includes(q) || (c.household ?? "").toLowerCase().includes(q);
  });

  const sorted = [...filtered].sort((a, b) => {
    const ac = checkedIds.has(a.id) ? 0 : 1, bc = checkedIds.has(b.id) ? 0 : 1;
    if (ac !== bc) return ac - bc;
    if (isCurfew) { const am = a.isMinor ? 0 : 1, bm = b.isMinor ? 0 : 1; if (am !== bm) return am - bm; }
    return a.name.localeCompare(b.name);
  });

  const toggle = (id) => setCheckedIds((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const handleConfirm = async () => {
    setResolving(true);
    const names = candidates.filter((c) => checkedIds.has(c.id)).map((c) => c.fullName).join("; ");
    await onConfirm(names || null);
  };

  const VIcon = vcfg.icon;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}>
      <div className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: "rgba(16,185,129,0.12)" }}>
              <CheckCircle size={14} style={{ color: "#10b981" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Confirm Resolution</div>
              <div className="flex items-center gap-1.5 mt-0.5">
                <VIcon size={10} style={{ color: vcfg.color }} />
                <span className="text-[11px] font-medium" style={{ color: vcfg.color }}>{vcfg.label}</span>
                <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>· {alert.id}</span>
              </div>
            </div>
          </div>
          {!resolving && (
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={14} />
            </button>
          )}
        </div>

        {/* Instruction */}
        <div className="px-5 pt-4 pb-2 flex-shrink-0">
          <div className="text-[12px] px-3 py-2.5 rounded-xl"
            style={{ background: "rgba(16,185,129,0.06)", border: "1px solid rgba(16,185,129,0.15)", color: "var(--muted-foreground)", lineHeight: 1.5 }}>
            Select the resident(s) involved. Checking them adds this violation to their log in Resident Violations.
          </div>
        </div>

        {/* Search */}
        <div className="px-5 py-3 flex-shrink-0">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2"
              style={{ color: "var(--muted-foreground)" }} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={isCurfew ? "Search minors or residents…" : "Search residents…"}
              className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
            />
          </div>
        </div>

        {/* Count label */}
        <div className="px-5 pb-1 flex items-center justify-between flex-shrink-0">
          <span className="text-[10px] font-semibold uppercase tracking-wide"
            style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            {isCurfew ? "Potential violators" : "Household candidates"} · {sorted.length}
          </span>
          {checkedIds.size > 0 && (
            <span className="text-[10px] font-semibold" style={{ color: "#10b981" }}>
              {checkedIds.size} selected
            </span>
          )}
        </div>

        {/* Candidate list */}
        <div className="flex-1 overflow-y-auto px-5 pb-3 pt-1 space-y-1.5" style={{ minHeight: 0 }}>
          {sorted.length === 0 ? (
            <div className="py-10 text-center text-sm" style={{ color: "var(--muted-foreground)" }}>
              {candidates.length === 0 ? "Loading residents…" : "No matches found"}
            </div>
          ) : sorted.map((c) => {
            const isChecked = checkedIds.has(c.id);
            const isIdentified = suspectMatches(alert.suspect, c.fullName);
            return (
              <button key={c.id} onClick={() => toggle(c.id)}
                className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left transition-all"
                style={{
                  background: isChecked ? "rgba(16,185,129,0.07)" : "var(--secondary)",
                  border: `1px solid ${isChecked ? "rgba(16,185,129,0.3)" : "var(--border)"}`,
                }}>
                {/* Checkbox */}
                <div className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center transition-all"
                  style={{ background: isChecked ? "#10b981" : "transparent", border: `1.5px solid ${isChecked ? "#10b981" : "var(--muted-foreground)"}` }}>
                  {isChecked && <CheckCircle size={10} color="#fff" strokeWidth={3} />}
                </div>
                {/* Avatar */}
                <div className="w-8 h-8 rounded-lg flex-shrink-0 flex items-center justify-center text-xs font-bold overflow-hidden"
                  style={{ background: c.isMinor ? "rgba(224,151,42,0.15)" : "var(--secondary)", color: c.isMinor ? "#e0972a" : "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                  {c.imageUrl
                    ? <img src={c.imageUrl} alt={c.name} className="w-full h-full object-cover" onError={(e) => { e.currentTarget.style.display = "none"; }} />
                    : (c.name[0] ?? "?")}
                </div>
                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[12px] font-medium" style={{ color: "var(--foreground)" }}>{c.name}</span>
                    {c.isMinor && (
                      <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
                        style={{ background: "rgba(224,151,42,0.15)", color: "#e0972a" }}>Minor</span>
                    )}
                    {isIdentified && (
                      <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
                        style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>Possible candidate</span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 mt-0.5 text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                    <span style={{ fontFamily: "'DM Mono', monospace" }}>{c.barangayId}</span>
                    {c.age != null && <><span>·</span><span>Age {c.age}</span></>}
                    {c.household && <><span>·</span><span className="truncate">{c.household}</span></>}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 flex items-center justify-between gap-3 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
            {checkedIds.size === 0
              ? "No residents selected — resolves without linking"
              : `${checkedIds.size} resident${checkedIds.size !== 1 ? "s" : ""} will be linked`}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {!resolving && (
              <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
            )}
            <button disabled={resolving} onClick={handleConfirm}
              className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium"
              style={{ background: "#10b981", color: "#fff", cursor: resolving ? "not-allowed" : "pointer", opacity: resolving ? 0.7 : 1 }}>
              {resolving ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle size={13} />}
              {resolving ? "Resolving…" : "Confirm & Resolve"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Contact Guardian Modal ────────────────────────────────────────────────────
function ContactGuardianModal({ alert, violationType, households: rawHouseholds, onClose }) {
  const [mapped, setMapped] = useState([]);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState(new Set());
  const [selected, setSelected] = useState(new Set());
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [potentialHhId, setPotentialHhId] = useState(null);

  const mapHH = (raw) => ({
    id: raw.code,
    familyName: raw.family_name,
    address: raw.address || "",
    contact: raw.contact || "",
    members: (raw.members || []).map((m) => ({
      id: m.code,
      firstName: m.first_name,
      lastName: m.last_name,
      relation: m.relation || "",
      phone: m.phone || "",
    })),
  });

  const getHHPhones = (hh) => {
    const phones = [];
    if (hh.contact) phones.push(hh.contact);
    hh.members.forEach((m) => { if (m.phone) phones.push(m.phone); });
    return phones;
  };

  // Map raw API households from the parent (AlertFeed already fetched them)
  useEffect(() => {
    const hhs = (rawHouseholds ?? []).map(mapHH);
    setMapped(hhs);
    if (!alert.suspect) return;
    const sq = alert.suspect.toLowerCase().replace(/[,\.]/g, "").trim();
    for (const hh of hhs) {
      const match = hh.members.some((m) => {
        const full = `${m.firstName} ${m.lastName}`.toLowerCase();
        const rev  = `${m.lastName} ${m.firstName}`.toLowerCase();
        return full.includes(sq) || sq.includes(m.firstName.toLowerCase()) || sq.includes(m.lastName.toLowerCase()) || rev.includes(sq);
      });
      if (match) {
        setPotentialHhId(hh.id);
        setExpanded(new Set([hh.id]));
        break;
      }
    }
  }, [rawHouseholds]);

  useEffect(() => {
    const ts = formatShort(alert.timestamp);
    const subject = alert.suspect ? alert.suspect : "an individual";
    setMessage(
      `Good day! This is an alert from Barangay Tetuan LookOut System.\n\n${subject} was flagged for a ${violationType} violation on ${ts}.\n\nPlease contact the Barangay Hall immediately for more information.\n\n- LookOut Security System`
    );
  }, []);

  const toggleExpand = (id) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const togglePhone = (phone) => {
    if (!phone) return;
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(phone) ? next.delete(phone) : next.add(phone);
      return next;
    });
  };

  const toggleAllInHH = (hh) => {
    const phones = getHHPhones(hh);
    if (phones.length === 0) return;
    const allSel = phones.every((p) => selected.has(p));
    setSelected((prev) => {
      const next = new Set(prev);
      allSel ? phones.forEach((p) => next.delete(p)) : phones.forEach((p) => next.add(p));
      return next;
    });
  };

  const handleSend = async () => {
    if (selected.size === 0 || !message.trim()) return;
    setSending(true);
    try {
      await sendSms({ recipients: [...selected], message: message.trim() });
      setSent(true);
    } catch (err) {
      window.alert(`Failed to send: ${err.message}`);
    } finally {
      setSending(false);
    }
  };

  const filtered = mapped.filter((hh) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      hh.familyName.toLowerCase().includes(q) ||
      hh.address.toLowerCase().includes(q) ||
      hh.members.some((m) => `${m.firstName} ${m.lastName}`.toLowerCase().includes(q))
    );
  });

  const sorted = potentialHhId
    ? [...filtered.filter((h) => h.id === potentialHhId), ...filtered.filter((h) => h.id !== potentialHhId)]
    : filtered;

  const canSend = selected.size > 0 && message.trim().length > 0;

  if (sent) {
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center p-4"
        style={{ background: "rgba(0,0,0,0.7)", backdropFilter: "blur(6px)" }}
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        <div className="w-full max-w-sm rounded-2xl p-8 flex flex-col items-center gap-5 text-center shadow-2xl"
          style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
          <div className="w-16 h-16 rounded-full flex items-center justify-center"
            style={{ background: "rgba(16,185,129,0.12)", border: "2px solid rgba(16,185,129,0.3)" }}>
            <CheckCircle size={32} style={{ color: "#10b981" }} />
          </div>
          <div>
            <div className="text-sm font-semibold mb-1" style={{ color: "var(--foreground)" }}>SMS Sent</div>
            <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
              Message dispatched to {selected.size} recipient{selected.size !== 1 ? "s" : ""}.
            </div>
          </div>
          <button onClick={onClose} className="w-full py-2.5 rounded-xl text-sm font-medium"
            style={{ background: "#10b981", color: "#fff" }}>
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-lg rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: "rgba(16,185,129,0.12)" }}>
              <MessageSquare size={14} style={{ color: "#10b981" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Contact Guardian</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                {alert.id} · {alert.suspect ?? "Unknown subject"}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Search */}
        <div className="px-5 pt-4 pb-3 flex-shrink-0">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2"
              style={{ color: "var(--muted-foreground)" }} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search households or member names…"
              className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm outline-none"
              style={{
                background: "var(--secondary)",
                border: "1px solid var(--border)",
                color: "var(--foreground)",
              }}
            />
          </div>
        </div>

        {/* Household list */}
        <div className="flex-1 overflow-y-auto px-5 pb-3 space-y-2" style={{ minHeight: 0 }}>
          {sorted.length === 0 ? (
            <div className="py-10 text-center text-sm" style={{ color: "var(--muted-foreground)" }}>
              {mapped.length === 0 ? "Loading households…" : "No households found"}
            </div>
          ) : (
            sorted.map((hh) => {
              const isPotential = hh.id === potentialHhId;
              const isOpen = expanded.has(hh.id);
              const phones = getHHPhones(hh);
              const selCount = phones.filter((p) => selected.has(p)).length;
              const allSel = phones.length > 0 && selCount === phones.length;

              return (
                <div key={hh.id} className="rounded-xl overflow-hidden transition-all"
                  style={{
                    border: `1px solid ${isPotential ? "rgba(245,158,11,0.4)" : "var(--border)"}`,
                    background: isPotential ? "rgba(245,158,11,0.04)" : "var(--secondary)",
                    boxShadow: isPotential ? "0 0 0 1px rgba(245,158,11,0.08)" : "none",
                  }}>
                  {/* Household header */}
                  <div className="flex items-center gap-2 px-3.5 py-3">
                    <button
                      onClick={() => toggleExpand(hh.id)}
                      className="flex items-center gap-2 flex-1 min-w-0 text-left">
                      {isOpen
                        ? <ChevronDown size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                        : <ChevronRight size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                      }
                      <Home size={13} style={{ color: isPotential ? "#f59e0b" : "var(--muted-foreground)", flexShrink: 0 }} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[13px] font-semibold" style={{ color: "var(--foreground)" }}>
                            {hh.familyName} household
                          </span>
                          {isPotential && (
                            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                              style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
                              Potential guardian
                            </span>
                          )}
                          {selCount > 0 && (
                            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full"
                              style={{ background: "rgba(16,185,129,0.12)", color: "#10b981" }}>
                              {selCount} selected
                            </span>
                          )}
                        </div>
                        {hh.address && (
                          <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--muted-foreground)" }}>
                            {hh.address}, Brgy. Tetuan
                          </div>
                        )}
                      </div>
                    </button>

                    {phones.length > 0 && (
                      <button
                        onClick={() => { if (!isOpen) toggleExpand(hh.id); toggleAllInHH(hh); }}
                        className="flex-shrink-0 text-[10px] font-semibold px-2.5 py-1 rounded-lg transition-all"
                        style={{
                          background: allSel ? "rgba(16,185,129,0.15)" : "rgba(245,158,11,0.1)",
                          color: allSel ? "#10b981" : "var(--primary)",
                          border: `1px solid ${allSel ? "rgba(16,185,129,0.3)" : "rgba(245,158,11,0.2)"}`,
                        }}>
                        {allSel ? "✓ All" : `Select all (${phones.length})`}
                      </button>
                    )}
                  </div>

                  {/* Expanded content */}
                  {isOpen && (
                    <div className="px-3.5 pb-3 space-y-1.5" style={{ borderTop: "1px solid var(--border)" }}>
                      <div className="pt-2 space-y-1.5">
                        {hh.contact && (
                          <PhoneRow
                            label="Main contact"
                            sublabel={hh.id}
                            phone={hh.contact}
                            isSelected={selected.has(hh.contact)}
                            onToggle={() => togglePhone(hh.contact)}
                            accent
                          />
                        )}
                        {hh.members.map((m) => (
                          <PhoneRow
                            key={m.id}
                            label={`${m.firstName} ${m.lastName}`}
                            sublabel={m.relation}
                            phone={m.phone}
                            isSelected={m.phone ? selected.has(m.phone) : false}
                            onToggle={() => togglePhone(m.phone)}
                          />
                        ))}
                        {hh.members.length === 0 && !hh.contact && (
                          <div className="text-[11px] py-2 text-center" style={{ color: "var(--muted-foreground)" }}>
                            No contact numbers on file
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Compose area */}
        <div className="flex-shrink-0 px-5 pt-4 pb-5 space-y-3"
          style={{ borderTop: "1px solid var(--border)" }}>
          {/* Selected recipient chips */}
          {selected.size > 0 && (
            <div className="flex flex-wrap gap-1.5 items-center">
              <span className="text-[11px] font-semibold" style={{ color: "var(--muted-foreground)" }}>To:</span>
              {[...selected].map((p) => (
                <span key={p}
                  className="flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: "rgba(16,185,129,0.12)", color: "#10b981", border: "1px solid rgba(16,185,129,0.25)" }}>
                  <Phone size={9} /> {p}
                  <button onClick={() => togglePhone(p)} className="ml-0.5 opacity-60 hover:opacity-100">×</button>
                </span>
              ))}
              <button onClick={() => setSelected(new Set())}
                className="text-[10px] ml-1 underline"
                style={{ color: "var(--muted-foreground)" }}>
                Clear all
              </button>
            </div>
          )}
          {selected.size === 0 && (
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
              Click a phone number above to add recipients.
            </div>
          )}

          <textarea
            rows={4}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Type your message here…"
            className="w-full px-3 py-2.5 rounded-xl text-[12px] resize-none outline-none"
            style={{
              background: "var(--secondary)",
              border: "1px solid var(--border)",
              color: "var(--foreground)",
              lineHeight: 1.6,
            }}
          />

          <div className="flex items-center justify-between gap-3">
            <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
              {message.length} chars · {selected.size} recipient{selected.size !== 1 ? "s" : ""}
            </span>
            <button
              disabled={!canSend || sending}
              onClick={handleSend}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-all"
              style={{
                background: canSend ? "#10b981" : "rgba(16,185,129,0.15)",
                color: canSend ? "#fff" : "rgba(16,185,129,0.4)",
                cursor: (canSend && !sending) ? "pointer" : "not-allowed",
              }}>
              {sending ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
              {sending ? "Sending…" : "Send SMS"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Set Candidate Modal ───────────────────────────────────────────────────────
function SetCandidateModal({ alert, households: rawHH, residents: rawRes, onSave, onClose }) {
  const [search, setSearch] = useState("");
  const [checkedIds, setCheckedIds] = useState(new Set());
  const [saving, setSaving] = useState(false);
  const candidates = useMemo(() => buildCandidates(rawHH, rawRes), [rawHH, rawRes]);

  useEffect(() => {
    if (!alert.suspect || candidates.length === 0) return;
    const existing = alert.suspect.split(";").map((s) => s.trim().toLowerCase());
    setCheckedIds(new Set(
      candidates.filter((c) => existing.some((e) => e && c.fullName.toLowerCase().includes(e))).map((c) => c.id)
    ));
  }, [candidates, alert.suspect]);

  const filtered = candidates.filter((c) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return c.name.toLowerCase().includes(q) || c.barangayId.toLowerCase().includes(q) || (c.household ?? "").toLowerCase().includes(q);
  });

  const sorted = [...filtered].sort((a, b) => {
    const ac = checkedIds.has(a.id) ? 0 : 1, bc = checkedIds.has(b.id) ? 0 : 1;
    if (ac !== bc) return ac - bc;
    return a.name.localeCompare(b.name);
  });

  const toggle = (id) => setCheckedIds((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const handleSave = async () => {
    setSaving(true);
    const names = candidates.filter((c) => checkedIds.has(c.id)).map((c) => c.fullName).join("; ");
    await onSave(names || null);
  };

  return (
    <div className="fixed inset-0 z-[75] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}>
      <div className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "rgba(59,130,246,0.12)" }}>
              <User size={14} style={{ color: "#3b82f6" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Select Match</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>Select one or more residents involved</div>
            </div>
          </div>
          {!saving && (
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={14} />
            </button>
          )}
        </div>

        {/* Search */}
        <div className="px-5 py-3 flex-shrink-0">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search residents…"
              className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
          </div>
        </div>

        {/* Count */}
        <div className="px-5 pb-1 flex items-center justify-between flex-shrink-0">
          <span className="text-[10px] font-semibold uppercase tracking-wide"
            style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            Residents · {sorted.length}
          </span>
          {checkedIds.size > 0 && (
            <span className="text-[10px] font-semibold" style={{ color: "#3b82f6" }}>{checkedIds.size} selected</span>
          )}
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-5 pb-3 pt-1 space-y-1.5" style={{ minHeight: 0 }}>
          {sorted.length === 0 ? (
            <div className="py-10 text-center text-sm" style={{ color: "var(--muted-foreground)" }}>
              {candidates.length === 0 ? "Loading residents…" : "No matches found"}
            </div>
          ) : sorted.map((c) => {
            const isChecked = checkedIds.has(c.id);
            const isPossible = suspectMatches(alert.suspect, c.fullName);
            return (
              <button key={c.id} onClick={() => toggle(c.id)}
                className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left transition-all"
                style={{
                  background: isChecked ? "rgba(59,130,246,0.07)" : "var(--secondary)",
                  border: `1px solid ${isChecked ? "rgba(59,130,246,0.3)" : "var(--border)"}`,
                }}>
                <div className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center transition-all"
                  style={{ background: isChecked ? "#3b82f6" : "transparent", border: `1.5px solid ${isChecked ? "#3b82f6" : "var(--muted-foreground)"}` }}>
                  {isChecked && <CheckCircle size={10} color="#fff" strokeWidth={3} />}
                </div>
                <div className="w-8 h-8 rounded-lg flex-shrink-0 flex items-center justify-center text-xs font-bold overflow-hidden"
                  style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                  {c.imageUrl
                    ? <img src={c.imageUrl} alt={c.name} className="w-full h-full object-cover" onError={(e) => { e.currentTarget.style.display = "none"; }} />
                    : (c.name[0] ?? "?")}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[12px] font-medium" style={{ color: "var(--foreground)" }}>{c.name}</span>
                    {c.isMinor && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(224,151,42,0.15)", color: "#e0972a" }}>Minor</span>}
                    {isPossible && <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(59,130,246,0.15)", color: "#3b82f6" }}>AI match</span>}
                  </div>
                  <div className="flex items-center gap-1.5 mt-0.5 text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                    <span style={{ fontFamily: "'DM Mono', monospace" }}>{c.barangayId}</span>
                    {c.age != null && <><span>·</span><span>Age {c.age}</span></>}
                    {c.household && <><span>·</span><span className="truncate">{c.household}</span></>}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 flex items-center justify-between gap-3 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
            {checkedIds.size === 0 ? "None selected — clears current candidate" : `${checkedIds.size} candidate${checkedIds.size !== 1 ? "s" : ""} will be set`}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {!saving && (
              <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
            )}
            <button disabled={saving} onClick={handleSave}
              className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium"
              style={{ background: "rgba(59,130,246,0.2)", color: "#3b82f6", border: "1px solid rgba(59,130,246,0.35)", cursor: saving ? "not-allowed" : "pointer", opacity: saving ? 0.7 : 1 }}>
              {saving ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle size={13} />}
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main modal ─────────────────────────────────────────────────────────────────
export function ViolationModal({
  alert, assignedOfficerNames, households, residents, onDismiss, onDispatch, onResolve, onClose, onUpdateSuspect, verifierName,
}) {
  const vcfg = VIOLATION_CONFIG[alert.type] ?? { label: alert.type, color: "#f59e0b", icon: AlertTriangle };
  const scfg = statusConfig[alert.status] ?? statusConfig.acknowledged;
  const VIcon = vcfg.icon;
  const [showContact, setShowContact] = useState(false);
  const [showResolveChecklist, setShowResolveChecklist] = useState(false);
  const [showAllOfficers, setShowAllOfficers] = useState(false);
  const [showSetCandidate, setShowSetCandidate] = useState(false);
  const [candidateConfirmed, setCandidateConfirmed] = useState(false);
  const [confirmedAt, setConfirmedAt] = useState(null);

  const isCandidateViolation = alert.type === "curfew" || alert.type === "waste";
  const isNoiseViolation = alert.type === "noise";
  const [noiseConfirmed, setNoiseConfirmed] = useState(false);
  const candidates = useMemo(() => buildCandidates(households, residents), [households, residents]);

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
        style={{ background: "rgba(0,0,0,0.8)", backdropFilter: "blur(8px)" }}
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      >
        <div
          className="w-full max-w-2xl rounded-2xl overflow-hidden shadow-2xl flex flex-col"
          style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 flex-shrink-0"
            style={{ borderBottom: "1px solid var(--border)" }}>
            <div className="flex items-center gap-3">
              <VIcon size={22} color={vcfg.color} />
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-[15px] font-semibold" style={{ color: "var(--foreground)" }}>{vcfg.label}</span>
                  <span className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                    style={{ background: scfg.bg, color: scfg.color }}>
                    {scfg.label}
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-0.5 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                  <span className="flex items-center gap-1"><MapPin size={9} /> {alert.cameraZone}</span>
                  <span className="flex items-center gap-1"><Clock size={9} /> {formatFull(alert.timestamp)}</span>
                  <span style={{ fontFamily: "'DM Mono', monospace" }}>{alert.id}</span>
                </div>
              </div>
            </div>
            <button onClick={onClose} className="p-2 rounded-lg flex-shrink-0"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={15} />
            </button>
          </div>

          {/* Body */}
          <div className="overflow-y-auto flex-1 p-6 space-y-5">
            <RecordingPlayer alert={alert} />

            <div className="grid grid-cols-2 gap-4" style={{ alignItems: "stretch" }}>
              {/* Left: detail grid */}
              <div className="flex flex-col gap-3">
                <div className={`gap-2 flex-1 ${isCandidateViolation || isNoiseViolation ? "flex flex-col" : "grid grid-cols-2"}`} style={isCandidateViolation || isNoiseViolation ? {} : { gridTemplateRows: "1fr 1fr" }}>
                  {/* Camera — hidden for curfew/waste/noise */}
                  {!isCandidateViolation && !isNoiseViolation && (
                    <div className="rounded-lg px-3 py-3 flex flex-col justify-center"
                      style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                      <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Camera</div>
                      <div className="text-[12px] font-medium mt-0.5"
                        style={{ color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }}>
                        {alert.camera}
                      </div>
                    </div>
                  )}

                  {/* Confidence — hidden for curfew/waste/noise */}
                  {!isCandidateViolation && !isNoiseViolation && (
                    <div className="rounded-lg px-3 py-3 flex flex-col justify-center"
                      style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                      <div className="flex items-center gap-1">
                        <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Confidence</div>
                        <div className="relative group flex items-center">
                          <Info size={10} style={{ color: "var(--muted-foreground)", cursor: "pointer" }} />
                          <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-52 rounded-xl px-3 py-2.5 text-[11px] leading-relaxed pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50 shadow-xl"
                            style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--muted-foreground)" }}>
                            <div className="font-semibold mb-1" style={{ color: "var(--foreground)" }}>AI Confidence Score</div>
                            How certain the YOLOv8 model is that a violation was detected. A higher score means the AI is more confident in its detection.
                            <div className="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0"
                              style={{ borderLeft: "5px solid transparent", borderRight: "5px solid transparent", borderTop: "5px solid var(--border)" }} />
                          </div>
                        </div>
                      </div>
                      <div className="text-[12px] font-medium mt-0.5" style={{ color: vcfg.color }}>
                        {(alert.confidence * 100).toFixed(0)}% conf
                      </div>
                    </div>
                  )}

                  {/* Potential Candidate / Noise card */}
                  {isNoiseViolation ? (
                    /* Noise violation card */
                    (() => {
                      const loudnessPct = Math.round((alert.confidence ?? 0) * 100);
                      const dBFS = Math.round(-30 + (alert.confidence ?? 0) * 30);
                      const accentColor = noiseConfirmed ? "#10b981" : "#f59e0b";
                      return (
                        <div className="rounded-lg overflow-hidden"
                          style={{ border: "1px solid var(--border)", borderLeft: `3px solid ${accentColor}`, background: "var(--secondary)" }}>
                          {/* Label */}
                          <div className="px-3 pt-2.5 pb-1 text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                            Noise violation — no facial recognition, loudness only
                          </div>
                          {/* Source + Duration */}
                          <div className="flex px-3 pb-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
                            <div className="flex-1">
                              <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Source</div>
                              <div className="text-[12px] font-bold mt-0.5" style={{ color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }}>
                                {alert.camera} · mic
                              </div>
                            </div>
                            <div style={{ width: 1, background: "var(--border)", margin: "0 12px" }} />
                            <div className="flex-1">
                              <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Duration above threshold</div>
                              <div className="text-[12px] font-bold mt-0.5" style={{ color: accentColor }}>— s</div>
                            </div>
                          </div>
                          {/* Loudness section */}
                          <div className="px-3 py-2.5">
                            <div className="flex items-center justify-between mb-2">
                              <div className="flex items-center gap-1">
                                <span className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Relative loudness</span>
                                <div className="relative group flex items-center">
                                  <Info size={10} style={{ color: "var(--muted-foreground)", cursor: "pointer" }} />
                                  <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-48 rounded-xl px-3 py-2 text-[11px] leading-relaxed pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50 shadow-xl"
                                    style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--muted-foreground)" }}>
                                    How loud the detected sound is relative to the noise threshold. Values above 0 dBFS indicate clipping.
                                  </div>
                                </div>
                              </div>
                              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                                style={{ background: noiseConfirmed ? "rgba(16,185,129,0.15)" : "rgba(245,158,11,0.15)", color: accentColor }}>
                                {noiseConfirmed ? "Confirmed" : "Pending verification"}
                              </span>
                            </div>
                            {/* Bar */}
                            <div className="relative h-2 rounded-full" style={{ background: "var(--muted)" }}>
                              <div className="absolute left-0 top-0 h-full rounded-full transition-all"
                                style={{ width: `${loudnessPct}%`, background: accentColor }} />
                              <div className="absolute top-1/2 -translate-y-1/2 w-[2px] h-4 rounded-full"
                                style={{ left: "75%", background: "var(--foreground)" }} />
                            </div>
                            <div className="flex justify-end mt-1 relative">
                              <span className="absolute text-[9px]" style={{ left: "73%", color: "var(--muted-foreground)" }}>threshold</span>
                              <span className="text-[10px] font-semibold" style={{ color: accentColor }}>{dBFS} dBFS</span>
                            </div>
                            {/* Action buttons */}
                            {!noiseConfirmed && (
                              <div className="flex gap-2 mt-2.5">
                                <button
                                  onClick={() => setNoiseConfirmed(false)}
                                  className="flex-1 text-[11px] font-semibold py-1.5 rounded-lg"
                                  style={{ background: "var(--muted)", border: "1px solid var(--border)", color: "var(--foreground)" }}>
                                  Not a violation
                                </button>
                                <button
                                  onClick={() => setNoiseConfirmed(true)}
                                  className="flex-1 flex items-center justify-center gap-1 text-[11px] font-semibold py-1.5 rounded-lg"
                                  style={{ background: "#f59e0b", color: "#fff" }}>
                                  <CheckCircle size={10} /> Confirm
                                </button>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })()
                  ) : isCandidateViolation ? (
                    /* 3-state candidate match card for curfew / waste */
                    (() => {
                      const candidateName = alert.suspect ? alert.suspect.split(";")[0].trim() : null;
                      const initials = candidateName
                        ? candidateName.split(" ").slice(0, 2).map((w) => w[0]).join("").toUpperCase()
                        : "";
                      const accentColor = candidateConfirmed ? "#10b981" : candidateName ? "#f59e0b" : "var(--border)";
                      const formatHHMM = (iso) => new Date(iso).toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", hour12: true });
                      const matchedCandidate = candidateName
                        ? candidates.find((c) => c.fullName.trim() === candidateName)
                          ?? candidates.find((c) => suspectMatches(alert.suspect, c.fullName))
                        : null;

                      return (
                        <div className="rounded-lg overflow-hidden"
                          style={{ border: "1px solid var(--border)", borderLeft: `3px solid ${accentColor}`, background: "var(--secondary)" }}>
                          {/* Camera + Confidence row */}
                          <div className="flex items-stretch" style={{ borderBottom: "1px solid var(--border)" }}>
                            <div className="flex-1 px-3 py-2.5">
                              <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Camera</div>
                              <div className="text-[12px] font-bold mt-0.5" style={{ color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }}>{alert.camera}</div>
                            </div>
                            <div style={{ width: 1, background: "var(--border)" }} />
                            <div className="flex-1 px-3 py-2.5">
                              <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Confidence</div>
                              <div className="text-[12px] font-bold mt-0.5" style={{ color: accentColor, fontFamily: "'DM Mono', monospace" }}>
                                {alert.confidence ? `${(alert.confidence * 100).toFixed(0)}%` : "—"}
                              </div>
                            </div>
                          </div>

                          {/* Candidate section */}
                          <div className="px-3 py-2.5">
                            {!candidateName ? (
                              /* State 1 — no candidate */
                              <div className="flex items-center justify-between">
                                <div>
                                  <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Candidate match</div>
                                  <div className="text-[12px] font-semibold mt-0.5" style={{ color: "var(--foreground)" }}>No match found</div>
                                </div>
                                <button onClick={() => setShowSetCandidate(true)}
                                  className="flex items-center gap-1 text-[11px] font-semibold px-3 py-1.5 rounded-lg"
                                  style={{ background: "var(--muted)", border: "1px solid var(--border)", color: "var(--foreground)" }}>
                                  <User size={10} /> Select
                                </button>
                              </div>
                            ) : candidateConfirmed ? (
                              /* State 3 — confirmed */
                              <div className="flex items-center gap-2">
                                <div className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0"
                                  style={{ background: "rgba(16,185,129,0.18)", color: "#10b981" }}>{initials}</div>
                                <div className="flex-1 min-w-0">
                                  <div className="text-[12px] font-semibold truncate" style={{ color: "var(--foreground)" }}>{candidateName}</div>
                                  <div className="text-[10px] truncate" style={{ color: "var(--muted-foreground)" }}>
                                    Verified by {verifierName || "officer"}{confirmedAt ? ` · ${formatHHMM(confirmedAt)}` : ""}
                                  </div>
                                </div>
                                <span className="flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0"
                                  style={{ background: "rgba(16,185,129,0.15)", color: "#10b981" }}>
                                  <CheckCircle size={9} /> Confirmed
                                </span>
                              </div>
                            ) : (
                              /* State 2 — pending verification */
                              <div className="flex flex-col gap-2">
                                <div className="flex items-center gap-2">
                                  <div className="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0"
                                    style={{ background: "rgba(245,158,11,0.18)", color: "#f59e0b" }}>{initials}</div>
                                  <div className="flex-1 min-w-0">
                                    <div className="text-[12px] font-semibold truncate" style={{ color: "var(--foreground)" }}>{candidateName}</div>
                                    <div className="text-[10px] truncate" style={{ color: "var(--muted-foreground)" }}>
                                      {matchedCandidate ? `Resident ID · ${matchedCandidate.barangayId}` : "Potential match"}
                                    </div>
                                  </div>
                                  <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full flex-shrink-0"
                                    style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>Pending verification</span>
                                </div>
                                <div className="flex gap-2">
                                  <button
                                    onClick={() => { setCandidateConfirmed(false); setConfirmedAt(null); setShowSetCandidate(true); }}
                                    className="flex-1 text-[11px] font-semibold py-1.5 rounded-lg"
                                    style={{ background: "var(--muted)", border: "1px solid var(--border)", color: "var(--foreground)" }}>
                                    Not a match
                                  </button>
                                  <button
                                    onClick={() => { setCandidateConfirmed(true); setConfirmedAt(new Date().toISOString()); }}
                                    className="flex-1 flex items-center justify-center gap-1 text-[11px] font-semibold py-1.5 rounded-lg"
                                    style={{ background: "#f59e0b", color: "#fff" }}>
                                    <CheckCircle size={10} /> Confirm
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })()
                  ) : (
                    /* Default chip style for other violations */
                    <div className="col-span-2 rounded-lg px-3 py-2.5"
                      style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                      <div className="flex items-center justify-between mb-1.5">
                        <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Potential Candidate</div>
                        <button
                          onClick={() => setShowSetCandidate(true)}
                          className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full transition-all"
                          style={{ background: "rgba(59,130,246,0.1)", color: "#3b82f6", border: "1px solid rgba(59,130,246,0.2)" }}>
                          <User size={9} /> {alert.suspect ? "Edit" : "+ Add"}
                        </button>
                      </div>
                      {alert.suspect ? (
                        <div className="flex flex-wrap gap-1">
                          {alert.suspect.split(";").map((n) => n.trim()).filter(Boolean).map((name) => (
                            <span key={name} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium"
                              style={{ background: "rgba(59,130,246,0.1)", color: "#3b82f6", border: "1px solid rgba(59,130,246,0.2)" }}>
                              <User size={9} /> {name}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>—</div>
                      )}
                    </div>
                  )}
                </div>


                {alert.notes && (
                  <div className="rounded-lg px-3 py-3"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                    <div className="text-[10px] mb-1" style={{ color: "var(--muted-foreground)" }}>Notes</div>
                    <p className="text-[12px] italic" style={{ color: "var(--muted-foreground)" }}>"{alert.notes}"</p>
                  </div>
                )}
              </div>

              {/* Right: officers + description */}
              <div className="flex flex-col gap-3">
                {/* Officers — compact with expandable */}
                <div className="rounded-lg px-3 py-2.5"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                  <div className="text-[10px] mb-1" style={{ color: "var(--muted-foreground)" }}>
                    Assigned officers {assignedOfficerNames.length > 0 && `(${assignedOfficerNames.length})`}
                  </div>
                  {assignedOfficerNames.length === 0 ? (
                    <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>None assigned</div>
                  ) : (
                    <div className="flex items-center gap-1.5 text-[12px]">
                      <Shield size={10} style={{ color: "#10b981", flexShrink: 0 }} />
                      <span style={{ color: "var(--foreground)" }}>
                        {assignedOfficerNames[0].split(" ")[0]}
                      </span>
                      {assignedOfficerNames.length > 1 && (
                        <button
                          onClick={() => setShowAllOfficers(true)}
                          className="text-[11px] font-medium"
                          style={{ color: "#3b82f6" }}>
                          …more
                        </button>
                      )}
                    </div>
                  )}

                  {/* Officers popup modal */}
                  {showAllOfficers && (
                    <div
                      className="fixed inset-0 z-[80] flex items-center justify-center p-4"
                      style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
                      onClick={() => setShowAllOfficers(false)}
                    >
                      <div
                        className="w-full max-w-xs rounded-2xl overflow-hidden shadow-2xl"
                        style={{ background: "var(--card)", border: "1px solid var(--border)" }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="flex items-center justify-between px-4 py-3"
                          style={{ borderBottom: "1px solid var(--border)" }}>
                          <div className="flex items-center gap-2">
                            <Shield size={13} style={{ color: "#10b981" }} />
                            <span className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
                              Assigned Officers ({assignedOfficerNames.length})
                            </span>
                          </div>
                          <button onClick={() => setShowAllOfficers(false)}
                            className="p-1 rounded-lg"
                            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
                            <X size={13} />
                          </button>
                        </div>
                        <div className="px-4 py-3 space-y-2">
                          {assignedOfficerNames.map((name) => (
                            <div key={name} className="flex items-center gap-2.5 px-3 py-2 rounded-lg"
                              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                              <div className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0"
                                style={{ background: "rgba(16,185,129,0.15)", color: "#10b981" }}>
                                {name[0]}
                              </div>
                              <span className="text-[12px] font-medium" style={{ color: "var(--foreground)" }}>{name}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Description — compact */}
                <div className="rounded-lg px-3 py-2.5"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                  <div className="text-[10px] mb-1" style={{ color: "var(--muted-foreground)" }}>Description</div>
                  <p className="text-[12px] leading-relaxed line-clamp-2" style={{ color: "var(--muted-foreground)" }}>
                    {alert.description || "—"}
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* Footer actions */}
          {(alert.status === "active" || alert.status === "dispatched") && (
            <div className="flex items-center gap-2 px-6 py-4 flex-shrink-0"
              style={{ borderTop: "1px solid var(--border)" }}>
              {/* Contact Guardian — curfew involves minors, so a guardian to notify always exists */}
              {alert.type === "curfew" && (
                <button
                  onClick={() => setShowContact(true)}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all"
                  style={{ background: "rgba(16,185,129,0.1)", color: "#10b981", border: "1px solid rgba(16,185,129,0.25)" }}>
                  <MessageSquare size={13} /> Contact guardian
                </button>
              )}

              <div className="flex-1" />

              {alert.status === "active" && (
                <>
                  <button
                    onClick={onDismiss}
                    className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                    style={{ background: "rgba(100,116,139,0.22)", color: "#94a3b8", border: "1px solid rgba(100,116,139,0.45)" }}>
                    <X size={14} /> Dismiss
                  </button>
                  <button
                    onClick={onDispatch}
                    className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                    style={{ background: "rgba(245,158,11,0.22)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.5)" }}>
                    <Radio size={14} />
                    {assignedOfficerNames.length > 0 ? "Reassign officers" : "Dispatch officers"}
                  </button>
                </>
              )}
              {alert.status === "dispatched" && (
                <>
                  <button
                    onClick={onDispatch}
                    className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                    style={{ background: "rgba(59,130,246,0.22)", color: "#3b82f6", border: "1px solid rgba(59,130,246,0.45)" }}>
                    <Radio size={14} /> Reassign officers
                  </button>
                  <button
                    onClick={() => setShowResolveChecklist(true)}
                    className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                    style={{ background: "rgba(16,185,129,0.22)", color: "#10b981", border: "1px solid rgba(16,185,129,0.45)" }}>
                    <CheckCircle size={14} /> Mark resolved
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {showContact && (
        <ContactGuardianModal
          alert={alert}
          violationType={vcfg.label}
          households={households}
          onClose={() => setShowContact(false)}
        />
      )}

      {showResolveChecklist && (
        <ResolveChecklistModal
          alert={alert}
          vcfg={vcfg}
          households={households}
          residents={residents}
          onConfirm={(suspectNames) => {
            setShowResolveChecklist(false);
            onResolve(suspectNames);
          }}
          onClose={() => setShowResolveChecklist(false)}
        />
      )}

      {showSetCandidate && (
        <SetCandidateModal
          alert={alert}
          households={households}
          residents={residents}
          onSave={async (names) => {
            if (onUpdateSuspect) await onUpdateSuspect(names);
            setShowSetCandidate(false);
          }}
          onClose={() => setShowSetCandidate(false)}
        />
      )}
    </>
  );
}
