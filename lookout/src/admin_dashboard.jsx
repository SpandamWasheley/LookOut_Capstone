import { useState, useEffect } from "react";
import { AlertTriangle, Camera, Users, Zap, ArrowUpRight } from "lucide-react";
import { AlertFeed } from "./AlertFeed";
import { CameraGrid } from "./CameraGrid";
import { RecordsPage } from "./RecordsPage";
import { Sidebar } from "./Sidebar";
import { OfficersPage } from "./OfficersPage";
import { ResidentDatabase } from "./ResidentDatabase";
import { SystemConfig } from "./SystemConfig";
import { mockAlerts, mockCameras, mockOfficers } from "../data/mockData";

const ROLE_PAGES = {
  admin:      ["dashboard", "cameras", "alerts", "records", "residents", "officers", "config"],
  dispatcher: ["dashboard", "cameras", "alerts", "records"],
  officer:    ["cameras", "alerts", "records"],
};

function LiveClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = window.setInterval(() => setTime(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const formatted = time.toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const date = time.toLocaleDateString("en-PH", { weekday: "short", month: "short", day: "numeric" });
  return (
    <div className="text-right">
      <div className="text-base font-semibold text-white tabular-nums" style={{ fontFamily: "'DM Mono', monospace" }}>{formatted}</div>
      <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>{date}</div>
    </div>
  );
}

function AdminDashboard({ user, onLogout }) {
  const role = user?.role ?? "officer";
  const allowed = ROLE_PAGES[role] ?? [];
  const [activePage, setActivePage] = useState(allowed[0]);
  const safePage = allowed.includes(activePage) ? activePage : allowed[0];

  const activeAlerts = mockAlerts.filter((a) => a.status === "active");
  const onlineCount = mockCameras.filter((c) => c.status === "online").length;
  const degradedCount = mockCameras.filter((c) => c.status === "degraded").length;
  const officersOnDuty = mockOfficers.filter((o) => o.status !== "off-duty").length;
  const responding = mockOfficers.filter((o) => o.status === "responding").length;
  const alertCount = activeAlerts.length;

  const kpis = [
    { label: "Active Violations", value: alertCount, sub: "Requires review", accent: "#ef4444", icon: AlertTriangle },
    { label: "Cameras Online", value: `${onlineCount} / ${mockCameras.length}`, sub: `${degradedCount} degraded`, accent: "#f59e0b", icon: Camera },
    { label: "Officers on Duty", value: officersOnDuty, sub: `${responding} responding`, accent: "#3b82f6", icon: Users },
    { label: "Detections Today", value: "1,284", sub: "46 confirmed violations", accent: "#a855f7", icon: Zap },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-slate-950 text-white">
      <Sidebar
        activeView={safePage}
        onViewChange={setActivePage}
        activeRole={role}
        userName={user?.name}
        alertCount={alertCount}
        onLogout={onLogout}
      />

      <main className="flex-1 overflow-auto">

        {safePage === "dashboard" && (
          <div className="flex flex-col h-full">
            {/* Topbar */}
            <div className="flex items-center justify-between px-6 h-16 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
              <div className="flex items-center gap-3">
                <h1 className="text-[20px] font-bold text-white">Overview</h1>
                <div className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs"
                  style={{
                    background: alertCount > 0 ? "rgba(239,68,68,0.1)" : "rgba(16,185,129,0.1)",
                    color: alertCount > 0 ? "#ef4444" : "#10b981",
                  }}>
                  <span className={`w-1.5 h-1.5 rounded-full ${alertCount > 0 ? "animate-pulse" : ""}`}
                    style={{ background: alertCount > 0 ? "#ef4444" : "#10b981" }} />
                  <span className="font-medium">{alertCount > 0 ? `${alertCount} alerts` : "All clear"}</span>
                </div>
              </div>
              <div className="flex items-center gap-6">
                <div className="flex items-center gap-1.5 text-xs" style={{ color: "var(--muted-foreground)" }}>
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
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
                  <div key={kpi.label} className="rounded-2xl p-5 flex items-start gap-3"
                    style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                    <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
                      style={{ background: `${kpi.accent}22` }}>
                      <Icon size={16} style={{ color: kpi.accent }} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-2xl font-bold text-white leading-none">{kpi.value}</div>
                      <div className="text-sm font-medium mt-1.5 text-white">{kpi.label}</div>
                      <div className="text-[12px] mt-0.5" style={{ color: kpi.accent }}>{kpi.sub}</div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Main content */}
            <div className="flex-1 overflow-hidden grid gap-4 px-6 pb-6" style={{ gridTemplateColumns: "1.7fr 1fr" }}>
              {/* Live feeds */}
              <div className="flex flex-col min-h-0 rounded-2xl overflow-hidden"
                style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between px-5 py-3 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
                  <div className="flex items-center gap-2">
                    <Camera size={16} style={{ color: "var(--muted-foreground)" }} />
                    <span className="text-base font-semibold text-white">Live Feeds</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "#ef4444" }}>
                      <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" /> REC
                    </div>
                    <button className="flex items-center gap-1 text-xs" style={{ color: "var(--muted-foreground)" }}>
                      View all <ArrowUpRight size={11} />
                    </button>
                  </div>
                </div>
                <div className="flex-1 overflow-y-auto p-4">
                  <CameraGrid compact />
                </div>
              </div>

              {/* Recent violations */}
              <div className="flex flex-col min-h-0 overflow-y-auto">
                <AlertFeed />
              </div>
            </div>
          </div>
        )}

        {safePage === "alerts" && (
          <div className="p-6">
            <h2 className="text-3xl font-bold mb-6">Violations</h2>
            <AlertFeed />
          </div>
        )}

        {safePage === "records" && <div className="h-full"><RecordsPage /></div>}

        {safePage === "cameras" && (
          <div className="p-6">
            <h2 className="text-3xl font-bold mb-6">Live Feeds</h2>
            <CameraGrid />
          </div>
        )}

        {safePage === "residents" && <div className="h-full"><ResidentDatabase /></div>}
        {safePage === "officers" && <div className="h-full"><OfficersPage /></div>}
        {safePage === "config" && <div className="h-full"><SystemConfig /></div>}

      </main>
    </div>
  );
}



export default AdminDashboard;

