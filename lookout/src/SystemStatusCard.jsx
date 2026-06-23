import { useState, useEffect } from "react";
import { Camera, Clock, AlertTriangle, Moon, Sun } from "lucide-react";
import { getSettings } from "./api";

function formatHour12(timeStr) {
  if (!timeStr) return "--";
  const [h, m] = timeStr.split(":").map(Number);
  const period = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m === 0 ? `${h12} ${period}` : `${h12}:${String(m).padStart(2, "0")} ${period}`;
}

function isWithinWindow(now, startStr, endStr) {
  if (!startStr || !endStr) return false;
  const [sh, sm] = startStr.split(":").map(Number);
  const [eh, em] = endStr.split(":").map(Number);
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const startMin = sh * 60 + sm;
  const endMin = eh * 60 + em;
  if (startMin <= endMin) return nowMin >= startMin && nowMin < endMin;
  return nowMin >= startMin || nowMin < endMin; // overnight window
}

export function SystemStatusCard({ alerts = [], cameras = [] }) {
  const [settings, setSettings] = useState(null);
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    getSettings().then(setSettings).catch(() => {});
    const id = window.setInterval(() => setNow(new Date()), 30000);
    return () => window.clearInterval(id);
  }, []);

  const onlineCams = cameras.filter((c) => c.status === "online").length;
  const totalCams  = cameras.length;
  const degraded   = cameras.filter((c) => c.status === "degraded").length;
  const totalToday = alerts.length;
  const pending    = alerts.filter((a) => a.status === "active" || a.status === "acknowledged").length;

  const latest = [...alerts].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))[0];

  const isNight = settings ? isWithinWindow(now, settings.curfew_start, settings.curfew_end) : null;

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
    {
      icon: isNight ? Moon : Sun,
      value: settings ? (isNight ? "Active" : "Inactive") : "—",
      label: "Curfew Status",
      sub: settings ? `${formatHour12(settings.curfew_start)} – ${formatHour12(settings.curfew_end)}` : "—",
      accent: isNight ? "#a78bfa" : "#10b981",
    },
  ];

  return (
    <div className="grid grid-cols-4 gap-4">
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
  );
}
