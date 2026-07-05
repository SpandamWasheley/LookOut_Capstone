import { useState, useEffect } from "react";
import { AlertTriangle, Camera, Users, Zap, ArrowUpRight } from "lucide-react";
import { CameraGrid } from "./CameraGrid";
import { AlertFeed } from "./AlertFeed";
import { mockAlerts, mockCameras, mockOfficers, VIOLATION_CONFIG } from "../data/mockData";

function LiveClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = window.setInterval(() => setTime(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const formatted = time.toLocaleTimeString("en-PH", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
  });
  const date = time.toLocaleDateString("en-PH", { weekday: "short", month: "short", day: "numeric" });
  return (
    <div className="text-right">
      <div className="text-base font-semibold text-white tabular-nums" style={{ fontFamily: "'DM Mono', monospace" }}>
        {formatted}
      </div>
      <div className="text-xs" style={{ color: "rgba(255,255,255,0.5)" }}>{date}</div>
    </div>
  );
}

const statusDot = (color, pulse = false) => (
  <span
    className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${pulse ? "animate-pulse" : ""}`}
    style={{ background: color }}
  />
);

export function Dashboard() {
  const activeAlerts = mockAlerts.filter((a) => a.status === "active");
  const onlineCount = mockCameras.filter((c) => c.status === "online").length;
  const officersOnDuty = mockOfficers.filter((o) => o.status !== "off-duty").length;

  const kpis = [
    {
      label: "Active Violations",
      value: activeAlerts.length,
      sub: "Requires review",
      accent: "#ef4444",
      icon: AlertTriangle,
    },
    {
      label: "Cameras Online",
      value: `${onlineCount} / ${mockCameras.length}`,
      sub: `${mockCameras.filter((c) => c.status === "degraded").length} degraded`,
      accent: "#f59e0b",
      icon: Camera,
    },
    {
      label: "Officers on Duty",
      value: officersOnDuty,
      sub: `${mockOfficers.filter((o) => o.status === "responding").length} responding`,
      accent: "#3b82f6",
      icon: Users,
    },
    {
      label: "Total Alerts",
      value: mockAlerts.length,
      sub: `${mockAlerts.filter((a) => a.status === "resolved").length} resolved`,
      accent: "#a855f7",
      icon: Zap,
    },
  ];

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Topbar */}
      <div
        className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}
      >
        <div className="flex items-center gap-3">
          <h1 className="text-[15px] font-semibold text-white">Overview</h1>
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs"
            style={{
              background: activeAlerts.length > 0 ? "rgba(239,68,68,0.1)" : "rgba(16,185,129,0.1)",
              color: activeAlerts.length > 0 ? "#ef4444" : "#10b981",
            }}
          >
            {statusDot(activeAlerts.length > 0 ? "#ef4444" : "#10b981", activeAlerts.length > 0)}
            <span className="font-medium">{activeAlerts.length > 0 ? `${activeAlerts.length} alerts` : "All clear"}</span>
          </div>
        </div>
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-1.5 text-xs" style={{ color: "rgba(255,255,255,0.5)" }}>
            {statusDot("#10b981")}
            <span>AI running · YOLOv8 + ArcFace</span>
          </div>
          <LiveClock />
        </div>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-4 px-6 py-4 flex-shrink-0">
        {kpis.map((kpi) => {
          const Icon = kpi.icon;
          return (
            <div
              key={kpi.label}
              className="rounded-xl p-4 flex items-start gap-3"
              style={{ background: "#12161c", border: "1px solid rgba(255,255,255,0.08)" }}
            >
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
                style={{ background: `${kpi.accent}14` }}
              >
                <Icon size={15} style={{ color: kpi.accent }} />
              </div>
              <div className="min-w-0">
                <div className="text-xl font-semibold text-white leading-none">{kpi.value}</div>
                <div className="text-xs font-medium mt-1" style={{ color: "rgba(255,255,255,0.5)" }}>{kpi.label}</div>
                <div className="text-[11px] mt-0.5" style={{ color: kpi.accent, opacity: 0.85 }}>{kpi.sub}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-hidden grid gap-4 px-6 pb-6" style={{ gridTemplateColumns: "1fr 340px" }}>

        {/* Camera feeds */}
        <div
          className="flex flex-col min-h-0 rounded-xl overflow-hidden"
          style={{ background: "#12161c", border: "1px solid rgba(255,255,255,0.08)" }}
        >
          <div
            className="flex items-center justify-between px-5 py-3 flex-shrink-0"
            style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}
          >
            <div className="flex items-center gap-2">
              <Camera size={14} style={{ color: "rgba(255,255,255,0.5)" }} />
              <span className="text-sm font-medium text-white">Live Feeds</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "#ef4444" }}>
                <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse inline-block" />
                REC
              </div>
              <button className="flex items-center gap-1 text-xs" style={{ color: "rgba(255,255,255,0.5)" }}>
                View all <ArrowUpRight size={11} />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <CameraGrid compact />
          </div>
        </div>

        {/* Alert feed */}
        <div
          className="flex flex-col min-h-0 rounded-xl overflow-hidden"
          style={{ background: "#12161c", border: "1px solid rgba(255,255,255,0.08)" }}
        >
          <div
            className="flex items-center justify-between px-5 py-3 flex-shrink-0"
            style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}
          >
            <div className="flex items-center gap-2">
              <AlertTriangle size={14} style={{ color: "rgba(255,255,255,0.5)" }} />
              <span className="text-sm font-medium text-white">Recent Violations</span>
            </div>
            {activeAlerts.length > 0 && (
              <span
                className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444" }}
              >
                {activeAlerts.length} active
              </span>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <AlertFeed compact />
          </div>
        </div>
      </div>
    </div>
  );
}
