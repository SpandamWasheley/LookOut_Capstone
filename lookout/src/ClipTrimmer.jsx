import { useEffect, useRef, useState } from "react";
import { Scissors, Play, Pause, RotateCcw } from "lucide-react";

// Pick the span of an uploaded clip to actually run detection over.
//
// Detection costs roughly 0.2-0.5s per frame on CPU, so a five-minute clip is
// twenty-plus minutes of waiting — almost all of it footage nobody is testing.
// This is the difference between asking "is my zone right?" and getting an
// answer in two minutes rather than twenty.
//
// The clip is previewed straight from the local File via an object URL: the
// browser already has the bytes, so there is nothing to download and this works
// before (and independently of) the upload. Nothing is cut — the span is sent
// as seconds and the detector seeks to it (core/vision/trim.py), so no second
// copy of the clip is ever written.
const MIN_SPAN = 1; // seconds; a span shorter than this can't show anything

function clock(seconds) {
  if (!Number.isFinite(seconds)) return "0:00";
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function ClipTrimmer({ file, onChange }) {
  const videoRef = useRef(null);
  const [duration, setDuration] = useState(0);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [at, setAt] = useState(0);

  const [url, setUrl] = useState("");

  // Create and revoke in the SAME effect, so the URL's lifetime is exactly the
  // effect's. Deriving it with useMemo and revoking in a separate effect looks
  // tidier and is broken: StrictMode runs effect -> cleanup -> effect, the
  // cleanup revokes the blob, but useMemo does not re-run for unchanged deps,
  // so the <video> is left pointing at a revoked URL — ERR_FILE_NOT_FOUND.
  // Here the second run mints a fresh URL, which is why the pairing matters.
  //
  // Revoking is not optional housekeeping: an object URL pins the whole clip in
  // memory until released, and these run to hundreds of MB.
  useEffect(() => {
    if (!file) return undefined;
    const next = URL.createObjectURL(file);
    // The URL is a browser resource whose lifetime this effect owns, so it
    // cannot be derived during render — see the note above.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);

  // Report upward. 0/0 means "whole clip" to the backend, so an untouched
  // trimmer sends nothing and behaves exactly as before this existed.
  useEffect(() => {
    const whole = start <= 0 && (end <= 0 || end >= duration);
    onChange?.(whole ? { start: 0, end: 0 } : { start, end });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [start, end, duration]);

  const onLoaded = () => {
    const d = videoRef.current?.duration ?? 0;
    if (!Number.isFinite(d) || d <= 0) return;
    setDuration(d);
    setStart(0);
    setEnd(d);
  };

  // Previewing the SELECTION, not the clip: playback loops inside [start, end]
  // so what you see is exactly what the detector will be given.
  //
  // Looping by rewinding only — never by pausing. pause() here used to run off
  // a stale `playing` and cancel a play() that had not resolved yet, which the
  // browser reports as "AbortError: The play() request was interrupted by a
  // call to pause()".
  const onTimeUpdate = () => {
    const v = videoRef.current;
    if (!v) return;
    setAt(v.currentTime);
    if (end > 0 && v.currentTime >= end) v.currentTime = start;
  };

  // `playing` is driven by the element's own play/pause events rather than set
  // alongside the call, so it cannot disagree with what the video is doing —
  // autoplay refusals and end-of-media included.
  const toggle = () => {
    const v = videoRef.current;
    if (!v) return;
    if (!v.paused) {
      v.pause();
      return;
    }
    if (v.currentTime < start || v.currentTime >= end) v.currentTime = start;
    // play() rejects on an interrupted or refused start; unhandled, that is an
    // "Uncaught (in promise)" in the console for something entirely routine.
    v.play().catch(() => {});
  };

  const seekTo = (t) => {
    const v = videoRef.current;
    if (v) v.currentTime = t;
  };

  const changeStart = (value) => {
    const next = Math.min(Number(value), Math.max(end - MIN_SPAN, 0));
    setStart(next);
    seekTo(next);
  };

  const changeEnd = (value) => {
    const next = Math.max(Number(value), start + MIN_SPAN);
    setEnd(Math.min(next, duration));
    seekTo(Math.max(next - 1, start));
  };

  const reset = () => { setStart(0); setEnd(duration); seekTo(0); };

  if (!file) return null;

  const span = Math.max(end - start, 0);
  const trimmed = start > 0 || (duration > 0 && end < duration);
  const pct = (t) => (duration > 0 ? (t / duration) * 100 : 0);

  return (
    <div className="rounded-xl p-4 space-y-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide"
          style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
          <Scissors size={13} /> Trim the clip
        </div>
        <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
          {trimmed
            ? <>Running <b style={{ color: "#22c55e" }}>{clock(span)}</b> of {clock(duration)} — {clock(start)} to {clock(end)}</>
            : <>Whole clip · {clock(duration)}</>}
        </div>
      </div>

      <video
        ref={videoRef}
        src={url}
        onLoadedMetadata={onLoaded}
        onTimeUpdate={onTimeUpdate}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        className="w-full rounded-lg"
        style={{ maxHeight: 280, background: "#000" }}
        muted
        playsInline
      />

      {/* The selected span, drawn over the full length. */}
      <div className="relative h-2 rounded-full" style={{ background: "var(--secondary)" }}>
        <div className="absolute h-2 rounded-full"
          style={{ left: `${pct(start)}%`, width: `${pct(span)}%`, background: "#22c55e" }} />
        <div className="absolute w-0.5 h-2" style={{ left: `${pct(at)}%`, background: "var(--foreground)" }} />
      </div>

      <div className="space-y-1.5">
        <label className="flex items-center gap-3 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
          <span className="w-10">Start</span>
          <input type="range" min={0} max={duration || 0} step={0.1} value={start}
            onChange={(e) => changeStart(e.target.value)} className="flex-1" />
          <span className="w-12 text-right font-mono" style={{ color: "var(--foreground)" }}>{clock(start)}</span>
        </label>
        <label className="flex items-center gap-3 text-[13px]" style={{ color: "var(--muted-foreground)" }}>
          <span className="w-10">End</span>
          <input type="range" min={0} max={duration || 0} step={0.1} value={end}
            onChange={(e) => changeEnd(e.target.value)} className="flex-1" />
          <span className="w-12 text-right font-mono" style={{ color: "var(--foreground)" }}>{clock(end)}</span>
        </label>
      </div>

      <div className="flex items-center gap-2">
        <button onClick={toggle}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-medium"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          {playing ? <Pause size={12} /> : <Play size={12} />} Preview selection
        </button>
        <button onClick={reset} disabled={!trimmed}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-medium disabled:opacity-40"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          <RotateCcw size={12} /> Whole clip
        </button>
        <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
          Nothing is re-encoded — the detector seeks to this span.
        </span>
      </div>
    </div>
  );
}
