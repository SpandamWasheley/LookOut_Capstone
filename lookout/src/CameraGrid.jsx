import { useEffect, useRef, useState } from "react";
import { Maximize2, WifiOff, LayoutGrid, Check, X, Upload, Loader2, CheckCircle2, AlertTriangle, History, Shapes, Ban } from "lucide-react";
import { getAlerts, getCameras, getCameraSnapshotUrl, getDetectionJobs, cancelDetectionJob } from "./api";
import { UploadDetectionModal } from "./UploadDetectionModal";
import { DetectionJobHistoryModal } from "./DetectionJobHistoryModal";
import { EdgeEditorModal } from "./EdgeEditorModal";

// Polls a live camera's snapshot proxy and returns the latest frame as an
// object URL, or null for a non-live camera. Object URLs are revoked as they're
// replaced and on unmount, so nothing leaks. A failed poll (camera briefly
// unreachable) keeps the previous frame rather than flashing black.
// The gap AFTER each fetch completes (the loop is self-pacing), so the real
// frame rate is ~fetch(104ms) + intervalMs. At 40ms that's ~7 fps — near the
// camera's ~104ms/frame ceiling, about as smooth as JPEG polling can get. It
// can't go faster than the camera serves frames no matter how low this is, and
// near-continuous polling keeps a dev-server thread and the camera busy, so this
// is the practical floor rather than 0.
function useLiveSnapshot(cam, intervalMs = 40) {
  const [url, setUrl] = useState(null);
  const urlRef = useRef(null);

  useEffect(() => {
    if (!cam.isLive || cam.status === "offline") return undefined;
    let cancelled = false;
    let timer = null;
    const controller = new AbortController();

    // Self-pacing loop: the next fetch is scheduled only AFTER the current one
    // finishes, so a slow or stalled frame delays the feed instead of stacking
    // overlapping requests on the dev server.
    const tick = async () => {
      try {
        const next = await getCameraSnapshotUrl(cam.dbId, controller.signal);
        if (cancelled) { URL.revokeObjectURL(next); return; }
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = next;
        setUrl(next);
      } catch {
        /* keep the last good frame */
      }
      if (!cancelled) timer = setTimeout(tick, intervalMs);
    };

    tick();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null; }
    };
  }, [cam.dbId, cam.isLive, cam.status, intervalMs]);

  return url;
}

// NVR-style wall layouts. `cols` = grid columns; `tiles` = slots shown;
// `hero` (optional) = span of the first tile, in cells, for the "1 + N" walls
// (which are square grids, so the hero spans hero×hero and the rest fill around it).
const LAYOUTS = [
  { key: "1x1", label: "1 × 1", tiles: 1, cols: 1 },
  { key: "1+1", label: "1 + 1", tiles: 2, cols: 2 },
  { key: "2x2", label: "2 × 2", tiles: 4, cols: 2 },
  { key: "1+5", label: "1 + 5", tiles: 6, cols: 3, hero: 2 },
  { key: "1+7", label: "1 + 7", tiles: 8, cols: 4, hero: 3 },
  { key: "3x3", label: "3 × 3", tiles: 9, cols: 3 },
  { key: "4x4", label: "4 × 4", tiles: 16, cols: 4 },
];

const LAYOUT_STORAGE_KEY = "lookout.cameraLayout";

function timeAgo(iso) {
  if (!iso) return "—";
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function mapCamera(raw) {
  return {
    id: raw.code,
    dbId: raw.id,          // numeric pk, for the snapshot endpoint
    name: raw.name,
    zone: raw.zone,
    status: raw.status,
    fps: raw.fps,
    lastMotion: timeAgo(raw.last_motion_at),
    imageUrl: raw.image_url,
    isLive: raw.is_live,   // poll the snapshot proxy instead of the static image
    // Obstruction-zone config, passed through as-is for the edge editor.
    edges: raw.edges,
    edges_width: raw.edges_width,
    edges_height: raw.edges_height,
    obstruction_pct: raw.obstruction_pct,
    obstruction_minutes: raw.obstruction_minutes,
  };
}

function mapDetectionJob(raw) {
  return {
    id: raw.id,
    violationType: raw.violation_type,
    sourceFilename: raw.source_filename,
    status: raw.status,
    startedAt: raw.started_at,
    finishedAt: raw.finished_at,
    error: raw.error,
  };
}

function elapsedLabel(startedAt) {
  const secs = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  const m = Math.floor(secs / 60), s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

const JOB_STATUS_CONFIG = {
  running: { icon: Loader2, color: "#f59e0b", spin: true, label: "Running" },
  done: { icon: CheckCircle2, color: "#10b981", spin: false, label: "Done" },
  failed: { icon: AlertTriangle, color: "#ef4444", spin: false, label: "Failed" },
  cancelled: { icon: Ban, color: "#6b7280", spin: false, label: "Cancelled" },
};

// How long a finished job stays in the floating panel after a fresh poll
// picks it up (e.g. the page was reloaded moments after it finished) — a
// safety net around the auto-dismiss timer below, not the primary mechanism.
const RECENT_JOB_MS = 60_000;
// A "done" card closes itself this long after finishing, instead of sitting
// there until manually dismissed. Failed jobs are left for the admin to read
// and close themselves — their error detail is worth deliberately dismissing.
const AUTO_DISMISS_DONE_MS = 6_000;

// Floating status panel for admin-launched test-detection jobs (see
// UploadDetectionModal). Only shows jobs that are still running or finished
// recently — otherwise every job from a session's history piles up here on
// every page refresh. Dismissing a card (manually, or automatically once a
// "done" card ages out) only hides it from this panel — the job row and any
// alerts it produced are untouched, and remain visible in the full history
// (DetectionJobHistoryModal).
function DetectionJobsPanel({ jobs, onDismiss, onCancel }) {
  // The current time, sampled only from the ticking effect below — never
  // read directly during render (Date.now() there would make render impure).
  const [now, setNow] = useState(() => Date.now());

  const visible = jobs.filter((j) => {
    if (j.status === "running" || !j.finishedAt) return true;
    return now - new Date(j.finishedAt).getTime() < RECENT_JOB_MS;
  });
  const hasRunning = visible.some((j) => j.status === "running");
  const hasFinished = visible.some((j) => j.status !== "running");

  // Ticks once a second whenever there's a running job's elapsed time to
  // update, or a finished job that still needs to age out of `visible` /
  // reach its auto-dismiss time — both of those are time-based, not
  // triggered by new poll data, so nothing else would re-render this.
  useEffect(() => {
    if (!hasRunning && !hasFinished) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [hasRunning, hasFinished]);

  // Auto-dismiss "done" (and self-cancelled) cards a few seconds after they
  // finish. Recomputed from each job's real finishedAt every time this runs
  // (on every jobs change, e.g. each 4s poll), so it stays correct across
  // re-renders rather than restarting the countdown.
  useEffect(() => {
    const timers = jobs
      .filter((j) => (j.status === "done" || j.status === "cancelled") && j.finishedAt)
      .map((j) => {
        const remaining = AUTO_DISMISS_DONE_MS - (Date.now() - new Date(j.finishedAt).getTime());
        return setTimeout(() => onDismiss(j.id), Math.max(0, remaining));
      });
    return () => timers.forEach(clearTimeout);
  }, [jobs, onDismiss]);

  if (visible.length === 0) return null;

  return (
    <div className="fixed bottom-5 right-5 z-40 flex flex-col gap-2 w-72">
      {visible.map((job) => {
        const cfg = JOB_STATUS_CONFIG[job.status] ?? JOB_STATUS_CONFIG.running;
        const Icon = cfg.icon;
        return (
          <div key={job.id} className="rounded-xl shadow-xl px-3.5 py-3"
            style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
            <div className="flex items-start gap-2.5">
              <Icon size={15} className={cfg.spin ? "animate-spin" : ""}
                style={{ color: cfg.color, flexShrink: 0, marginTop: 1 }} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[12px] font-semibold capitalize" style={{ color: "var(--foreground)" }}>
                    {job.violationType} test
                  </span>
                  {job.status === "running" && onCancel ? (
                    <button onClick={() => onCancel(job.id)} className="flex-shrink-0"
                      title="Cancel this detection job" style={{ color: "#ef4444" }}>
                      <Ban size={13} />
                    </button>
                  ) : (
                    <button onClick={() => onDismiss(job.id)} className="flex-shrink-0"
                      style={{ color: "var(--muted-foreground)" }}>
                      <X size={12} />
                    </button>
                  )}
                </div>
                <div className="text-[10px] truncate" style={{ color: "var(--muted-foreground)" }} title={job.sourceFilename}>
                  {job.sourceFilename}
                </div>
                <div className="text-[10px] mt-1" style={{ color: cfg.color, fontFamily: "'DM Mono', monospace" }}>
                  {cfg.label}{job.status === "running" ? ` · ${elapsedLabel(job.startedAt)}` : ""}
                </div>
                {job.status === "failed" && job.error && (
                  <div className="text-[10px] mt-1 line-clamp-3" style={{ color: "var(--muted-foreground)" }}>
                    {job.error.slice(0, 200)}
                  </div>
                )}
                {job.status === "done" && (
                  <div className="text-[10px] mt-1" style={{ color: "var(--muted-foreground)" }}>
                    Check the Violations tab for new alerts.
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Tiny visual preview of a layout, drawn from its own spec (used in the menu).
function LayoutIcon({ layout }) {
  return (
    <div
      style={{
        display: "grid",
        width: 18,
        height: 18,
        gap: 1.5,
        gridTemplateColumns: `repeat(${layout.cols}, 1fr)`,
        ...(layout.hero ? { gridTemplateRows: `repeat(${layout.cols}, 1fr)` } : {}),
      }}
    >
      {Array.from({ length: layout.tiles }).map((_, i) => (
        <span
          key={i}
          style={{
            background: "currentColor",
            borderRadius: 1,
            ...(layout.hero && i === 0
              ? { gridColumn: `span ${layout.hero}`, gridRow: `span ${layout.hero}` }
              : {}),
          }}
        />
      ))}
    </div>
  );
}

function EmptyTile({ fill }) {
  return (
    <div
      className={`rounded-xl overflow-hidden flex flex-col items-center justify-center ${fill ? "h-full" : "aspect-video"}`}
      style={{ border: "1px dashed var(--border)", background: "rgba(0,0,0,0.35)" }}
    >
      <WifiOff size={16} style={{ color: "var(--muted-foreground)" }} />
      <span
        className="text-[9px] mt-1"
        style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}
      >
        NO CAMERA
      </span>
    </div>
  );
}

function CameraTile({ cam, alert, isSelected, onSelect, onExpand, fill }) {
  const liveUrl = useLiveSnapshot(cam);
  return (
    <div
      onClick={onSelect}
      className={`rounded-xl overflow-hidden cursor-pointer group transition-all duration-200 flex flex-col ${fill ? "h-full" : ""}`}
      style={{
        border: `1px solid ${
          alert ? "rgba(239,68,68,0.3)" : isSelected ? "rgba(245,158,11,0.4)" : "var(--border)"
        }`,
        background: "var(--card)",
      }}
    >
      {/* Feed image — live snapshot for CCTV cameras, static image otherwise */}
      <div className={`relative w-full overflow-hidden bg-black ${fill ? "flex-1 min-h-0" : "aspect-video"}`}>
        <img
          src={liveUrl || cam.imageUrl}
          alt={`${cam.name} feed`}
          className="w-full h-full object-cover transition-all duration-300"
          style={{ opacity: cam.status === "offline" ? 0.2 : 1 }}
        />

        {/* LIVE badge for a streaming camera */}
        {cam.isLive && cam.status !== "offline" && (
          <div className="absolute top-2 right-2 flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold"
            style={{ background: "rgba(239,68,68,0.85)", color: "#fff" }}>
            <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
            LIVE
          </div>
        )}

        {/* Scanline */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage:
              "repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.06) 3px, rgba(0,0,0,0.06) 4px)",
          }}
        />

        {/* Top-left badges */}
        <div className="absolute top-2 left-2 flex items-center gap-1.5">
          <div
            className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium"
            style={{ background: "rgba(0,0,0,0.65)", color: "#cbd5e1", backdropFilter: "blur(4px)", fontFamily: "'DM Mono', monospace" }}
          >
            {cam.id}
          </div>
          {cam.status === "online" && (
            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded" style={{ background: "rgba(239,68,68,0.85)" }}>
              <span className="w-1 h-1 rounded-full bg-white animate-pulse" />
              <span className="text-[9px] font-semibold" style={{ color: "#fff", fontFamily: "'DM Mono', monospace" }}>LIVE</span>
            </div>
          )}
        </div>

        {/* Top-right FPS */}
        <div
          className="absolute top-2 right-2 text-[9px] px-1.5 py-0.5 rounded"
          style={{ background: "rgba(0,0,0,0.55)", color: "#94a3b8", fontFamily: "'DM Mono', monospace" }}
        >
          {cam.fps}fps
        </div>

        {/* Expand to fullscreen */}
        <button
          type="button"
          title="Expand"
          onClick={(e) => { e.stopPropagation(); onExpand?.(cam); }}
          className="absolute bottom-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded cursor-pointer hover:scale-110"
          style={{ background: "rgba(0,0,0,0.6)", border: "none" }}
        >
          <Maximize2 size={12} style={{ color: "#fff" }} />
        </button>

        {/* Offline */}
        {cam.status === "offline" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1">
            <WifiOff size={18} style={{ color: "#ef4444" }} />
            <span className="text-[10px] font-medium" style={{ color: "#ef4444", fontFamily: "'DM Mono', monospace" }}>
              NO SIGNAL
            </span>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-2 flex items-center justify-between shrink-0">
        <div className="min-w-0">
          <div className="text-[12px] font-semibold text-white leading-none truncate">{cam.name}</div>
          <div className="text-[10px] mt-0.5 truncate" style={{ color: "var(--muted-foreground)" }}>{cam.zone}</div>
        </div>
        <div className="text-right shrink-0 ml-2">
          <div
            className="text-[10px] font-medium"
            style={{
              color: cam.status === "online" ? "#10b981" : cam.status === "degraded" ? "#f59e0b" : "#ef4444",
              fontFamily: "'DM Mono', monospace",
            }}
          >
            {cam.status}
          </div>
          <div className="text-[10px] mt-0.5" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            {cam.lastMotion}
          </div>
        </div>
      </div>
    </div>
  );
}

// Fullscreen overlay for a single camera — a large live view with its own
// snapshot poll. Closes on the X, on backdrop click, or Escape.
function ExpandedCamera({ cam, alert, onClose, isAdmin, onCameraUpdated }) {
  const liveUrl = useLiveSnapshot(cam);
  const [showEdgeEditor, setShowEdgeEditor] = useState(false);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      style={{ background: "rgba(0,0,0,0.85)" }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative w-full rounded-xl overflow-hidden"
        style={{ maxWidth: "min(95vw, 1600px)", border: "1px solid var(--border)", background: "#000" }}
      >
        <div className="relative w-full bg-black" style={{ aspectRatio: "16 / 9" }}>
          <img
            src={liveUrl || cam.imageUrl}
            alt={`${cam.name} feed`}
            className="w-full h-full object-contain"
            style={{ opacity: cam.status === "offline" ? 0.2 : 1 }}
          />
          {cam.isLive && cam.status !== "offline" && (
            <div className="absolute top-3 left-3 flex items-center gap-1.5 px-2 py-1 rounded text-xs font-semibold"
              style={{ background: "rgba(239,68,68,0.85)", color: "#fff" }}>
              <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
              LIVE
            </div>
          )}
          {alert && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded text-xs font-semibold"
              style={{ background: "rgba(239,68,68,0.9)", color: "#fff" }}>
              ⚠ VIOLATION DETECTED
            </div>
          )}
          <button
            type="button"
            onClick={onClose}
            title="Close (Esc)"
            className="absolute top-3 right-3 p-2 rounded-lg cursor-pointer hover:scale-110 transition-transform"
            style={{ background: "rgba(0,0,0,0.6)", border: "none" }}
          >
            <X size={18} style={{ color: "#fff" }} />
          </button>
        </div>
        <div className="flex items-center justify-between px-4 py-3"
          style={{ background: "var(--card)", borderTop: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>{cam.name}</div>
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              {cam.id} · {cam.zone || "—"} · {cam.status}
            </div>
          </div>
          <div className="flex items-center gap-3">
            {isAdmin && (
              <button
                type="button"
                onClick={() => setShowEdgeEditor(true)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium"
                style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}
              >
                <Shapes size={12} /> Edge Zones
              </button>
            )}
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              {cam.lastMotion ? `motion ${cam.lastMotion}` : ""}
            </div>
          </div>
        </div>
      </div>
    </div>

    {showEdgeEditor && (
      <EdgeEditorModal
        camera={cam}
        onClose={() => setShowEdgeEditor(false)}
        onSaved={(updated) => onCameraUpdated?.(updated)}
      />
    )}
    </>
  );
}

export function CameraGrid({ compact = false, isAdmin = false }) {
  const [expanded, setExpanded] = useState(null);
  const [selected, setSelected] = useState(null);
  const [allCameras, setAllCameras] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [layoutKey, setLayoutKey] = useState(
    () => localStorage.getItem(LAYOUT_STORAGE_KEY) || "2x2"
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const [showUpload, setShowUpload] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [detectionJobs, setDetectionJobs] = useState([]);
  const [dismissedJobIds, setDismissedJobIds] = useState(() => new Set());

  useEffect(() => {
    const refresh = () => {
      getCameras()
        .then((res) => {
          // Live CCTV cameras lead the wall so a real feed is always visible in
          // the first tiles, ahead of any seeded demo cameras; order is stable
          // within each group.
          const mapped = (res.results ?? res).map(mapCamera);
          mapped.sort((a, b) => (b.isLive ? 1 : 0) - (a.isLive ? 1 : 0));
          setAllCameras(mapped);
        })
        .catch(() => {});
      getAlerts().then((res) => setAlerts(res.results ?? res)).catch(() => {});
    };
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, []);

  // Admin-only test-detection jobs (see UploadDetectionModal) — same 4s
  // polling cadence as everything else on this page. Not polled for
  // non-admins, since the endpoint is admin-gated server-side too.
  useEffect(() => {
    if (!isAdmin) return undefined;
    const refresh = () => {
      getDetectionJobs()
        .then((res) => setDetectionJobs((res.results ?? res).map(mapDetectionJob)))
        .catch(() => {});
    };
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, [isAdmin]);

  // Stops a running job's subprocess server-side (see DetectionJobViewSet.cancel)
  // — e.g. the wrong video got uploaded and there's no other way to stop it.
  // Applies the server's response immediately instead of waiting up to 4s for
  // the next poll, so the card/row updates right away.
  const handleCancelJob = (id) => {
    cancelDetectionJob(id)
      .then((updated) => {
        const mapped = mapDetectionJob(updated);
        setDetectionJobs((prev) => prev.map((j) => (j.id === id ? mapped : j)));
      })
      .catch((err) => {
        // Most likely it already finished on its own a moment before the
        // click landed — refresh so the row/card reflects its real status
        // instead of looking stuck, and only bother the admin if that
        // wasn't it.
        getDetectionJobs()
          .then((res) => setDetectionJobs((res.results ?? res).map(mapDetectionJob)))
          .catch(() => {});
        alert(`Could not cancel: ${err.message || "unknown error"}`);
      });
  };

  // Close the layout menu when clicking outside it.
  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  const getAlert = (cameraId) =>
    alerts.find((a) => a.camera === cameraId && (a.status === "active" || a.status === "acknowledged"));

  // Merges a saved edge-zone update back into the grid's own camera list, so
  // it reflects immediately without waiting for the next 4s poll.
  const handleCameraUpdated = (raw) => {
    const mapped = mapCamera(raw);
    setAllCameras((prev) => prev.map((c) => (c.dbId === mapped.dbId ? mapped : c)));
    setExpanded((prev) => (prev && prev.dbId === mapped.dbId ? mapped : prev));
  };

  const selectLayout = (key) => {
    setLayoutKey(key);
    localStorage.setItem(LAYOUT_STORAGE_KEY, key);
    setMenuOpen(false);
  };

  // Compact (dashboard overview) keeps its simple fixed 2×2 of the first 4 cams.
  if (compact) {
    return (
      <div className="grid gap-3 grid-cols-2">
        {allCameras.slice(0, 4).map((cam) => (
          <CameraTile
            key={cam.id}
            cam={cam}
            alert={getAlert(cam.id)}
            isSelected={selected === cam.id}
            onSelect={() => setSelected(selected === cam.id ? null : cam.id)}
            onExpand={setExpanded}
          />
        ))}
        {expanded && (
          <ExpandedCamera cam={expanded} alert={getAlert(expanded.id)} onClose={() => setExpanded(null)}
            isAdmin={isAdmin} onCameraUpdated={handleCameraUpdated} />
        )}
      </div>
    );
  }

  const layout = LAYOUTS.find((l) => l.key === layoutKey) ?? LAYOUTS[2];
  const slots = Array.from({ length: layout.tiles }, (_, i) => allCameras[i] ?? null);
  const activeCount = allCameras.length;

  return (
    <div>
      {/* Toolbar with the layout switcher */}
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
          {activeCount} camera{activeCount === 1 ? "" : "s"} · showing {Math.min(activeCount, layout.tiles)}/{layout.tiles}
        </div>

        <div className="flex items-center gap-2">
          {isAdmin && (
            <button
              onClick={() => setShowUpload(true)}
              className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[12px] transition-colors"
              style={{ border: "1px solid var(--border)", background: "var(--card)", color: "var(--foreground)" }}
            >
              <Upload size={14} />
              <span>Upload Video</span>
            </button>
          )}

          {isAdmin && (
            <button
              onClick={() => setShowHistory(true)}
              className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[12px] transition-colors"
              style={{ border: "1px solid var(--border)", background: "var(--card)", color: "var(--foreground)" }}
            >
              <History size={14} />
              <span>History</span>
            </button>
          )}

          <div className="relative" ref={menuRef}>
          <button
            onClick={() => setMenuOpen((o) => !o)}
            className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[12px] transition-colors"
            style={{ border: "1px solid var(--border)", background: "var(--card)", color: "var(--foreground)" }}
          >
            <LayoutGrid size={14} />
            <span style={{ fontFamily: "'DM Mono', monospace" }}>{layout.label}</span>
          </button>

          {menuOpen && (
            <div
              className="absolute right-0 mt-1 py-1 rounded-lg z-20 shadow-xl"
              style={{ border: "1px solid var(--border)", background: "var(--card)", minWidth: 160 }}
            >
              {LAYOUTS.map((l) => {
                const isActive = l.key === layoutKey;
                return (
                  <button
                    key={l.key}
                    onClick={() => selectLayout(l.key)}
                    className="w-full flex items-center gap-3 px-3 py-1.5 text-[12px] transition-colors hover:opacity-80"
                    style={{ color: isActive ? "#f59e0b" : "var(--foreground)", background: isActive ? "rgba(245,158,11,0.08)" : "transparent" }}
                  >
                    <LayoutIcon layout={l} />
                    <span className="flex-1 text-left" style={{ fontFamily: "'DM Mono', monospace" }}>{l.label}</span>
                    {isActive && <Check size={13} />}
                  </button>
                );
              })}
            </div>
          )}
          </div>
        </div>
      </div>

      {/* The wall */}
      <div
        style={{
          display: "grid",
          gap: "0.75rem",
          gridTemplateColumns: `repeat(${layout.cols}, minmax(0, 1fr))`,
          ...(layout.hero
            ? { gridTemplateRows: `repeat(${layout.cols}, minmax(0, 1fr))`, aspectRatio: "16 / 9" }
            : {}),
        }}
      >
        {slots.map((cam, i) => {
          const heroStyle =
            layout.hero && i === 0 ? { gridColumn: `span ${layout.hero}`, gridRow: `span ${layout.hero}` } : {};
          return (
            <div key={cam ? cam.id : `empty-${i}`} style={heroStyle} className={layout.hero ? "min-h-0" : ""}>
              {cam ? (
                <CameraTile
                  cam={cam}
                  alert={getAlert(cam.id)}
                  isSelected={selected === cam.id}
                  onSelect={() => setSelected(selected === cam.id ? null : cam.id)}
                  onExpand={setExpanded}
                  fill={!!layout.hero}
                />
              ) : (
                <EmptyTile fill={!!layout.hero} />
              )}
            </div>
          );
        })}
      </div>

      {expanded && (
        <ExpandedCamera cam={expanded} alert={getAlert(expanded.id)} onClose={() => setExpanded(null)}
          isAdmin={isAdmin} onCameraUpdated={handleCameraUpdated} />
      )}

      {isAdmin && showUpload && (
        <UploadDetectionModal
          onClose={() => setShowUpload(false)}
          onJobStarted={(job) => setDetectionJobs((prev) => [mapDetectionJob(job), ...prev])}
        />
      )}

      {isAdmin && showHistory && (
        <DetectionJobHistoryModal jobs={detectionJobs} onClose={() => setShowHistory(false)} onCancel={handleCancelJob} />
      )}

      {isAdmin && (
        <DetectionJobsPanel
          jobs={detectionJobs.filter((j) => !dismissedJobIds.has(j.id))}
          onDismiss={(id) => setDismissedJobIds((prev) => new Set(prev).add(id))}
          onCancel={handleCancelJob}
        />
      )}
    </div>
  );
}
