import { useCallback, useEffect, useRef, useState } from "react";
import { Upload, FileVideo, Loader2, AlertTriangle, RotateCcw, Play, Radio, Square, CheckCircle2 } from "lucide-react";
import {
  stageDetectionFrame, startStagedDetectionJob, startLiveDetectionJob, getCameras, getJobState, cancelDetectionJob,
} from "./api";
import { DETECTION_TYPES, TYPES_WITH_EDGES } from "./constants/detectionTypes";
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
// Parking judges vehicles against road-edge lines. For an uploaded clip the edge is drawn on the
// clip's first frame, below the bar, every time (it is not remembered between uploads). For a live
// camera the edge is the one saved on the camera (Live Feeds -> Edge Zones).
export function RunDetectionPage() {
  const testingTools = useTestingTools(true);
  const [violationType, setViolationType] = useState(DETECTION_TYPES[0].key);
  const needsEdges = TYPES_WITH_EDGES.has(violationType);
  const [source, setSource] = useState("file"); // "file" | "camera"

  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  // { stagedToken, sourceFilename, frame: { src, width, height } } once the clip's first frame is read.
  const [staged, setStaged] = useState(null);
  const [staging, setStaging] = useState(false);
  const [stageError, setStageError] = useState("");

  const [cameras, setCameras] = useState([]);
  const [camerasLoading, setCamerasLoading] = useState(false);
  const [camerasError, setCamerasError] = useState("");
  const [cameraId, setCameraId] = useState("");

  const [edgeSpec, setEdgeSpec] = useState({});
  const [canSaveEdges, setCanSaveEdges] = useState(false);
  const [pct, setPct] = useState(50);
  const [minutes, setMinutes] = useState(5);

  // When the clip was recorded (datetime-local). Optional; becomes --clock for the detector.
  const [recordedAt, setRecordedAt] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [job, setJob] = useState(null);
  const [jobStatus, setJobStatus] = useState("running");
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    if (source !== "camera" || cameras.length || camerasLoading) return;
    let cancelled = false;
    (async () => {
      setCamerasLoading(true);
      setCamerasError("");
      try {
        const list = await getCameras();
        if (!cancelled) setCameras((list || []).filter((c) => c.is_live));
      } catch (err) {
        if (!cancelled) setCamerasError(err.message || "Could not load cameras.");
      } finally {
        if (!cancelled) setCamerasLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [source, cameras.length, camerasLoading]);

  // Choosing a clip reads its first frame straight away (needed for the parking edge and proves the
  // file decodes), so there is no separate "stage" step.
  const pickFile = async (f) => {
    if (!f) return;
    const problem = validateFile(f);
    if (problem) { setFileError(problem); setFile(null); return; }
    setFileError("");
    setFile(f);
    setStaged(null);
    setStageError("");
    setStaging(true);
    try {
      const res = await stageDetectionFrame(f);
      setStaged({
        stagedToken: res.staged_token,
        sourceFilename: res.source_filename,
        frame: { src: res.image, width: res.width, height: res.height },
      });
    } catch (err) {
      setStageError(err.message || "Could not read that clip.");
    } finally {
      setStaging(false);
    }
  };

  const usesClock = source === "file" && ["thief", "drinking", "merged"].includes(violationType);
  const canStart = source === "camera"
    ? !!cameraId
    : staged && (!needsEdges || canSaveEdges);

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
            ...(needsEdges ? {
              edges: edgeSpec,
              edgesWidth: staged.frame.width,
              edgesHeight: staged.frame.height,
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
    setFile(null); setFileError(""); setStaged(null); setStageError("");
    setCameraId(""); setRecordedAt(""); setEdgeSpec({}); setCanSaveEdges(false);
    setPct(50); setMinutes(5); setStartError(""); setJob(null); setStopping(false);
  };

  const stop = async () => {
    if (!job || stopping) return;
    setStopping(true);
    try { await cancelDetectionJob(job.id); } catch { /* it may already have finished */ }
    setStopping(false);
  };

  const changeSource = (next) => {
    if (next === source) return;
    setSource(next);
    setFile(null); setFileError(""); setStaged(null); setStageError(""); setCameraId("");
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
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xl cursor-pointer min-w-[16rem]"
                  style={{ border: `1.5px dashed ${dragOver ? "#f59e0b" : "var(--border)"}`, background: dragOver ? "rgba(245,158,11,0.06)" : "var(--secondary)" }}>
                  <input ref={inputRef} type="file" accept={ALLOWED_EXTENSIONS.join(",")} className="hidden"
                    disabled={staging} onChange={(e) => pickFile(e.target.files?.[0])} />
                  {staging ? <Loader2 size={14} className="animate-spin" style={{ color: "#f59e0b" }} />
                    : file ? <FileVideo size={14} style={{ color: staged ? "#22c55e" : "#f59e0b" }} />
                    : <Upload size={14} style={{ color: "var(--muted-foreground)" }} />}
                  <span className="text-[14px] truncate" style={{ color: file ? "var(--foreground)" : "var(--muted-foreground)" }}>
                    {file ? `${file.name} · ${formatBytes(file.size)}${staged ? " · ready" : ""}` : "Drop a clip here or click to browse (.mp4 .mkv .avi, up to 1GB)"}
                  </span>
                </div>
              ) : camerasLoading ? (
                <span className="flex items-center gap-2 text-[14px]" style={{ color: "var(--muted-foreground)" }}>
                  <Loader2 size={13} className="animate-spin" /> Loading cameras…
                </span>
              ) : cameras.length === 0 ? (
                <span className="text-[14px]" style={{ color: "var(--muted-foreground)" }}>
                  {camerasError || "No camera has a stream address yet. Add one on Live Feeds first."}
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
                  title="When the clip starts. Holdup scoring uses the time of day and drinking counts the evening hours. Left blank, the current clock is used.">
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

            {needsEdges && source === "file" && staged && (
              <div className="rounded-xl p-4 space-y-3" style={barStyle}>
                <div className="text-[13px] font-semibold uppercase tracking-wide"
                  style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                  Draw the road edge for parking
                </div>
                <EdgeCanvas frame={staged.frame} pct={pct} onPctChange={setPct} minutes={minutes}
                  onMinutesChange={setMinutes} onChange={(spec, can) => { setEdgeSpec(spec); setCanSaveEdges(can); }} />
              </div>
            )}
            {needsEdges && source === "camera" && cameraId && (
              <div className="text-[13px] px-3 py-2 rounded-xl"
                style={{ background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.2)", color: "#60a5fa" }}>
                Parking uses the road edge saved on the camera (Live Feeds → Edge Zones).
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
