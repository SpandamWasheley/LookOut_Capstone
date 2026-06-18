import { useState, useEffect } from "react";
import { Camera, Clock, AlertTriangle, Moon } from "lucide-react";
import { mockAlerts, mockCameras, VIOLATION_CONFIG } from "../data/mockData";

function LiveTime() {
  const [t, setT] = useState(new Date());
  useEffect(() => {
    const id = window.setInterval(() => setT(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <span style={{ fontFamily: "'DM Mono', monospace" }}>
      {t.toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", hour12: false })}
    </span>
  );
}

function Row({ icon: Icon, label, value, valueColor = "#f1f5f9" }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5"
      style={{ borderBottom: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2.5">
        <Icon size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
        <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{label}</span>
      </div>
      <div className="text-[12px] font-semibold" style={{ color: valueColor, fontFamily: "'DM Mono', monospace" }}>
        {value}
      </div>
    </div>
  );
}

export function SystemStatusCard() {
  const onlineCams   = mockCameras.filter((c) => c.status === "online").length;
  const totalCams    = mockCameras.length;
  const activeAlerts = mockAlerts.filter((a) => a.status === "active");
  const dispatched   = mockAlerts.filter((a) => a.status === "dispatched").length;
  const pending      = mockAlerts.filter((a) => a.status === "active" || a.status === "acknowledged").length;
  const totalToday   = mockAlerts.length;

  const latest = activeAlerts[0] ?? mockAlerts[0];
  const latestVcfg = latest ? VIOLATION_CONFIG[latest.type] : null;

  return (
    <div className="space-y-5">

      {/* System Status */}
      <div>
        <div className="text-[10px] font-semibold tracking-widest uppercase mb-1"
          style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace", letterSpacing: "0.1em" }}>
          System Status
        </div>
        <div className="rounded-xl px-3" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
          <Row
            icon={Camera}
            label="Cameras online"
            value={`${onlineCams} / ${totalCams}`}
            valueColor={onlineCams === totalCams ? "#10b981" : "#f59e0b"}
          />
          <Row icon={Clock} label="Last detection" value={<LiveTime />} />
        </div>
      </div>

      {/* Today's Violations */}
      <div>
        <div className="text-[10px] font-semibold tracking-widest uppercase mb-2"
          style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace", letterSpacing: "0.1em" }}>
          Today's Violations
        </div>
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: "Total",      value: totalToday, color: "#f59e0b" },
            { label: "Active",     value: activeAlerts.length, color: "#ef4444" },
            { label: "Dispatched", value: dispatched,  color: "#3b82f6" },
            { label: "Pending",    value: pending,     color: "#a78bfa" },
          ].map((s) => (
            <div key={s.label} className="rounded-xl p-3"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              <div className="text-2xl font-bold leading-none mb-1" style={{ color: s.color }}>
                {s.value}
              </div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                {s.label}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Latest Alert */}
      {latest && latestVcfg && (
        <div>
          <div className="text-[10px] font-semibold tracking-widest uppercase mb-2"
            style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace", letterSpacing: "0.1em" }}>
            Latest Alert
          </div>
          <div className="rounded-xl p-3.5"
            style={{
              background: "var(--secondary)",
              border: "1px solid rgba(239,68,68,0.25)",
              borderLeft: "3px solid #ef4444",
            }}>
            <div className="flex items-start justify-between gap-2 mb-2">
              <div className="text-[13px] font-semibold text-white leading-snug">
                {latestVcfg.label}
              </div>
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                style={{ background: "rgba(239,68,68,0.18)", color: "#ef4444" }}>
                {(latest.confidence * 100).toFixed(0)}% conf
              </span>
            </div>
            <div className="flex items-center gap-3 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
              <span className="flex items-center gap-1">
                <AlertTriangle size={9} style={{ color: "#f59e0b" }} />
                {latest.cameraZone}
              </span>
              <span className="flex items-center gap-1">
                <Clock size={9} />
                {new Date(latest.timestamp).toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", hour12: false })}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Shift Info */}
      <div>
        <div className="text-[10px] font-semibold tracking-widest uppercase mb-1"
          style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace", letterSpacing: "0.1em" }}>
          Shift Info
        </div>
        <div className="rounded-xl px-3" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
          <div className="flex items-center justify-between gap-3 py-2.5"
            style={{ borderBottom: "1px solid var(--border)" }}>
            <div className="flex items-center gap-2.5">
              <Moon size={13} style={{ color: "var(--muted-foreground)" }} />
              <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>Current shift</span>
            </div>
            <span className="text-[12px] font-semibold" style={{ color: "#f1f5f9" }}>Night</span>
          </div>
          <div className="flex items-center justify-between gap-3 py-2.5">
            <div className="flex items-center gap-2.5">
              <Clock size={13} style={{ color: "var(--muted-foreground)" }} />
              <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>Hours</span>
            </div>
            <span className="text-[12px] font-semibold" style={{ color: "#f1f5f9", fontFamily: "'DM Mono', monospace" }}>
              10
            </span>
          </div>
        </div>
      </div>

    </div>
  );
}