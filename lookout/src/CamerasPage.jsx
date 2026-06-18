import { Activity } from "lucide-react";
import { CameraGrid } from "./CameraGrid";
import { mockCameras } from "../data/mockData";

export function CamerasPage() {
  return (

    
    <div className="flex flex-col h-full overflow-hidden">
      <div
        className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <div className="flex items-center gap-3">
          <h1 className="text-[15px] font-semibold text-white">Live Feeds</h1>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
            RTSP · YOLOv8 inference · 4 zones
          </span>
        </div>
        <div className="flex items-center gap-4">
          {[
            { label: "Online", count: mockCameras.filter((c) => c.status === "online").length, color: "#10b981" },
            { label: "Degraded", count: mockCameras.filter((c) => c.status === "degraded").length, color: "#f59e0b" },
            { label: "Offline", count: mockCameras.filter((c) => c.status === "offline").length, color: "#ef4444" },
          ].map((s) => (
            <div key={s.label} className="flex items-center gap-1.5 text-xs" style={{ color: s.color }}>
              <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: s.color }} />
              {s.count} {s.label}
            </div>
          ))}
          <div className="flex items-center gap-1.5 text-xs" style={{ color: "#ef4444" }}>
            <Activity size={11} className="animate-pulse" />
            Recording
          </div>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        <CameraGrid />
      </div>
    </div>
  );
}
