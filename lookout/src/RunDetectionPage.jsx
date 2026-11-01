import { useCallback, useEffect, useRef, useState } from "react";
import { Upload, FileVideo, Loader2, AlertTriangle, RotateCcw, Play, Radio, Square, CheckCircle2, X, History, Wand2, Info } from "lucide-react";
import {
  startStagedDetectionJob, startLiveDetectionJob, getCameras, getJobState, cancelDetectionJob,
  traceZoneFromStaged,
} from "./api";
import { discardUpload, listPendingUploads, uploadClipInChunks } from "./chunkedUpload";
import { DETECTION_TYPES, TYPES_WITH_EDGES, TYPES_WITH_OPTIONAL_EDGES, TYPES_WITH_AREA_THRESHOLDS,
         edgeHeading, edgeCameraNote } from "./constants/detectionTypes";
import { ClipTrimmer } from "./ClipTrimmer";
import { EdgeCanvas } from "./EdgeCanvas";
import { ProcessingView } from "./ProcessingView";
import { useTestingTools } from "./useTestingTools";

const ALLOWED_EXTENSIONS = [".mp4", ".mkv", ".avi"];
const MAX_BYTES = 1024 * 1024 * 1024; // 1GB, matches the backend's limit

function formatBytes(bytes) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function validateFile(file) {
  const ext = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `Unsupported file type "${ext}". Allowed: ${ALLOWED_EXTENSIONS.join(", ")}.`;
  }
  if (file.size > MAX_BYTES) return `File too large (${formatBytes(file.size)}) — limit is 1 GB.`;
  return null;
}

const barStyle = { background: "var(--card)", border: "1px solid var(--border)" };
const Err = ({ children }) => (
  <div className="flex items-start gap-2 text-[14px] px-3 py-2 rounded-xl"
    style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
    <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" /> <span>{children}</span>
  </div>
);

// Run Detection: set up a run in one compact full-width bar, then watch it process.
//
//   Setup bar      detector, source (upload a clip / live camera), "recorded at", Start
//   While running  the bar collapses to "Stop / New run" and the processing view takes the page:
//                  the picture on the left (about 70%), the events on the right (about 30%)
//
// Parking and Merged (All 4) judge vehicles against a drawn no-parking area (see
// TYPES_WITH_EDGES). For an uploaded clip the area is drawn on the clip's first frame, below the
// bar, every time (it is not remembered between uploads) and Start stays disabled until it is. For
// a live camera the area is the one saved on the camera (Live Feeds -> Edge Zones).
export function RunDetectionPage() {
  const testingTools = useTestingTools(true);
  const [violationType, setViolationType] = useState(DETECTION_TYPES[0].key);
  const needsEdges = TYPES_WITH_EDGES.has(violationType);
  // Offered, but never a precondition — see TYPES_WITH_OPTIONAL_EDGES.
  const edgesOptional = TYPES_WITH_OPTIONAL_EDGES.has(violationType);
  // Whether the "inside the road %" / "for N minutes" boxes belong to this
  // detector. NOT the same question as whether the area is optional: parking's
  // area is optional now but its thresholds are its entire rule, while
  // disposal draws an area and has no thresholds at all.
  const hasAreaThresholds = TYPES_WITH_AREA_THRESHOLDS.has(violationType);
  const [source, setSource] = useState("file"); // "file" | "camera"

  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  // { stagedToken, sourceFilename, frame: { src, width, height } } once the clip's first frame is read.
  const [staged, setStaged] = useState(null);
  const [staging, setStaging] = useState(false);
  // What chunkedUpload.js last reported: { phase, pct, sentBytes, totalBytes,
  // resumedBytes }, or null when nothing is in flight. `phase` matters as much
  // as the number — "uploading" has a real percentage, "assembling" (the
  // server stitching the chunks and decoding the first frame) has none.
  const [progress, setProgress] = useState(null);
  const [stageError, setStageError] = useState("");
  // Uploads this user left part-finished on an earlier visit. The server still
  // holds those chunks; what it cannot hold is the file handle, so the only way
  // back is for the user to pick the same clip again.
  const [pending, setPending] = useState([]);
  // Cancels the chunks in flight. Deliberately does NOT throw away the chunks
  // already stored — that is what makes stopping cheap to undo.
  const abortRef = useRef(null);
  // Which clip pick is the current one; see pickFile. Only the newest pick may
  // write `staged`, so an older upload finishing late cannot decide which clip
  // the run uses.
  const pickSeqRef = useRef(0);

  const [cameras, setCameras] = useState([]);
  const [camerasLoading, setCamerasLoading] = useState(false);
  const [camerasError, setCamerasError] = useState("");
  const [cameraId, setCameraId] = useState("");

  // Seconds of the clip to run over; {0, 0} means all of it.
  const [trim, setTrim] = useState({ start: 0, end: 0 });
  const [edgeSpec, setEdgeSpec] = useState({});
  const [canSaveEdges, setCanSaveEdges] = useState(false);
  const [pct, setPct] = useState(50);
  const [minutes, setMinutes] = useState(5);

  // Auto-trace: the server watches where traffic drives in the clip that is
  // ALREADY STAGED and proposes the polygon, so the usual dozen clicks become
  // "check this". `canvasKey` is how the result reaches the canvas — EdgeCanvas
  // seeds its points from `initialPoints` at MOUNT, so remounting it under a
  // new key is what re-seeds it. Cheaper than teaching it to accept points
  // after the fact, and it keeps one seeding path rather than two that can
  // drift.
  const [tracing, setTracing] = useState(false);
  const [traceError, setTraceError] = useState("");
  const [traced, setTraced] = useState(null);     // the report, once one lands
  const [tracedPoints, setTracedPoints] = useState([]);
  const [canvasKey, setCanvasKey] = useState(0);

  // When the clip was recorded (datetime-local). Optional; becomes --clock for the detector.
  const [recordedAt, setRecordedAt] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [job, setJob] = useState(null);
  const [jobStatus, setJobStatus] = useState("running");
  const [stopping, setStopping] = useState(false);

  // Fetch once per visit to the camera source. "Loading" is NOT a dependency:
  // it used to be, so setting it re-ran the effect, whose cleanup discarded
  // the in-flight result and left "Loading cameras…" up for ever.
  const camerasFetchedRef = useRef(false);
  useEffect(() => {
    if (source !== "camera" || camerasFetchedRef.current) return;
    camerasFetchedRef.current = true;
    (async () => {
      setCamerasLoading(true);
      setCamerasError("");
      try {
        const list = await getCameras();
        setCameras((list?.results ?? list ?? []).filter((c) => c.is_live));
      } catch (err) {
        camerasFetchedRef.current = false;   // allow a retry on the next visit
        setCamerasError(err.message || "Could not load cameras.");
      } finally {
        setCamerasLoading(false);
      }
    })();
  }, [source]);

  // What the server is still holding from an earlier visit, so the page can
  // offer to carry on instead of quietly making somebody send 200 MB twice.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const list = await listPendingUploads();
      if (!cancelled) setPending(list);
    })();
    return () => { cancelled = true; };
  }, []);

  const refreshPending = async () => setPending(await listPendingUploads());

  // Choosing a clip uploads it in chunks and reads its first frame straight away (needed for the
  // parking edge, and it proves the file decodes), so there is no separate "stage" step. If the
  // server already holds part of this exact file — from a failed attempt, or from before a page
  // refresh — uploadClipInChunks carries on from there instead of starting over.
  const pickFile = async (f) => {
    if (!f) return;
    const problem = validateFile(f);
    if (problem) { setFileError(problem); setFile(null); return; }
    // Picking a second clip while the first is still going up used to leave
    // BOTH uploads running: this only overwrote abortRef, so the old
    // controller was dropped without being aborted and whichever upload
    // finished LAST won `staged`. Completion order is by size, not by click
    // order, so a 1GB clip picked first lands minutes after a small one picked
    // second and silently replaces it — Start then runs the clip the user had
    // already moved on from, while the drop-zone chip still shows the one they
    // chose. Stop the old upload (its chunks stay on the server, which is what
    // makes stopping cheap to undo) and stamp this pick, so a straggler that
    // resolves anyway cannot write over the newest one.
    pickSeqRef.current += 1;
    const seq = pickSeqRef.current;
    const isCurrent = () => pickSeqRef.current === seq;
    abortRef.current?.abort();
    setFileError("");
    setFile(f);
    setStaged(null);
    setStageError("");
    setStaging(true);
    setProgress({ phase: "uploading", pct: 0, sentBytes: 0, totalBytes: f.size, resumedBytes: 0 });
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const res = await uploadClipInChunks(f, {
        onProgress: (p) => { if (isCurrent()) setProgress(p); },
        signal: controller.signal,
      });
      if (!isCurrent()) return;
      setStaged({
        stagedToken: res.staged_token,
        sourceFilename: res.source_filename,
        frame: { src: res.image, width: res.width, height: res.height },
      });
      setPending((list) => list.filter((s) => s.id !== res.session_id));
    } catch (err) {
      // A superseded upload reports nothing: it was aborted on purpose, and its
      // "Upload stopped" notice would land on the clip that replaced it.
      if (!isCurrent()) return;
      if (err?.aborted) {
        setStageError("Upload stopped. What already went up is kept — pick the same clip again to carry on.");
      } else {
        setStageError(err.message || "Could not upload that clip.");
      }
      // Either way there is now a part-finished upload worth offering a resume
      // for, so the banner needs to hear about it.
      refreshPending();
    } finally {
      // Never clear the newer pick's controller or its progress bar.
      if (isCurrent()) {
        setStaging(false);
        setProgress(null);
        abortRef.current = null;
      }
    }
  };

  const stopUpload = () => abortRef.current?.abort();

  const forgetPending = async (session) => {
    setPending((list) => list.filter((s) => s.id !== session.id));
    await discardUpload(session.id);
  };

  // Two distinct phases hide behind `staging` and they need different UI: the
  // chunks going up have a real percentage, while the server stitching them and
  // reading the first frame reports nothing at all.
  const uploading = staging && progress?.phase === "uploading";
  const uploadPct = progress?.pct ?? 0;
  const stageSuffix = uploading ? ` · ${uploadPct}%`
    : staging ? " · assembling…"
    : staged ? " · ready"
    : "";
  // Worth saying out loud — a bar that starts at 62% is otherwise just puzzling.
  const resumedNote = uploading && progress?.resumedBytes > 0
    ? ` (resumed — ${formatBytes(progress.resumedBytes)} was already up)`
    : "";

  const usesClock = source === "file" && ["thief", "drinking", "merged", "merged4", "disposal"].includes(violationType);
  const canStart = source === "camera"
    ? !!cameraId
    : staged && (!needsEdges || edgesOptional || canSaveEdges);

  // Nothing is saved and nothing is started here: the shape lands in the
  // canvas as an ordinary editable polygon, and the operator still presses
  // Start themselves.
  const runTrace = async () => {
    if (!staged || tracing) return;
    setTracing(true);
    setTraceError("");
    setTraced(null);
    try {
      const res = await traceZoneFromStaged(staged.stagedToken);
      // Both read frame 0 of the same clip at native resolution, so the points
      // are already in the canvas's coordinates. Checked rather than assumed:
      // a silent mismatch would put the zone somewhere plausible and wrong.
      if (res.width !== staged.frame.width || res.height !== staged.frame.height) {
        setTraceError("The traced zone does not match the staged frame's size "
          + "— draw the area by hand for this clip.");
        return;
      }
      setTracedPoints(res.points.map(([x, y]) => ({ x, y })));
      setCanvasKey((k) => k + 1);
      setTraced(res.report || {});
    } catch (err) {
      // 422 is "the clip was fine, it just had no traffic in it" — the server
      // says which, in a sentence meant for the operator.
      setTraceError(err.data?.detail || err.message || "Could not trace a zone.");
    } finally {
      setTracing(false);
    }
  };

  const handleStart = async () => {
    if (!canStart || starting) return;
    setStarting(true);
    setStartError("");
    try {
      const started = source === "camera"
        ? await startLiveDetectionJob({ violationType, cameraId })
        : await startStagedDetectionJob({
            stagedToken: staged.stagedToken,
            sourceFilename: staged.sourceFilename,
            violationType,
            recordedAt: usesClock ? recordedAt : "",
            trimStart: trim.start,
            trimEnd: trim.end,
            // Only when one was actually drawn: disposal may legitimately
            // start with none, and the thresholds below are parking's rule,
            // which disposal's containment test does not use.
            // The area, only when one was actually drawn: parking works it
            // out from the footage otherwise, and disposal may legitimately
            // start with none.
            ...(needsEdges && canSaveEdges ? {
              edges: edgeSpec,
              edgesWidth: staged.frame.width,
              edgesHeight: staged.frame.height,
            } : {}),
            // The thresholds, whether or not an area was drawn — they are
            // parking's rule ("how much of the vehicle", "for how long"), not
            // a property of the polygon, and an auto-traced run has to be
            // judged by the numbers the operator typed.
            ...(hasAreaThresholds ? {
              obstructionPct: Number(pct),
              obstructionMinutes: Number(minutes),
            } : {}),
          });
      setJob(started);
      setJobStatus("running");
    } catch (err) {
      setStartError(err.message || "Could not start detection.");
    } finally {
      setStarting(false);
    }
  };

  const reset = () => {
    abortRef.current?.abort();
    setFile(null); setFileError(""); setStaged(null); setStageError("");
    setCameraId(""); setRecordedAt(""); setEdgeSpec({}); setCanSaveEdges(false); setTrim({ start: 0, end: 0 });
    setPct(50); setMinutes(5); setStartError(""); setJob(null); setStopping(false);
    setTracing(false); setTraceError(""); setTraced(null); setTracedPoints([]);
    setCanvasKey((k) => k + 1);
  };

  const stop = async () => {
    if (!job || stopping) return;
    setStopping(true);
    try { await cancelDetectionJob(job.id); } catch { /* it may already have finished */ }
    setStopping(false);
  };

  const changeSource = (next) => {
    if (next === source) return;
    abortRef.current?.abort();
    setSource(next);
    setFile(null); setFileError(""); setStaged(null); setStageError(""); setCameraId("");
    setTrim({ start: 0, end: 0 });
  };

  const fetchState = useCallback(async (since) => {
    const d = await getJobState(job.id, since);
    if (d?.job?.status) setJobStatus(d.job.status);
    return d;
  }, [job]);

  const detectorLabel = DETECTION_TYPES.find((t) => t.key === violationType)?.label ?? "";
  const running = jobStatus === "running";

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Run Detection</h1>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-3 w-full">
        {job ? (
          <>
            <div className="rounded-xl px-4 py-2.5 flex flex-wrap items-center justify-between gap-3" style={barStyle}>
              <div className="flex items-center gap-2 text-[14px]" style={{ color: "var(--foreground)" }}>
                {running
                  ? <Loader2 size={14} className="animate-spin" style={{ color: "#22c55e" }} />
                  : <CheckCircle2 size={14} style={{ color: jobStatus === "done" ? "#22c55e" : "#f59e0b" }} />}
                <b>{detectorLabel}</b>
                <span style={{ color: "var(--muted-foreground)" }}>
                  on {source === "camera" ? "the live camera" : staged?.sourceFilename}
                  {" · "}{running ? "processing" : jobStatus === "done" ? "finished" : jobStatus}
                </span>
              </div>
              <div className="flex items-center gap-2">
                {running && (
                  <button onClick={stop} disabled={stopping}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-medium"
                    style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.35)" }}>
                    <Square size={12} /> Stop
                  </button>
                )}
                <button onClick={reset}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[13px] font-medium"
                  style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
                  <RotateCcw size={12} /> New run
                </button>
              </div>
            </div>
            <ProcessingView
              fetchState={fetchState}
              testingTools={testingTools}
              title={`${detectorLabel} · processing`}
              finishedNote={!running ? "The run has finished." : ""}
            />
          </>
        ) : (
          <>
            {/* Compact full-width setup bar */}
            <div className="rounded-xl p-3 flex flex-wrap items-center gap-x-5 gap-y-3" style={barStyle}>
              <div className="flex items-center flex-wrap gap-1.5">
                {DETECTION_TYPES.map((t) => {
                  const Icon = t.icon;
                  const isActive = violationType === t.key;
                  return (
                    <button key={t.key} onClick={() => setViolationType(t.key)}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[14px] font-medium transition-all"
                      style={{
                        background: isActive ? `${t.color}1a` : "var(--secondary)",
                        border: `1px solid ${isActive ? `${t.color}66` : "var(--border)"}`,
                        color: isActive ? t.color : "var(--foreground)",
                      }}>
                      <Icon size={13} /> {t.label}
                    </button>
                  );
                })}
              </div>

              <div className="flex items-center gap-1 p-0.5 rounded-full" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                {[{ k: "file", l: "Upload clip", I: Upload }, { k: "camera", l: "Live camera", I: Radio }].map(({ k, l, I }) => (
                  <button key={k} onClick={() => changeSource(k)}
                    className="flex items-center gap-1.5 px-3 py-1 text-[13px] font-medium rounded-full"
                    style={{ background: source === k ? "var(--card)" : "transparent", color: source === k ? "#f59e0b" : "var(--muted-foreground)" }}>
                    <I size={12} /> {l}
                  </button>
                ))}
              </div>

              {source === "file" ? (
                <div
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={(e) => { e.preventDefault(); setDragOver(false); pickFile(e.dataTransfer.files?.[0]); }}
                  onClick={() => !staging && inputRef.current?.click()}
                  className="relative overflow-hidden flex items-center gap-2 px-3 py-1.5 rounded-xl cursor-pointer min-w-[16rem]"
                  style={{ border: `1.5px dashed ${dragOver ? "#f59e0b" : "var(--border)"}`, background: dragOver ? "rgba(245,158,11,0.06)" : "var(--secondary)" }}>
                  <input ref={inputRef} type="file" accept={ALLOWED_EXTENSIONS.join(",")} className="hidden"
                    disabled={staging} onChange={(e) => pickFile(e.target.files?.[0])} />
                  {/* While chunks are going up the BAR below carries the progress, so the
                      icon stays a plain file. The spinner is kept for the stitch-and-decode
                      that follows, which reports nothing and so has no bar to show. */}
                  {staging && !uploading ? <Loader2 size={14} className="animate-spin" style={{ color: "#f59e0b" }} />
                    : file ? <FileVideo size={14} style={{ color: staged ? "#22c55e" : "#f59e0b" }} />
                    : <Upload size={14} style={{ color: "var(--muted-foreground)" }} />}
                  <span className="text-[14px] truncate" style={{ color: file ? "var(--foreground)" : "var(--muted-foreground)" }}>
                    {file ? `${file.name} · ${formatBytes(file.size)}${stageSuffix}${resumedNote}` : "Drop a clip here or click to browse (.mp4 .mkv .avi, up to 1GB)"}
                  </span>
                  {/* Stops the chunks in flight without discarding the ones already
                      stored, so picking the same clip again carries on from here.
                      stopPropagation, or the click reopens the file dialog. */}
                  {uploading && (
                    <button onClick={(e) => { e.stopPropagation(); stopUpload(); }} title="Stop uploading"
                      className="ml-auto flex-shrink-0 p-1 rounded-md"
                      style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.3)" }}>
                      <X size={11} />
                    </button>
                  )}
                  {staging && (
                    <div className="absolute left-0 bottom-0 h-[3px] w-full" style={{ background: "var(--border)" }}>
                      <div
                        className={uploading ? "" : "animate-pulse"}
                        style={{
                          // Assembling reports no progress of its own, so it fills the
                          // track and pulses rather than parking the bar at a number
                          // that has stopped moving.
                          width: uploading ? `${uploadPct}%` : "100%",
                          height: "100%",
                          background: "#f59e0b",
                          transition: "width 150ms linear",
                        }}
                      />
                    </div>
                  )}
                </div>
              ) : camerasLoading ? (
                <span className="flex items-center gap-2 text-[14px]" style={{ color: "var(--muted-foreground)" }}>
                  <Loader2 size={13} className="animate-spin" /> Loading cameras…
                </span>
              ) : cameras.length === 0 ? (
                <span className="text-[14px]" style={{ color: "var(--muted-foreground)" }}>
                  {camerasError || "No camera has a stream address yet. Add one on Feeds first."}
                </span>
              ) : (
                <select value={cameraId} onChange={(e) => setCameraId(e.target.value)}
                  className="px-3 py-1.5 rounded-xl text-[14px]"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}>
                  <option value="">Choose a camera…</option>
                  {cameras.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.code})</option>)}
                </select>
              )}

              {usesClock && (
                <label className="flex items-center gap-2 text-[13px]" style={{ color: "var(--muted-foreground)" }}
                  title="When the clip starts. Holdup scoring uses the time of day, drinking counts the evening hours and improper disposal applies the waste-collection hours. Left blank, the current clock is used (improper disposal skips the collection-hours rule).">
                  Recorded at
                  <input type="datetime-local" value={recordedAt} onChange={(e) => setRecordedAt(e.target.value)}
                    className="px-2 py-1 rounded-lg text-[14px] outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
                </label>
              )}

              <button disabled={!canStart || starting} onClick={handleStart}
                className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium ml-auto"
                style={{ background: "#22c55e", color: "#08130a", opacity: !canStart || starting ? 0.5 : 1, cursor: !canStart || starting ? "not-allowed" : "pointer" }}>
                {starting ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                {starting ? "Starting…" : "Start Detection"}
              </button>
            </div>

            {(fileError || stageError || startError) && <Err>{fileError || stageError || startError}</Err>}

            {/* An upload the server still holds chunks for. It cannot finish on its own: a browser
                cannot re-open a File it no longer has a handle to, which is exactly why a refresh
                used to lose the whole thing. Picking the same clip again resumes from these bytes
                rather than resending them. */}
            {source === "file" && !staging && !staged && pending.map((session) => (
              <div key={session.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[13px] px-3 py-2 rounded-xl"
                style={{ background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.2)", color: "#60a5fa" }}>
                <History size={13} className="flex-shrink-0" />
                <span>
                  <b>{session.filename}</b> was {Math.round((session.received_bytes / session.size) * 100)}% uploaded
                  {" "}({formatBytes(session.received_bytes)} of {formatBytes(session.size)}).
                  {" "}Choose the same clip again and it carries on from there.
                </span>
                <button onClick={() => forgetPending(session)}
                  className="ml-auto px-2.5 py-1 rounded-lg text-[12px] font-medium"
                  style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                  Discard
                </button>
              </div>
            ))}

            {source === "file" && staged && (
              <ClipTrimmer file={file} onChange={setTrim} />
            )}

            {needsEdges && source === "file" && staged && (
              <div className="rounded-xl p-4 space-y-3" style={barStyle}>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="text-[13px] font-semibold uppercase tracking-wide"
                    style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                    {edgeHeading(violationType, detectorLabel)}
                  </div>
                  {/* The clip is already on the server, so this costs no
                      upload — it just asks the detector to look at it. */}
                  <button onClick={runTrace} disabled={tracing}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[13px] font-medium flex-shrink-0"
                    style={{
                      background: "#f59e0b", color: "#0c0f16",
                      opacity: tracing ? 0.5 : 1,
                      cursor: tracing ? "not-allowed" : "pointer",
                    }}>
                    {tracing ? <Loader2 size={12} className="animate-spin" /> : <Wand2 size={12} />}
                    {tracing ? "Watching the traffic…" : "Trace it for me"}
                  </button>
                </div>

                {/* The wait is tens of seconds of real inference, so it is
                    named rather than left as a spinner that looks like a hang. */}
                {tracing && (
                  <div className="text-[13px] px-3 py-2.5 rounded-xl"
                    style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.2)", color: "#f59e0b" }}>
                    Running the vehicle detector over this clip — this takes up to a minute.
                  </div>
                )}

                {traceError && (
                  <div className="flex items-start gap-2 text-[13px] px-3 py-2.5 rounded-xl"
                    style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                    <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                    <span>{traceError}</span>
                  </div>
                )}

                {traced && !tracing && (
                  <div className="flex items-start gap-2 text-[13px] px-3 py-2.5 rounded-xl"
                    style={{ background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.25)", color: "var(--foreground)" }}>
                    <Info size={13} className="flex-shrink-0 mt-0.5" style={{ color: "#3b82f6" }} />
                    <span>
                      <span className="font-medium">Suggested — check it before you start.</span>{" "}
                      Traced from {traced.moving_tracks ?? "?"} moving vehicle
                      {traced.moving_tracks === 1 ? "" : "s"} across {traced.samples ?? "?"} sampled
                      frames. Vehicles that stayed parked were ignored on purpose: the rule cannot
                      tell a car parked on the road from one parked in a yard, so only moving
                      traffic shapes the area.
                    </span>
                  </div>
                )}

                <EdgeCanvas key={canvasKey} frame={staged.frame} initialPoints={tracedPoints}
                  pct={pct} onPctChange={setPct} minutes={minutes}
                  onMinutesChange={setMinutes} showThresholds={hasAreaThresholds}
                  onChange={(spec, can) => { setEdgeSpec(spec); setCanSaveEdges(can); }} />
              </div>
            )}
            {needsEdges && source === "camera" && cameraId && (
              <div className="text-[13px] px-3 py-2 rounded-xl"
                style={{ background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.2)", color: "#60a5fa" }}>
                {edgeCameraNote(violationType, detectorLabel)}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
