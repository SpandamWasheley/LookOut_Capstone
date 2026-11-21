import { useCallback, useEffect, useState } from "react";
import { Play, Square, Loader2, AlertTriangle, Info, ChevronDown, ChevronUp } from "lucide-react";
import { getMonitor, startMonitor, stopMonitor, getMonitorState, getSettings, saveSettings } from "./api";
import { ProcessingView } from "./ProcessingView";
import { useTestingTools } from "./useTestingTools";

const DOT = { running: "#22c55e", starting: "#f59e0b", unreachable: "#ef4444", stopped: "#64748b" };

// Live monitoring of the camera — testing tools must be on to show this panel in Live Feeds.
// One live monitor for the whole system. The server restarts the detector by itself if it stops or
// the stream drops, and shows a warning here while that is happening.
export function LiveMonitor({ actions = null }) {
  const testingTools = useTestingTools(true);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showView, setShowView] = useState(true);
  // Two switches that are read when the detector launches; saving either relaunches it.
  const [cfg, setCfg] = useState(null);
  const [cfgBusy, setCfgBusy] = useState(false);

  useEffect(() => {
    getSettings().then((s) => setCfg({ far_mode: s.far_mode, vlm_enabled: s.vlm_enabled })).catch(() => {});
  }, []);

  const flip = async (key) => {
    if (cfgBusy || !cfg) return;
    setCfgBusy(true);
    setError("");
    try {
      const saved = await saveSettings({ [key]: !cfg[key] });
      setCfg({ far_mode: saved.far_mode, vlm_enabled: saved.vlm_enabled });
    } catch (e) {
      setError(e.message || "Could not save that setting.");
    } finally {
      setCfgBusy(false);
    }
  };

  useEffect(() => {
    let alive = true;
    const read = () => getMonitor().then((s) => { if (alive) setStatus(s); }).catch(() => {});
    read();
    const id = setInterval(read, 3000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const toggle = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setStatus(status?.state === "stopped" ? await startMonitor() : await stopMonitor());
    } catch (e) {
      setError(e.message || "Could not change detection.");
    } finally {
      setBusy(false);
    }
  };

  const fetchState = useCallback((since) => getMonitorState(since), []);

  if (!status || !testingTools) return null;
  const stopped = status.state === "stopped";
  const since = status.since
    ? new Date(status.since).toLocaleTimeString("en-PH", { hour: "numeric", minute: "2-digit", hour12: true })
    : "";

  return (
    <div className="mt-4 space-y-3">
      <div className="rounded-xl px-4 py-3 flex flex-wrap items-center gap-3"
        style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
        <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: DOT[status.state] ?? DOT.stopped }} />
        <div className="min-w-0">
          <div className="text-[15px] font-semibold" style={{ color: "var(--foreground)" }}>
            {status.label}{status.state === "running" && since ? ` · since ${since}` : ""}
          </div>
          <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
            Live monitoring · smoking, drinking, dangerous weapons and parking on the camera
          </div>
        </div>
        <div className="flex items-center gap-2 ml-auto">
          {actions}
          {!stopped && (
            <button onClick={() => setShowView((v) => !v)}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-[13px]"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              {showView ? <ChevronUp size={13} /> : <ChevronDown size={13} />} Processing view
            </button>
          )}
          <button onClick={toggle} disabled={busy}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium"
            style={stopped
              ? { background: "#22c55e", color: "#08130a" }
              : { background: "rgba(239,68,68,0.12)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.35)" }}>
            {busy ? <Loader2 size={13} className="animate-spin" /> : stopped ? <Play size={13} /> : <Square size={13} />}
            {stopped ? "Start monitoring" : "Stop monitoring"}
          </button>
        </div>
        {cfg && (
          <div className="basis-full flex flex-wrap items-center gap-2 text-[13px]">
            {[
              { key: "far_mode", label: "Long-range scanning",
                on: "On: frame scanned in 4 sections — catches objects far from the camera. Slower.",
                off: "Off: one pass, for close range. Several times faster." },
              { key: "vlm_enabled", label: "AI checker",
                on: "On: Ollama adds context to each alert.",
                off: "Off: frames are saved with each alert; context is added when it is back on." },
            ].map(({ key, label, on, off }) => (
              <button key={key} onClick={() => flip(key)} disabled={cfgBusy} title={cfg[key] ? on : off}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}>
                <span className="w-2 h-2 rounded-full" style={{ background: cfg[key] ? "#22c55e" : "#64748b" }} />
                {label}: {cfg[key] ? "On" : "Off"}
              </button>
            ))}
            <span style={{ color: "var(--muted-foreground)" }}>
              {cfg.far_mode ? "Far range" : "Close range"} · changing either restarts detection for a few seconds
            </span>
          </div>
        )}
        {(status.warning || error) && (
          <div className="basis-full flex items-start gap-2 text-[13px]" style={{ color: "#f59e0b" }}>
            <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" /> <span>{error || status.warning}</span>
          </div>
        )}
        {status.notice && (
          <div className="basis-full flex items-start gap-2 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
            <Info size={13} className="flex-shrink-0 mt-0.5" /> <span>{status.notice}</span>
          </div>
        )}
      </div>
      {!stopped && showView && (
        <ProcessingView fetchState={fetchState} testingTools={testingTools} title="Live processing" />
      )}
    </div>
  );
}
