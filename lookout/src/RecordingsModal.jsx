import { useCallback, useEffect, useRef, useState } from "react";
import { X, Video, Download, Circle, AlertTriangle, HardDrive, Loader2, Play, Pause } from "lucide-react";
import { getRecordings } from "./api";

// The continuous CCTV tape (core/recording.py), the way an NVR is browsed: a
// day down the side, that day's segments beside it, and one player.
//
// This is NOT the evidence clips. Those are cut around a detection and reached
// from an alert; this is everything the camera saw, which is what answers "what
// happened at about four o'clock on Tuesday" — a question a folder of
// twenty-second violation clips cannot answer at all.

const fmtBytes = (n) => {
  if (!n) return "0 MB";
  const gb = n / 1073741824;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(n / 1048576)} MB`;
};

const fmtClock = (iso) =>
  new Date(iso).toLocaleTimeString("en-PH", { hour: "numeric", minute: "2-digit", hour12: true });

const fmtDay = (isoDate) =>
  new Date(`${isoDate}T00:00:00`).toLocaleDateString("en-PH",
    { weekday: "short", day: "numeric", month: "short", year: "numeric" });

const fmtLength = (seconds) => {
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m ? `${m}m ${s.toString().padStart(2, "0")}s` : `${s}s`;
};

const fmtTime = (s) => {
  if (!isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  return `${m}:${Math.floor(s % 60).toString().padStart(2, "0")}`;
};

export function RecordingsModal({ onClose, jumpTo = null }) {
  const [days, setDays] = useState([]);
  const [status, setStatus] = useState(null);
  const [date, setDate] = useState(null);
  const [segments, setSegments] = useState([]);
  const [playing, setPlaying] = useState(null);     // the segment being watched
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [atTime, setAtTime] = useState(null);       // wall clock under the player
  const videoRef = useRef(null);
  const retriedRef = useRef(false);
  const isDraggingRef = useRef(false);
  const [vidCurrent, setVidCurrent] = useState(0);
  const [vidDuration, setVidDuration] = useState(0);
  const [vidPaused, setVidPaused] = useState(true);

  // The days index. Polled, not read once: the tape is being written while
  // this is open, so a day that gains its first segment should appear.
  useEffect(() => {
    let alive = true;
    const read = () =>
      getRecordings()
        .then((body) => {
          if (!alive) return;
          setDays(body.days ?? []);
          setStatus(body.recording ?? null);
          setDate((current) => current ?? (body.days?.[0]?.date ?? null));
          setError("");
        })
        .catch((e) => alive && setError(e.message || "Could not read the recordings."))
        .finally(() => alive && setLoading(false));
    read();
    const id = setInterval(read, 15000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const loadDay = useCallback((forDate) => {
    if (!forDate) return;
    getRecordings(forDate)
      .then((body) => {
        setSegments(body.segments ?? []);
        setStatus(body.recording ?? null);
      })
      .catch((e) => setError(e.message || "Could not read that day."));
  }, []);

  useEffect(() => { loadDay(date); }, [date, loadDay]);

  // The segment being watched keeps playing; the LIST behind it refreshes, so
  // the segment finishing right now turns playable without reopening the panel.
  useEffect(() => {
    if (!date) return undefined;
    const id = setInterval(() => loadDay(date), 15000);
    return () => clearInterval(id);
  }, [date, loadDay]);

  const autoJumpedRef = useRef(false);

  const open = (segment, resumeAt = 0) => {
    if (!segment.playable) return;
    retriedRef.current = false;
    isDraggingRef.current = false;
    setPlaying({ ...segment, src: segment.url, resumeAt: resumeAt || undefined });
    setAtTime(new Date(new Date(segment.started_at).getTime() + (resumeAt || 0) * 1000));
    setVidCurrent(resumeAt || 0);
    setVidDuration(0);
    setVidPaused(false);
  };

  // When opened from an alert's "In Tape" button, auto-select the matching day
  // and segment and seek to the moment the alert was detected.
  useEffect(() => {
    if (!jumpTo || autoJumpedRef.current || days.length === 0) return;
    const d = new Date(jumpTo);
    const dayStr = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const found = days.find((day) => day.date === dayStr);
    if (found) setDate(found.date);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, jumpTo]);

  const [jumpNotFound, setJumpNotFound] = useState(false);

  useEffect(() => {
    if (!jumpTo || autoJumpedRef.current || segments.length === 0) return;
    const ts = new Date(jumpTo).getTime();
    const seg = segments.find((s) => {
      const start = new Date(s.started_at).getTime();
      return ts >= start && ts < start + (s.seconds ?? 0) * 1000 && s.playable;
    });
    autoJumpedRef.current = true;
    if (!seg) { setJumpNotFound(true); return; }
    setJumpNotFound(false);
    open(seg, Math.max(0, (ts - new Date(seg.started_at).getTime()) / 1000));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segments, jumpTo]);

  // A segment's URL carries a ticket good for a few minutes, so a panel left
  // open on one paused segment eventually holds a stale one. Ask the server
  // for the day again — which mints fresh tickets — and carry on from the same
  // position, rather than showing the operator a video element that failed.
  const onVideoError = () => {
    if (!playing || retriedRef.current) {
      setError("That segment could not be played.");
      return;
    }
    retriedRef.current = true;
    const at = videoRef.current?.currentTime ?? 0;
    getRecordings(date)
      .then((body) => {
        const fresh = (body.segments ?? []).find((s) => s.name === playing.name);
        setSegments(body.segments ?? []);
        if (!fresh) { setError("That segment is no longer in the archive."); return; }
        setPlaying((p) => (p ? { ...p, src: fresh.url, resumeAt: at } : p));
      })
      .catch(() => setError("That segment could not be played."));
  };

  const onDurationChange = () => {
    const v = videoRef.current;
    if (v && isFinite(v.duration)) setVidDuration(v.duration);
  };

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {}); else v.pause();
  };

  // The clock under the player: where the playhead is in REAL time, which is
  // the only time an operator is thinking in. The segment's own position is
  // meaningless on its own — "four minutes into a file" is not an answer.
  const onTimeUpdate = () => {
    if (!playing || isDraggingRef.current) return;
    const v = videoRef.current;
    if (!v) return;
    setVidCurrent(v.currentTime);
    setAtTime(new Date(new Date(playing.started_at).getTime() + v.currentTime * 1000));
  };

  const live = status?.running;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-6xl rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: "rgba(59,130,246,0.12)" }}>
              <Video size={14} style={{ color: "#3b82f6" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
                Recordings
              </div>
              <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
                Everything the camera saw, kept for {status?.retention_days ?? "—"} days
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-[13px]"
              style={{ color: live ? "#ef4444" : "var(--muted-foreground)" }}>
              <Circle size={9} fill={live ? "#ef4444" : "transparent"}
                style={{ color: live ? "#ef4444" : "var(--muted-foreground)" }} />
              {live ? "Recording" : status?.enabled ? "Not recording" : "Recording is off"}
            </span>
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Archive summary */}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-5 py-2 text-[12px] flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)", color: "var(--muted-foreground)",
                   fontFamily: "'DM Mono', monospace" }}>
          <span className="flex items-center gap-1.5">
            <HardDrive size={12} /> {fmtBytes(status?.bytes)} in {status?.segments ?? 0} file
            {status?.segments === 1 ? "" : "s"}
          </span>
          {status?.oldest && <span>reaches back to {new Date(status.oldest).toLocaleString()}</span>}
          <span>{status?.mode === "source" ? "camera's own video" : "evidence-clip quality"}</span>
          <span>{status?.segment_minutes ?? "—"} min per file</span>
          {status?.free_bytes != null && <span>{fmtBytes(status.free_bytes)} free on disk</span>}
        </div>

        {jumpNotFound && jumpTo && (
          <div className="flex items-center gap-2 px-5 py-2 text-[13px] flex-shrink-0"
            style={{ color: "#f59e0b", borderBottom: "1px solid var(--border)" }}>
            <AlertTriangle size={13} />
            The recording that contained this clip could not be found — it may have been deleted by the retention policy.
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 px-5 py-2 text-[13px] flex-shrink-0"
            style={{ color: "#f59e0b" }}>
            <AlertTriangle size={13} /> {error}
          </div>
        )}

        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          {/* Days */}
          <div className="md:w-56 flex-shrink-0 overflow-y-auto"
            style={{ borderRight: "1px solid var(--border)" }}>
            {loading && (
              <div className="flex items-center gap-2 px-4 py-4 text-[13px]"
                style={{ color: "var(--muted-foreground)" }}>
                <Loader2 size={13} className="animate-spin" /> Reading the archive…
              </div>
            )}
            {!loading && days.length === 0 && (
              <div className="px-4 py-6 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
                No footage yet. Recording starts by itself while LookOut is running
                and the camera is reachable.
              </div>
            )}
            {days.map((d) => {
              const active = d.date === date;
              return (
                <button key={d.date} onClick={() => { setDate(d.date); setPlaying(null); }}
                  className="w-full text-left px-4 py-2.5 transition-colors"
                  style={{ background: active ? "rgba(59,130,246,0.10)" : "transparent",
                           borderBottom: "1px solid var(--border)" }}>
                  <div className="text-[13px] font-medium"
                    style={{ color: active ? "#3b82f6" : "var(--foreground)" }}>
                    {fmtDay(d.date)}
                  </div>
                  <div className="text-[12px]" style={{ color: "var(--muted-foreground)",
                                                        fontFamily: "'DM Mono', monospace" }}>
                    {fmtClock(d.first)} – {fmtClock(d.last)} · {fmtBytes(d.bytes)}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Player and the day's segments */}
          <div className="flex-1 min-w-0 flex flex-col">
            <div className="flex items-center justify-center flex-shrink-0 relative"
              style={{ background: "#000", aspectRatio: "16 / 9", maxHeight: "46vh" }}>
              {playing ? (
                <>
                  <video
                    ref={videoRef}
                    key={playing.src}
                    src={playing.src}
                    autoPlay
                    onPlay={() => setVidPaused(false)}
                    onPause={() => setVidPaused(true)}
                    onTimeUpdate={onTimeUpdate}
                    onDurationChange={onDurationChange}
                    onSeeked={() => { isDraggingRef.current = false; }}
                    onError={onVideoError}
                    onLoadedMetadata={() => {
                      onDurationChange();
                      const v = videoRef.current;
                      if (v && playing.resumeAt) v.currentTime = playing.resumeAt;
                    }}
                    style={{ width: "100%", height: "100%", objectFit: "contain" }}
                  />
                  {/* Custom controls overlay — avoids the React re-render vs native
                      scrubber fight that caused seeking to snap back to the start. */}
                  <div
                    className="absolute bottom-0 left-0 right-0"
                    style={{
                      background: "linear-gradient(to top, rgba(0,0,0,0.88) 0%, transparent 100%)",
                      padding: "36px 14px 10px",
                    }}
                  >
                    <input
                      type="range"
                      min={0}
                      max={vidDuration || 0.001}
                      step={0.1}
                      value={vidCurrent}
                      style={{ width: "100%", display: "block", cursor: "pointer", accentColor: "#ef4444" }}
                      onMouseDown={() => { isDraggingRef.current = true; }}
                      onChange={(e) => setVidCurrent(Number(e.target.value))}
                      onMouseUp={(e) => {
                        const t = Number(e.currentTarget.value);
                        setVidCurrent(t);
                        if (videoRef.current) videoRef.current.currentTime = t;
                        if (playing) setAtTime(new Date(new Date(playing.started_at).getTime() + t * 1000));
                        // isDraggingRef cleared by onSeeked — keeps timeupdate from
                        // snapping the scrubber back before the seek is confirmed.
                      }}
                      onTouchStart={() => { isDraggingRef.current = true; }}
                      onTouchEnd={(e) => {
                        const t = Number(e.currentTarget.value);
                        setVidCurrent(t);
                        if (videoRef.current) videoRef.current.currentTime = t;
                      }}
                    />
                    <div className="flex items-center gap-3 mt-1.5">
                      <button
                        onClick={togglePlay}
                        style={{ color: "#fff", background: "none", border: "none", cursor: "pointer", padding: 0, lineHeight: 0 }}
                      >
                        {vidPaused ? <Play size={15} /> : <Pause size={15} />}
                      </button>
                      <span style={{ color: "rgba(255,255,255,0.8)", fontFamily: "'DM Mono', monospace", fontSize: 11 }}>
                        {fmtTime(vidCurrent)} / {fmtTime(vidDuration)}
                      </span>
                    </div>
                  </div>
                </>
              ) : (
                <div className="text-[13px]" style={{ color: "#6b7280" }}>
                  Choose a time below to play it.
                </div>
              )}
            </div>

            {playing && (
              <div className="flex items-center gap-3 px-5 py-2 flex-shrink-0"
                style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="text-[13px]" style={{ color: "var(--foreground)",
                                                      fontFamily: "'DM Mono', monospace" }}>
                  {atTime ? atTime.toLocaleString() : ""}
                </div>
                <a href={playing.src} download={playing.name}
                  className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[13px]"
                  style={{ border: "1px solid var(--border)", background: "var(--secondary)",
                           color: "var(--foreground)" }}>
                  <Download size={13} /> Save this file
                </a>
              </div>
            )}

            <div className="flex-1 overflow-y-auto">
              {date && segments.length === 0 && (
                <div className="px-5 py-6 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
                  Nothing was recorded on this day.
                </div>
              )}
              <div className="p-3 grid gap-2"
                style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
                {segments.map((s) => {
                  const active = playing?.name === s.name;
                  return (
                    <button key={s.name} onClick={() => open(s)} disabled={!s.playable}
                      title={s.playable ? "" : "Still being recorded — it can be played once this file is finished."}
                      className="text-left px-3 py-2 rounded-lg transition-colors"
                      style={{
                        border: `1px solid ${active ? "#3b82f6" : "var(--border)"}`,
                        background: active ? "rgba(59,130,246,0.10)" : "var(--secondary)",
                        opacity: s.playable ? 1 : 0.55,
                        cursor: s.playable ? "pointer" : "default",
                      }}>
                      <div className="text-[13px] font-medium flex items-center gap-1.5"
                        style={{ color: active ? "#3b82f6" : "var(--foreground)" }}>
                        {fmtClock(s.started_at)}
                        {s.recording && <Circle size={8} fill="#ef4444" style={{ color: "#ef4444" }} />}
                      </div>
                      <div className="text-[11px]" style={{ color: "var(--muted-foreground)",
                                                            fontFamily: "'DM Mono', monospace" }}>
                        {s.recording ? "recording now" : `${fmtLength(s.seconds)} · ${fmtBytes(s.bytes)}`}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
