import { useEffect, useState } from "react";
import { Eye } from "lucide-react";
import { violationDisplay } from "./constants/violationTypes";
import { ViolationModal } from "./ViolationModal";
import { mapAlert, levelColor } from "./alertModel";
import { getAlerts, updateAlert } from "./api";

const WATCHING_WINDOW_MS = 20000;     // the row is refreshed about every 5 s while the object is seen
const LOOKBACK_MS = 24 * 3600 * 1000;

function clock(ts) {
  return new Date(ts).toLocaleTimeString("en-PH", { hour: "numeric", minute: "2-digit", hour12: true });
}

// Spec v6: Monitoring = an object has been seen for about 2 s but the evidence is still thin.
// A quiet watchlist for proactive watching: no notification, no clip. If the evidence grows the
// SAME event moves up to Possible / Likely and leaves this list (it appears in Violations).
export function MonitoringWatchlist({ user }) {
  const [items, setItems] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const refresh = () =>
      getAlerts({ level: "monitoring" })
        .then((res) => {
          setItems((res.results ?? res).map(mapAlert));
          setNow(Date.now());
        })
        .catch(() => {});
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, []);

  // The open modal always shows the latest poll of its event.
  const selected = items.find((a) => a.id === selectedId) ?? null;

  const recent = items.filter((a) => now - new Date(a.lastSeenAt || a.timestamp).getTime() < LOOKBACK_MS);
  const watching = recent.filter((a) => now - new Date(a.lastSeenAt || a.timestamp).getTime() < WATCHING_WINDOW_MS);

  return (
    <div className="flex flex-col min-h-0 rounded-xl overflow-hidden"
      style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      <div className="flex items-center justify-between px-5 py-2.5 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-2">
          <Eye size={14} style={{ color: levelColor("Monitoring") }} />
          <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Monitoring watchlist</span>
        </div>
        <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
          {watching.length > 0 ? `${watching.length} watching now` : "quiet"}
        </span>
      </div>
      <div className="overflow-y-auto min-h-0 px-3 py-2" style={{ maxHeight: 190 }}>
        {recent.length === 0 ? (
          <div className="text-[13px] py-3 text-center" style={{ color: "var(--muted-foreground)" }}>
            Nothing on watch. Objects seen for about 2 seconds appear here quietly.
          </div>
        ) : (
          recent.slice(0, 20).map((a) => {
            const v = violationDisplay(a.type);
            const VIcon = v.icon;
            const live = watching.includes(a);
            return (
              <button key={a.id} onClick={() => setSelectedId(a.id)}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left hover:opacity-90"
                style={{ background: "transparent" }}>
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${live ? "animate-pulse" : ""}`}
                  style={{ background: live ? levelColor("Monitoring") : "var(--border)" }} />
                <VIcon size={13} style={{ color: v.color, flexShrink: 0 }} />
                <span className="text-[14px] font-medium truncate" style={{ color: "var(--foreground)" }}>{v.label}</span>
                <span className="text-[12px] ml-auto flex-shrink-0" style={{ color: "var(--muted-foreground)" }}>
                  {live ? "since " : "seen "}{clock(a.timestamp)}
                </span>
              </button>
            );
          })
        )}
      </div>
      {selected && (
        <ViolationModal
          alert={selected}
          assignedOfficerNames={selected.officersAssignedNames ?? []}
          userRole={user?.role}
          onReview={async (value) => {
            await updateAlert(selected.dbId, { reviewed_valid: value });
            setItems((all) => all.map((a) => (a.id === selected.id ? { ...a, reviewedValid: value } : a)));
          }}
          onDismiss={() => {}}
          onDispatch={() => {}}
          onResolved={() => {}}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
