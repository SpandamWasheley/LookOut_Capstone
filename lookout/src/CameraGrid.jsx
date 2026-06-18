import { useState } from "react";
import { Maximize2, WifiOff, AlertTriangle } from "lucide-react";
import { mockCameras, mockAlerts, VIOLATION_CONFIG } from "../data/mockData";

export function CameraGrid({ compact = false }) {
  const [selected, setSelected] = useState(null);

  const getAlert = (cameraId) =>
    mockAlerts.find((a) => a.camera === cameraId && (a.status === "active" || a.status === "acknowledged"));

  const cameras = compact ? mockCameras.slice(0, 4) : mockCameras;

  return (
    <div className={`grid gap-3 ${compact ? "grid-cols-2" : "grid-cols-2 xl:grid-cols-2"}`}>
      {cameras.map((cam) => {
        const alert = getAlert(cam.id);
        const isSelected = selected === cam.id;
        return (
          <div
            key={cam.id}
            onClick={() => setSelected(isSelected ? null : cam.id)}
            className="rounded-xl overflow-hidden cursor-pointer group transition-all duration-200"
            style={{
              border: `1px solid ${
                alert ? "rgba(239,68,68,0.3)" : isSelected ? "rgba(245,158,11,0.4)" : "var(--border)"
              }`,
              background: "var(--card)",
            }}
          >
            {/* Feed image */}
            <div className="relative w-full aspect-video overflow-hidden bg-black">
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
                  backgroundImage: "repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.06) 3px, rgba(0,0,0,0.06) 4px)",
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
                  <div className="flex items-center gap-1 px-1.5 py-0.5 rounded"
                    style={{ background: "rgba(239,68,68,0.85)" }}>
                    <span className="w-1 h-1 rounded-full bg-white animate-pulse" />
                    <span className="text-[9px] font-semibold text-white" style={{ fontFamily: "'DM Mono', monospace" }}>LIVE</span>
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

              {/* Alert banner */}
              {alert && (
                <div
                  className="absolute bottom-0 left-0 right-0 px-2.5 py-2"
                  style={{ background: "linear-gradient(to top, rgba(0,0,0,0.85), transparent)" }}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs">{VIOLATION_CONFIG[alert.type].icon}</span>
                    <span className="text-[11px] font-medium text-white">
                      {VIOLATION_CONFIG[alert.type].label}
                    </span>
                  </div>
                </div>
              )}

              {/* Expand on hover */}
              <div className="absolute bottom-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <div className="p-1 rounded" style={{ background: "rgba(0,0,0,0.6)" }}>
                  <Maximize2 size={10} className="text-white" />
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

              {/* Degraded badge */}
              {cam.status === "degraded" && (
                <div className="absolute top-2 right-8 flex items-center gap-1 px-1.5 py-0.5 rounded"
                  style={{ background: "rgba(245,158,11,0.8)" }}>
                  <AlertTriangle size={8} className="text-black" />
                  <span className="text-[9px] font-semibold text-black" style={{ fontFamily: "'DM Mono', monospace" }}>DEGRADED</span>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="px-3 py-2 flex items-center justify-between">
              <div>
                <div className="text-[12px] font-semibold text-white leading-none">{cam.name}</div>
                <div className="text-[10px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{cam.zone}</div>
              </div>
              <div className="text-right">
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
      })}
    </div>
  );
}