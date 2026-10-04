import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Maximize2, Minimize2, Eye, FlaskConical } from "lucide-react";

// The detector's live processing view, for the projector and for testing.
//
// The detector publishes a CLEAN frame (no boxes, no labels) plus the list of everything it is
// tracking; this component draws the boxes and labels on top, in one of two modes:
//
//   Panel view    (default)  boxes + status only (Monitoring / Possible / Likely), colour-coded.
//                            No numbers, scores, momentum or indicator lists: safe to show a panel.
//   Testing view             the full label and a debug table (only offered when "Show testing
//                            tools" is on in Settings).
//
// Because the labels are drawn here, never by the detector, they cannot end up in evidence images
// or clips, and they never appear anywhere when this component is not on screen.

const STATUS_COLOR = {
  Monitoring: "#94a3b8",
  Possible: "#f59e0b",
  Likely: "#ef4444",
  "Below Monitoring": "#64748b",
};
const BELOW = "Below Monitoring";
const POLL_MS = 500;

const clock = (epochSeconds) =>
  new Date(epochSeconds * 1000).toLocaleTimeString("en-PH", { hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true });

// How long before the latest published frame this subject was last updated.
function ago(seen, published) {
  const s = Math.max(0, Math.round((published ?? seen) - seen));
  return s < 1 ? "this frame" : `${s}s before this frame`;
}

function testingLabel(e) {
  const parts = [
    `ID ${e.id}`,
    e.violation,
    `${e.status} (${e.score})`,
  ];
  if (e.indicators?.length) parts.push(e.indicators.map((i) => `${i.name} ${i.points}`).join(", "));
  parts.push(`momentum ${e.momentum}`);
  return parts.join(" · ");
}

// fetchState(since) -> Promise<{available, seq, frame?, frame_w, frame_h, subjects, age, job|monitor}>
export function ProcessingView({ fetchState, testingTools = false, title = "Processing", finishedNote = "" }) {
  const [mode, setMode] = useState("panel");             // "panel" | "testing"
  const [data, setData] = useState(null);
  const [frame, setFrame] = useState(null);
  const [error, setError] = useState("");
  const [fullscreen, setFullscreen] = useState(false);
  const boxRef = useRef(null);
  const seqRef = useRef(null);

  const view = testingTools ? mode : "panel";              // the Testing view is never reachable when tools are off

  const poll = useCallback(async () => {
    try {
      const d = await fetchState(seqRef.current);
      if (d?.frame) setFrame(d.frame);
      if (d?.seq != null) seqRef.current = d.seq;
      setData(d);
      setError("");
    } catch (e) {
      setError(e.message || "Lost contact with the server.");
    }
  }, [fetchState]);

  useEffect(() => {
    const first = setTimeout(poll, 0);
    const id = setInterval(poll, POLL_MS);
    return () => { clearTimeout(first); clearInterval(id); };
  }, [poll]);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === boxRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else boxRef.current?.requestFullscreen?.();
  };

  const subjects = data?.subjects ?? [];
  const flagged = subjects.filter((s) => s.status !== BELOW);
  const drawn = view === "testing" ? subjects : flagged;
  const fw = data?.frame_w || 16;
  const fh = data?.frame_h || 9;
  // The region the detector is judging against, in frame pixels. Only parking
  // publishes one; everything else leaves it null and draws nothing.
  const area = data?.area ?? [];
  const waiting = !data?.available;
  const stale = data?.available && data.age > 10;

  return (
    <div ref={boxRef} className="flex flex-col rounded-xl overflow-hidden min-h-0"
      style={{ background: "var(--card)", border: "1px solid var(--border)", height: fullscreen ? "100vh" : undefined }}>
      {/* header */}
      <div className="flex items-center justify-between gap-3 px-4 py-2 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <span className="text-[14px] font-semibold" style={{ color: "var(--foreground)" }}>{title}</span>
        <div className="flex items-center gap-2">
          {testingTools && (
            <div className="flex items-center gap-0.5 p-0.5 rounded-full" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              {[{ k: "panel", l: "Panel view", I: Eye }, { k: "testing", l: "Testing view", I: FlaskConical }].map(({ k, l, I }) => (
                <button key={k} onClick={() => setMode(k)}
                  className="flex items-center gap-1 px-2.5 py-1 text-[13px] font-medium rounded-full"
                  style={{ background: view === k ? "var(--card)" : "transparent", color: view === k ? "var(--foreground)" : "var(--muted-foreground)" }}>
                  <I size={12} /> {l}
                </button>
              ))}
            </div>
          )}
          <button onClick={toggleFullscreen} title={fullscreen ? "Exit full screen" : "Full screen"}
            className="p-1.5 rounded-lg" style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
            {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>

      <div className="flex flex-1 min-h-0 flex-col lg:flex-row">
        {/* the picture, about 70% */}
        <div className="lg:basis-[70%] lg:grow-0 min-w-0 flex items-center justify-center p-3" style={{ background: "#05080c" }}>
          <div className="relative w-full" style={{ aspectRatio: `${fw} / ${fh}`, maxHeight: fullscreen ? "calc(100vh - 80px)" : "70vh", maxWidth: fullscreen ? `calc((100vh - 80px) * ${fw / fh})` : undefined }}>
            {frame && <img src={frame} alt="Detector view" className="absolute inset-0 w-full h-full object-contain" />}
            {/* The fixed region the detector judges against — parking's
                no-parking polygon. Drawn here rather than baked into the frame
                for the same reason the boxes are: the published image stays
                clean, so it is still usable as evidence. Without it the one
                detector whose entire rule is "is the vehicle inside this
                shape" gave no way to see the shape, and a zone traced on the
                wrong part of the street looked identical to a broken detector.
                viewBox is the frame's own pixel space, so this lines up with
                the boxes above without any scaling of its own. */}
            {!waiting && area.length >= 3 && (
              <svg className="absolute inset-0 w-full h-full pointer-events-none"
                viewBox={`0 0 ${fw} ${fh}`} preserveAspectRatio="none" aria-hidden="true">
                <polygon points={area.map(([x, y]) => `${x},${y}`).join(" ")}
                  fill="rgba(255,191,0,0.12)" stroke="#ffbf00"
                  strokeWidth={Math.max(2, fw / 400)} strokeLinejoin="round" />
              </svg>
            )}
            {!waiting && drawn.map((e) => e.box && (
              <div key={e.key} className="absolute pointer-events-none"
                style={{
                  left: `${(e.box[0] / fw) * 100}%`, top: `${(e.box[1] / fh) * 100}%`,
                  width: `${((e.box[2] - e.box[0]) / fw) * 100}%`, height: `${((e.box[3] - e.box[1]) / fh) * 100}%`,
                  border: `2px ${e.status === BELOW ? "dashed" : "solid"} ${STATUS_COLOR[e.status]}`,
                  opacity: e.status === BELOW ? 0.7 : 1,
                }}>
                <span className="absolute left-0 whitespace-nowrap px-1.5 py-0.5 rounded text-[12px] font-semibold"
                  style={{ bottom: "100%", marginBottom: 2, background: STATUS_COLOR[e.status], color: e.status === "Monitoring" || e.status === BELOW ? "#0b1220" : "#fff", maxWidth: "none" }}>
                  {view === "testing" ? testingLabel(e) : `${e.violation} · ${e.status}`}
                </span>
              </div>
            ))}
            {(waiting || stale) && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center px-4"
                style={{ background: frame ? "rgba(5,8,12,0.55)" : "transparent", color: "#94a3b8" }}>
                <Loader2 size={22} className="animate-spin" />
                <span className="text-[14px]">
                  {finishedNote || (waiting ? "Waiting for the detector to start…" : "Waiting for the next frame…")}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* the events list, about 30% */}
        <div className="lg:basis-[30%] lg:grow min-w-0 min-h-0 overflow-y-auto p-3" style={{ borderLeft: "1px solid var(--border)" }}>
          {view === "panel" ? (
            <>
              <div className="text-[13px] font-semibold uppercase tracking-wide mb-2" style={{ color: "var(--muted-foreground)" }}>
                Events
              </div>
              {flagged.length === 0 ? (
                <div className="text-[14px] py-4 text-center" style={{ color: "var(--muted-foreground)" }}>
                  Nothing flagged right now.
                </div>
              ) : (
                <div className="space-y-2">
                  {[...flagged].sort((a, b) => b.changed - a.changed).map((e) => (
                    <div key={e.key} className="rounded-lg px-3 py-2" style={{ background: "var(--secondary)", borderLeft: `4px solid ${STATUS_COLOR[e.status]}` }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[15px] font-semibold" style={{ color: "var(--foreground)" }}>{e.violation}</span>
                        <span className="text-[13px] font-semibold" style={{ color: STATUS_COLOR[e.status] }}>{e.status}</span>
                      </div>
                      <div className="text-[12px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                        Last change {clock(e.changed)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <>
              <div className="text-[13px] font-semibold uppercase tracking-wide mb-2" style={{ color: "var(--muted-foreground)" }}>
                Debug · everything tracked ({subjects.length})
              </div>
              <div className="space-y-2">
                {subjects.length === 0 && (
                  <div className="text-[14px] py-4 text-center" style={{ color: "var(--muted-foreground)" }}>Nothing tracked yet.</div>
                )}
                {subjects.map((e) => (
                  <div key={e.key} className="rounded-lg px-2.5 py-2 text-[12px] leading-snug"
                    style={{ background: "var(--secondary)", borderLeft: `4px solid ${STATUS_COLOR[e.status]}`, color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }}>
                    <div className="flex justify-between gap-2">
                      <b>ID {e.id} · {e.violation}</b>
                      <span style={{ color: STATUS_COLOR[e.status] }}>{e.status} ({e.score})</span>
                    </div>
                    <div style={{ color: "var(--muted-foreground)" }}>
                      {e.indicators?.length ? e.indicators.map((i) => `${i.name} ${i.points}`).join(", ") : "no indicators"}
                    </div>
                    <div style={{ color: "var(--muted-foreground)" }}>
                      momentum {e.momentum}
                      {Object.keys(e.multipliers || {}).length ? ` · ×${Object.entries(e.multipliers).map(([k, v]) => `${k} ${v}`).join(", ")}` : ""}
                    </div>
                    <div style={{ color: "var(--muted-foreground)" }}>
                      updated {ago(e.seen, data?.wall)} · status changed {clock(e.changed)}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
          {error && <div className="text-[12px] mt-3" style={{ color: "#ef4444" }}>{error}</div>}
        </div>
      </div>
    </div>
  );
}
