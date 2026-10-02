import { useRef, useEffect, useState } from "react";
import {
  X, Shield, Play, Pause,
  SkipBack, Download, Radio, CheckCircle, AlertTriangle,
  ChevronDown, Loader2, Search, Info,
  Clock, Check, Minus, MapPin, Sparkles,
} from "lucide-react";
import { violationDisplay } from "./constants/violationTypes";
import { getViolationTypes, getBarangays, createCitation, searchViolators } from "./api";

const SUFFIX_OPTIONS = ["", "Jr.", "Sr.", "II", "III", "IV"];

const statusConfig = {
  active:       { label: "Active",     color: "#ef4444", bg: "rgba(239,68,68,0.1)" },
  acknowledged: { label: "Dismissed",  color: "#64748b", bg: "rgba(100,116,139,0.1)" },
  dispatched:   { label: "Assigned",   color: "#3b82f6", bg: "rgba(59,130,246,0.1)" },
  resolved:     { label: "Resolved",   color: "#10b981", bg: "rgba(16,185,129,0.1)" },
};

function formatFull(ts) {
  return new Date(ts).toLocaleString("en-PH", {
    month: "short", day: "numeric", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

// Header stamp: when the violation happened, written the way someone would
// say it out loud. A middot separates the date from the time -- the
// comma-comma form ("Tue, Sep 15, 2026, 12:23 PM") runs the two together and
// the eye has to find the boundary itself.
function formatStamp(ts) {
  const d = new Date(ts);
  const date = d.toLocaleDateString("en-PH", {
    weekday: "short", month: "short", day: "numeric", year: "numeric",
  });
  const time = d.toLocaleTimeString("en-PH", {
    hour: "numeric", minute: "2-digit", hour12: true,
  });
  return { date, time };
}


// ── Recording player ──────────────────────────────────────────────────────────
// Evidence clips have no audio track (frame-only capture, no microphone
// anywhere in this pipeline), so there's no mute/volume control here — it
// would be a dead control implying an audio path that doesn't exist.
export function RecordingPlayer({ alert }) {
  const videoRef = useRef(null);
  const hasRaw = !!alert.rawVideoUrl;
  const hasAnnotated = !!alert.videoUrl;
  const hasVideo = hasRaw || hasAnnotated;
  // RAW by default when it exists; otherwise fall back to whichever exists.
  const [useRaw, setUseRaw] = useState(hasRaw);
  const [playing, setPlaying] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [duration, setDuration] = useState(0);
  // Sized from the actual clip's own dimensions once metadata loads, rather
  // than assumed — annotated clips are scaled to whatever the source's
  // aspect ratio was (ffmpeg -vf scale=1280:-2) and raw clips are a verbatim
  // copy, so neither is guaranteed to be 16:9 for every camera/upload. 16:9
  // is just the pre-metadata default so the box doesn't jump from 0 height.
  const [aspectRatio, setAspectRatio] = useState(16 / 9);

  const src = useRaw && hasRaw ? alert.rawVideoUrl : (hasAnnotated ? alert.videoUrl : alert.rawVideoUrl);

  useEffect(() => {
    setPlaying(false);
    setElapsed(0);
    setDuration(0);
    setAspectRatio(16 / 9);
  }, [src]);

  const fmtSec = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const pct = duration > 0 ? (elapsed / duration) * 100 : 0;

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) { v.play(); setPlaying(true); } else { v.pause(); setPlaying(false); }
  };

  if (!hasVideo) {
    return (
      <div className="rounded-xl overflow-hidden" style={{ background: "#000", border: "1px solid var(--border)" }}>
        <div className="relative w-full" style={{ aspectRatio: 16 / 9 }}>
          <img src={alert.imageUrl} alt="Evidence" className="absolute inset-0 w-full h-full object-cover" />
          <div className="absolute bottom-3 left-3 right-3 text-[13px] text-center py-1.5 rounded-lg"
            style={{ background: "rgba(0,0,0,0.6)", color: "var(--muted-foreground)" }}>
            No evidence clip available for this alert — still image only.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl overflow-hidden" style={{ background: "#000", border: "1px solid var(--border)" }}>
      <div className="relative w-full" style={{ aspectRatio: aspectRatio }}>
        <video
          key={src}
          ref={videoRef}
          src={src}
          playsInline
          className="absolute inset-0 w-full h-full object-contain bg-black"
          onLoadedMetadata={(e) => {
            const v = e.currentTarget;
            setDuration(v.duration || 0);
            if (v.videoWidth && v.videoHeight) setAspectRatio(v.videoWidth / v.videoHeight);
          }}
          onTimeUpdate={(e) => setElapsed(e.currentTarget.currentTime)}
          onEnded={() => setPlaying(false)}
          onClick={togglePlay}
        />
        <div className="absolute top-0 left-0 right-0 px-3 py-2 flex items-center justify-between pointer-events-none"
          style={{ background: "linear-gradient(to bottom, rgba(0,0,0,0.72), transparent)" }}>
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-medium px-1.5 py-0.5 rounded"
              style={{ background: "rgba(239,68,68,0.85)", color: "#fff", fontFamily: "'DM Mono', monospace" }}>
              ● {useRaw && hasRaw ? "RAW" : "ANNOTATED"}
            </span>
            <span className="text-[12px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              {alert.camera}
            </span>
          </div>
          <span className="text-[12px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            {formatFull(alert.timestamp)}
          </span>
        </div>
        {!playing && (
          <button onClick={togglePlay} className="absolute inset-0 flex items-center justify-center group">
            <div className="w-14 h-14 rounded-full flex items-center justify-center transition-all group-hover:scale-110"
              style={{ background: "rgba(245,158,11,0.9)", boxShadow: "0 0 28px rgba(245,158,11,0.35)" }}>
              <Play size={22} color="#0c0f16" fill="#0c0f16" style={{ marginLeft: 2 }} />
            </div>
          </button>
        )}
      </div>
      <div className="px-4 py-3" style={{ background: "var(--sidebar)" }}>
        {/* RAW/ANNOTATED toggle — hidden when only one version exists (older
            alerts, or a raw/annotated cut that failed) so it degrades gracefully. */}
        {hasRaw && hasAnnotated && (
          <div className="flex items-center gap-1.5 mb-3">
            <button
              onClick={() => setUseRaw(true)}
              className="flex-1 text-[13px] font-medium py-1.5 rounded-lg transition-all"
              style={{
                background: useRaw ? "var(--primary)" : "var(--secondary)",
                color: useRaw ? "#0c0f16" : "var(--muted-foreground)",
              }}>
              Raw
            </button>
            <button
              onClick={() => setUseRaw(false)}
              className="flex-1 text-[13px] font-medium py-1.5 rounded-lg transition-all"
              style={{
                background: !useRaw ? "var(--primary)" : "var(--secondary)",
                color: !useRaw ? "#0c0f16" : "var(--muted-foreground)",
              }}>
              Annotated
            </button>
          </div>
        )}
        <div
          className="relative h-1 rounded-full mb-3 cursor-pointer"
          style={{ background: "var(--border)" }}
          onClick={(e) => {
            const v = videoRef.current;
            if (!v || !duration) return;
            const r = e.currentTarget.getBoundingClientRect();
            const t = ((e.clientX - r.left) / r.width) * duration;
            v.currentTime = Math.max(0, Math.min(t, duration));
          }}
        >
          <div className="absolute left-0 top-0 h-full rounded-full transition-all"
            style={{ width: `${pct}%`, background: "var(--primary)" }} />
        </div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <button
              onClick={() => { const v = videoRef.current; if (v) v.currentTime = 0; }}
              className="p-1 rounded transition-colors"
              style={{ color: "var(--muted-foreground)" }}
              onMouseEnter={(e) => (e.currentTarget.style.color = "var(--foreground)")}
              onMouseLeave={(e) => (e.currentTarget.style.color = "var(--muted-foreground)")}
            >
              <SkipBack size={13} />
            </button>
            <button
              onClick={togglePlay}
              className="w-8 h-8 rounded-full flex items-center justify-center"
              style={{ background: "var(--primary)" }}
            >
              {playing
                ? <Pause size={13} color="#0c0f16" fill="#0c0f16" />
                : <Play  size={13} color="#0c0f16" fill="#0c0f16" style={{ marginLeft: 1 }} />}
            </button>
            <span className="text-[13px] tabular-nums"
              style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              {fmtSec(elapsed)} / {fmtSec(duration)}
            </span>
          </div>
          <a
            href={src}
            download
            className="flex items-center gap-1 text-[13px] px-2 py-1 rounded-md"
            style={{ color: "var(--muted-foreground)" }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "var(--foreground)"; e.currentTarget.style.background = "var(--border)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}
          >
            <Download size={11} /> Save clip
          </a>
        </div>
      </div>
    </div>
  );
}

// ── Searchable barangay select ────────────────────────────────────────────────
function BarangaySelect({ options, value, onChange, error }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const rootRef = useRef(null);

  useEffect(() => {
    const onDocClick = (e) => { if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const filtered = options.filter((o) => o.label.toLowerCase().includes(search.toLowerCase()));
  const selectedLabel = options.find((o) => o.value === value)?.label ?? "";

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative w-full px-3 py-2.5 pr-9 rounded-xl text-sm text-left outline-none"
        style={{ background: "var(--secondary)", border: `1px solid ${error ? "#ef4444" : "var(--border)"}`, color: selectedLabel ? "var(--foreground)" : "var(--muted-foreground)" }}
      >
        <span className="block truncate">{selectedLabel || "Select barangay…"}</span>
        {/* Absolutely positioned (not a flex sibling) so this lines up
            pixel-for-pixel with the native <select> chevrons below, which
            use the same right-3/top-1/2/-translate-y-1/2 positioning. */}
        <ChevronDown size={13} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--muted-foreground)" }} />
      </button>
      {open && (
        <div className="absolute z-10 mt-1.5 w-full rounded-xl overflow-hidden shadow-2xl"
          style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
          <div className="p-2" style={{ borderBottom: "1px solid var(--border)" }}>
            <div className="relative">
              <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
              <input
                autoFocus
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search barangay…"
                className="w-full pl-7 pr-2 py-1.5 rounded-lg text-[14px] outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
              />
            </div>
          </div>
          <div className="max-h-48 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <div className="px-3 py-3 text-[14px] text-center" style={{ color: "var(--muted-foreground)" }}>No matches</div>
            ) : filtered.map((o) => (
              <button
                key={o.value}
                type="button"
                onClick={() => { onChange(o.value); setOpen(false); setSearch(""); }}
                className="w-full text-left px-3 py-1.5 text-[14px] transition-colors"
                style={{ color: o.value === value ? "var(--primary)" : "var(--foreground)", background: o.value === value ? "rgba(11,84,113,0.08)" : "transparent" }}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Citation form (Confirm Resolution) ────────────────────────────────────────
function CitationFormModal({ alert, vcfg, officers = [], currentOfficerId, onResolved, onClose }) {
  const [firstName, setFirstName] = useState("");
  const [middleName, setMiddleName] = useState("");
  const [lastName, setLastName] = useState("");
  const [suffix, setSuffix] = useState("");
  const [officerId, setOfficerId] = useState(currentOfficerId ? String(currentOfficerId) : "");
  const [violatorBarangay, setViolatorBarangay] = useState("");
  const [checkedTypeIds, setCheckedTypeIds] = useState(new Set());
  const [notes, setNotes] = useState("");

  // "Did you mean" suggestions from /api/violators/search, debounced off
  // First+Last only (per spec — middle/suffix aren't part of the query).
  const [selectedViolatorId, setSelectedViolatorId] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [searchingViolators, setSearchingViolators] = useState(false);

  const [violationTypes, setViolationTypes] = useState([]);
  const [barangayOptions, setBarangayOptions] = useState([]);
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [fieldErrors, setFieldErrors] = useState({});
  const [formError, setFormError] = useState("");

  const typesSeededRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadingOptions(true);
      setLoadError("");
      try {
        const [typesRes, barangaysRes] = await Promise.all([getViolationTypes(), getBarangays()]);
        if (cancelled) return;
        const types = (typesRes.results ?? typesRes);
        setViolationTypes(types);
        setBarangayOptions((barangaysRes ?? []).map((b) => ({ value: b.value, label: b.label })));
        if (!typesSeededRef.current) {
          typesSeededRef.current = true;
          const detected = types.find((t) => t.code === alert.type);
          if (detected) setCheckedTypeIds(new Set([detected.id]));
        }
      } catch (err) {
        if (!cancelled) setLoadError(err.message || "Failed to load form options.");
      } finally {
        if (!cancelled) setLoadingOptions(false);
      }
    })();
    return () => { cancelled = true; };
  }, [alert.type]);

  // Debounced "did you mean" lookup — skipped once a suggestion has been
  // picked, until the officer edits the name again (see the pickers below).
  useEffect(() => {
    if (selectedViolatorId) return;
    const q = `${firstName} ${lastName}`.trim();
    if (q.length < 3) { setSuggestions([]); return; }
    const timer = setTimeout(async () => {
      setSearchingViolators(true);
      try {
        const results = await searchViolators(q);
        setSuggestions(results ?? []);
      } catch {
        setSuggestions([]);
      } finally {
        setSearchingViolators(false);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [firstName, lastName, selectedViolatorId]);

  const pickSuggestion = (violator) => {
    setFirstName(violator.first_name);
    setMiddleName(violator.middle_name);
    setLastName(violator.last_name);
    setSuffix(violator.suffix);
    setSelectedViolatorId(violator.id);
    setSuggestions([]);
  };

  // Editing the name after a pick means it may no longer refer to the
  // violator that was confirmed, so that link is dropped — the server falls
  // back to its own exact-match-or-create resolution at submit time.
  const editName = (setter) => (value) => {
    setSelectedViolatorId(null);
    setter(value);
  };

  const toggleType = (id) =>
    setCheckedTypeIds((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const valid = firstName.trim() && lastName.trim() && officerId && violatorBarangay && checkedTypeIds.size > 0;

  const handleSubmit = async () => {
    if (!valid) return;
    setSubmitting(true);
    setFieldErrors({});
    setFormError("");
    try {
      await createCitation({
        alert: alert.dbId,
        violator: selectedViolatorId,
        first_name_entered: firstName.trim(),
        middle_name_entered: middleName.trim(),
        last_name_entered: lastName.trim(),
        suffix_entered: suffix,
        officer: Number(officerId),
        barangay_of_violation: "TETUAN",
        violator_barangay: violatorBarangay,
        violations: [...checkedTypeIds],
        notes: notes.trim(),
      });
      onResolved();
    } catch (err) {
      if (err.data && typeof err.data === "object") {
        setFieldErrors(err.data);
      } else {
        setFormError(err.message || "Failed to submit citation.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const VIcon = vcfg.icon;
  const fieldError = (name) => (Array.isArray(fieldErrors[name]) ? fieldErrors[name].join(" ") : fieldErrors[name]);

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
                <span className="text-[13px] font-medium" style={{ color: vcfg.color }}>{vcfg.label}</span>
                <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>· {alert.id}</span>
              </div>
            </div>
          </div>
          {!submitting && (
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={14} />
            </button>
          )}
        </div>

        {/* Form body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4" style={{ minHeight: 0 }}>
          {loadError && (
            <div className="flex items-center gap-2 text-[13px] px-3 py-2 rounded-lg"
              style={{ background: "rgba(239,68,68,0.08)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.2)" }}>
              <AlertTriangle size={12} /> {loadError}
            </div>
          )}
          {formError && (
            <div className="flex items-center gap-2 text-[13px] px-3 py-2 rounded-lg"
              style={{ background: "rgba(239,68,68,0.08)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.2)" }}>
              <AlertTriangle size={12} /> {formError}
            </div>
          )}

          {/* 1. Name of violator */}
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Name of violator <span style={{ color: "#ef4444" }}>*</span>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <input
                  value={firstName}
                  onChange={(e) => editName(setFirstName)(e.target.value)}
                  placeholder="First name"
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: `1px solid ${fieldError("first_name_entered") ? "#ef4444" : "var(--border)"}`, color: "var(--foreground)" }}
                />
                {fieldError("first_name_entered") && (
                  <div className="text-[13px] mt-1" style={{ color: "#ef4444" }}>{fieldError("first_name_entered")}</div>
                )}
              </div>
              <div>
                <input
                  value={middleName}
                  onChange={(e) => editName(setMiddleName)(e.target.value)}
                  placeholder="Middle name"
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: `1px solid ${fieldError("middle_name_entered") ? "#ef4444" : "var(--border)"}`, color: "var(--foreground)" }}
                />
              </div>
              <div>
                <input
                  value={lastName}
                  onChange={(e) => editName(setLastName)(e.target.value)}
                  placeholder="Last name"
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: `1px solid ${fieldError("last_name_entered") ? "#ef4444" : "var(--border)"}`, color: "var(--foreground)" }}
                />
                {fieldError("last_name_entered") && (
                  <div className="text-[13px] mt-1" style={{ color: "#ef4444" }}>{fieldError("last_name_entered")}</div>
                )}
              </div>
              <div className="relative">
                <select
                  value={suffix}
                  onChange={(e) => editName(setSuffix)(e.target.value)}
                  className="w-full appearance-none px-3 py-2.5 pr-9 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: suffix ? "var(--foreground)" : "var(--muted-foreground)" }}
                >
                  {SUFFIX_OPTIONS.map((s) => <option key={s} value={s}>{s || "Suffix"}</option>)}
                </select>
                <ChevronDown size={13} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--muted-foreground)" }} />
              </div>
            </div>

            {/* "Did you mean" suggestions from /api/violators/search */}
            {searchingViolators && (
              <div className="flex items-center gap-1.5 mt-1.5 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
                <Loader2 size={11} className="animate-spin" /> Checking existing violators…
              </div>
            )}
            {!searchingViolators && !selectedViolatorId && suggestions.length > 0 && (
              <div className="mt-1.5 rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
                <div className="px-2.5 py-1 text-[12px] font-semibold uppercase tracking-wide"
                  style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
                  Did you mean?
                </div>
                {suggestions.map((s) => (
                  <button key={s.id} type="button" onClick={() => pickSuggestion(s)}
                    className="w-full flex items-center justify-between px-2.5 py-1.5 text-[14px] text-left transition-colors"
                    style={{ color: "var(--foreground)", background: "var(--card)" }}>
                    <span>{s.full_name}</span>
                    <span style={{ color: "var(--muted-foreground)" }}>
                      {s.citation_count} citation{s.citation_count !== 1 ? "s" : ""}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {selectedViolatorId && (
              <div className="text-[13px] mt-1.5" style={{ color: "#10b981" }}>
                Linked to an existing violator record.
              </div>
            )}
          </div>

          {/* 2. Officer */}
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Officer <span style={{ color: "#ef4444" }}>*</span>
            </label>
            <div className="relative">
              <select
                value={officerId}
                onChange={(e) => setOfficerId(e.target.value)}
                className="w-full appearance-none px-3 py-2.5 pr-9 rounded-xl text-sm outline-none"
                style={{ background: "var(--secondary)", border: `1px solid ${fieldError("officer") ? "#ef4444" : "var(--border)"}`, color: officerId ? "var(--foreground)" : "var(--muted-foreground)" }}
              >
                <option value="">Select officer…</option>
                {officers.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
              <ChevronDown size={13} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--muted-foreground)" }} />
            </div>
            {fieldError("officer") && (
              <div className="text-[13px] mt-1" style={{ color: "#ef4444" }}>{fieldError("officer")}</div>
            )}
          </div>

          {/* 3. Barangay where it occurred — locked to Tetuan, so no chevron:
              there's nothing to open. */}
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Barangay where it occurred
            </label>
            <select disabled value="TETUAN"
              className="w-full appearance-none px-3 py-2.5 rounded-xl text-sm outline-none opacity-70 cursor-not-allowed"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}>
              <option value="TETUAN">Tetuan</option>
            </select>
          </div>

          {/* 4. Violator's barangay */}
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Violator's barangay <span style={{ color: "#ef4444" }}>*</span>
            </label>
            <BarangaySelect options={barangayOptions} value={violatorBarangay} onChange={setViolatorBarangay} error={fieldError("violator_barangay")} />
            {fieldError("violator_barangay") && (
              <div className="text-[13px] mt-1" style={{ color: "#ef4444" }}>{fieldError("violator_barangay")}</div>
            )}
          </div>

          {/* 5. Violations */}
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Violation(s) <span style={{ color: "#ef4444" }}>*</span>
            </label>
            {loadingOptions ? (
              <div className="flex items-center gap-2 py-2 text-[14px]" style={{ color: "var(--muted-foreground)" }}>
                <Loader2 size={13} className="animate-spin" /> Loading violation types…
              </div>
            ) : (
              <div className="space-y-1.5">
                {violationTypes.map((t) => {
                  const checked = checkedTypeIds.has(t.id);
                  return (
                    <button key={t.id} type="button" onClick={() => toggleType(t.id)}
                      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left transition-all"
                      style={{ background: checked ? "rgba(16,185,129,0.07)" : "var(--secondary)", border: `1px solid ${checked ? "rgba(16,185,129,0.3)" : "var(--border)"}` }}>
                      <div className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center"
                        style={{ background: checked ? "#10b981" : "transparent", border: `1.5px solid ${checked ? "#10b981" : "var(--muted-foreground)"}` }}>
                        {checked && <CheckCircle size={10} color="#fff" strokeWidth={3} />}
                      </div>
                      <span className="text-[14px] font-medium" style={{ color: "var(--foreground)" }}>{t.label}</span>
                    </button>
                  );
                })}
              </div>
            )}
            {fieldError("violations") && (
              <div className="text-[13px] mt-1" style={{ color: "#ef4444" }}>{fieldError("violations")}</div>
            )}
          </div>

          {/* 6. Notes */}
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Notes <span className="font-normal" style={{ color: "var(--muted-foreground)" }}>(optional)</span>
            </label>
            <textarea
              rows={3}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Additional details…"
              className="w-full px-3 py-2.5 rounded-xl text-sm resize-none outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
            />
          </div>
        </div>

        {/* Footer */}
        <div className="px-5 py-4 flex items-center justify-end gap-2 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          {!submitting && (
            <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Cancel
            </button>
          )}
          <button disabled={!valid || submitting} onClick={handleSubmit}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
            style={{
              background: valid ? "#10b981" : "rgba(16,185,129,0.2)",
              color: valid ? "#fff" : "rgba(16,185,129,0.5)",
              cursor: valid && !submitting ? "pointer" : "not-allowed",
            }}>
            {submitting ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle size={13} />}
            {submitting ? "Submitting…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Quiet reference-detail card ───────────────────────────────────────────────
// Used for the redesigned modal's right-column metadata (Camera, Confidence,
// What was detected, Detected object, Assigned officers) — an 11px muted
// label above a 13px value, on a quiet surface so these read as reference
// details rather than competing with the video or footer actions.
// v3 2's two visible levels, by severity.
//
// Deliberately NOT the violation type's colour, which is what this used to use:
// a smoking alert and a holdup alert each have their own hue, so "Possible" and
// "Likely" rendered identically within a type and the level carried no visual
// weight at all. Since the level is now the main thing a tanod reads, it needs
// to be the thing that changes colour.
//
// Amber for Possible, red for Likely -- the same two the spec uses, and the
// pairing a barangay officer already reads correctly from traffic lights.
const LEVEL_COLORS = {
  Likely: "#dc2626",
  Possible: "#f59e0b",
  "Not shown": "var(--muted-foreground)",
};

// The checker's JSON field names, in words an officer reads.
//
// Kept on the client rather than sent from the server because these are pure
// presentation: the field names are the model's contract and must not drift,
// while the wording here can be improved freely without touching a prompt.
const AI_FIELD_LABELS = {
  group_appears_to_be_drinking_together: "Drinking together",
  table_chairs_or_seating_visible: "Seating",
  drinking_items_visible: "Drinks set out",
  smoking_item_visible: "Smoking item",
  object_pointed_at_a_person: "Pointed at a person",
  victim_response_visible: "Victim reacting",
  appears_to_be_a_holdup: "Looks like a holdup",
};

// The two choice fields, and which of their answers mean "ordinary activity".
// Those answers are what cut a score to a quarter, so they are the one thing on
// this card that must be impossible to miss.
const AI_ORDINARY = {
  scene_type: "other_activity",
  hand_to_mouth_activity: ["drinking", "eating", "phone"],
};

function aiReadsAsOrdinary(answers) {
  return Object.entries(AI_ORDINARY).some(([field, ordinary]) => {
    const value = answers?.[field];
    return Array.isArray(ordinary) ? ordinary.includes(value) : value === ordinary;
  });
}

// What the AI context card shows. Separated from the markup so the rules are
// readable on their own: which answers are booleans, which are choices, and
// which are not worth a chip at all.
function aiChips(answers) {
  const chips = [];
  for (const [field, value] of Object.entries(answers ?? {})) {
    const label = AI_FIELD_LABELS[field];
    if (label) {
      chips.push({ key: field, label, yes: value === true });
      continue;
    }
    // A choice field. "unclear" is skipped: it is the model declining to
    // answer, and a chip reading "Scene: unclear" is noise on a card that is
    // already dense.
    if (field in AI_ORDINARY && value && value !== "unclear") {
      chips.push({
        key: field,
        label: `${field === "scene_type" ? "Scene" : "Hand to mouth"}: ` +
               String(value).replace(/_/g, " "),
        plain: true,
      });
    }
  }
  return chips;
}

export function QuietCard({ label, value, mono, valueColor, tooltip,
                           tooltipAlign = "left", tooltipSpan = "auto" }) {
  // A fixed-width tooltip overflows a narrow card. These sit in the 2fr side of
  // a 3fr/2fr split and then again in a 2-column grid -- roughly 20% of the
  // modal, about 120px -- so a 208px tooltip hangs ~90px outside the card and
  // the panel's overflow-y-auto clips it.
  //
  // `tooltipSpan="row"` sizes it to the two-card row instead: 200% of this card
  // plus the gap-2 between them. The percentage is why the tooltip has to be
  // positioned against the CARD rather than against the little icon wrapper it
  // used to live in -- 200% of a 10px icon is 20px, not a row.
  const widthClass = tooltipSpan === "row"
    ? "w-[calc(200%+0.5rem)]"
    : "w-52 max-w-[min(13rem,60vw)]";

  // Where the tooltip hangs from. Edge-anchoring (left-0 / right-0) put the
  // box against one card's edge, which on a ~120px card meant it stuck out and
  // the panel's overflow clipped it.
  //
  // For a row-width tooltip, centre it on the ROW instead. Each card's inner
  // edge plus half the gap IS the row's midpoint, so hanging the box there and
  // pulling it back by half its own width lands it exactly over the pair --
  // symmetrical, and with nothing protruding on either side to be cut.
  const positionClass = tooltipSpan === "row"
    ? (tooltipAlign === "right"
        ? "right-[calc(100%+0.25rem)] translate-x-1/2"
        : "left-[calc(100%+0.25rem)] -translate-x-1/2")
    : (tooltipAlign === "right" ? "right-0" : "left-0");
  return (
    // `relative group` on the card, not the icon: the card is the positioning
    // context the tooltip's width and edge-anchoring are measured against, and
    // hovering anywhere on the card is a larger, easier target than a 10px
    // glyph.
    <div className="rounded-lg px-3 py-2.5 min-w-0 relative group"
      style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-1">
        <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{label}</div>
        {tooltip && (
          <Info size={10} style={{ color: "var(--muted-foreground)", cursor: "pointer" }} />
        )}
      </div>
      <div className={`text-[15px] font-medium mt-0.5 ${mono ? "truncate" : "break-words"}`}
        style={{ color: valueColor || "var(--foreground)", fontFamily: mono ? "'DM Mono', monospace" : undefined }}>
        {value}
      </div>
      {tooltip && (
        <div className={`absolute bottom-full mb-2 ${widthClass} rounded-xl px-3 py-2.5 text-[13px] leading-relaxed pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity z-50 shadow-xl ${positionClass}`}
          style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--muted-foreground)" }}>
          {tooltip}
        </div>
      )}
    </div>
  );
}

// ── Main modal ─────────────────────────────────────────────────────────────────
export function ViolationModal({
  alert, assignedOfficerNames, officers = [], currentOfficerId,
  onDismiss, onDispatch, onResolved, onClose,
  userRole,
}) {
  // Resolving closes a violation and is what a citation is filed against, so
  // it belongs to whoever actually attended the scene. A dispatcher assigns
  // officers and dismisses false alarms; they do not attend, so they are not
  // in a position to say an incident was dealt with.
  //
  // "both" (Officer & Dispatcher) keeps it -- that role IS an officer.
  // An explicit allowlist, not "anyone who is not a dispatcher".
  //
  // The old form failed OPEN: RecordsPage rendered this modal without passing
  // userRole, so the prop was undefined, `undefined !== "dispatcher"` was true,
  // and a dispatcher got the Mark resolved button on the Records page -- the
  // exact thing the check existed to prevent. A missing prop must hide a
  // privileged action, never reveal one.
  const canResolve = ["admin", "officer", "both"].includes(userRole);
  // Icon + color identity from violationTypes.js (same source the rest of
  // the app's chips use). It's deliberately scoped to smoking/drinking/
  // parking/theft, so any other (old) type falls through to
  // violationDisplay's own humanized fallback — never the raw db code, and
  // never (as watch_thief.py's now-fixed code split used to cause)
  // something as opaque as "thief".
  const vcfg = violationDisplay(alert.type);
  const scfg = statusConfig[alert.status] ?? statusConfig.acknowledged;
  const VIcon = vcfg.icon;
  const [showResolveChecklist, setShowResolveChecklist] = useState(false);
  const [showAllOfficers, setShowAllOfficers] = useState(false);

  // Assigned officers card — paired with "Detected object".
  const officersCard = (
    <div className="rounded-lg px-3 py-2.5 min-w-0"
      style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
      <div className="text-[13px] mb-1" style={{ color: "var(--muted-foreground)" }}>
        Assigned officers {assignedOfficerNames.length > 0 && `(${assignedOfficerNames.length})`}
      </div>
      {assignedOfficerNames.length === 0 ? (
        <div className="text-[15px]" style={{ color: "var(--muted-foreground)" }}>None assigned</div>
      ) : (
        <div className="flex items-center gap-1.5 text-[15px]">
          <Shield size={10} style={{ color: "#10b981", flexShrink: 0 }} />
          <span className="truncate" style={{ color: "var(--foreground)" }}>
            {assignedOfficerNames[0].split(" ")[0]}
          </span>
          {assignedOfficerNames.length > 1 && (
            <button
              onClick={() => setShowAllOfficers(true)}
              className="text-[13px] font-medium flex-shrink-0"
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
                  <div className="w-6 h-6 rounded-full flex items-center justify-center text-[12px] font-bold flex-shrink-0"
                    style={{ background: "rgba(16,185,129,0.15)", color: "#10b981" }}>
                    {name[0]}
                  </div>
                  <span className="text-[14px] font-medium" style={{ color: "var(--foreground)" }}>{name}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
        style={{ background: "rgba(0,0,0,0.8)", backdropFilter: "blur(8px)" }}
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      >
        <div
          className="w-full max-w-6xl rounded-2xl overflow-hidden shadow-2xl flex flex-col"
          style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}
        >
          {/* Header — camera name, timestamp and alert ID collapse into one
              muted metadata line instead of three separate chips. */}
          <div className="flex items-center justify-between px-6 py-4 flex-shrink-0"
            style={{ borderBottom: "1px solid var(--border)" }}>
            <div className="flex items-center gap-3">
              <VIcon size={22} style={{ color: vcfg.color }} />
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-[17px] font-semibold" style={{ color: "var(--foreground)" }}>{vcfg.label}</span>
                  <span className="text-[13px] font-medium px-2 py-0.5 rounded-full"
                    style={{ background: scfg.bg, color: scfg.color }}>
                    {scfg.label}
                  </span>
                </div>
                <div className="mt-1 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
                  <span style={{ fontFamily: "'DM Mono', monospace" }}>{alert.id}</span>
                  {" · "}{alert.cameraZone}
                </div>
              </div>
            </div>

            {/* WHEN and WHERE, right-aligned. Both answer "where do I go and
                was this just now?", which is what an officer reads first --
                and neither competes with the violation type on the left. */}
            <div className="flex items-start gap-3 flex-shrink-0">
              <div className="text-right min-w-0">
                <div className="flex items-center justify-end gap-2">
                  <Clock size={17} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                  <span className="text-[20px] leading-tight" style={{ color: "var(--foreground)" }}>
                    <span style={{ fontWeight: 500 }}>{formatStamp(alert.timestamp).date}</span>
                    <span style={{ color: "var(--muted-foreground)", margin: "0 7px" }}>·</span>
                    <span style={{ fontWeight: 700 }}>{formatStamp(alert.timestamp).time}</span>
                  </span>
                </div>
                {/* The camera's own address. Absent until someone types one in
                    Live Feeds, so the line is omitted rather than showing an
                    empty pin that reads like missing data. */}
                {alert.cameraAddress ? (
                  <div className="mt-1 flex items-center justify-end gap-1.5">
                    <MapPin size={14} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                    <span className="text-[14px] break-words" style={{ color: "var(--muted-foreground)" }}>
                      {alert.cameraAddress}
                    </span>
                  </div>
                ) : null}
              </div>
              <button onClick={onClose} className="p-2 rounded-lg flex-shrink-0 self-start"
                style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
                <X size={15} />
              </button>
            </div>
          </div>

          {/* Body — video left (60%), stacked reference cards right (40%);
              this one region scrolls if content overflows a shorter screen. */}
          <div className="overflow-y-auto flex-1 p-6">
            <div className="grid grid-cols-[3fr_2fr] gap-5" style={{ alignItems: "start" }}>
              {/* Left: video, scales with the column */}
              <div className="min-w-0">
                <RecordingPlayer alert={alert} />
              </div>

              {/* Right: stacked reference-detail cards */}
              <div className="flex flex-col gap-3 min-w-0">
                    /* Camera on its own row — monospace camera codes (e.g.
                       CAM-DRINKING-TEST) truncate badly at half width — then
                       the two confidences side by side.

                       They are SEPARATE cards on purpose. One number used to
                       carry both meanings, and they answer different questions:
                       "how sure is the model that this is a bottle?" is not
                       "how likely is it that this is a drinking violation?".
                       A crisp bottle detection on a man walking home is high on
                       the first and low on the second, and showing a single
                       figure hid exactly that gap. */
                    <>
                      <QuietCard label="Camera" value={alert.camera} mono />
                      {/* Status and Object detection, side by side.
                          Separate on purpose: they answer different questions
                          and routinely disagree. "How sure is the detector that
                          this is a bottle?" is not "how likely is it that this
                          is a drinking violation?" -- a crisp bottle detection
                          on a man walking home is high on the right and low on
                          the left, and one merged figure hid exactly that gap. */}
                      <div className="grid grid-cols-2 gap-2 min-w-0">
                        <QuietCard
                          label="Status"
                          value={alert.levelLabel || "Possible"}
                          valueColor={LEVEL_COLORS[alert.levelLabel] || vcfg.color}
                          tooltipSpan="row"
                          tooltip={
                            <>
                              <div className="font-semibold mb-1" style={{ color: "var(--foreground)" }}>Status</div>
                              Shows how strongly it points to a violation. It is based only on
                              what the system detected (such as objects), not on the AI.
                              <div className="mt-1.5" style={{ opacity: 0.85 }}>
                                Possible — listed and notified.<br />
                                Likely — full alert with video evidence.
                              </div>
                            </>
                          }
                        />
                        <QuietCard
                          label="Object detection"
                          value={
                            alert.objectConfidence != null
                              ? `${(alert.objectConfidence * 100).toFixed(0)}%`
                              : "—"
                          }
                          tooltipAlign="right"
                          tooltipSpan="row"
                          tooltip={
                            <>
                              <div className="font-semibold mb-1" style={{ color: "var(--foreground)" }}>Object confidence</div>
                              How confident the YOLOv8 model detected the respective object of
                              the violation.
                            </>
                          }
                        />
                      </div>
                      {alert.checklist && (
                        <div className="rounded-lg px-3 py-2.5 min-w-0"
                          style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                          <div className="text-[13px] mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                            Evidence found
                          </div>
                          {alert.checklist.found?.map((line) => (
                            <div key={line} className="flex items-start gap-1.5 text-[14px] leading-snug mt-0.5"
                              style={{ color: "var(--foreground)" }}>
                              <Check size={13} className="flex-shrink-0 mt-0.5" style={{ color: vcfg.color }} />
                              <span className="break-words">{line}</span>
                            </div>
                          ))}
                          {alert.vlmReason && (
                            /* The checker's own sentence. Marked as
                               AI-generated and set in italics because a
                               reviewer must be able to tell a model's words
                               from a dispatcher's note at a glance --
                               especially when the sentence argues AGAINST the
                               alert it is attached to. */
                            <div className="mt-2 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
                              <div className="text-[13px] mb-1" style={{ color: "var(--muted-foreground)" }}>
                                AI checker said
                              </div>
                              <div className="text-[14px] leading-snug italic break-words"
                                style={{ color: "var(--muted-foreground)" }}>
                                &ldquo;{alert.vlmReason}&rdquo;
                              </div>
                            </div>
                          )}
                          {alert.checklist.reduced_by?.length > 0 && (
                            /* Shown because it explains why an alert the
                               indicators would have raised was held back --
                               the single most useful line when it fires. */
                            <div className="mt-2 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
                              <div className="text-[13px] mb-1" style={{ color: "var(--muted-foreground)" }}>
                                Score reduced by
                              </div>
                              {alert.checklist.reduced_by.map((line) => (
                                <div key={line} className="flex items-start gap-1.5 text-[14px] leading-snug"
                                  style={{ color: "var(--muted-foreground)" }}>
                                  <Minus size={13} className="flex-shrink-0 mt-0.5" />
                                  <span className="break-words">{line}</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </>

                {/* What the checker made of the scene.
                    Shown INSTEAD of the raw description when there is a reading
                    to show: the description is the detector restating its own
                    trigger ("knife on person #2, present 3s"), which tells a
                    reviewer nothing they cannot see in the video. The checker's
                    sentence is the only line on this card that describes the
                    situation rather than the detection. */}
                {alert.vlmReason ? (
                  <div className="rounded-lg px-3 py-2.5 min-w-0"
                    style={{ background: "var(--secondary)", border: "1px dashed var(--border)" }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 text-[13px]"
                        style={{ color: "var(--muted-foreground)" }}>
                        <Sparkles size={13} />
                        AI context
                      </div>
                      {/* Stated every time, not only when it is wrong. A model's
                          sentence reads as authoritative precisely because it is
                          fluent, and an officer acting on it deserves to know
                          which lines a machine wrote. */}
                      <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                        AI-generated · may be wrong
                      </div>
                    </div>

                    {aiReadsAsOrdinary(alert.ai?.cues) && (
                      <div className="mt-2 inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-[13px] font-medium"
                        style={{ background: "rgba(245,158,11,0.15)", color: "#b45309" }}>
                        <AlertTriangle size={12} /> May be ordinary activity
                      </div>
                    )}

                    <div className="mt-2 text-[15px] leading-snug italic break-words"
                      style={{ color: "var(--foreground)" }}>
                      &ldquo;{alert.vlmReason}&rdquo;
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]"
                      style={{ color: "var(--muted-foreground)" }}>
                      {aiChips(alert.ai?.cues).map((chip) => (
                        <span key={chip.key} className="inline-flex items-center gap-1">
                          {chip.plain ? null : chip.yes
                            ? <Check size={12} style={{ color: vcfg.color }} />
                            : <X size={12} />}
                          {chip.label}
                        </span>
                      ))}
                      {alert.ai?.tier && <span>Confidence: {alert.ai.tier}</span>}
                    </div>
                  </div>
                ) : (
                  /* No reading to show -- the checker is off, or it failed and
                     the alert published on the geometry alone. Fall back to the
                     detector's own description rather than an empty card. */
                  <QuietCard label="What was detected" value={alert.description || "—"} />
                )}

                {/* Detected object (the model's class label — NOT a resident
                    match) paired with Assigned officers. */}
                <div className="grid grid-cols-2 gap-3">
                  <QuietCard label="Detected object" value={alert.suspect || "—"} />
                  {officersCard}
                </div>
              </div>
            </div>

            {/* Dismissal reason — full-width block below the cards */}
            {alert.status === "acknowledged" && (
              <div className="flex items-start gap-2.5 w-full rounded-lg px-4 py-3"
                style={{ border: "1px solid rgba(239,68,68,0.25)", background: "rgba(239,68,68,0.06)" }}>
                <X size={15} style={{ color: "#ef4444", flexShrink: 0, marginTop: 1 }} />
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] font-semibold uppercase tracking-wider mb-1" style={{ color: "#ef4444" }}>
                    Dismissal reason
                  </div>
                  <p className="text-[14px] leading-relaxed" style={{ color: "var(--foreground)" }}>
                    {alert.notes || "No reason provided."}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Footer actions */}
          {(alert.status === "active" || alert.status === "dispatched") && (
            <div className="flex items-center gap-2 px-6 py-4 flex-shrink-0"
              style={{ borderTop: "1px solid var(--border)" }}>
              <div className="flex-1" />

              {alert.status === "active" && (
                <>
                  <button
                    onClick={onDismiss}
                    className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                    style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.4)" }}>
                    <X size={14} /> Dismiss
                  </button>
                  <button
                    onClick={onDispatch}
                    className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                    style={{ background: "rgba(245,158,11,0.22)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.5)" }}>
                    <Radio size={14} />
                    {assignedOfficerNames.length > 0 ? "Reassign officers" : "Assign officers"}
                  </button>
                </>
              )}
              {alert.status === "dispatched" && (
                <>
                  <button
                    onClick={onDismiss}
                    className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                    style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.4)" }}>
                    <X size={14} /> Dismiss
                  </button>
                  <button
                    onClick={onDispatch}
                    className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                    style={{ background: "rgba(59,130,246,0.22)", color: "#3b82f6", border: "1px solid rgba(59,130,246,0.45)" }}>
                    <Radio size={14} /> Reassign officers
                  </button>
                  {canResolve && (
                    <button
                      onClick={() => setShowResolveChecklist(true)}
                      className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                      style={{ background: "rgba(16,185,129,0.22)", color: "#10b981", border: "1px solid rgba(16,185,129,0.45)" }}>
                      <CheckCircle size={14} /> Mark resolved
                    </button>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {showResolveChecklist && (
        <CitationFormModal
          alert={alert}
          vcfg={vcfg}
          officers={officers}
          currentOfficerId={currentOfficerId}
          onResolved={() => {
            setShowResolveChecklist(false);
            onResolved();
          }}
          onClose={() => setShowResolveChecklist(false)}
        />
      )}
    </>
  );
}
