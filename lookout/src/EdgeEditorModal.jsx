import { useEffect, useRef, useState } from "react";
import {
  X, Loader2, FileVideo, AlertTriangle, Save, WifiOff, Wand2, Info,
  Car, Trash2, Undo2, Maximize2, Minimize2,
} from "lucide-react";
import {
  getCameraSnapshotUrl, uploadCameraEdgeFrame, updateCamera,
  startCameraZoneTrace, getCameraZoneTrace, cancelCameraZoneTrace,
} from "./api";
import { EdgeCanvas } from "./EdgeCanvas";
import { ConfirmDialog } from "./ConfirmDialog";

const ALLOWED_EXTENSIONS = [".mp4", ".mkv", ".avi"];
const MAX_BYTES = 1024 * 1024 * 1024; // 1GB, matches the backend's limit

// How tall the picture may get while expanded: the window, less this modal's
// own header, footer and padding. Points are placed by eye against kerbs and
// wall lines a few pixels wide in the frame's own scale, so the only thing
// that makes them placeable is size — but a picture taller than the window
// hides the far end of the road behind the footer, and the editor would have
// traded one unclickable area for another. One number, used both by the
// canvas and by the hint badge pinned to its corner.
const EXPANDED_FIT = "calc(96vh - 200px)";

// The two areas `Camera.edges` holds, in the order the selector lists them.
// `kind` is the one name that travels: EdgeCanvas's prop, the trace job's
// argument and `traceFor` all speak it, so nothing has to translate between a
// tab name and a zone type.
const ZONES = [
  {
    key: "parking",
    kind: "zone",
    icon: Car,
    label: "No-parking road",
    section: "Road zone",
    traceLabel: "Trace zone",
    tip: "Outline the road edges (3+ points). Follow its perspective; it'll look like a trapezium, not a box. Any vehicle stopped inside counts.",
  },
  {
    key: "disposal",
    kind: "disposal",
    icon: Trash2,
    label: "Improper disposal",
    section: "Rubbish area",
    traceLabel: "Trace area",
    tip: "Outline where rubbish gets dumped: kerb, footpath, corners. Drag to move a point, right-click to remove. Optional; if left empty, it's auto-traced.",
  },
];

// The circled i beside the section header, holding what the long grey
// instruction box used to say. Opens on hover for a mouse and TOGGLES on
// click, because a touch screen has no hover — a tap is the only way in.
function InfoTip({ children }) {
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!pinned) return undefined;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setPinned(false); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setPinned(false); } };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [pinned]);
  const open = hover || pinned;
  return (
    <span ref={ref} className="relative inline-flex align-middle"
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button type="button" aria-label="About this zone" aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setPinned((prev) => !prev); }}
        className="inline-flex items-center justify-center rounded-full"
        style={{ color: open ? "var(--foreground)" : "var(--muted-foreground)", cursor: "pointer" }}>
        <Info size={14} />
      </button>
      {open && (
        <div role="tooltip"
          className="absolute top-full right-0 mt-2 z-[80] w-64 max-w-[80vw] rounded-xl px-3 py-2.5 text-[12px] leading-relaxed shadow-2xl"
          style={{ background: "var(--card)", border: "1px solid var(--foreground)", color: "var(--foreground)" }}>
          {children}
        </div>
      )}
    </span>
  );
}

function validateFile(file) {
  const ext = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `Unsupported file type "${ext}". Allowed: ${ALLOWED_EXTENSIONS.join(", ")}.`;
  }
  if (file.size > MAX_BYTES) return "File too large — limit is 1GB.";
  return null;
}

export function EdgeEditorModal({ camera, onClose, onSaved }) {
  // frame = { src, width, height } — width/height are the SOURCE frame's real
  // pixel dimensions, never the CSS-displayed canvas size.
  const [frame, setFrame] = useState(null);
  const [loadingLive, setLoadingLive] = useState(true);
  const [needsUpload, setNeedsUpload] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [frameError, setFrameError] = useState("");

  // Seeds EdgeCanvas once the frame is known — see acceptFrame below.
  const [prefill, setPrefill] = useState({ points: [], legacyKerbs: false });
  const [edgeSpec, setEdgeSpec] = useState({});
  const [canSave, setCanSave] = useState(false);

  // Two independent areas: parking's no-parking road polygon and improper
  // disposal's watch area. Both canvases stay mounted (the inactive one is just
  // hidden) because EdgeCanvas owns its points — unmounting on a tab switch
  // would throw away a half-drawn shape.
  const [tab, setTab] = useState("parking");
  const [disposalPrefill, setDisposalPrefill] = useState([]);
  const [disposalSpec, setDisposalSpec] = useState({});
  const [disposalPoints, setDisposalPoints] = useState(0);
  // What each canvas first reported, to tell "edited" from "just opened".
  const [parkingInitial, setParkingInitial] = useState(null);
  const [disposalInitial, setDisposalInitial] = useState(null);
  // Undo/Clear live in the controls column, not under the picture, so each
  // canvas hands its two actions out through a ref — see EdgeCanvas.actionsRef.
  const parkingActions = useRef({});
  const disposalActions = useRef({});

  const [pct, setPct] = useState(camera.obstruction_pct ?? 35);
  const [minutes, setMinutes] = useState(camera.obstruction_minutes ?? 2);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  // Expanded = the picture gets the whole window instead of a 1000px modal.
  // Drawing is the one job here and a 2MP frame scaled into a 600px column
  // puts four source pixels under every screen one, which is the difference
  // between a point on the kerb and a point in the gutter. Off by default:
  // the compact modal is the better shape for checking a zone that is already
  // right, which is most openings.
  const [expanded, setExpanded] = useState(false);

  // Live zone trace. `canvasKey` remounts EdgeCanvas (it only reads
  // initialPoints at mount) so a traced polygon lands as ordinary editable
  // points; `edgePoints` mirrors how many points the canvas holds right now.
  const [trace, setTrace] = useState(null);       // null | running job
  const [traceNote, setTraceNote] = useState("");
  const [traceError, setTraceError] = useState("");
  const [canvasKey, setCanvasKey] = useState(0);
  const [disposalCanvasKey, setDisposalCanvasKey] = useState(0);
  // Which canvas the latest trace was for, so its note/error shows on that tab only.
  const [traceFor, setTraceFor] = useState("zone");
  const [edgePoints, setEdgePoints] = useState(0);
  // The `kind` whose retrace is waiting to be confirmed, or null. State rather
  // than window.confirm: that one blocks the whole tab, which stops the alert
  // poll dead, and its OK/Cancel say nothing about which of the two areas is
  // about to be thrown away.
  const [confirmKind, setConfirmKind] = useState(null);

  // Every other question this editor asks, as one record: { title, body,
  // confirmLabel, cancelLabel, tone, run }. One piece of state and one dialog
  // rather than a boolean and a <ConfirmDialog> per question -- leaving is
  // leaving whether it was the X, the backdrop or Cancel that did it, and the
  // three would otherwise drift apart in wording.
  const [pending, setPending] = useState(null);

  const [applyNote, setApplyNote] = useState("");
  const pollRef = useRef(null);
  const tracing = trace?.state === "running";

  const liveUrlRef = useRef(null);
  const prefilledRef = useRef(false);
  const fileInputRef = useRef(null);

  // Escape LEAVES the expanded view rather than closing the editor — with the
  // frame filling the window there is no visible backdrop left to click, and
  // a key that threw away a half-drawn polygon would be the wrong reflex to
  // teach. It only acts while expanded, so the modal's behaviour is otherwise
  // unchanged, and not while a confirmation is up: Escape belongs to the
  // question on top, and shrinking the view out from under it answers nothing.
  useEffect(() => {
    if (!expanded || confirmKind || pending) return undefined;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setExpanded(false); } };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [expanded, confirmKind, pending]);

  // Pre-loads any edges already saved on this camera, scaled from the
  // resolution they were drawn at to whatever frame we just loaded — mirrors
  // watch_parking._build_monitors's own scale-at-read-time logic, so a saved
  // zone still lines up correctly even if this session's frame is a
  // different size than last time. EdgeCanvas only reads `prefill` once, at
  // its own mount, so this must be set in the same tick as `frame` — it is,
  // since acceptFrame is the only thing that sets either and React batches
  // the two setState calls together.
  const acceptFrame = (nextFrame) => {
    setFrame(nextFrame);
    setFrameError("");
    if (prefilledRef.current) return;
    prefilledRef.current = true;

    const stored = camera.edges || {};
    const srcW = camera.edges_width, srcH = camera.edges_height;
    const scaleX = srcW ? nextFrame.width / srcW : 1;
    const scaleY = srcH ? nextFrame.height / srcH : 1;
    const scale = (points) => points.map(([x, y]) => ({ x: x * scaleX, y: y * scaleY }));

    // A ZONE is found by its "type", not by its key, so a camera saved under
    // any key name still loads.
    const zoneSpec = Object.values(stored).find(
      (spec) => spec && spec.type === "zone" && Array.isArray(spec.points) && spec.points.length >= 3,
    );

    // Kerb lines (open paths keyed left/right, no "type") were the other way
    // of marking the area before the move to a single polygon. They cannot be
    // converted — a half-plane running off to infinity is not a closed shape —
    // so a camera still holding one opens with an EMPTY canvas and is told to
    // retrace. Silently prefilling nothing with no explanation would read as
    // the saved area having been lost.
    const legacyKerbs = !zoneSpec && ["left", "right"].some((side) => {
      const spec = stored[side];
      return spec && spec.type !== "zone" && Array.isArray(spec.points) && spec.points.length >= 2;
    });

    setPrefill({
      points: zoneSpec ? scale(zoneSpec.points) : [],
      legacyKerbs,
    });

    // The disposal area is the "disposal" entry of the same `edges` object,
    // drawn at the same frame size as the road, so it scales the same way.
    const disposalZone = Object.values(stored).find(
      (spec) => spec && spec.type === "disposal" && Array.isArray(spec.points) && spec.points.length >= 3,
    );
    setDisposalPrefill(disposalZone ? scale(disposalZone.points) : []);
  };

  // Try a live snapshot first; fall back to "upload a clip" if the camera
  // isn't reachable. naturalWidth/naturalHeight (not any CSS size) become
  // this frame's recorded resolution.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const url = await getCameraSnapshotUrl(camera.dbId);
        const img = new Image();
        img.onload = () => {
          if (cancelled) { URL.revokeObjectURL(url); return; }
          liveUrlRef.current = url;
          acceptFrame({ src: url, width: img.naturalWidth, height: img.naturalHeight });
          setLoadingLive(false);
        };
        img.onerror = () => {
          URL.revokeObjectURL(url);
          if (!cancelled) { setLoadingLive(false); setNeedsUpload(true); }
        };
        img.src = url;
      } catch {
        if (!cancelled) { setLoadingLive(false); setNeedsUpload(true); }
      }
    })();
    return () => {
      cancelled = true;
      if (liveUrlRef.current) URL.revokeObjectURL(liveUrlRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camera.dbId]);

  const pickFile = async (file) => {
    if (!file) return;
    const problem = validateFile(file);
    if (problem) { setFrameError(problem); return; }
    setUploading(true);
    setFrameError("");
    try {
      const res = await uploadCameraEdgeFrame(camera.dbId, file);
      acceptFrame({ src: res.image, width: res.width, height: res.height });
      setNeedsUpload(false);
    } catch (err) {
      setFrameError(err.message || "Could not read that clip.");
    } finally {
      setUploading(false);
    }
  };

  useEffect(() => () => clearInterval(pollRef.current), []);

  const finishTrace = (job) => {
    clearInterval(pollRef.current);
    setTrace(null);
    if (job.state === "done") {
      // Points are in the stream's own pixels (main stream); the frame on screen
      // is the snapshot (often the sub-stream), so scale — the same job
      // acceptFrame does for saved edges.
      const sx = frame.width / job.width, sy = frame.height / job.height;
      const traced = job.points.map(([x, y]) => ({ x: x * sx, y: y * sy }));
      // The result belongs to whichever canvas asked for it, and ONLY that
      // one is reseeded: tracing the rubbish area must not move the road.
      if (job.kind === "disposal") {
        setDisposalPrefill(traced);
        setDisposalCanvasKey((k) => k + 1);
      } else {
        setPrefill({ points: traced, legacyKerbs: false });
        setCanvasKey((k) => k + 1);
      }
      const n = job.report.moving_tracks;
      setTraceNote(
        `Traced from ${n} moving vehicle${n === 1 ? "" : "s"} across ${job.report.samples} frames — check it before saving.`);
    } else if (job.state !== "cancelled") {
      setTraceError(job.reason || "Could not trace a zone.");
    }
  };

  const pollTrace = () => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const job = await getCameraZoneTrace(camera.dbId);
        if (job.state === "running") setTrace(job);
        else finishTrace(job);
      } catch (err) {
        clearInterval(pollRef.current);
        setTrace(null);
        setTraceError(err.message || "Lost contact with the server while tracing.");
      }
    }, 1000);
  };

  // `confirmed` is set by the dialog's own button, so the first call with a
  // shape already drawn only ASKS and the second one traces.
  const handleTrace = async (kind = "zone", confirmed = false) => {
    if (tracing || !frame) return;
    const existing = kind === "disposal" ? disposalPoints : edgePoints;
    if (existing > 0 && !confirmed) { setConfirmKind(kind); return; }
    setTraceError(""); setTraceNote(""); setApplyNote("");
    setTraceFor(kind);
    try {
      setTrace(await startCameraZoneTrace(camera.dbId, kind));
    } catch (err) {
      if (err.status !== 409) {
        setTraceError(err.message || "Could not start the trace.");
        return;
      }
      setTrace({ state: "running", progress: 0 });   // already running: just follow it
    }
    pollTrace();
  };

  const handleCancelTrace = async () => {
    try { await cancelCameraZoneTrace(camera.dbId); } catch { /* the poll reports the outcome */ }
  };

  // Only the area(s) actually changed are sent, so saving a disposal edit does
  // not restart parking (and vice versa) — the server swaps one process per
  // area that arrives.
  const parkingDirty = JSON.stringify(edgeSpec) !== parkingInitial
    || Number(pct) !== Number(camera.obstruction_pct ?? 35)
    || Number(minutes) !== Number(camera.obstruction_minutes ?? 2);
  const disposalDirty = JSON.stringify(disposalSpec) !== disposalInitial;
  const savable = (parkingDirty || disposalDirty) && (!parkingDirty || canSave);

  const handleSave = async () => {
    if (!savable || !frame) return;
    setSaving(true);
    setSaveError("");
    try {
      // Both areas live in ONE `edges` object and share its frame size, so a
      // save always sends both — each canvas's current shape, which for the
      // one that was not touched is simply what it was opened with, rescaled
      // to this frame. Sending only the edited one would delete the other.
      // The SERVER works out which of the two actually changed and restarts
      // only that detector.
      const body = {
        edges: { ...edgeSpec, ...disposalSpec },
        edges_width: frame.width,
        edges_height: frame.height,
      };
      if (parkingDirty) {
        body.obstruction_pct = Number(pct);
        body.obstruction_minutes = Number(minutes);
      }
      const updated = await updateCamera(camera.dbId, body);
      onSaved?.(updated);
      // Each detector reads its area once at start; the server swaps only that
      // process when monitoring is running (CameraViewSet.update). Say so
      // instead of closing, so the admin knows the new zone is being applied.
      const notes = [
        {
          restarting: "Parking is restarting to use the new zone — the other detectors keep running.",
          started: "Parking is starting now that the camera has a zone.",
          stopped: "The zone was cleared, so parking is switched off.",
        }[updated?.zone_applied],
        {
          restarting: "Improper disposal is restarting to use its new area.",
          started: "Improper disposal is starting with its new area.",
        }[updated?.disposal_zone_applied],
      ].filter(Boolean);
      setParkingInitial(JSON.stringify(edgeSpec));
      setDisposalInitial(JSON.stringify(disposalSpec));
      if (notes.length) setApplyNote(`Saved. ${notes.join(" ")}`);
      else onClose();
    } catch (err) {
      setSaveError(err.message || "Could not save.");
    } finally {
      setSaving(false);
    }
  };

  // Leaving with a shape drawn and not saved. All three ways out come through
  // here -- the X, the backdrop and Cancel -- so none of them can quietly bin
  // a traced or hand-drawn area. Clean (or mid-save) just leaves.
  const requestClose = () => {
    if (saving) return;
    if (!parkingDirty && !disposalDirty) { onClose(); return; }
    const parts = [
      parkingDirty ? "the road zone" : null,
      disposalDirty ? "the rubbish area" : null,
    ].filter(Boolean);
    setPending({
      title: "Leave without saving?",
      body: `Your changes to ${parts.join(" and ")} have not been saved.`
        + " Closing now leaves the camera on whatever was stored before,"
        + " and the detectors keep running against that.",
      confirmLabel: "Discard and close",
      cancelLabel: "Keep editing",
      tone: "#ef4444",
      run: () => onClose(),
    });
  };

  // Clear wipes the whole polygon in one press and Undo only walks back one
  // point at a time, so an accidental Clear on a twelve-point road is twelve
  // presses to recover -- if the operator even notices which area went.
  const requestClear = () => {
    if (!activeCount) return;
    setPending({
      title: `Clear the ${active.key === "disposal" ? "rubbish area" : "road zone"}?`,
      body: `This removes all ${activeCount} point${activeCount === 1 ? "" : "s"}`
        + ` from the ${active.label.toLowerCase()} and leaves the other area alone.`
        + " Nothing is saved until you press Save zones.",
      confirmLabel: "Clear it",
      cancelLabel: "Keep mine",
      tone: "#ef4444",
      run: () => activeActions.current?.clear?.(),
    });
  };

  // Saving is not just a write: the server swaps the live detector process for
  // each area that changed, so a camera being watched stops and restarts.
  const requestSave = () => {
    if (!savable || !frame) return;
    const parts = [
      parkingDirty ? "Parking" : null,
      disposalDirty ? "Improper disposal" : null,
    ].filter(Boolean);
    setPending({
      title: "Save and apply these zones?",
      body: `${parts.join(" and ")} ${parts.length > 1 ? "restart" : "restarts"}`
        + " to pick up the new area, which takes a few seconds. Any other"
        + " detector on this camera keeps running.",
      confirmLabel: "Save zones",
      cancelLabel: "Not yet",
      run: () => handleSave(),
    });
  };

  // Whichever area the selector is on: its copy, its point count and the
  // canvas the shared Undo/Clear pair drives.
  const active = ZONES.find((zone) => zone.key === tab) ?? ZONES[0];
  const activeActions = tab === "disposal" ? disposalActions : parkingActions;
  const activeCount = tab === "disposal" ? disposalPoints : edgePoints;

  // Expanded, the picture is capped at EXPANDED_FIT and its column is wider
  // than that cap allows, so the wrapper is capped to the same width: the
  // "Click to add points" badge is pinned to the wrapper's top-right corner
  // and would otherwise float in the empty gap beside the frame.
  const fitHeight = expanded ? EXPANDED_FIT : null;
  const pictureStyle = fitHeight && frame
    ? { width: "100%", maxWidth: `calc(${fitHeight} * ${frame.width / frame.height})`, margin: "0 auto" }
    : undefined;

  return (
    <div
      className={`fixed inset-0 z-[70] flex items-center justify-center ${expanded ? "p-2" : "p-4"}`}
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget && !saving) requestClose(); }}
    >
      <div
        className={`w-full rounded-2xl overflow-hidden shadow-2xl flex flex-col ${expanded ? "max-w-[1800px]" : "max-w-[1000px]"}`}
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: expanded ? "96vh" : "92vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
              Edge zones
            </div>
            <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
              {camera.name} ({camera.id})
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            {frame && (
              <button onClick={() => setExpanded((v) => !v)}
                title={expanded ? "Shrink the view (Esc)" : "Expand the view for drawing"}
                aria-label={expanded ? "Shrink the view" : "Expand the view"}
                aria-pressed={expanded}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[12px] font-medium"
                style={{ color: "var(--foreground)", background: "var(--secondary)", border: "1px solid var(--border)" }}>
                {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                {expanded ? "Shrink" : "Expand"}
              </button>
            )}
            {!saving && (
              <button onClick={requestClose} className="p-1.5 rounded-lg"
                style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
                <X size={14} />
              </button>
            )}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3.5">
          {loadingLive && (
            <div className="flex flex-col items-center justify-center gap-2 py-16">
              <Loader2 size={22} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
              <div className="text-[14px]" style={{ color: "var(--muted-foreground)" }}>
                Requesting a live snapshot…
              </div>
            </div>
          )}

          {!loadingLive && needsUpload && !frame && (
            <div>
              <div className="flex items-center gap-2 text-[14px] px-3 py-2.5 rounded-xl mb-3"
                style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)", color: "#f59e0b" }}>
                <WifiOff size={13} className="flex-shrink-0" />
                <span>Camera unreachable for a live snapshot — upload a clip to grab a frame from it instead.</span>
              </div>
              <div
                onClick={() => !uploading && fileInputRef.current?.click()}
                className="rounded-xl flex flex-col items-center justify-center gap-2 py-10 px-4 cursor-pointer transition-all"
                style={{
                  border: "1.5px dashed var(--border)",
                  background: "var(--secondary)",
                  cursor: uploading ? "not-allowed" : "pointer",
                }}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ALLOWED_EXTENSIONS.join(",")}
                  className="hidden"
                  disabled={uploading}
                  onChange={(e) => pickFile(e.target.files?.[0])}
                />
                {uploading ? (
                  <>
                    <Loader2 size={22} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
                    <div className="text-[14px]" style={{ color: "var(--muted-foreground)" }}>Reading first frame…</div>
                  </>
                ) : (
                  <>
                    <FileVideo size={22} style={{ color: "var(--muted-foreground)" }} />
                    <div className="text-[14px] text-center" style={{ color: "var(--muted-foreground)" }}>
                      Click to choose a clip
                    </div>
                    <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                      .mp4, .mkv, .avi — up to 1GB
                    </div>
                  </>
                )}
              </div>
              {frameError && (
                <div className="flex items-start gap-2 text-[14px] px-3 py-2.5 rounded-xl mt-3"
                  style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                  <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                  <span>{frameError}</span>
                </div>
              )}
            </div>
          )}

          {frame && (
            <>
              {prefill.legacyKerbs && (
                <div className="text-[13px] px-3 py-2.5 rounded-xl"
                  style={{ background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.3)", color: "#f59e0b" }}>
                  This camera still has the old kerb lines saved. Parking now uses a single
                  closed polygon around the road, which a kerb line cannot be converted into —
                  trace the road again and save to replace them.
                </div>
              )}
              {/* Picture on the left, every control on the right. Below md
                  the controls stack under the frame rather than being squeezed
                  beside it. */}
              <div className={`grid grid-cols-1 gap-4 items-start ${
                expanded ? "md:grid-cols-[minmax(0,1fr)_300px]" : "md:grid-cols-[minmax(0,60fr)_minmax(0,40fr)]"
              }`}>
                {/* LEFT - the frame and its overlay. Both canvases stay MOUNTED
                    (the inactive one is only hidden) because EdgeCanvas owns
                    its points: unmounting on a switch would throw away a
                    half-drawn shape. */}
                <div className="relative" style={pictureStyle}>
                  <div className={tab === "parking" ? "" : "hidden"}>
                    <EdgeCanvas
                      key={canvasKey}
                      frame={frame}
                      initialPoints={prefill.points}
                      chrome={false}
                      showThresholds={false}
                      maxHeight={fitHeight}
                      actionsRef={parkingActions}
                      onChange={(spec, can, count) => {
                        setParkingInitial((prev) => prev ?? JSON.stringify(spec));
                        setEdgeSpec(spec); setCanSave(can);
                        setEdgePoints(count);
                      }}
                    />
                  </div>
                  <div className={tab === "disposal" ? "" : "hidden"}>
                    <EdgeCanvas
                      key={disposalCanvasKey}
                      frame={frame}
                      kind="disposal"
                      initialPoints={disposalPrefill}
                      chrome={false}
                      showThresholds={false}
                      maxHeight={fitHeight}
                      actionsRef={disposalActions}
                      onChange={(spec, can, count) => {
                        setDisposalInitial((prev) => prev ?? JSON.stringify(spec));
                        setDisposalSpec(spec);
                        setDisposalPoints(count);
                      }}
                    />
                  </div>
                  {/* The drawing affordance, in place of the instruction box.
                      Hidden while the server is tracing, when the next thing to
                      happen is a polygon arriving rather than a click. */}
                  {!tracing && (
                    <div className="absolute top-2 right-2 px-2 py-1 rounded-lg text-[11px] font-medium pointer-events-none"
                      style={{ background: "rgba(0,0,0,0.6)", color: "#ffffff" }}>
                      Click to add points
                    </div>
                  )}
                </div>

                {/* RIGHT - the controls for whichever area is selected. */}
                <div className="space-y-3">
                  <div className="text-[11px] font-medium uppercase tracking-wider"
                    style={{ color: "var(--muted-foreground)" }}>
                    Zone type
                  </div>

                  {/* Each row carries its own status, so how much is drawn on
                      the OTHER area is readable without switching to it. */}
                  <div className="space-y-1.5">
                    {ZONES.map((zone) => {
                      const on = tab === zone.key;
                      const count = zone.key === "disposal" ? disposalPoints : edgePoints;
                      const Icon = zone.icon;
                      return (
                        <button key={zone.key} onClick={() => setTab(zone.key)}
                          className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl text-[13px] font-medium"
                          style={{
                            background: on ? "var(--foreground)" : "var(--secondary)",
                            color: on ? "var(--card)" : "var(--muted-foreground)",
                            border: "1px solid var(--border)",
                          }}>
                          <Icon size={14} className="flex-shrink-0" />
                          <span className="flex-1 text-left">{zone.label}</span>
                          <span className="text-[12px]" style={{ opacity: 0.7 }}>
                            {count ? `${count} pt${count === 1 ? "" : "s"}` : "Not set"}
                          </span>
                        </button>
                      );
                    })}
                  </div>

                  <div style={{ borderTop: "1px solid var(--border)" }} />

                  <div className="flex items-center justify-between gap-2">
                    <div className="text-[13px] font-semibold" style={{ color: "var(--foreground)" }}>
                      {active.section}
                    </div>
                    <InfoTip>{active.tip}</InfoTip>
                  </div>

                  <button
                    onClick={() => handleTrace(active.kind)}
                    disabled={tracing || saving}
                    className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl text-[13px] font-medium"
                    style={{ background: "var(--secondary)", color: "var(--foreground)",
                      border: "1px solid var(--border)", opacity: tracing ? 0.6 : 1,
                      cursor: tracing ? "not-allowed" : "pointer" }}
                  >
                    {tracing ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                    {active.traceLabel}
                  </button>

                  {tracing && (
                    <div className="flex items-center justify-between gap-2 text-[13px]"
                      style={{ color: "var(--muted-foreground)" }}>
                      <span>
                        Watching traffic… ~{trace.estimated_seconds ?? 48} s
                        {" "}({Math.round((trace.progress ?? 0) * 100)}%)
                      </span>
                      <button onClick={handleCancelTrace} className="underline flex-shrink-0"
                        style={{ color: "var(--muted-foreground)" }}>
                        Cancel
                      </button>
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-2">
                    <button onClick={() => activeActions.current?.undo?.()} disabled={!activeCount}
                      className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-[13px] font-medium disabled:opacity-40"
                      style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
                      <Undo2 size={13} /> Undo
                    </button>
                    <button onClick={requestClear} disabled={!activeCount}
                      className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-[13px] font-medium disabled:opacity-40"
                      style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
                      <Trash2 size={13} /> Clear
                    </button>
                  </div>

                  {/* A trace's outcome belongs to the area that asked for it. */}
                  {traceNote && traceFor === active.kind && (
                    <div className="flex items-start gap-2 text-[13px] px-3 py-2.5 rounded-xl"
                      style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.25)", color: "#22c55e" }}>
                      <Info size={13} className="flex-shrink-0 mt-0.5" /><span>{traceNote}</span>
                    </div>
                  )}
                  {traceError && traceFor === active.kind && (
                    <div className="flex items-start gap-2 text-[13px] px-3 py-2.5 rounded-xl"
                      style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                      <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                      <span>{traceError} You can draw it by hand instead.</span>
                    </div>
                  )}

                  {/* Parking's two thresholds. This modal saves them, so they
                      stay with the area they belong to rather than going out
                      with the instruction box. */}
                  {/* One sentence on one line — "Inside the road 35 % for 2
                      minutes" reads as the rule it is, and two stacked rows
                      spent the height of the controls column saying it.
                      Wraps rather than overflows on a narrow column. */}
                  {tab === "parking" && (
                    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1.5 pt-1 text-[13px]"
                      style={{ color: "var(--muted-foreground)" }}>
                      <span>Inside the road</span>
                      <input type="number" min={10} max={90} value={pct}
                        aria-label="How much of the vehicle must be inside the road, as a percentage"
                        onChange={(e) => setPct(e.target.value)}
                        className="w-14 px-2 py-1 rounded-lg text-sm outline-none"
                        style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
                      <span>% for</span>
                      <input type="number" min={0.1} step={0.1} value={minutes}
                        aria-label="How long it must stand there, in minutes"
                        onChange={(e) => setMinutes(e.target.value)}
                        className="w-14 px-2 py-1 rounded-lg text-sm outline-none"
                        style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
                      <span>minutes</span>
                    </div>
                  )}
                </div>
              </div>

              {applyNote && (
                <div className="flex items-start gap-2 text-[13px] px-3 py-2.5 rounded-xl"
                  style={{ background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.25)", color: "#22c55e" }}>
                  <Info size={13} className="flex-shrink-0 mt-0.5" /><span>{applyNote}</span>
                </div>
              )}

              {saveError && (
                <div className="flex items-start gap-2 text-[14px] px-3 py-2.5 rounded-xl"
                  style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                  <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                  <span>{saveError}</span>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 flex items-center justify-end gap-2 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          {!saving && (
            <button onClick={requestClose} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              {applyNote ? "Close" : "Cancel"}
            </button>
          )}
          <button
            disabled={!savable || saving || !!applyNote}
            onClick={requestSave}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium"
            style={{
              background: "#f59e0b",
              color: "#0c0f16",
              cursor: (!savable || saving) ? "not-allowed" : "pointer",
              opacity: (!savable || saving) ? 0.5 : 1,
            }}
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            {saving ? "Saving…" : "Save zones"}
          </button>
        </div>
      </div>

      {/* Tracing over a shape somebody has already drawn. The question names
          the area and how many points go, because the two areas look alike on
          screen and only one of them is being replaced. */}
      <ConfirmDialog
        open={!!confirmKind}
        title={`Replace the ${confirmKind === "disposal" ? "rubbish area" : "road zone"}?`}
        body={(() => {
          const n = confirmKind === "disposal" ? disposalPoints : edgePoints;
          const where = confirmKind === "disposal" ? "rubbish area" : "road zone";
          return `Tracing throws away the ${n} point${n === 1 ? "" : "s"} drawn on the ${where}`
            + " and puts its own there instead. Nothing is saved until you press Save zones,"
            + " and the other area is left alone.";
        })()}
        confirmLabel="Replace and trace"
        cancelLabel="Keep mine"
        onCancel={() => setConfirmKind(null)}
        onConfirm={() => {
          const kind = confirmKind;
          setConfirmKind(null);
          handleTrace(kind, true);
        }}
      />

      {/* Leaving, clearing and saving. One dialog driven by `pending`, so the
          wording lives with the action that raised it. */}
      <ConfirmDialog
        open={!!pending}
        title={pending?.title ?? ""}
        body={pending?.body ?? ""}
        confirmLabel={pending?.confirmLabel ?? "Confirm"}
        cancelLabel={pending?.cancelLabel ?? "Cancel"}
        tone={pending?.tone}
        onCancel={() => setPending(null)}
        onConfirm={() => {
          const run = pending?.run;
          setPending(null);
          run?.();
        }}
      />
    </div>
  );
}
