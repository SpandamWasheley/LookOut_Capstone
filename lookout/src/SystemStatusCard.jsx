import { Camera, Clock, AlertTriangle } from "lucide-react";

export function SystemStatusCard({ alerts = [], cameras = [] }) {
  const onlineCams = cameras.filter((c) => c.status === "online").length;
  const totalCams  = cameras.length;
  const degraded   = cameras.filter((c) => c.status === "degraded").length;
  const totalToday = alerts.length;
  const pending    = alerts.filter((a) => a.status === "active" || a.status === "dispatched").length;

  const latest = [...alerts].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))[0];

  const kpis = [
    {
      icon: Camera,
      value: `${onlineCams} / ${totalCams}`,
      label: "Cameras Online",
      sub: degraded > 0 ? `${degraded} degraded` : "All online",
      accent: degraded > 0 ? "#f59e0b" : "#10b981",
    },
    {
      icon: Clock,
      value: latest ? new Date(latest.timestamp).toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", hour12: false }) : "—",
      label: "Last Detection",
      sub: latest ? latest.camera_zone : "No detections yet",
      accent: "#3b82f6",
    },
    {
      icon: AlertTriangle,
      value: totalToday,
      label: "Total Violations",
      sub: `${pending} pending`,
      accent: "#ef4444",
    },
  ];

  return (
    <div className="grid grid-cols-3 gap-4">
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
              <div className="text-[14px] mt-0.5" style={{ color: kpi.accent }}>{kpi.sub}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
