import { useRef, useState } from "react";
import { X, Upload, FileVideo, Loader2, Cigarette, Beer, Car, ShieldAlert, AlertTriangle, Layers } from "lucide-react";
import { uploadDetectionJob } from "./api";

// Local label/icon map for the upload flow only — deliberately NOT reusing
// VIOLATION_CONFIG from data/mockData.js, since that file keys the theft
// entry as "theft" while the backend's ViolationType/Alert.type value (and
// the watch_thief command) uses "thief". That mismatch predates this feature
// and is out of scope here; this map just uses the backend's real keys.
const DETECTION_TYPES = [
  { key: "smoking", label: "Smoking", icon: Cigarette, color: "#f59e0b" },
  { key: "drinking", label: "Drinking", icon: Beer, color: "#8b5cf6" },
  { key: "thief", label: "Theft (Holdup)", icon: ShieldAlert, color: "#ef4444" },
  { key: "parking", label: "Parking", icon: Car, color: "#f97316" },
  // Merged Bottle/Cigarette/knife model — one detection pass that can
  // produce smoking, drinking AND theft alerts from a single clip (see
  // watch_merged.py). Not a ViolationType itself, just a 5th job type.
  { key: "merged", label: "Merged (All 3)", icon: Layers, color: "#22c55e" },
];

const ALLOWED_EXTENSIONS = [".mp4", ".mkv", ".avi"];
const MAX_BYTES = 1024 * 1024 * 1024; // 1GB

function formatBytes(bytes) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function validateFile(file) {
  const ext = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `Unsupported file type "${ext}". Allowed: ${ALLOWED_EXTENSIONS.join(", ")}.`;
  }
  if (file.size > MAX_BYTES) {
    return `File too large (${formatBytes(file.size)}) — limit is 1 GB.`;
  }
  return null;
}

export function UploadDetectionModal({ onClose, onJobStarted }) {
  const [file, setFile] = useState(null);
  const [violationType, setViolationType] = useState(DETECTION_TYPES[0].key);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef(null);

  const pickFile = (f) => {
    if (!f) return;
    const problem = validateFile(f);
    if (problem) {
      setError(problem);
      setFile(null);
      return;
    }
    setError("");
    setFile(f);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    pickFile(e.dataTransfer.files?.[0]);
  };

  const handleSubmit = async () => {
    if (!file || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const job = await uploadDetectionJob(file, violationType);
      onJobStarted(job);
      onClose();
    } catch (err) {
      setError(err.message || "Upload failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget && !submitting) onClose(); }}
    >
      <div
        className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "rgba(245,158,11,0.12)" }}>
              <Upload size={14} style={{ color: "#f59e0b" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Test Detection</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                Run a detector against an uploaded clip
              </div>
            </div>
          </div>
          {!submitting && (
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={14} />
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* Violation type selector */}
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide mb-2"
              style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              Detector
            </div>
            <div className="grid grid-cols-2 gap-2">
              {DETECTION_TYPES.map((t) => {
                const Icon = t.icon;
                const isActive = violationType === t.key;
                return (
                  <button
                    key={t.key}
                    disabled={submitting}
                    onClick={() => setViolationType(t.key)}
                    className="flex items-center gap-2 px-3 py-2.5 rounded-xl text-left transition-all"
                    style={{
                      background: isActive ? `${t.color}1a` : "var(--secondary)",
                      border: `1px solid ${isActive ? `${t.color}66` : "var(--border)"}`,
                      opacity: submitting ? 0.6 : 1,
                    }}
                  >
                    <Icon size={14} style={{ color: isActive ? t.color : "var(--muted-foreground)" }} />
                    <span className="text-[12px] font-medium"
                      style={{ color: isActive ? t.color : "var(--foreground)" }}>
                      {t.label}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Drop zone / file picker */}
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide mb-2"
              style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              Clip
            </div>
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => !submitting && inputRef.current?.click()}
              className="rounded-xl flex flex-col items-center justify-center gap-2 py-8 px-4 cursor-pointer transition-all"
              style={{
                border: `1.5px dashed ${dragOver ? "#f59e0b" : "var(--border)"}`,
                background: dragOver ? "rgba(245,158,11,0.06)" : "var(--secondary)",
                cursor: submitting ? "not-allowed" : "pointer",
              }}
            >
              <input
                ref={inputRef}
                type="file"
                accept={ALLOWED_EXTENSIONS.join(",")}
                className="hidden"
                disabled={submitting}
                onChange={(e) => pickFile(e.target.files?.[0])}
              />
              {file ? (
                <>
                  <FileVideo size={22} style={{ color: "#f59e0b" }} />
                  <div className="text-[12px] font-medium text-center" style={{ color: "var(--foreground)" }}>
                    {file.name}
                  </div>
                  <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                    {formatBytes(file.size)}
                  </div>
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
          </div>

          {error && (
            <div className="flex items-start gap-2 text-[12px] px-3 py-2.5 rounded-xl"
              style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
              <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          <div className="text-[11px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
            Detection runs in the background and may take a few minutes for a longer clip.
            Any alerts it produces will appear in the Violations tab as detection runs.
          </div>
        </div>

        {/* Footer */}
        <div className="px-5 py-4 flex items-center justify-end gap-2 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          {!submitting && (
            <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Cancel
            </button>
          )}
          <button
            disabled={!file || submitting}
            onClick={handleSubmit}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium"
            style={{
              background: "#f59e0b",
              color: "#0c0f16",
              cursor: (!file || submitting) ? "not-allowed" : "pointer",
              opacity: (!file || submitting) ? 0.5 : 1,
            }}
          >
            {submitting ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
            {submitting ? "Starting…" : "Run Detection"}
          </button>
        </div>
      </div>
    </div>
  );
}
