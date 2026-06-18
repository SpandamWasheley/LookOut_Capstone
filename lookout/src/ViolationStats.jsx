import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell, Legend
} from "recharts";
import { mockAlerts, hourlyViolations, weeklyTrend, VIOLATION_CONFIG } from "../data/mockData";

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg px-3 py-2 text-xs" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      <div className="font-medium text-white mb-1.5">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 mb-0.5">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color }} />
          <span style={{ color: "#94a3b8" }}>{p.name}</span>
          <span className="font-medium" style={{ color: "#f1f5f9", fontFamily: "'DM Mono', monospace" }}>{p.value}</span>
        </div>
      ))}
    </div>
  );
};

const pieData = Object.entries(VIOLATION_CONFIG).map(([key, cfg]) => ({
  name: cfg.label.replace("Violation", "").replace("Ordinance", "").trim(),
  value: mockAlerts.filter((a) => a.type === key).length,
  color: cfg.color,
}));

export function ViolationStats() {
  const activeCount = mockAlerts.filter((a) => a.status === "active").length;
  const resolvedToday = mockAlerts.filter((a) => a.status === "resolved").length;
  const dispatchedCount = mockAlerts.filter((a) => a.status === "dispatched").length;

  return (
    <div className="space-y-5">
      {/* KPIs */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: "Active",     value: activeCount,     color: "#ef4444" },
          { label: "Dispatched", value: dispatchedCount, color: "#3b82f6" },
          { label: "Resolved",   value: resolvedToday,   color: "#10b981" },
        ].map((kpi) => (
          <div key={kpi.label} className="rounded-lg p-3 text-center"
            style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
            <div className="text-2xl font-bold leading-none" style={{ color: kpi.color }}>{kpi.value}</div>
            <div className="text-[11px] mt-1" style={{ color: "var(--muted-foreground)" }}>{kpi.label}</div>
          </div>
        ))}
      </div>

      {/* Hourly */}
      <div>
        <div className="text-xs font-medium mb-3" style={{ color: "var(--muted-foreground)" }}>Violations by hour</div>
        <ResponsiveContainer width="100%" height={110}>
          <BarChart data={hourlyViolations} margin={{ top: 0, right: 0, left: -28, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 6" stroke="rgba(255,255,255,0.04)" />
            <XAxis dataKey="hour" tick={{ fill: "#64748b", fontSize: 10, fontFamily: "'DM Mono', monospace" }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: "#64748b", fontSize: 10, fontFamily: "'DM Mono', monospace" }} axisLine={false} tickLine={false} />
            <Tooltip content={<CustomTooltip />} />
            <Bar dataKey="curfew"    stackId="a" fill="#f59e0b" name="Curfew" />
            <Bar dataKey="waste"     stackId="a" fill="#84cc16" name="Waste" />
            <Bar dataKey="noise"     stackId="a" fill="#a78bfa" name="Noise" />
            <Bar dataKey="indecency" stackId="a" fill="#f97316" name="Indecency" />
            <Bar dataKey="accident"  stackId="a" fill="#ef4444" radius={[2, 2, 0, 0]} name="Accident" />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Weekly trend */}
      <div>
        <div className="text-xs font-medium mb-3" style={{ color: "var(--muted-foreground)" }}>Weekly trend</div>
        <ResponsiveContainer width="100%" height={80}>
          <LineChart data={weeklyTrend} margin={{ top: 0, right: 0, left: -28, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 6" stroke="rgba(255,255,255,0.04)" />
            <XAxis dataKey="day" tick={{ fill: "#64748b", fontSize: 10, fontFamily: "'DM Mono', monospace" }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: "#64748b", fontSize: 10, fontFamily: "'DM Mono', monospace" }} axisLine={false} tickLine={false} />
            <Tooltip content={<CustomTooltip />} />
            <Line type="monotone" dataKey="violations" stroke="#f59e0b" strokeWidth={1.5} dot={{ fill: "#f59e0b", r: 2 }} name="Total" />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Breakdown */}
      <div>
        <div className="text-xs font-medium mb-3" style={{ color: "var(--muted-foreground)" }}>By type</div>
        <ResponsiveContainer width="100%" height={130}>
          <PieChart>
            <Pie data={pieData} cx="38%" cy="50%" innerRadius={30} outerRadius={50} paddingAngle={3} dataKey="value">
              {pieData.map((entry, i) => <Cell key={i} fill={entry.color} />)}
            </Pie>
            <Legend
              layout="vertical"
              align="right"
              verticalAlign="middle"
              formatter={(val) => <span style={{ fontSize: 10, color: "#94a3b8" }}>{val}</span>}
              iconSize={7}
            />
            <Tooltip content={<CustomTooltip />} />
          </PieChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}