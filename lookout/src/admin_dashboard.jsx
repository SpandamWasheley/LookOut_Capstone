import { useState, useEffect } from "react";
import { AlertTriangle, Camera, Users, Zap, ArrowUpRight } from "lucide-react";
import { AlertFeed } from "./AlertFeed";
import { CameraGrid } from "./CameraGrid";
import { RecordsPage } from "./RecordsPage";
import { Sidebar } from "./Sidebar";
import { OfficersPage } from "./OfficersPage";
import { ResidentDatabase } from "./ResidentDatabase";
import { SystemConfig } from "./SystemConfig";
import { ResidentLog } from "./ResidentLog";
import { getAlerts, getCameras, getOfficers } from "./api";

function useLiveOverviewData() {
  const [alerts, setAlerts] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [officers, setOfficers] = useState([]);

  useEffect(() => {
    const refresh = () => {
      getAlerts().then((res) => setAlerts(res.results ?? res)).catch(() => {});
      getCameras().then((res) => setCameras(res.results ?? res)).catch(() => {});
      getOfficers().then((res) => setOfficers(res.results ?? res)).catch(() => {});
    };
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, []);

  return { alerts, cameras, officers };
}

const ROLE_PAGES = {
  admin:      ["dashboard", "cameras", "alerts", "records", "residentlog", "residents", "officers", "config"],
  dispatcher: ["dashboard", "cameras", "alerts", "records", "residentlog"],
  officer:    ["cameras", "alerts", "records"],
  both:       ["dashboard", "cameras", "alerts", "records"],
};

function LiveClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const id = window.setInterval(() => setTime(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const formatted = time.toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true });
  const date = time.toLocaleDateString("en-PH", { weekday: "short", month: "short", day: "numeric" });
  return (
    <div className="text-right">
      <div className="text-base font-semibold tabular-nums" style={{ fontFamily: "'DM Mono', monospace", color: "var(--foreground)" }}>{formatted}</div>
      <div className="text-xs" style={{ color: "var(--muted-foreground)" }}>{date}</div>
    </div>
  );
}

const statusDot = (color, pulse = false) => (
  <span
    className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${pulse ? "animate-pulse" : ""}`}
    style={{ background: color }}
  />
);

function AdminDashboard({ user, onLogout }) {
  const role = user?.role ?? "officer";
  const allowed = ROLE_PAGES[role] ?? [];
  const [activePage, setActivePage] = useState(allowed[0]);
  const safePage = allowed.includes(activePage) ? activePage : allowed[0];

  const { alerts, cameras, officers } = useLiveOverviewData();

  const activeAlerts = alerts.filter((a) => a.status === "active");
  const onlineCount = cameras.filter((c) => c.status === "online").length;
  const degradedCount = cameras.filter((c) => c.status === "degraded").length;
  const officersOnDuty = officers.filter((o) => o.status !== "off-duty").length;
  const responding = officers.filter((o) => o.status === "responding").length;
  const alertCount = activeAlerts.length;

  const kpis = [
    { label: "Active Violations", value: alertCount, sub: "Requires review", accent: "#ef4444", icon: AlertTriangle },
    { label: "Cameras Online", value: `${onlineCount} / ${cameras.length}`, sub: `${degradedCount} degraded`, accent: "#f59e0b", icon: Camera },
    { label: "Officers on Duty", value: officersOnDuty, sub: `${responding} responding`, accent: "#3b82f6", icon: Users },
    { label: "Total Alerts", value: alerts.length, sub: `${alerts.filter((a) => a.status === "resolved").length} resolved`, accent: "#a855f7", icon: Zap },
  ];

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "var(--background)", color: "var(--foreground)" }}>
      <Sidebar
        activeView={safePage}
        onViewChange={setActivePage}
        activeRole={role}
        alertCount={alertCount}
        onLogout={onLogout}
      />

      <main className="flex-1 overflow-auto">

        {safePage === "dashboard" && (
          <div className="flex flex-col h-full overflow-hidden">
            {/* Topbar */}
            <div
              className="flex items-center justify-between px-6 h-14 flex-shrink-0"
              style={{ borderBottom: "1px solid var(--border)" }}
            >
              <div className="flex items-center gap-3">
                <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Overview</h1>
                <div
                  className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs"
                  style={{
                    background: alertCount > 0 ? "rgba(239,68,68,0.1)" : "rgba(16,185,129,0.1)",
                    color: alertCount > 0 ? "#ef4444" : "#10b981",
                  }}
                >
                  {statusDot(alertCount > 0 ? "#ef4444" : "#10b981", alertCount > 0)}
                  <span className="font-medium">{alertCount > 0 ? `${alertCount} alerts` : "All clear"}</span>
                </div>
              </div>
              <div className="flex items-center gap-6">
                <div className="flex items-center gap-1.5 text-xs" style={{ color: "var(--muted-foreground)" }}>
                  {statusDot("#10b981")}
                  <span>AI running · YOLOv8 + InsightFace</span>
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
                    className="rounded-xl p-4 flex items-center gap-3"
                    style={{ background: "var(--card)", border: "1px solid var(--border)" }}
                  >
                    <div
                      className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                      style={{ background: `${kpi.accent}14` }}
                    >
                      <Icon size={15} style={{ color: kpi.accent }} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-xl font-semibold leading-none" style={{ color: "var(--foreground)" }}>{kpi.value}</div>
                      <div className="text-xs font-medium mt-1" style={{ color: "var(--muted-foreground)" }}>{kpi.label}</div>
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
                style={{ background: "var(--card)", border: "1px solid var(--border)" }}
              >
                <div
                  className="flex items-center justify-between px-5 py-3 flex-shrink-0"
                  style={{ borderBottom: "1px solid var(--border)" }}
                >
                  <div className="flex items-center gap-2">
                    <Camera size={14} style={{ color: "var(--muted-foreground)" }} />
                    <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Live Feeds</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "#ef4444" }}>
                      <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse inline-block" />
                      REC
                    </div>
                    <button
                      className="flex items-center gap-1 text-xs hover:cursor-pointer hover:underline"
                      style={{ color: "var(--muted-foreground)" }}
                      onClick={() => setActivePage("cameras")}
                    >
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
                style={{ background: "var(--card)", border: "1px solid var(--border)" }}
              >
                <div
                  className="flex items-center justify-between px-5 py-3 flex-shrink-0"
                  style={{ borderBottom: "1px solid var(--border)" }}
                >
                  <div className="flex items-center gap-2">
                    <AlertTriangle size={14} style={{ color: "#ef4444" }} />
                    <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Recent Violations</span>
                  </div>
                  {alertCount > 0 && (
                    <span
                      className="text-[11px] font-medium px-2 py-0.5 rounded-full"
                      style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444" }}
                    >
                      {alertCount} active
                    </span>
                  )}
                </div>
                <div className="flex-1 overflow-y-auto p-4">
                  <AlertFeed compact user={user} />
                </div>
              </div>
            </div>
          </div>
        )}

        {safePage === "alerts" && <div className="h-full"><AlertFeed showFilters user={user} /></div>}

        {safePage === "records" && <div className="h-full"><RecordsPage /></div>}

        {safePage === "cameras" && (
          <div className="flex flex-col h-full overflow-hidden">
            <div
              className="flex items-center px-6 h-14 flex-shrink-0"
              style={{ borderBottom: "1px solid var(--border)" }}
            >
              <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Live Feeds</h1>
            </div>
            <div className="flex-1 overflow-auto p-6">
              <CameraGrid />
            </div>
          </div>
        )}

        {safePage === "residents" && <div className="h-full"><ResidentDatabase /></div>}
        {safePage === "officers" && <div className="h-full"><OfficersPage /></div>}
        {safePage === "config" && <div className="h-full"><SystemConfig /></div>}
        {safePage === "residentlog" && <div className="h-full"><ResidentLog /></div>}

      </main>
    </div>
  );
}



export default AdminDashboard;

