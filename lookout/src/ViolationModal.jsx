import { useRef, useEffect, useState } from "react";
import {
  X, Shield, Play, Pause,
  SkipBack, Download, Radio, CheckCircle, AlertTriangle,
  ChevronDown, Loader2, Search, Info,
  Clock, ListChecks, Check, Minus, MapPin, Sparkles, RotateCcw,
} from "lucide-react";
import { violationDisplay } from "./constants/violationTypes";
import { levelColor } from "./alertModel";
import { useTestingTools } from "./useTestingTools";
import { getViolationTypes, getBarangays, createCitation, searchViolators } from "./api";

const SUFFIX_OPTIONS = ["", "Jr.", "Sr.", "II", "III", "IV"];

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
export function RecordingPlayer({ alert, timeline = false }) {
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
          {timeline && <TimelineButton alert={alert} />}
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
        {timeline && <TimelineButton alert={alert} />}
        <div className="absolute top-0 left-0 right-0 pl-3 pr-12 py-2 flex items-center justify-between pointer-events-none"
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

// ── Click-only info tooltip ─────────────────────────────────────────────────────
// Opens ONLY when the (i) itself is clicked (never on hover). Solid background; closes on a click
// outside, on Escape, or on a second click of the (i).
function InfoTip({ children, align = "left" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);
  return (
    <span ref={ref} className="relative inline-flex align-middle">
      <button type="button" aria-label="More information" aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        className="inline-flex items-center justify-center rounded-full"
        style={{ color: open ? "var(--foreground)" : "var(--muted-foreground)", cursor: "pointer" }}>
        <Info size={13} />
      </button>
      {open && (
        <div role="tooltip"
          className={`absolute top-full mt-2 z-[70] w-72 max-w-[80vw] rounded-xl px-3.5 py-3 text-[13px] leading-relaxed shadow-2xl ${align === "right" ? "right-0" : "left-0"}`}
          style={{ background: "var(--card)", border: "1px solid var(--foreground)", color: "var(--foreground)", opacity: 1 }}>
          {children}
        </div>
      )}
    </span>
  );
}

// ── Quiet reference-detail card ───────────────────────────────────────────────
// An 13px muted label above a 15px value, on a quiet surface, so these read as reference details
// rather than competing with the video or footer actions. `tooltip` opens on click of the (i).
export function QuietCard({ label, value, mono, valueColor, tooltip, tooltipAlign = "left" }) {
  return (
    <div className="rounded-lg px-3 py-2.5 min-w-0"
      style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-1.5">
        <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{label}</div>
        {tooltip && <InfoTip align={tooltipAlign}>{tooltip}</InfoTip>}
      </div>
      <div className={`text-[14px] font-medium mt-0.5 ${mono ? "truncate" : "break-words"}`}
        style={{ color: valueColor || "var(--foreground)", fontFamily: mono ? "'DM Mono', monospace" : undefined }}>
        {value}
      </div>
    </div>
  );
}

// ── Alert detail cards (scoring spec v6.2) ─────────────────────────────────────
//   Status                 solid border  - the official status only; the evidence is behind Details
//   Object confidence      solid border  - how sure the YOLOv8 model was about the object
//   AI context             dashed border - badge + what the AI saw (AI-generated, may be wrong)
//   Status with AI context dashed border - a SUGGESTION; the official status never changes
// The status is never shown as a number: a score reads like a percentage, which it is not.

const STATUS_TOOLTIP = (
  <>
    <div className="font-semibold mb-1">Status</div>
    Shows how strongly the detected evidence points to a violation. It&rsquo;s based only on
    what the system detected (objects, movement, duration, and time), not on the AI.
    <div className="mt-1.5">
      <b>Monitoring:</b> An object linked to a violation was detected. Watch the scene.<br />
      <b>Possible:</b> Some signs of a violation, but not enough to be sure. Review the alert before acting.<br />
      <b>Likely:</b> Strong evidence of a violation. Review and respond.
    </div>
  </>
);

const SUGGESTED_TOOLTIP = (
  <>
    <div className="font-semibold mb-1">Status with AI context</div>
    What the status would be if the AI&rsquo;s view of the scene were taken into account.
    This is only a suggestion. The official status does not change.
    <div className="mt-1.5">
      If the AI is confident the scene is a violation, it may suggest one step higher.
      If it is confident the scene is ordinary activity (such as vending, selling, or
      eating), it may suggest one step lower. Review the clip to decide.
    </div>
  </>
);

const OBJECT_TOOLTIP = "How certain the YOLOv8 model detected the respective object of the violation.";

function InfoCard({ label, tooltip, tooltipAlign, dashed, icon, aside, children, className = "" }) {
  return (
    <div className={`rounded-lg px-3 py-2 min-w-0 h-full ${className}`}
      style={{ background: "var(--secondary)", border: `1px ${dashed ? "dashed" : "solid"} var(--border)` }}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
          {icon}
          {label}
          {tooltip && <InfoTip align={tooltipAlign}>{tooltip}</InfoTip>}
        </div>
        {aside && <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--muted-foreground)" }}>{aside}</div>}
      </div>
      {children}
    </div>
  );
}

// Tinted pill for a status: Monitoring grey, Possible amber, Likely red.
const PILL = {
  Monitoring: { color: "#64748b", bg: "rgba(100,116,139,0.16)" },
  Possible:   { color: "#d97706", bg: "rgba(245,158,11,0.16)" },
  Likely:     { color: "#dc2626", bg: "rgba(220,38,38,0.12)" },
};
function LevelPill({ label }) {
  const st = PILL[label] ?? { color: "var(--muted-foreground)", bg: "rgba(100,116,139,0.16)" };
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[12px] font-semibold leading-none"
      style={{ background: st.bg, color: st.color }}>{label || "—"}</span>
  );
}

// Small icon in a card's top-right corner that opens a popover (testing tools only). Click outside or Esc closes it.
function CardDetails({ title, children }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey, true); };
  }, [open]);
  return (
    <span ref={ref} className="relative inline-flex">
      <button type="button" title={title} aria-label={title} aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        className="inline-flex items-center justify-center rounded"
        style={{ color: open ? "var(--foreground)" : "var(--muted-foreground)", cursor: "pointer" }}>
        <ListChecks size={14} />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-2 z-[70] w-72 max-w-[80vw] max-h-[50vh] overflow-y-auto rounded-xl px-3 py-2.5 shadow-2xl text-left"
          style={{ background: "var(--card)", border: "1px solid var(--foreground)", color: "var(--foreground)" }}>
          <div className="text-[12px] font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>{title}</div>
          {children}
        </div>
      )}
    </span>
  );
}

export function StatusCard({ alert, testingTools = false }) {
  const label = alert.levelLabel || "—";
  const color = levelColor(label);
  const found = alert.checklist?.found ?? [];
  const adjusted = alert.checklist?.adjusted_by ?? alert.checklist?.reduced_by ?? [];
  const tag = alert.checklist?.tag;
  const hasDetails = found.length > 0 || adjusted.length > 0 || !!tag;
  const details = testingTools && hasDetails && (
    <CardDetails title="Evidence found">
      {tag ? <div className="mb-1 text-[12px] italic" style={{ color: "var(--muted-foreground)" }}>{tag}</div> : null}
      {found.map((line) => (
        <div key={line} className="flex items-start gap-1.5 text-[13px] leading-snug mt-0.5" style={{ color: "var(--foreground)" }}>
          <Check size={12} className="flex-shrink-0 mt-0.5" style={{ color }} />
          <span className="break-words">{line}</span>
        </div>
      ))}
      {adjusted.map((line) => (
        <div key={line} className="flex items-start gap-1.5 text-[12px] leading-snug mt-0.5" style={{ color: "var(--muted-foreground)" }}>
          <Minus size={12} className="flex-shrink-0 mt-0.5" />
          <span className="break-words">Adjusted by: {line}</span>
        </div>
      ))}
    </CardDetails>
  );
  return (
    <InfoCard label="Status" tooltip={STATUS_TOOLTIP} aside={details || undefined}>
      <div className="mt-1.5"><LevelPill label={label} /></div>
    </InfoCard>
  );
}

export function ObjectConfidenceCard({ alert }) {
  // No object detected (puff-only smoking is hand movement alone): nothing to be confident about.
  const none = alert.objectConfidence == null || alert.cues?.puff_only;
  const vcolor = violationDisplay(alert.type).color;
  return (
    <InfoCard label="Object confidence" tooltip={OBJECT_TOOLTIP} tooltipAlign="right">
      <div className="text-[14px] font-semibold mt-1.5 leading-[22px]" style={{ color: none ? "var(--foreground)" : vcolor }}>
        {none ? "—" : `${Math.round(alert.objectConfidence * 100)}% conf`}
      </div>
    </InfoCard>
  );
}

const AI_BADGE_STYLE = {
  supports:    { color: "#047857", bg: "rgba(16,185,129,0.14)", icon: Check },
  ordinary:    { color: "#b45309", bg: "rgba(245,158,11,0.16)", icon: AlertTriangle },
  unclear:     { color: "var(--muted-foreground)", bg: "rgba(100,116,139,0.14)", icon: Info },
  unavailable: { color: "var(--muted-foreground)", bg: "rgba(100,116,139,0.14)", icon: Info },
};

function AIContextCard({ ai, testingTools = false }) {
  const state = ai?.state ?? "unavailable";
  const badge = ai?.badge ?? { code: "unavailable", text: "AI context unavailable" };
  const style = AI_BADGE_STYLE[badge.code] ?? AI_BADGE_STYLE.unavailable;
  const BadgeIcon = style.icon;
  const frames = ai?.frames ?? [];
  const checklist = ai?.checklist ?? [];
  const details = testingTools && state === "done" && (checklist.length > 0 || frames.length > 0) && (
    <CardDetails title="AI details">
      <div className="flex flex-col gap-1">
        {checklist.map((c) => (
          <div key={c.field} className="flex items-center gap-1.5 text-[13px]" style={{ color: "var(--foreground)" }}>
            {c.value === true ? <Check size={12} style={{ color: "#10b981" }} />
              : c.value === false ? <X size={12} style={{ color: "var(--muted-foreground)" }} />
              : <Minus size={12} style={{ color: "var(--muted-foreground)" }} />}
            <span>{c.label}{typeof c.value === "string" ? `: ${c.value}` : ""}</span>
          </div>
        ))}
      </div>
      {frames.length > 0 && (
        <div className="mt-2">
          <div className="text-[12px] mb-1" style={{ color: "var(--muted-foreground)" }}>Frames the AI saw ({frames.length})</div>
          <div className="grid grid-cols-4 gap-1">
            {frames.map((u) => (
              <a key={u} href={u} target="_blank" rel="noreferrer">
                <img src={u} alt="frame sent to the AI" loading="lazy" className="w-full h-12 object-cover rounded" />
              </a>
            ))}
          </div>
        </div>
      )}
    </CardDetails>
  );
  return (
    <InfoCard dashed label="AI context" icon={<Sparkles size={12} />}
      aside={<><span>AI-generated · may be wrong</span>{details || null}</>}>
      {state === "pending" ? (
        <div className="mt-1.5 flex items-center gap-2 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
          <Loader2 size={13} className="animate-spin" /> AI is checking this event…
        </div>
      ) : (
        <>
          <div className="mt-1.5 inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[12px] font-medium"
            style={{ background: style.bg, color: style.color }}>
            <BadgeIcon size={11} /> {badge.text}
            {state === "done" && ai.confidence ? <span style={{ opacity: 0.8 }}>· {ai.confidence} confidence</span> : null}
          </div>
          {state === "done" && ai.observations && (
            <div className="mt-1.5 text-[13px] leading-snug italic break-words" style={{ color: "var(--foreground)" }}>
              &ldquo;{ai.observations}&rdquo;
            </div>
          )}
        </>
      )}
    </InfoCard>
  );
}

// The server sends the sentence ("Likely → Possible (suggested) — AI sees ordinary activity"); the card shows
// it as the old status struck through, an arrow, and the suggested status as a pill, with the reason in grey.
function SuggestedStatusCard({ ai, official }) {
  const sug = ai?.suggestion;
  const grey = { color: "var(--muted-foreground)" };
  const text = ai?.state === "pending" ? "AI context pending…" : (sug?.text || "AI context unavailable");
  const reason = text.includes(" — ") ? text.split(" — ").slice(1).join(" — ") : "";
  let body;
  if (sug?.changed && sug.suggested && official) {
    body = (
      <>
        <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
          <span className="text-[12px]" style={{ ...grey, textDecoration: "line-through" }}>{official}</span>
          <span className="text-[12px]" style={grey}>→</span>
          <LevelPill label={sug.suggested} />
        </div>
        <div className="mt-1 text-[12px]" style={grey}>(suggested){reason ? ` — ${reason}` : ""}</div>
      </>
    );
  } else if (sug?.suggested && PILL[sug.suggested]) {
    const noChange = text.startsWith("No change");
    body = (
      <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
        {noChange && <span className="text-[12px]" style={grey}>No change —</span>}
        <LevelPill label={sug.suggested} />
        {reason ? <span className="text-[12px]" style={grey}>{noChange ? reason : `— ${reason}`}</span> : null}
      </div>
    );
  } else {
    body = <div className="mt-1.5 text-[13px]" style={grey}>{text}</div>;
  }
  return (
    <InfoCard dashed label="Status with AI context" icon={<Sparkles size={12} />}
      tooltip={SUGGESTED_TOOLTIP} tooltipAlign="right" aside="suggestion only">
      {body}
    </InfoCard>
  );
}

// TESTING VIEW: how the status was reached, indicator by indicator. Shown only while "Show testing
// tools" is on (Settings -> System). Plain numbers on purpose: this is for checking the system,
// not for the tanod.
function ScoreBreakdown({ alert }) {
  const c = alert.cues || {};
  const indicators = Object.entries(c.cues || {}).sort((a, b) => b[1] - a[1]);
  const multipliers = Object.entries(c.multipliers || {});
  const pts = (v) => Math.round(Number(v) * 100);
  const settings = c.settings ? Object.entries(c.settings) : [];
  const mono = { fontFamily: "'DM Mono', monospace" };
  return (
    <InfoCard dashed label="Testing view · score breakdown" aside="testing tools on">
      <table className="w-full mt-2 text-[13px]" style={{ color: "var(--foreground)", ...mono }}>
        <tbody>
          {indicators.map(([name, w]) => (
            <tr key={name}>
              <td className="py-0.5">{name.replace(/_/g, " ")}</td>
              <td className="py-0.5 text-right">+{pts(w)}</td>
            </tr>
          ))}
          {indicators.length === 0 && <tr><td className="py-0.5" colSpan={2}>no indicators fired</td></tr>}
          <tr style={{ borderTop: "1px solid var(--border)" }}>
            <td className="py-0.5">sum of indicators</td>
            <td className="py-0.5 text-right">{c.raw_score != null ? pts(c.raw_score) : "—"}</td>
          </tr>
          {multipliers.map(([name, v]) => (
            <tr key={name}>
              <td className="py-0.5">time multiplier ({name})</td>
              <td className="py-0.5 text-right">×{v}</td>
            </tr>
          ))}
          <tr style={{ borderTop: "1px solid var(--border)", fontWeight: 600 }}>
            <td className="py-0.5">total (capped at 100)</td>
            <td className="py-0.5 text-right">{c.score != null ? pts(c.score) : "—"}</td>
          </tr>
          <tr>
            <td className="py-0.5">status</td>
            <td className="py-0.5 text-right">{c.label || alert.levelLabel || "—"}{c.tag ? ` · ${c.tag}` : ""}</td>
          </tr>
          <tr>
            <td className="py-0.5">momentum</td>
            <td className="py-0.5 text-right">
              {c.momentum ? `${c.momentum.momentum} (peak ${c.momentum.peak}; on ${c.momentum.on}, off ${c.momentum.off}, decay ${c.momentum.decay})` : "—"}
            </td>
          </tr>
        </tbody>
      </table>
      {settings.length > 0 && (
        <details className="mt-2 text-[12px]" style={{ color: "var(--muted-foreground)", ...mono }}>
          <summary className="cursor-pointer">Settings logged with this alert</summary>
          <div className="mt-1 space-y-0.5">
            {settings.map(([k, v]) => <div key={k}>{k}: {String(v)}</div>)}
          </div>
        </details>
      )}
    </InfoCard>
  );
}


// ── Closed alerts ────────────────────────────────────────────────────────────────

function whenText(ts) {
  if (!ts) return "";
  const { date, time } = formatStamp(ts);
  return `${date}, ${time}`;
}

// Directly under the header: who closed the alert, when, and why.
function ClosedBanner({ alert }) {
  const dismissed = alert.status === "acknowledged";
  const tone = dismissed
    ? { bg: "rgba(244,63,94,0.08)", border: "rgba(244,63,94,0.25)", icon: "#e11d48", Icon: X, title: "Dismissed" }
    : { bg: "rgba(16,185,129,0.08)", border: "rgba(16,185,129,0.28)", icon: "#059669", Icon: Check, title: "Resolved" };
  const Icon = tone.Icon;
  const by = alert.reviewedBy ? `by ${alert.reviewedBy}` : "";
  const when = whenText(alert.reviewedAt);
  return (
    <div className="flex items-start gap-3 w-full rounded-lg px-4 py-3 mb-5"
      style={{ background: tone.bg, border: `1px solid ${tone.border}` }}>
      <span className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5"
        style={{ background: tone.icon + "22", color: tone.icon }}>
        <Icon size={13} />
      </span>
      <div className="min-w-0">
        <div className="text-[16px] font-semibold" style={{ color: "var(--foreground)" }}>{tone.title}</div>
        {(by || when) && (
          <div className="text-[13px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
            {[by, when].filter(Boolean).join(" · ")}
          </div>
        )}
        <div className="text-[14px] mt-1.5 leading-relaxed break-words" style={{ color: "var(--foreground)" }}>
          {dismissed ? (alert.notes || "No reason provided.") : (alert.citationIssued ? "Citation issued" : "No citation")}
        </div>
      </div>
    </div>
  );
}

// Detected -> status changes -> assigned -> dismissed / resolved, each with its time.
function TimelineBody({ alert }) {
  const detected = { t: alert.timestamp, label: alert.timeSource === "processed" ? "Detected (processed at)" : "Detected", kind: "detected" };
  const events = (alert.timeline ?? []).map((e) => ({
    t: e.t, kind: e.type,
    label: e.type === "status" ? e.label
      : e.type === "dismissed" ? `Dismissed${e.by ? ` by ${e.by}` : ""}`
      : e.type === "resolved" ? `Resolved${e.by ? ` by ${e.by}` : ""}`
      : e.type === "reopened" ? `Reopened${e.by ? ` by ${e.by}` : ""}`
      : e.label,
  }));
  // Alerts closed before timelines were kept still show how they ended.
  const closedKnown = events.some((e) => e.kind === "dismissed" || e.kind === "resolved");
  if (!closedKnown && alert.reviewedAt && (alert.status === "acknowledged" || alert.status === "resolved")) {
    events.push({ t: alert.reviewedAt, kind: "closed", label: `${alert.status === "resolved" ? "Resolved" : "Dismissed"}${alert.reviewedBy ? ` by ${alert.reviewedBy}` : ""}` });
  }
  const rows = [detected, ...events].sort((a, b) => new Date(a.t) - new Date(b.t));
  const colorOf = (r) => (r.kind === "status" ? levelColor(r.label) : r.kind === "dismissed" || r.label.startsWith("Dismissed") ? "#e11d48"
    : r.kind === "resolved" || r.label.startsWith("Resolved") ? "#059669" : r.kind === "assigned" ? "#3b82f6" : "var(--muted-foreground)");
  return (
    <>
      <div className="text-[13px] mb-2 font-medium" style={{ color: "var(--foreground)" }}>Timeline</div>
      <ol className="space-y-1.5">
        {rows.map((r, i) => (
          <li key={i} className="flex items-start gap-2.5">
            <span className="w-2 h-2 rounded-full flex-shrink-0 mt-[7px]" style={{ background: colorOf(r) }} />
            <span className="text-[14px] flex-1 break-words" style={{ color: "var(--foreground)" }}>{r.label}</span>
            <span className="text-[13px] flex-shrink-0" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              {new Date(r.t).toLocaleTimeString("en-PH", { hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true })}
            </span>
          </li>
        ))}
      </ol>
    </>
  );
}

// Clock button in the top-right corner of the video; opens the event timeline. Click outside or Esc closes it.
function TimelineButton({ alert }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey, true); };
  }, [open]);
  return (
    <div ref={ref} className="absolute top-2 right-2 z-20">
      <button onClick={() => setOpen((o) => !o)} title="Timeline" aria-label="Timeline"
        className="w-8 h-8 rounded-full flex items-center justify-center"
        style={{ background: open ? "rgba(245,158,11,0.9)" : "rgba(0,0,0,0.55)", color: open ? "#0c0f16" : "#fff" }}>
        <Clock size={15} />
      </button>
      {open && (
        <div className="absolute right-0 top-10 w-[300px] max-w-[80vw] max-h-[60vh] overflow-y-auto rounded-lg px-3 py-2.5 shadow-xl"
          style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
          <TimelineBody alert={alert} />
        </div>
      )}
    </div>
  );
}

// ── Main modal ─────────────────────────────────────────────────────────────────
export function ViolationModal({
  alert, assignedOfficerNames, officers = [], currentOfficerId,
  onDismiss, onDispatch, onResolved, onClose, onReopen,
  userRole,
}) {
  // Resolving closes a violation and is what a citation is filed against, so it belongs to whoever
  // attended the scene. An explicit allowlist: a missing prop must hide a privileged action.
  const canResolve = ["admin", "officer", "both"].includes(userRole);
  const canReopen = userRole === "admin" && !!onReopen;
  const vcfg = violationDisplay(alert.type);
  const VIcon = vcfg.icon;
  // Testing view (score breakdown): admin only, and only while "Show testing tools" is on.
  const testingTools = useTestingTools(userRole === "admin");
  const [showResolveChecklist, setShowResolveChecklist] = useState(false);
  const [showAllOfficers, setShowAllOfficers] = useState(false);
  const closed = alert.status === "acknowledged" || alert.status === "resolved";

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

  const stamp = formatStamp(alert.timestamp);

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
        style={{ background: "rgba(0,0,0,0.8)", backdropFilter: "blur(8px)" }}
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      >
        <div
          className="w-[90vw] max-w-[1440px] rounded-2xl overflow-hidden shadow-2xl flex flex-col"
          style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "92vh" }}
        >
          {/* Header — what, which camera, and when. No review tag: the verdict is recorded
              silently from Dismiss / Assign. */}
          <div className="flex items-center justify-between gap-4 px-6 py-4 flex-shrink-0"
            style={{ borderBottom: "1px solid var(--border)" }}>
            <div className="flex items-center gap-3 min-w-0">
              <VIcon size={22} style={{ color: vcfg.color, flexShrink: 0 }} />
              <div className="min-w-0">
                <span className="text-[17px] font-semibold" style={{ color: "var(--foreground)" }}>{vcfg.label}</span>
                <div className="mt-1 text-[13px] truncate" style={{ color: "var(--muted-foreground)" }}>
                  <span style={{ fontFamily: "'DM Mono', monospace" }}>{alert.id}</span>
                  {" · "}{alert.cameraZone || alert.camera}
                </div>
              </div>
            </div>

            <div className="flex items-start gap-3 flex-shrink-0">
              <div className="text-right min-w-0">
                <div className="flex items-center justify-end gap-2">
                  <Clock size={17} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                  {alert.timeSource === "processed" && (
                    <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>processed at</span>
                  )}
                  <span className="text-[20px] leading-tight" style={{ color: "var(--foreground)" }}>
                    <span style={{ fontWeight: 500 }}>{stamp.date}</span>
                    <span style={{ color: "var(--muted-foreground)", margin: "0 7px" }}>·</span>
                    <span style={{ fontWeight: 700 }}>{stamp.time}</span>
                  </span>
                </div>
                {alert.cameraAddress ? (
                  <div className="mt-1 flex items-center justify-end gap-1.5">
                    <MapPin size={14} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                    <span className="text-[14px] break-words" style={{ color: "var(--muted-foreground)" }}>{alert.cameraAddress}</span>
                  </div>
                ) : null}
              </div>
              <button onClick={onClose} className="p-2 rounded-lg flex-shrink-0 self-start"
                style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
                <X size={15} />
              </button>
            </div>
          </div>

          {/* Body: ONE scrolling area. Evidence ~60% on the left, cards ~40% on the right; on a
              narrow screen the video is on top and the cards stack below it. */}
          <div className="overflow-y-auto flex-1 p-6">
            {closed && <ClosedBanner alert={alert} />}
            <div className="grid grid-cols-1 lg:grid-cols-[60fr_40fr] gap-5 items-start">
              <div className="min-w-0">
                <RecordingPlayer alert={alert} timeline />
              </div>

              <div className="flex flex-col gap-3 min-w-0">
                <QuietCard label="Camera" value={alert.cameraZone || alert.cameraAddress || "—"} />
                <div className="grid grid-cols-2 gap-3 items-stretch">
                  <StatusCard alert={alert} testingTools={testingTools} />
                  <ObjectConfidenceCard alert={alert} />
                </div>
                {/* Null (not merely empty) means this violation has no AI checker
                    at all — parking, whose rule is a measurement with nothing for
                    a vision model to adjudicate. Drawing "AI context unavailable"
                    on those alerts advertises a missing feature that was never
                    meant to exist. A kind that DOES have a checker still gets the
                    cards when a check failed or is pending, which is information. */}
                {alert.aiContext && (
                  <>
                    <AIContextCard ai={alert.aiContext} testingTools={testingTools} />
                    <SuggestedStatusCard ai={alert.aiContext} official={alert.levelLabel} />
                  </>
                )}
                {testingTools && <ScoreBreakdown alert={alert} />}
                <div className="grid grid-cols-2 gap-3">
                  <QuietCard label="Detected object" value={alert.suspect || "—"} />
                  {officersCard}
                </div>
              </div>
            </div>
          </div>

          {/* Footer. Open alerts: Dismiss and Assign officers (plus Mark resolved once assigned).
              Closed alerts: only a small Reopen, for admins. */}
          {!closed && (
            <div className="flex items-center gap-2 px-6 py-4 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
              <div className="flex-1" />
              <button onClick={onDismiss}
                className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.4)" }}>
                <X size={14} /> Dismiss
              </button>
              <button onClick={onDispatch}
                className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                style={{ background: "rgba(245,158,11,0.22)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.5)" }}>
                <Radio size={14} /> Assign officers
              </button>
              {alert.status === "dispatched" && canResolve && (
                <button onClick={() => setShowResolveChecklist(true)}
                  className="flex items-center gap-1.5 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all"
                  style={{ background: "rgba(16,185,129,0.22)", color: "#10b981", border: "1px solid rgba(16,185,129,0.45)" }}>
                  <CheckCircle size={14} /> Mark resolved
                </button>
              )}
            </div>
          )}
          {closed && canReopen && (
            <div className="flex items-center px-6 py-3 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
              <div className="flex-1" />
              <button onClick={onReopen}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-medium"
                style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
                <RotateCcw size={12} /> Reopen
              </button>
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
