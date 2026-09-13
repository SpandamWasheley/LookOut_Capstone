import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Bell, Clock, AlertTriangle, Radio, CheckCircle, X, Camera as CameraIcon, Moon, Sun, Sunset, ChevronRight, Search } from "lucide-react";
import { resolveViolationType, violationDisplay } from "./constants/violationTypes";
import { ViolationModal } from "./ViolationModal";
import { DispatchModal } from "./DispatchModal";
import { TypeFilterDropdown } from "./TypeFilterDropdown";
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
    videoUrl: raw.video_url,
    rawVideoUrl: raw.raw_video_url,
    officersAssignedIds: raw.officers_assigned ?? [],
    officersAssignedNames: raw.officers_assigned_names ?? [],
    suspect: raw.suspect,
    notes: raw.notes,
    matchedPersonId: raw.matched_person,
    matchedPersonName: raw.matched_person_name,
    matchConfidence: raw.match_confidence,
  };
}

function mapOfficer(raw) {
  return { id: raw.id, name: raw.name, status: raw.status, location: raw.location, badge: raw.badge };
}

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString("en-PH", { hour: "numeric", minute: "2-digit", hour12: true });
}

// Lightweight fuzzy match: every character of the query must appear in order
// somewhere in the target (gaps allowed), so a typo or partial ID like
// "alt42" or "0042" still finds "ALT-0042" without needing an exact
// substring. An exact substring still wins outright and short-circuits the
// common case. Returns -1 when the query doesn't match at all.
// Collapses hyphens/spaces/underscores so "ALT 0042", "alt-0042" and
// "alt0042" are all treated as the same separator, matching how people
// actually type IDs — punctuation differences shouldn't be the reason a
// search misses.
const normalizeSeparators = (s) => s.replace(/[-_\s]+/g, " ").trim();

function fuzzyScore(query, text) {
  const q = normalizeSeparators(query.toLowerCase());
  const t = normalizeSeparators((text ?? "").toLowerCase());
  if (!q) return 0;
  if (!t) return -1;

  const idx = t.indexOf(q);
  if (idx !== -1) return 1000 - idx;

  let qi = 0, score = 0, gap = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      score += 10 - Math.min(gap, 8); // tighter runs score higher than scattered ones
      if (ti === 0 || /\W/.test(t[ti - 1])) score += 5; // bonus for landing on a word boundary
      gap = 0;
      qi++;
    } else {
      gap++;
    }
  }
  return qi === q.length ? score : -1;
}

// Best fuzzy score for an alert across the fields an operator would actually
// search by — ID first (it's the whole point of this search box), then type,
// description, zone, and suspect.
function alertSearchScore(alert, typeLabel, query) {
  const fields = [alert.id, typeLabel, alert.description, alert.cameraZone, alert.suspect];
  let best = -1;
  for (const f of fields) {
    if (!f) continue;
    const s = fuzzyScore(query, f);
    if (s > best) best = s;
  }
  return best;
}

const statusConfig = {
  active:       { label: "Active",     color: "#ef4444", bg: "rgba(239,68,68,0.1)"   },
  acknowledged: { label: "Dismissed",  color: "#64748b", bg: "rgba(100,116,139,0.1)" },
  dispatched:   { label: "Assigned",   color: "#3b82f6", bg: "rgba(59,130,246,0.1)"  },
  resolved:     { label: "Resolved",   color: "#10b981", bg: "rgba(16,185,129,0.1)"  },
};

const dismissReasons = [
  "False positive — no violation present",
  "Duplicate alert — already handled",
  "Outside barangay jurisdiction",
  "Handled before an officer was assigned",
  "Technical glitch / sensor error",
  "Other",
];

// ── Dismiss modal ─────────────────────────────────────────────────────────────
function DismissModal({ alert, onConfirm, onClose }) {
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const vcfg = violationDisplay(alert.type);
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
              style={{ background: vcfg.bg }}>
              <VIcon size={14} style={{ color: vcfg.color }} />
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
                    className="sr-only"
                  />
                  <span
                    className="w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0"
                    style={{ border: `2px solid ${reason === r ? "#f59e0b" : "var(--muted-foreground)"}` }}
                  >
                    {reason === r && <span className="w-2 h-2 rounded-full" style={{ background: "#f59e0b" }} />}
                  </span>
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
              background: reason ? "rgba(16,185,129,0.15)" : "rgba(100,116,139,0.08)",
              color: reason ? "#10b981" : "var(--muted-foreground)",
              border: `1px solid ${reason ? "rgba(16,185,129,0.4)" : "var(--border)"}`,
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
const THIN_CARD_H = 56;

function AlertCard({ alert, onView, thin = false, extraPad = 0 }) {
  const vcfg = violationDisplay(alert.type);
  const VIcon = vcfg.icon;
  const scfg = statusConfig[alert.status] ?? statusConfig.acknowledged;
  const officerCount = alert.officersAssignedNames.length;

  return (
    <div
      onClick={onView}
      data-alert-card
      className={`group rounded-2xl cursor-pointer transition-all duration-150 overflow-hidden flex flex-shrink-0 ${thin ? "mb-1.5 h-[56px]" : "mb-3 min-h-[56px]"}`}
      style={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        // Compact panel only: grows the card past its base 56px to soak up
        // leftover space that wouldn't otherwise fit another card — inline
        // style wins over the Tailwind h-[56px] class above.
        ...(thin && extraPad > 0 ? { height: THIN_CARD_H + extraPad } : {}),
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = vcfg.border; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border)"; }}
    >
      {/* Left color stripe */}
      <div className="flex-shrink-0 w-1 rounded-l-2xl" style={{ background: vcfg.color }} />
      <div className="flex items-center flex-1 min-w-0 gap-2.5 px-3.5 py-2">
        {/* Icon box */}
        <div className="rounded-lg flex items-center justify-center flex-shrink-0 w-8 h-8"
          style={{ background: vcfg.bg }}>
          <VIcon size={14} style={{ color: vcfg.color }} />
        </div>

        {thin ? (
          <>
            {/* Left content */}
            <div className="flex-1 min-w-0">
              {/* Row 1: title only — the active count in the panel header above
                  already covers what the per-card "Active" dot used to say,
                  and dropping it keeps this row to one line so every card in
                  the list stays the same height regardless of title length
                  (e.g. "Parking Obstruction in Area" vs "Smoking"). */}
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-semibold truncate" style={{ color: "var(--foreground)" }}>
                  {vcfg.label}
                </span>
              </div>
              {/* Row 2: alert ID + reported time + optional officers */}
              <div className="flex items-center gap-2 text-[10px] overflow-hidden" style={{ color: "var(--muted-foreground)" }}>
                <span className="font-medium flex-shrink-0" style={{ fontFamily: "'DM Mono', monospace", color: "var(--foreground)" }}>
                  {alert.id}
                </span>
                <span className="flex items-center gap-1 flex-shrink-0"><Clock size={11} /> Reported {formatTime(alert.timestamp)}</span>
                {officerCount > 0 && (
                  <span className="flex items-center gap-1 flex-shrink-0" style={{ color: "#3b82f6" }}>
                    · <Radio size={10} /> {officerCount} officer{officerCount !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
            </div>

            {/* Right: chevron */}
            <div className="flex items-center gap-2.5 flex-shrink-0">
              <ChevronRight size={16} className="transition-transform duration-150 group-hover:translate-x-0.5"
                style={{ color: "var(--muted-foreground)" }} />
            </div>
          </>
        ) : (
          <>
            {/* Left content */}
            <div className="flex-1 min-w-0">
              {/* Row 1: title + status */}
              <div className="flex items-center gap-2 flex-wrap mb-0.5">
                <span className="text-[13px] font-semibold" style={{ color: "var(--foreground)" }}>
                  {vcfg.label}
                </span>
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                  style={{ background: scfg.bg, color: scfg.color }}>
                  {scfg.label}
                </span>
              </div>
              {/* Row 2: alert ID + time */}
              <div className="flex items-center gap-3 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                <span className="font-medium" style={{ fontFamily: "'DM Mono', monospace", color: "var(--foreground)" }}>
                  {alert.id}
                </span>
                <span className="flex items-center gap-1"><Clock size={9} /> {formatTime(alert.timestamp)}</span>
              </div>
            </div>

            {/* Right: confidence + officer count */}
            <div className="flex flex-col items-end gap-1 flex-shrink-0">
              <span className="text-[11px] font-medium" style={{ color: vcfg.color }}>
                {(alert.confidence * 100).toFixed(0)}% conf
              </span>
              {officerCount > 0 && (
                <span className="flex items-center gap-1 text-[11px] font-medium" style={{ color: "#3b82f6" }}>
                  <Radio size={9} />
                  {`${officerCount} officer${officerCount !== 1 ? "s" : ""}`}
                </span>
              )}
            </div>
          </>
        )}
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
  const allTimeTotal = alerts.length;
  const todayStr = new Date().toDateString();
  const todayAlerts = alerts.filter((a) => new Date(a.timestamp).toDateString() === todayStr);
  const todayTotal = todayAlerts.length;
  const todayResolved = todayAlerts.filter((a) => a.status === "resolved").length;
  const todayDismissed = todayAlerts.filter((a) => a.status === "acknowledged").length;

  const recentAlerts = [...alerts]
    .filter((a) => a.status === "active" || a.status === "dispatched")
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

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
    <div className="flex-1 min-h-0 overflow-hidden flex flex-col" style={{ background: "var(--card)" }}>
      {/* System Status */}
      <div className="px-4 pt-4 pb-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
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
      <div className="px-4 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        {sectionLabel("Today's Violations")}
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: "Total",           value: allTimeTotal,    color: "#a855f7" },
            { label: "Total today",     value: todayTotal,      color: "#f59e0b" },
            { label: "Assigned",        value: dispatchedCount, color: "#3b82f6" },
            { label: "Active",          value: activeCount,     color: "#ef4444" },
            { label: "Resolve today",   value: todayResolved,   color: "#10b981" },
            { label: "Dismissed today", value: todayDismissed,  color: "#64748b" },
          ].map((s) => (
            <div key={s.label} className="rounded-lg p-2.5"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              <div className="text-xl font-bold leading-none" style={{ color: s.color }}>{s.value}</div>
              <div className="text-[10px] mt-1" style={{ color: "var(--muted-foreground)" }}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Recent Alerts — fills remaining height */}
      <div className="px-4 py-4 flex flex-col flex-1 min-h-0">
        {sectionLabel(`Recent Alerts${recentAlerts.length > 0 ? ` · ${recentAlerts.length}` : ""}`)}
        {recentAlerts.length === 0 ? (
          <div className="flex flex-col items-center justify-center flex-1 gap-1.5">
            <Bell size={22} style={{ color: "var(--muted-foreground)", opacity: 0.4 }} />
            <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>No active violations</span>
          </div>
        ) : (
          <div className="scrollbar-visible flex flex-col gap-2 overflow-y-auto flex-1 min-h-0">
            {recentAlerts.slice(0, 5).map((a) => {
              const vcfg = violationDisplay(a.type);
              const scfg = statusConfig[a.status] ?? statusConfig.active;
              return (
                <div key={a.id} className="rounded-lg p-2.5 flex-shrink-0"
                  style={{ background: "var(--secondary)", borderLeft: `3px solid ${vcfg.color}`, border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[12px] font-semibold truncate pr-1" style={{ color: "var(--foreground)" }}>{vcfg.label}</span>
                    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded flex-shrink-0"
                      style={{ background: scfg.bg, color: scfg.color }}>
                      {scfg.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                    <span className="flex items-center gap-1"><Clock size={9} /> {formatTime(a.timestamp)}</span>
                    <span className="ml-auto font-medium" style={{ color: vcfg.color }}>{(a.confidence * 100).toFixed(0)}%</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Shift Info */}
      <ShiftInfo />
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────
export function AlertFeed({ showFilters = false, user }) {
  const [alerts, setAlerts] = useState([]);
  const [officers, setOfficers] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [households, setHouseholds] = useState([]);
  const [residents, setResidents] = useState([]);
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [dispatchingAlert, setDispatchingAlert] = useState(null);
  const [dismissTarget, setDismissTarget] = useState(null);
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState(new Set());
  const [search, setSearch] = useState("");
  const [sortOrder, setSortOrder] = useState("newest"); // "newest" | "oldest"
  const [actionError, setActionError] = useState("");
  const [toast, setToast] = useState(null);
  // Compact (overview) panel: fit as many cards as the space allows; the rest collapse
  // into a "…more" row that opens a modal listing every recent violation.
  const compactListRef = useRef(null);
  const [compactMax, setCompactMax] = useState(Infinity);
  const [compactMaxH, setCompactMaxH] = useState(null);
  const [compactExtraPad, setCompactExtraPad] = useState(0);
  const [showAllRecent, setShowAllRecent] = useState(false);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const ongoing = alerts.filter((a) => a.status === "active" || a.status === "dispatched");
  const filtered = ongoing.filter((a) => {
    // Match on the RESOLVED canonical code, not the raw one — a "thief"-coded
    // alert must still match a "theft" filter selection (the checkboxes
    // below are built from VIOLATION_TYPES' canonical codes).
    if (typeFilter.size > 0 && !typeFilter.has(resolveViolationType({ code: a.type }).code)) return false;
    if (!showFilters) return a.status === "active";
    if (statusFilter === "active")     return a.status === "active";
    if (statusFilter === "dispatched") return a.status === "dispatched";
    return true;
  });
  // Alerts tab only: fuzzy-searchable by ID/type/description/zone/suspect, then
  // sorted purely by the visible Latest/Oldest toggle — no hidden secondary sort,
  // so the order on screen is exactly what the toggle says it is.
  const searched = showFilters && search.trim()
    ? filtered.filter((a) => alertSearchScore(a, violationDisplay(a.type).label, search) >= 0)
    : filtered;
  const visible = showFilters
    ? [...searched].sort((a, b) =>
        sortOrder === "oldest"
          ? new Date(a.timestamp) - new Date(b.timestamp)
          : new Date(b.timestamp) - new Date(a.timestamp)
      )
    : filtered;

  // Responsive fit for the compact overview panel: measure the available height and a
  // real card, then show as many as fit. Re-measures on container/window resize.
  useLayoutEffect(() => {
    if (showFilters) return;
    const el = compactListRef.current;
    if (!el) return;

    const parent = el.parentElement;
    if (!parent) return;

    const recompute = () => {
      const card = el.querySelector("[data-alert-card]");
      if (!card) return;
      const cs = window.getComputedStyle(card);
      const marginH = parseFloat(cs.marginTop || "0") + parseFloat(cs.marginBottom || "0");
      // Use the known base card height rather than measuring card.offsetHeight:
      // this same effect mutates that height (via extraPad below), and a stray
      // ResizeObserver callback can fire before React has painted our last
      // update, reading a stale height and corrupting the count. THIN_CARD_H
      // is authoritative and never changes, so every invocation — no matter
      // when it lands relative to a pending render — computes the same answer.
      const boxH = THIN_CARD_H;
      const cardH = boxH + marginH;
      if (cardH <= 0) return;

      // Measure the available height from the STABLE parent, not from `el` itself:
      // we pin `el`'s maxHeight below, so reading `el`'s own height would feed back
      // and shrink the count on every pass. Distance from the list's top to the
      // bottom of the parent's content box = the room the list actually has.
      const parentH = parent.clientHeight;
      const offsetTop = el.getBoundingClientRect().top - parent.getBoundingClientRect().top;
      const containerH = parentH - offsetTop;
      if (containerH <= 0) return;

      // Show as many cards as fit; overflow collapses into the "…more" row. Pin the
      // list to the exact content height so no gap trails after the last row.
      const fitAll = Math.floor(containerH / cardH);
      if (visible.length <= fitAll) {
        setCompactMax(visible.length);
        setCompactMaxH(visible.length * cardH);
        setCompactExtraPad(0);
      } else {
        const MORE_ROW_H = 34; // the "…more" row
        const shown = Math.max(1, Math.floor((containerH - MORE_ROW_H) / cardH));
        setCompactMax(shown);

        // Rather than stranding the floor()'d remainder as a gap (either
        // trailing below the "…more" row or, worse, between the last card and
        // that row), grow the `shown` cards themselves to soak it up: split
        // the leftover evenly across them as extra height, capped so no card
        // exceeds ~80px total — past that, a "card" starts reading as a fat
        // block rather than a list row. If the cap binds, the residual
        // leftover is left as an (expected, reported) trailing gap.
        const CAP_TOTAL_H = 80;
        const maxExtraPerCard = Math.max(0, CAP_TOTAL_H - boxH);
        const usedBase = shown * cardH + MORE_ROW_H;
        const leftover = Math.max(0, containerH - usedBase);
        let extraPerCard = shown > 0 ? leftover / shown : 0;
        if (extraPerCard > maxExtraPerCard) {
          const strandedPerSize = Math.round(leftover - shown * maxExtraPerCard);
          console.warn(
            `[AlertFeed] compact card padding capped at ${CAP_TOTAL_H}px/card ` +
            `(would need +${Math.round(extraPerCard)}px/card); ${strandedPerSize}px of ` +
            `leftover space remains below the "more" row at this size.`
          );
          extraPerCard = maxExtraPerCard;
        }
        setCompactExtraPad(extraPerCard);
        setCompactMaxH(shown * (cardH + extraPerCard) + MORE_ROW_H);
      }
    };

    recompute();
    // Observe the parent (its size is layout-driven and stable) rather than `el`,
    // whose height we pin — observing `el` would just watch our own writes.
    const ro = new ResizeObserver(recompute);
    ro.observe(parent);
    window.addEventListener("resize", recompute);
    return () => { ro.disconnect(); window.removeEventListener("resize", recompute); };
  }, [showFilters, visible.length]);

  const compactShown = Number.isFinite(compactMax) ? compactMax : visible.length;
  const compactHidden = Math.max(0, visible.length - compactShown);

  // The "at most N-1 selected" cap lives in TypeFilterDropdown (it disables
  // the checkbox), not here — a plain toggle keeps this in one place instead
  // of two copies of the same limit that could drift apart.
  const toggleType = (type) => {
    setTypeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const refresh = async () => {
    // households/residents are Phase-1-retired endpoints (404 now) — only
    // SetCandidateModal/ContactGuardianModal's suspect-tagging still reads
    // them, so degrade those to empty rather than letting a 404 here take
    // down the alert list itself via Promise.all's fail-fast behavior.
    const [alertsRes, officersRes, camerasRes, householdsRes, residentsRes] = await Promise.all([
      getAlerts(), getOfficers(), getCameras(),
      getHouseholds().catch(() => []), getResidents().catch(() => []),
    ]);
    setAlerts((alertsRes.results ?? alertsRes).map(mapAlert));
    setOfficers((officersRes.results ?? officersRes).map(mapOfficer));
    setCameras(camerasRes.results ?? camerasRes);
    setHouseholds(householdsRes.results ?? householdsRes);
    setResidents(residentsRes.results ?? residentsRes);
  };

  useEffect(() => {
    refresh().catch(() => {});
    const id = setInterval(() => refresh().catch(() => {}), 4000);
    return () => clearInterval(id);
  }, []);

  // Keep the open modal's alert in sync with the latest fetch — otherwise
  // saves (candidate, dispatch, etc.) only show up after closing/reopening.
  useEffect(() => {
    if (!selectedAlert) return;
    const updated = alerts.find((a) => a.id === selectedAlert.id);
    if (updated && updated !== selectedAlert) setSelectedAlert(updated);
  }, [alerts, selectedAlert]);

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
    const removing = officerIds.length === 0;
    try {
      await updateAlert(dispatchingAlert.dbId, {
        status: removing ? "active" : "dispatched",
        officers_assigned: officerIds,
      });
      setDispatchingAlert(null);
      await refresh();
      if (!removing) showToast(`${officerIds.length} officer${officerIds.length !== 1 ? "s" : ""} assigned`);
    } catch (err) {
      setActionError(err.message || "Failed to assign officers.");
    }
  };

  const handleUpdateSuspect = async (alertId, names) => {
    const a = alerts.find((x) => x.id === alertId);
    if (!a) return;
    try {
      await updateAlert(a.dbId, { suspect: names ?? "" });
      await refresh();
    } catch (err) {
      setActionError(err.message || "Failed to update candidate.");
    }
  };

  // Resolution itself now happens server-side inside the citation POST (see
  // CitationFormModal / core/views.py CitationViewSet.perform_create) — this
  // just refreshes the list and closes up once that's already succeeded.
  const handleCitationResolved = async () => {
    await refresh();
    showToast("Violation marked resolved");
    setSelectedAlert(null);
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
          officers={officers}
          currentOfficerId={user?.role === "officer" || user?.role === "both" ? user?.officerId : null}
          verifierName={user?.name}
          onClose={() => setSelectedAlert(null)}
          onDismiss={() => setDismissTarget(selectedAlert)}
          onResolved={handleCitationResolved}
          onUpdateSuspect={(names) => handleUpdateSuspect(selectedAlert.id, names)}
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
      <div className="relative h-full flex flex-col min-h-0">
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
          <div ref={compactListRef} className="flex-1 min-h-0 overflow-hidden flex flex-col"
            style={compactMaxH ? { maxHeight: compactMaxH } : undefined}>
            {visible.slice(0, compactShown).map((a) => (
              <AlertCard key={a.id} alert={a} onView={() => setSelectedAlert(a)} thin extraPad={compactExtraPad} />
            ))}
            {compactHidden > 0 && (
              <button
                onClick={() => setShowAllRecent(true)}
                className="h-[34px] flex-shrink-0 flex items-center justify-center gap-1 text-[11px] font-semibold rounded-lg transition-colors"
                style={{ color: "var(--primary)", background: "transparent" }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--secondary)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                {compactHidden} more…
              </button>
            )}
          </div>
        )}

        {showAllRecent && (
          <div
            className="fixed inset-0 z-[60] flex items-center justify-center p-4"
            style={{ background: "rgba(0,0,0,0.8)", backdropFilter: "blur(8px)" }}
            onClick={(e) => { if (e.target === e.currentTarget) setShowAllRecent(false); }}
          >
            <div className="w-full max-w-lg rounded-2xl overflow-hidden shadow-2xl flex flex-col"
              style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "80vh" }}>
              <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
                style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2">
                  <AlertTriangle size={15} style={{ color: "#ef4444" }} />
                  <span className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
                    Recent Violations
                  </span>
                  <span className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                    style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444" }}>
                    {visible.length}
                  </span>
                </div>
                <button onClick={() => setShowAllRecent(false)} className="p-1.5 rounded-lg"
                  style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
                  <X size={14} />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto scrollbar-visible px-4 py-4">
                {visible.map((a) => (
                  <AlertCard
                    key={a.id}
                    alert={a}
                    onView={() => { setShowAllRecent(false); setSelectedAlert(a); }}
                    thin
                  />
                ))}
              </div>
            </div>
          </div>
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
                Potential Violations
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

      {/* Two-column split starts right after header */}
      <div className="flex-1 min-h-0 flex overflow-hidden">

        {/* Left column: filter bar + alert list */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden px-6">
          {/* Filter row */}
          <div className="flex items-center justify-between flex-wrap gap-x-4 gap-y-2 py-3 flex-shrink-0">
            <div className="flex items-center gap-1.5 flex-shrink-0">
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

              {/* Type filter — shared with the Violator Log's, so the two
                  can't drift into different rules/looks again. Checkbox
                  values are canonical codes, matched in `filtered` above via
                  resolveViolationType so a "thief"-coded alert still counts
                  under "Theft". */}
              <div className="ml-1">
                <TypeFilterDropdown
                  selected={typeFilter}
                  onToggle={toggleType}
                  onClear={() => setTypeFilter(new Set())}
                />
              </div>
            </div>

            <div className="flex items-center gap-2 min-w-0">
              <div className="relative w-56 min-w-0">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--muted-foreground)" }} />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search by Alert ID, type, zone…"
                  className="w-full pl-8 pr-3 py-1.5 rounded-lg text-xs outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                />
              </div>

              <div className="flex items-center gap-0.5 p-0.5 rounded-full flex-shrink-0"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                {[{ key: "newest", label: "Latest first" }, { key: "oldest", label: "Oldest first" }].map((o) => {
                  const isActive = sortOrder === o.key;
                  return (
                    <button
                      key={o.key}
                      onClick={() => setSortOrder(o.key)}
                      className="px-2.5 py-1 text-[11px] font-medium rounded-full transition-all whitespace-nowrap"
                      style={{
                        background: isActive ? "var(--card)" : "transparent",
                        color: isActive ? "var(--foreground)" : "var(--muted-foreground)",
                        boxShadow: isActive ? "0 1px 2px rgba(0,0,0,0.08)" : "none",
                      }}
                    >
                      {o.label}
                    </button>
                  );
                })}
              </div>

              <span className="text-[12px] whitespace-nowrap flex-shrink-0" style={{ color: "var(--muted-foreground)" }}>
                {visible.length} record{visible.length !== 1 ? "s" : ""}
              </span>
            </div>
          </div>

          {/* Alert list */}
          <div className="flex-1 min-h-0 overflow-y-auto pb-4">
            {errorBanner}
            {visible.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 gap-2">
                <CheckCircle size={28} style={{ color: "#10b981" }} />
                <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>
                  {ongoing.length === 0
                    ? "No active violations"
                    : search.trim()
                      ? "No violations match your search"
                      : "No violations match this filter"}
                </div>
                <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>
                  {search.trim() ? `Try a different Alert ID or keyword than "${search.trim()}"` : "All zones clear"}
                </div>
              </div>
            ) : (
              visible.map((a) => <AlertCard key={a.id} alert={a} onView={() => setSelectedAlert(a)} />)
            )}
          </div>
        </div>

        {/* Right panel — top border aligns with filter row top */}
        <div className="w-[280px] flex-shrink-0 flex flex-col pt-3 pr-3 pb-3">
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden rounded-2xl"
            style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
            <RightPanel alerts={alerts} cameras={cameras} />
          </div>
        </div>

      </div>

      {modals}
    </div>
  );
}

export default AlertFeed;
