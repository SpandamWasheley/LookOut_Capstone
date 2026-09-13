import { useEffect, useRef, useState } from "react";
import {
  X, Loader2, FileVideo, AlertTriangle, Save, WifiOff,
} from "lucide-react";
import { getCameraSnapshotUrl, uploadCameraEdgeFrame, updateCamera } from "./api";
import { EdgeCanvas } from "./EdgeCanvas";

const ALLOWED_EXTENSIONS = [".mp4", ".mkv", ".avi"];
const MAX_BYTES = 1024 * 1024 * 1024; // 1GB, matches the backend's limit

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
  const [prefill, setPrefill] = useState({ paths: { left: [], right: [] }, sides: { left: 1, right: 1 } });
  const [edgeSpec, setEdgeSpec] = useState({});
  const [canSave, setCanSave] = useState(false);

  const [pct, setPct] = useState(camera.obstruction_pct ?? 50);
  const [minutes, setMinutes] = useState(camera.obstruction_minutes ?? 5);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const liveUrlRef = useRef(null);
  const prefilledRef = useRef(false);
  const fileInputRef = useRef(null);

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
    const nextPaths = { left: [], right: [] };
    const nextSides = { left: 1, right: 1 };
    for (const side of ["left", "right"]) {
      const spec = stored[side];
      if (spec && Array.isArray(spec.points) && spec.points.length >= 2) {
        nextPaths[side] = spec.points.map(([x, y]) => ({ x: x * scaleX, y: y * scaleY }));
        nextSides[side] = spec.side ?? 1;
      }
    }
    setPrefill({ paths: nextPaths, sides: nextSides });
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

  const handleSave = async () => {
    if (!canSave || !frame) return;
    setSaving(true);
    setSaveError("");
    try {
      const updated = await updateCamera(camera.dbId, {
        edges: edgeSpec,
        edges_width: frame.width,
        edges_height: frame.height,
        obstruction_pct: Number(pct),
        obstruction_minutes: Number(minutes),
      });
      onSaved?.(updated);
      onClose();
    } catch (err) {
      setSaveError(err.message || "Could not save.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget && !saving) onClose(); }}
    >
      <div
        className="w-full max-w-3xl rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "92vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
              Road-edge obstruction zones
            </div>
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
              {camera.name} ({camera.id})
            </div>
          </div>
          {!saving && (
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={14} />
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3.5">
          {loadingLive && (
            <div className="flex flex-col items-center justify-center gap-2 py-16">
              <Loader2 size={22} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
              <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                Requesting a live snapshot…
              </div>
            </div>
          )}

          {!loadingLive && needsUpload && !frame && (
            <div>
              <div className="flex items-center gap-2 text-[12px] px-3 py-2.5 rounded-xl mb-3"
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
                    <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>Reading first frame…</div>
                  </>
                ) : (
                  <>
                    <FileVideo size={22} style={{ color: "var(--muted-foreground)" }} />
                    <div className="text-[12px] text-center" style={{ color: "var(--muted-foreground)" }}>
                      Click to choose a clip
                    </div>
                    <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                      .mp4, .mkv, .avi — up to 1GB
                    </div>
                  </>
                )}
              </div>
              {frameError && (
                <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-xl mt-3"
                  style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
                  <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
                  <span>{frameError}</span>
                </div>
              )}
            </div>
          )}

          {frame && (
            <>
              <EdgeCanvas
                frame={frame}
                initialPaths={prefill.paths}
                initialSides={prefill.sides}
                pct={pct}
                onPctChange={setPct}
                minutes={minutes}
                onMinutesChange={setMinutes}
                onChange={(spec, can) => { setEdgeSpec(spec); setCanSave(can); }}
              />

              {saveError && (
                <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-xl"
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
            <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Cancel
            </button>
          )}
          <button
            disabled={!canSave || saving}
            onClick={handleSave}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium"
            style={{
              background: "#f59e0b",
              color: "#0c0f16",
              cursor: (!canSave || saving) ? "not-allowed" : "pointer",
              opacity: (!canSave || saving) ? 0.5 : 1,
            }}
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            {saving ? "Saving…" : "Save zones"}
          </button>
        </div>
      </div>
    </div>
  );
}
