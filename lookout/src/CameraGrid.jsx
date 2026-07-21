import { useEffect, useRef, useState } from "react";
import { Maximize2, WifiOff, LayoutGrid, Check } from "lucide-react";
import { getAlerts, getCameras } from "./api";

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
    name: raw.name,
    zone: raw.zone,
    status: raw.status,
    fps: raw.fps,
    lastMotion: timeAgo(raw.last_motion_at),
    imageUrl: raw.image_url,
  };
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

function CameraTile({ cam, alert, isSelected, onSelect, fill }) {
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
      {/* Feed image */}
      <div className={`relative w-full overflow-hidden bg-black ${fill ? "flex-1 min-h-0" : "aspect-video"}`}>
        <img
          src={cam.imageUrl}
          alt={`${cam.name} feed`}
          className="w-full h-full object-cover transition-all duration-300"
          style={{ opacity: cam.status === "offline" ? 0.2 : 1 }}
        />

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

        {/* Expand on hover */}
        <div className="absolute bottom-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <div className="p-1 rounded" style={{ background: "rgba(0,0,0,0.6)" }}>
            <Maximize2 size={10} style={{ color: "#fff" }} />
          </div>
        </div>

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

export function CameraGrid({ compact = false }) {
  const [selected, setSelected] = useState(null);
  const [allCameras, setAllCameras] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [layoutKey, setLayoutKey] = useState(
    () => localStorage.getItem(LAYOUT_STORAGE_KEY) || "2x2"
  );
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    const refresh = () => {
      getCameras().then((res) => setAllCameras((res.results ?? res).map(mapCamera))).catch(() => {});
      getAlerts().then((res) => setAlerts(res.results ?? res)).catch(() => {});
    };
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, []);

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
          />
        ))}
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
                  fill={!!layout.hero}
                />
              ) : (
                <EmptyTile fill={!!layout.hero} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
