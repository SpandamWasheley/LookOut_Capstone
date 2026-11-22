import { useState, useEffect, useRef } from "react";
import { AlertTriangle, Camera, CalendarDays, Zap, ArrowUpRight, Radio } from "lucide-react";
import { mapAlert } from "./alertModel";
import { NotificationBell } from "./NotificationBell";
import { AlertToast } from "./AlertToast";
import { useAlertToaster } from "./useAlertToaster";
import { armAlertSound } from "./alertSound";
import { AlertFeed } from "./AlertFeed";
import { CameraGrid } from "./CameraGrid";
import { RecordsPage } from "./RecordsPage";
import { Sidebar } from "./Sidebar";
import { OfficersPage } from "./OfficersPage";
import { SystemConfig } from "./SystemConfig";
import { ResidentLog } from "./ResidentLog";
import { RunDetectionPage } from "./RunDetectionPage";
import { AboutPage } from "./AboutPage";
import { useTestingTools } from "./useTestingTools";
import { getAlerts, getAlertCounts, getCameras, getOfficers } from "./api";

// `onAlerts` is handed each polled alert list so arrivals can be spotted where
// they actually arrive — in the response — rather than by an effect trying to
// work out what changed between two renders of the same array.
function useLiveOverviewData(onAlerts) {
  const [alerts, setAlerts] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [officers, setOfficers] = useState([]);
  const [counts, setCounts] = useState(null);

  // Kept in a ref so a new callback identity cannot restart the poll.
  const onAlertsRef = useRef(onAlerts);
  useEffect(() => { onAlertsRef.current = onAlerts; }, [onAlerts]);

  useEffect(() => {
    const refresh = () => {
      // Page 1 only — the compact panel and toaster only need recent alerts,
      // and new arrivals are always newest (ordered by -id).
      getAlerts().then((res) => {
        const list = res.results ?? res;
        setAlerts(list);
        onAlertsRef.current?.(list);
      }).catch(() => {});
      // Counts come from a dedicated endpoint so KPI tiles are always accurate
      // regardless of how many pages of alerts exist.
      getAlertCounts().then(setCounts).catch(() => {});
      getCameras().then((res) => setCameras(res.results ?? res)).catch(() => {});
      getOfficers().then((res) => setOfficers(res.results ?? res)).catch(() => {});
    };
    refresh();
    const interval = setInterval(refresh, 4000);
    return () => clearInterval(interval);
  }, []);

  return { alerts, cameras, officers, counts };
}

// "config" is on EVERY role, because My Account is a section of Settings
// rather than its own page -- it holds the mobile number an alert would be
// texted to, and each role is responsible for its own. A non-admin opening
// Settings gets that one section and nothing else: the barangay's detection
// settings stay admin-only, and SystemSettingsView enforces that server-side
// regardless of what this map says.
const ROLE_PAGES = {
  admin:      ["dashboard", "cameras", "alerts", "records", "residentlog", "rundetection", "officers", "config", "about"],
  dispatcher: ["dashboard", "cameras", "alerts", "records", "residentlog", "config", "about"],
  officer:    ["cameras", "alerts", "records", "config", "about"],
  both:       ["dashboard", "cameras", "alerts", "records", "config", "about"],
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
  // Run Detection is a testing tool: only for an admin, and only while "Show testing tools" is on
  // in Settings -> System. When off its route is blocked as well as hidden.
  const testingTools = useTestingTools(role === "admin");
  const allowed = (ROLE_PAGES[role] ?? []).filter((p) => p !== "rundetection" || testingTools);
  const [activePage, setActivePage] = useState(allowed[0]);
  const safePage = allowed.includes(activePage) ? activePage : allowed[0];

  const { toast, dismiss, handleAlerts, setSuppressed } = useAlertToaster();
  const { alerts, counts } = useLiveOverviewData(handleAlerts);

  // Capture the timestamp from the event and store it so it survives the page
  // switch — CameraGrid may not be mounted yet when the event fires, so passing
  // it as a prop is the only way it reaches the RecordingsModal reliably.
  const [recordingsJumpTo, setRecordingsJumpTo] = useState(null);
  const [openRecordingsTick, setOpenRecordingsTick] = useState(0);
  useEffect(() => {
    const onOpenRec = (e) => {
      setActivePage("cameras");
      setRecordingsJumpTo(e.detail?.timestamp ?? null);
      setOpenRecordingsTick((n) => n + 1);
    };
    window.addEventListener("lookout:open-recordings", onOpenRec);
    return () => window.removeEventListener("lookout:open-recordings", onOpenRec);
  }, []);

  // No toasts on the Violations tab: the list there already shows every
  // arrival, so a popup would be covering the thing it is announcing.
  useEffect(() => { setSuppressed(safePage === "alerts"); }, [safePage, setSuppressed]);

  // Opens the audio device on the first click anywhere in the dashboard, so
  // the first alert chime of a shift is not the one the browser swallows
  // while it decides whether the page is allowed to make a sound.
  useEffect(() => armAlertSound(), []);

  // KPI values come from the counts endpoint (accurate totals, no page cap).
  // Fall back to 0 until the first counts response arrives.
  const alertCount     = counts?.active     ?? 0;
  const dispatchedCount = counts?.dispatched ?? 0;
  const totalCount     = counts?.total      ?? 0;
  const resolvedCount  = counts?.resolved   ?? 0;
  const todayCount     = counts?.today      ?? 0;
  const todayActiveCount = counts?.today_active ?? 0;

  const kpis = [
    { label: "Pending Review", value: alertCount,      sub: "Not yet assigned",                   accent: "#ef4444", icon: AlertTriangle },
    { label: "Assigned",       value: dispatchedCount,  sub: `${alertCount} awaiting assignment`, accent: "#3b82f6", icon: Radio },
    { label: "Alerts Today",   value: todayCount,       sub: `${todayActiveCount} unassigned`,    accent: "#3b82f6", icon: CalendarDays },
    { label: "Total Alerts",   value: totalCount,       sub: `${resolvedCount} resolved`,         accent: "#a855f7", icon: Zap },
  ];

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "var(--background)", color: "var(--foreground)" }}>
      <Sidebar
        activeView={safePage}
        onViewChange={setActivePage}
        activeRole={role}
        alertCount={alertCount}
        onLogout={onLogout}
        hiddenItems={testingTools ? [] : ["rundetection"]}
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
                  <span className="font-medium">{alertCount > 0 ? `${alertCount} to review` : "Nothing to review"}</span>
                </div>
              </div>
              <div className="flex items-center gap-6">
                {/* mapAlert because the bell works in the UI shape, not raw
                    API rows — see NotificationBell. */}
                <NotificationBell
                  alerts={alerts.filter((a) => a.status === "active").map(mapAlert)}
                  onOpenAlerts={() => setActivePage("alerts")}
                />
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
                      <div className="text-[13px] mt-0.5" style={{ color: kpi.accent, opacity: 0.85 }}>{kpi.sub}</div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Main content */}
            <div className="flex-1 overflow-hidden grid gap-4 px-6 pb-6" style={{ gridTemplateColumns: "1fr 340px", gridTemplateRows: "minmax(0, 1fr)" }}>
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
                    <span className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Feeds</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1.5 text-[13px]" style={{ color: "#ef4444" }}>
                      <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse inline-block" />
                      REC
                    </div>
                    <button
                      className="flex items-center gap-1 text-xs hover:cursor-pointer hover:underline"
                      style={{ color: "var(--muted-foreground)" }}
                      onClick={() => setActivePage("cameras")}
                    >
                      Open Feeds <ArrowUpRight size={11} />
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
                      className="text-[13px] font-medium px-2 py-0.5 rounded-full"
                      style={{ background: "rgba(239,68,68,0.12)", color: "#ef4444" }}
                    >
                      {alertCount} to review
                    </span>
                  )}
                </div>
                <div className="flex-1 min-h-0 overflow-hidden p-4">
                  <AlertFeed compact externalAlerts={alerts} user={user} />
                </div>
              </div>
            </div>
          </div>
        )}

        {safePage === "alerts" && <div className="h-full"><AlertFeed showFilters user={user} /></div>}

        {safePage === "records" && (
          <div className="h-full">
            <RecordsPage user={user} onOpenAlerts={() => setActivePage("alerts")} />
          </div>
        )}

        {safePage === "cameras" && (
          <div className="flex flex-col h-full overflow-hidden">
            <div
              className="flex items-center justify-between px-6 h-14 flex-shrink-0"
              style={{ borderBottom: "1px solid var(--border)" }}
            >
              <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Feeds</h1>
              <LiveClock />
            </div>
            <div className="flex-1 overflow-auto p-6">
              <CameraGrid isAdmin={role === "admin"} jumpToTimestamp={recordingsJumpTo} openRecordingsTick={openRecordingsTick} onRecordingsOpened={() => setRecordingsJumpTo(null)} />
            </div>
          </div>
        )}

        {safePage === "rundetection" && <RunDetectionPage />}
        {safePage === "about" && <div className="h-full"><AboutPage /></div>}
        {safePage === "officers" && (
          <div className="h-full">
            <OfficersPage
              alerts={alerts.filter((a) => a.status === "active").map(mapAlert)}
              onOpenAlerts={() => setActivePage("alerts")}
            />
          </div>
        )}
        {safePage === "config" && <div className="h-full"><SystemConfig role={role} /></div>}
        {safePage === "residentlog" && (
          <div className="h-full">
            <ResidentLog
              alerts={alerts.filter((a) => a.status === "active").map(mapAlert)}
              onOpenAlerts={() => setActivePage("alerts")}
              role={role}
            />
          </div>
        )}

      </main>

      <AlertToast
        toast={toast}
        onOpen={() => { dismiss(); setActivePage("alerts"); }}
        onDismiss={dismiss}
      />
    </div>
  );
}



export default AdminDashboard;

