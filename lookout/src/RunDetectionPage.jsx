import { useEffect, useRef, useState } from "react";
import {
  Upload, FileVideo, Loader2, AlertTriangle, CheckCircle2, RotateCcw, Play, Video, Radio,
} from "lucide-react";
import { stageDetectionFrame, startStagedDetectionJob, startLiveDetectionJob, getCameras } from "./api";
import { DETECTION_TYPES, TYPES_WITH_EDGES } from "./constants/detectionTypes";
import { EdgeCanvas } from "./EdgeCanvas";

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

// The single "pick a source -> (draw edge lines) -> start detection" screen,
// reachable directly from the sidebar rather than buried behind a camera
// tile. Source is either an uploaded clip (staged first so edges can be
// drawn against its first frame) or a live camera with a stream_url
// configured (see CameraGrid's Stream button) — a live run has no natural
// end, so it only stops via the Cancel/Stop control in DetectionJobsPanel.
// Edge drawing only appears for violation types that judge against a drawn
// line (currently just parking, see TYPES_WITH_EDGES), and only for the file
// source — a live camera's edges are drawn separately via its own Edge Zones
// modal (EdgeEditorModal), which already works against that camera's live
// snapshot and writes camera.edges directly; watch_parking picks it up the
// same way it does for any other --camera.
export function RunDetectionPage() {
  const [violationType, setViolationType] = useState(DETECTION_TYPES[0].key);
  const needsEdges = TYPES_WITH_EDGES.has(violationType);

  const [source, setSource] = useState("file"); // "file" | "camera"

  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  // Set once stageDetectionFrame() returns — { stagedToken, sourceFilename,
  // frame: { src, width, height } }. Staying null gates the edge/start steps.
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

  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [job, setJob] = useState(null);

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

  const pickFile = (f) => {
    if (!f) return;
    const problem = validateFile(f);
    if (problem) { setFileError(problem); setFile(null); return; }
    setFileError("");
    setFile(f);
    setStaged(null);
    setStageError("");
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    pickFile(e.dataTransfer.files?.[0]);
  };

  const handleStage = async () => {
    if (!file || staging) return;
    setStaging(true);
    setStageError("");
    try {
      const res = await stageDetectionFrame(file);
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
            ...(needsEdges ? {
              edges: edgeSpec,
              edgesWidth: staged.frame.width,
              edgesHeight: staged.frame.height,
              obstructionPct: Number(pct),
              obstructionMinutes: Number(minutes),
            } : {}),
          });
      setJob(started);
    } catch (err) {
      setStartError(err.message || "Could not start detection.");
    } finally {
      setStarting(false);
    }
  };

  const reset = () => {
    setFile(null);
    setFileError("");
    setStaged(null);
    setStageError("");
    setCameraId("");
    setEdgeSpec({});
    setCanSaveEdges(false);
    setPct(50);
    setMinutes(5);
    setStartError("");
    setJob(null);
  };

  const changeSource = (next) => {
    if (next === source) return;
    setSource(next);
    setFile(null);
    setFileError("");
    setStaged(null);
    setStageError("");
    setCameraId("");
  };

  const detectorLocked = staging || !!staged || (source === "camera" && starting);
  const selectedCamera = cameras.find((c) => String(c.id) === String(cameraId));

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Run Detection</h1>
      </div>

      <div className="flex-1 overflow-auto p-6">
        <div className="max-w-2xl mx-auto space-y-4">

          {job ? (
            <div className="rounded-xl p-5 flex flex-col items-center text-center gap-2"
              style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
              <CheckCircle2 size={28} style={{ color: "#22c55e" }} />
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Detection started</div>
              <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                {source === "camera"
                  ? "Running continuously against the live camera until you stop it from the running-jobs panel. Any alerts it produces will appear in the Violations tab."
                  : "Running in the background and may take a few minutes. Any alerts it produces will appear in the Violations tab as detection runs."}
              </div>
              <button onClick={reset}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium mt-2"
                style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
                <RotateCcw size={13} /> Run another
              </button>
            </div>
          ) : (
            <>
              {/* Step 1: detector */}
              <div className="rounded-xl p-4" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                <div className="text-[11px] font-semibold uppercase tracking-wide mb-2"
                  style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                  1. Detector
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {DETECTION_TYPES.map((t) => {
                    const Icon = t.icon;
                    const isActive = violationType === t.key;
                    return (
                      <button
                        key={t.key}
                        disabled={detectorLocked}
                        onClick={() => setViolationType(t.key)}
                        className="flex items-center gap-2 px-3 py-2.5 rounded-xl text-left transition-all disabled:opacity-60"
                        style={{
                          background: isActive ? `${t.color}1a` : "var(--secondary)",
                          border: `1px solid ${isActive ? `${t.color}66` : "var(--border)"}`,
                        }}
                      >
                        <Icon size={14} style={{ color: isActive ? t.color : "var(--muted-foreground)" }} />
                        <span className="text-[12px] font-medium" style={{ color: isActive ? t.color : "var(--foreground)" }}>
                          {t.label}
                        </span>
                      </button>
                    );
                  })}
                </div>
                {staged && (
                  <div className="text-[11px] mt-2" style={{ color: "var(--muted-foreground)" }}>
                    Locked in once a clip is staged below —{" "}
                    <button onClick={reset} className="underline">start over</button> to change it.
                  </div>
                )}
              </div>

              {/* Step 2: source */}
              <div className="rounded-xl p-4" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                <div className="text-[11px] font-semibold uppercase tracking-wide mb-2"
                  style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                  2. Source
                </div>

                <div className="flex gap-2 mb-3">
                  <button
                    disabled={detectorLocked}
                    onClick={() => changeSource("file")}
                    className="flex items-center gap-2 px-3 py-2 rounded-xl text-[12px] font-medium transition-all disabled:opacity-60"
                    style={{
                      background: source === "file" ? "rgba(245,158,11,0.12)" : "var(--secondary)",
                      border: `1px solid ${source === "file" ? "rgba(245,158,11,0.4)" : "var(--border)"}`,
                      color: source === "file" ? "#f59e0b" : "var(--foreground)",
                    }}
                  >
                    <Upload size={13} /> Upload file
                  </button>
                  <button
                    disabled={detectorLocked}
                    onClick={() => changeSource("camera")}
                    className="flex items-center gap-2 px-3 py-2 rounded-xl text-[12px] font-medium transition-all disabled:opacity-60"
                    style={{
                      background: source === "camera" ? "rgba(245,158,11,0.12)" : "var(--secondary)",
                      border: `1px solid ${source === "camera" ? "rgba(245,158,11,0.4)" : "var(--border)"}`,
                      color: source === "camera" ? "#f59e0b" : "var(--foreground)",
                    }}
                  >
                    <Radio size={13} /> Live camera
                  </button>
                </div>

                {source === "file" && !staged && (
                  <>
                    <div
                      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                      onDragLeave={() => setDragOver(false)}
                      onDrop={handleDrop}
                      onClick={() => !staging && inputRef.current?.click()}
                      className="rounded-xl flex flex-col items-center justify-center gap-2 py-8 px-4 cursor-pointer transition-all"
                      style={{
                        border: `1.5px dashed ${dragOver ? "#f59e0b" : "var(--border)"}`,
                        background: dragOver ? "rgba(245,158,11,0.06)" : "var(--secondary)",
                        cursor: staging ? "not-allowed" : "pointer",
                      }}
                    >
                      <input
                        ref={inputRef}
                        type="file"
                        accept={ALLOWED_EXTENSIONS.join(",")}
                        className="hidden"
                        disabled={staging}
                        onChange={(e) => pickFile(e.target.files?.[0])}
                      />
                      {file ? (
                        <>
                          <FileVideo size={22} style={{ color: "#f59e0b" }} />
                          <div className="text-[12px] font-medium text-center" style={{ color: "var(--foreground)" }}>{file.name}</div>
                          <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>{formatBytes(file.size)}</div>
                        </>
                      ) : (
                        <>
                          <Upload size={22} style={{ color: "var(--muted-foreground)" }} />
                          <div className="text-[12px] text-center" style={{ color: "var(--muted-foreground)" }}>
                            Drag a clip here, or click to browse
                          </div>
                          <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                            .mp4, .mkv, .avi — up to 1GB
                          </div>
                        </>
                      )}
                    </div>

                    {fileError && (
                      <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-xl mt-3"
                        style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                        <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                        <span>{fileError}</span>
                      </div>
                    )}
                    {stageError && (
                      <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-xl mt-3"
                        style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                        <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                        <span>{stageError}</span>
                      </div>
                    )}

                    <button
                      disabled={!file || staging}
                      onClick={handleStage}
                      className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium mt-3"
                      style={{
                        background: "#f59e0b", color: "#0c0f16",
                        cursor: (!file || staging) ? "not-allowed" : "pointer",
                        opacity: (!file || staging) ? 0.5 : 1,
                      }}
                    >
                      {staging ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                      {staging ? "Reading first frame…" : needsEdges ? "Upload & continue to edges" : "Upload"}
                    </button>
                  </>
                )}

                {source === "file" && staged && (
                  <div className="flex items-center gap-2 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                    <FileVideo size={14} style={{ color: "#22c55e" }} />
                    <span style={{ color: "var(--foreground)" }}>{staged.sourceFilename}</span> staged and ready.
                  </div>
                )}

                {source === "camera" && (
                  <>
                    {camerasLoading && (
                      <div className="flex items-center gap-2 text-[12px] py-3" style={{ color: "var(--muted-foreground)" }}>
                        <Loader2 size={13} className="animate-spin" /> Loading cameras…
                      </div>
                    )}
                    {camerasError && (
                      <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-xl"
                        style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                        <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                        <span>{camerasError}</span>
                      </div>
                    )}
                    {!camerasLoading && !camerasError && cameras.length === 0 && (
                      <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                        No camera has a stream URL configured yet. Set one from the Cameras page (Stream button) first.
                      </div>
                    )}
                    {!camerasLoading && cameras.length > 0 && (
                      <select
                        value={cameraId}
                        onChange={(e) => setCameraId(e.target.value)}
                        disabled={starting}
                        className="w-full px-3 py-2.5 rounded-xl text-[12px]"
                        style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                      >
                        <option value="">Choose a camera…</option>
                        {cameras.map((c) => (
                          <option key={c.id} value={c.id}>{c.name} ({c.code})</option>
                        ))}
                      </select>
                    )}
                    {needsEdges && cameraId && (
                      <div className="flex items-start gap-2 text-[11px] px-3 py-2.5 rounded-xl mt-3"
                        style={{ background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.2)", color: "#60a5fa" }}>
                        <Video size={13} className="flex-shrink-0 mt-0.5" />
                        <span>
                          Parking edges for a live camera are drawn separately — use the "Edge Zones" button on{" "}
                          {selectedCamera?.name || "this camera"} in the Cameras page before starting, if you haven't already.
                        </span>
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Step 3: edges — parking, file source only (a live camera's
                  edges are drawn on the Cameras page instead, see above) */}
              {needsEdges && source === "file" && staged && (
                <div className="rounded-xl p-4 space-y-3.5" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                  <div className="text-[11px] font-semibold uppercase tracking-wide"
                    style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                    3. Draw road-edge lines
                  </div>
                  <EdgeCanvas
                    frame={staged.frame}
                    pct={pct}
                    onPctChange={setPct}
                    minutes={minutes}
                    onMinutesChange={setMinutes}
                    onChange={(spec, can) => { setEdgeSpec(spec); setCanSaveEdges(can); }}
                  />
                </div>
              )}

              {/* Step 4: start */}
              {canStart && (
                <div className="rounded-xl p-4 flex items-center justify-between gap-3"
                  style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                  <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                    Ready — {DETECTION_TYPES.find((t) => t.key === violationType)?.label} on{" "}
                    {source === "camera" ? `${selectedCamera?.name} (live)` : staged.sourceFilename}.
                  </div>
                  <button
                    disabled={starting}
                    onClick={handleStart}
                    className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium flex-shrink-0"
                    style={{
                      background: "#22c55e", color: "#08130a",
                      cursor: starting ? "not-allowed" : "pointer",
                      opacity: starting ? 0.6 : 1,
                    }}
                  >
                    {starting ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                    {starting ? "Starting…" : "Start Detection"}
                  </button>
                </div>
              )}

              {startError && (
                <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-xl"
                  style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                  <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                  <span>{startError}</span>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
