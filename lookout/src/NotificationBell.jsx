import { useState, useEffect, useRef } from "react";
import { Bell, BellOff, ArrowUpRight, Volume2, VolumeX } from "lucide-react";

import { violationDisplay } from "./constants/violationTypes";
import { levelColor } from "./alertModel";
import { useAlertSoundPref, writeAlertSound } from "./alertSound";

// The topbar notification bell: how many violations are waiting to be reviewed,
// and which ones, without leaving the page you are on.
//
// Takes MAPPED alerts (alertModel.mapAlert), not raw API rows — the same shape
// the Violations tab and Records already work in, so a caller cannot hand it
// `level_label` on one page and `levelLabel` on another and silently render a
// blank badge.
//
// "New" means status `active`: raised and not yet assigned to anyone. A
// dispatched alert is somebody's job already and drops off the bell, which is
// what stops this becoming a list that only grows. Deciding that here rather
// than in each caller keeps the bell saying the same thing everywhere it
// appears.

// "4m ago". Short on purpose — this sits in a dropdown row where the violation
// and the camera matter more than the exact second, and the full timestamp is
// one click away on the alert itself. Anything past a day falls back to a date,
// since "37h ago" is harder to place than "Jun 12".
function timeAgo(ts) {
  const then = new Date(ts);
  if (Number.isNaN(then.getTime())) return "";
  const seconds = Math.floor((Date.now() - then.getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return then.toLocaleDateString("en-PH", { month: "short", day: "numeric" });
}

export function NotificationBell({ alerts, onOpenAlerts }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  // Whether this computer chimes on a new violation. It lives in the bell
  // rather than in Settings because Settings is admin-only, and the person
  // who needs the speaker turned down is whoever is sitting at the desk —
  // usually a dispatcher, who cannot open that page at all.
  const soundOn = useAlertSoundPref();

  // A dropdown that outlives a click elsewhere is a dropdown that covers the
  // page it is sitting on, so it closes on an outside click and on Escape.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKeyDown = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const unreviewed = alerts.filter((a) => a.status === "active");
  // Newest first, and capped: the panel is for the ones that just came in, not
  // a second Violations page. The footer sends you to the real list.
  const newest = [...unreviewed]
    .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
    .slice(0, 6);
  const count = unreviewed.length;
  const hidden = count - newest.length;

  return (
    <div className="relative" ref={wrapRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={count > 0 ? `${count} violations to review` : "No violations to review"}
        title={count > 0 ? `${count} to review` : "Nothing to review"}
        className="relative flex items-center justify-center w-9 h-9 rounded-lg transition-colors"
        style={{
          color: count > 0 ? "#ef4444" : "var(--muted-foreground)",
          background: open ? "var(--secondary)" : "transparent",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = "var(--secondary)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = open ? "var(--secondary)" : "transparent"; }}
      >
        <Bell size={17} />
        {count > 0 && (
          <span
            className="absolute -top-0.5 -right-0.5 min-w-[17px] h-[17px] px-1 rounded-full flex items-center justify-center text-[10px] font-bold tabular-nums"
            style={{ background: "#ef4444", color: "#fff", border: "2px solid var(--background)" }}
          >
            {count > 9 ? "9+" : count}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 mt-2 w-80 rounded-xl overflow-hidden shadow-2xl z-50"
          style={{ background: "var(--card)", border: "1px solid var(--border)" }}
        >
          <div
            className="flex items-center justify-between px-4 py-3"
            style={{ borderBottom: "1px solid var(--border)" }}
          >
            <span className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
              New violations
            </span>
            <div className="flex items-center gap-2">
              <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
                {count > 0 ? `${count} to review` : "All clear"}
              </span>
              <button
                onClick={() => writeAlertSound(!soundOn)}
                aria-pressed={soundOn}
                aria-label={soundOn ? "Turn the alert sound off" : "Turn the alert sound on"}
                title={soundOn
                  ? "Alert sound on — this computer chimes when a violation arrives"
                  : "Alert sound off on this computer"}
                className="p-1 rounded-md"
                style={{ color: soundOn ? "var(--foreground)" : "var(--muted-foreground)" }}
              >
                {soundOn ? <Volume2 size={14} /> : <VolumeX size={14} />}
              </button>
            </div>
          </div>

          {newest.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-8 px-4">
              <BellOff size={22} style={{ color: "var(--muted-foreground)" }} />
              <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
                Nothing waiting to be reviewed.
              </span>
            </div>
          ) : (
            <div className="max-h-80 overflow-y-auto">
              {newest.map((a) => {
                const vcfg = violationDisplay(a.type);
                const Icon = vcfg.icon;
                return (
                  <button
                    key={a.id ?? a.dbId}
                    onClick={() => { setOpen(false); onOpenAlerts(); }}
                    className="w-full flex items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-white/[0.03]"
                    style={{ borderBottom: "1px solid var(--border)" }}
                  >
                    <span
                      className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0"
                      style={{ background: vcfg.bg }}
                    >
                      <Icon size={14} style={{ color: vcfg.color }} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="text-[13px] font-medium truncate" style={{ color: "var(--foreground)" }}>
                          {vcfg.label}
                        </span>
                        {a.levelLabel && (
                          <span
                            className="text-[11px] font-semibold flex-shrink-0"
                            style={{ color: levelColor(a.levelLabel) }}
                          >
                            {a.levelLabel}
                          </span>
                        )}
                      </span>
                      <span className="flex items-center gap-1.5 text-[12px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                        <span className="whitespace-nowrap" style={{ fontFamily: "'DM Mono', monospace" }}>{a.id}</span>
                      </span>
                    </span>
                    <span className="text-[11px] flex-shrink-0 mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                      {timeAgo(a.timestamp)}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          <button
            onClick={() => { setOpen(false); onOpenAlerts(); }}
            className="w-full flex items-center justify-center gap-1.5 py-2.5 text-[13px] font-medium transition-colors"
            style={{ color: "var(--primary)", background: "var(--secondary)" }}
          >
            {hidden > 0 ? `View all violations (${hidden} more)` : "View all violations"}
            <ArrowUpRight size={13} />
          </button>
        </div>
      )}
    </div>
  );
}
