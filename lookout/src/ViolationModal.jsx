import { useRef, useEffect, useState } from "react";
import {
  X, MapPin, Clock, User, Shield, Play, Pause,
  SkipBack, Volume2, VolumeX, Download, Radio, CheckCircle, AlertTriangle,
} from "lucide-react";
import { VIOLATION_CONFIG } from "../data/mockData";

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
      {/* Frame */}
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

        {/* Top bar */}
        <div className="absolute top-0 left-0 right-0 px-3 py-2 flex items-center justify-between"
          style={{ background: "linear-gradient(to bottom, rgba(0,0,0,0.72), transparent)" }}>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded" style={{ background: "rgba(239,68,68,0.85)", color: "#fff", fontFamily: "'DM Mono', monospace" }}>
              ● REC
            </span>
            <span className="text-[10px]" style={{ color: "#94a3b8", fontFamily: "'DM Mono', monospace" }}>{alert.camera}</span>
          </div>
          <span className="text-[10px]" style={{ color: "#94a3b8", fontFamily: "'DM Mono', monospace" }}>{formatFull(alert.timestamp)}</span>
        </div>

        {/* Zone label */}
        <div className="absolute bottom-10 left-3 text-[10px]" style={{ color: "#64748b", fontFamily: "'DM Mono', monospace" }}>
          {alert.cameraZone}
        </div>

        {/* Play button */}
        {!playing && (
          <button onClick={() => setPlaying(true)} className="absolute inset-0 flex items-center justify-center group">
            <div className="w-14 h-14 rounded-full flex items-center justify-center transition-all group-hover:scale-110"
              style={{ background: "rgba(245,158,11,0.9)", boxShadow: "0 0 28px rgba(245,158,11,0.35)" }}>
              <Play size={22} color="#0c0f16" fill="#0c0f16" style={{ marginLeft: 2 }} />
            </div>
          </button>
        )}
      </div>

      {/* Controls */}
      <div className="px-4 py-3" style={{ background: "#0a0d14" }}>
        {/* Scrubber */}
        <div
          className="relative h-1 rounded-full mb-3 cursor-pointer"
          style={{ background: "rgba(255,255,255,0.1)" }}
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const t = ((e.clientX - r.left) / r.width) * duration;
            setElapsed(Math.max(0, Math.min(t, duration)));
          }}
        >
          <div className="absolute left-0 top-0 h-full rounded-full transition-all" style={{ width: `${pct}%`, background: "#f59e0b" }} />
          {/* Event marker */}
          <div className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full border-2"
            style={{ left: `${(18 / duration) * 100}%`, background: "#ef4444", borderColor: "#0a0d14" }}
            title="Violation detected" />
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <button onClick={() => { setElapsed(0); setPlaying(false); }} className="p-1 rounded transition-colors" style={{ color: "#64748b" }}
              onMouseEnter={(e) => (e.currentTarget.style.color = "#f1f5f9")}
              onMouseLeave={(e) => (e.currentTarget.style.color = "#64748b")}>
              <SkipBack size={13} />
            </button>
            <button onClick={() => setPlaying(!playing)}
              className="w-8 h-8 rounded-full flex items-center justify-center" style={{ background: "#f59e0b" }}>
              {playing
                ? <Pause size={13} color="#0c0f16" fill="#0c0f16" />
                : <Play  size={13} color="#0c0f16" fill="#0c0f16" style={{ marginLeft: 1 }} />}
            </button>
            <button onClick={() => setMuted(!muted)} className="p-1 rounded" style={{ color: "#64748b" }}
              onMouseEnter={(e) => (e.currentTarget.style.color = "#f1f5f9")}
              onMouseLeave={(e) => (e.currentTarget.style.color = "#64748b")}>
              {muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
            </button>
            <span className="text-[11px] tabular-nums" style={{ color: "#64748b", fontFamily: "'DM Mono', monospace" }}>
              {fmtSec(elapsed)} / {fmtSec(duration)}
            </span>
          </div>
          <button className="flex items-center gap-1 text-[11px] px-2 py-1 rounded-md" style={{ color: "#64748b" }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "#f1f5f9"; e.currentTarget.style.background = "rgba(255,255,255,0.06)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "#64748b"; e.currentTarget.style.background = "transparent"; }}>
            <Download size={11} /> Save clip
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Modal ─────────────────────────────────────────────────────────────────────
export function ViolationModal({
  alert, assignedOfficerNames, onDismiss, onDispatch, onResolve, onClose,
}) {
  const vcfg = VIOLATION_CONFIG[alert.type] ?? { label: alert.type, color: "#f59e0b", icon: AlertTriangle };
  const scfg = statusConfig[alert.status];

  return (
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
        <div className="flex items-center justify-between px-6 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <span><vcfg.icon size={22} color={vcfg.color} /></span>
            <div>
              <div className="flex items-center gap-2">
                <span className="text-[15px] font-semibold text-white">{vcfg.label}</span>
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full" style={{ background: scfg.bg, color: scfg.color }}>
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
          <button onClick={onClose} className="p-2 rounded-lg flex-shrink-0" style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={15} />
          </button>
        </div>

        {/* Body — scrollable */}
        <div className="overflow-y-auto flex-1 p-6 space-y-5">
          {/* Recording player */}
          <RecordingPlayer alert={alert} />

          {/* Two-column meta */}
          <div className="grid grid-cols-2 gap-4">
            {/* Left: alert details */}
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                {[
                  ["Camera",     alert.camera],
                  ["Alert ID",   alert.id],
                  ["Confidence", `${(alert.confidence * 100).toFixed(0)}%`],
                  ["Status",     scfg.label],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-lg px-3 py-2" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                    <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>{k}</div>
                    <div className="text-[12px] font-medium text-white mt-0.5"
                      style={{ fontFamily: k === "Camera" || k === "Alert ID" ? "'DM Mono', monospace" : undefined }}>
                      {v}
                    </div>
                  </div>
                ))}
              </div>

              {/* Description */}
              <div className="rounded-lg px-3 py-3" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                <div className="text-[10px] mb-1" style={{ color: "var(--muted-foreground)" }}>Description</div>
                <p className="text-[12px] leading-relaxed" style={{ color: "#94a3b8" }}>{alert.description}</p>
              </div>
            </div>

            {/* Right: people + assigned */}
            <div className="space-y-3">
              {alert.suspect && (
                <div className="rounded-lg px-3 py-3" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                  <div className="text-[10px] mb-1.5" style={{ color: "var(--muted-foreground)" }}>Identified subject</div>
                  <div className="flex items-center gap-2 text-[12px]" style={{ color: "#f59e0b" }}>
                    <User size={11} /> {alert.suspect}
                  </div>
                </div>
              )}

              <div className="rounded-lg px-3 py-3" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                <div className="text-[10px] mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                  Assigned officers {assignedOfficerNames.length > 0 && `(${assignedOfficerNames.length})`}
                </div>
                {assignedOfficerNames.length === 0 ? (
                  <div className="text-[12px]" style={{ color: "#4a5568" }}>None assigned</div>
                ) : (
                  <div className="space-y-1.5">
                    {assignedOfficerNames.map((name) => (
                      <div key={name} className="flex items-center gap-2 text-[12px]" style={{ color: "#94a3b8" }}>
                        <Shield size={10} style={{ color: "#10b981", flexShrink: 0 }} /> {name}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {alert.notes && (
                <div className="rounded-lg px-3 py-3" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                  <div className="text-[10px] mb-1" style={{ color: "var(--muted-foreground)" }}>Notes</div>
                  <p className="text-[12px] italic" style={{ color: "#64748b" }}>"{alert.notes}"</p>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer actions */}
        {(alert.status === "active" || alert.status === "dispatched") && (
          <div className="flex items-center gap-2 px-6 py-4 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
            {alert.status === "active" && (
              <>
                <button
                  onClick={onDismiss}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all"
                  style={{ background: "rgba(100,116,139,0.1)", color: "#94a3b8", border: "1px solid rgba(100,116,139,0.2)" }}
                >
                  <X size={13} /> Dismiss
                </button>
                <button
                  onClick={onDispatch}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all"
                  style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}
                >
                  <Radio size={13} />
                  {assignedOfficerNames.length > 0 ? "Reassign officers" : "Dispatch officers"}
                </button>
              </>
            )}
            {alert.status === "dispatched" && (
              <>
                <button
                  onClick={onDispatch}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all"
                  style={{ background: "rgba(59,130,246,0.1)", color: "#3b82f6", border: "1px solid rgba(59,130,246,0.2)" }}
                >
                  <Radio size={13} /> Reassign officers
                </button>
                <button
                  onClick={onResolve}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium transition-all"
                  style={{ background: "rgba(16,185,129,0.12)", color: "#10b981", border: "1px solid rgba(16,185,129,0.2)" }}
                >
                  <CheckCircle size={13} /> Mark resolved
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}