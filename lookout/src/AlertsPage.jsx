import { Bell } from "lucide-react";
import { AlertFeed } from "./AlertFeed";
import { SystemStatusCard } from "./SystemStatusCard";
import { mockAlerts } from "../data/mockData";

export function AlertsPage() {
  const activeCount = mockAlerts.filter((a) => a.status === "active").length;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div
        className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <div className="flex items-center gap-3">
          <h1 className="text-[15px] font-semibold text-white">Violations</h1>
          {activeCount > 0 && (
            <span
              className="flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full"
              style={{ background: "rgba(239,68,68,0.1)", color: "#ef4444" }}
            >
              <Bell size={10} />
              {activeCount} active
            </span>
          )}
        </div>
        <p className="text-xs" style={{ color: "var(--muted-foreground)" }}>
          AI-detected · evidence captured · human review required
        </p>
      </div>

      <div className="flex-1 overflow-hidden grid gap-4 px-6 py-5" style={{ gridTemplateColumns: "1fr 268px" }}>
        {/* Alert feed */}
        <div className="flex flex-col min-h-0 overflow-y-auto">
          <AlertFeed />
        </div>

        {/* System status card */}
        <div
          className="flex flex-col min-h-0 overflow-y-auto rounded-xl p-4"
          style={{ background: "var(--card)", border: "1px solid var(--border)" }}
        >
          <SystemStatusCard />
        </div>
      </div>
    </div>
  );
}
