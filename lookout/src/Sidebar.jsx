import { useState } from "react";
import {
  LayoutDashboard, Camera, Bell, Users, Settings,
  ChevronLeft, ChevronRight, Eye, LogOut, Shield, Archive,
} from "lucide-react";

const navGroups = [
  {
    label: "Monitor",
    items: [
      { id: "dashboard", label: "Overview",   icon: LayoutDashboard, roles: ["admin", "dispatcher"] },
      { id: "cameras",   label: "Live Feeds", icon: Camera,          roles: ["admin", "dispatcher", "officer"] },
      { id: "alerts",    label: "Violations", icon: Bell,            roles: ["admin", "dispatcher", "officer"] },
      { id: "records",   label: "Records",    icon: Archive,         roles: ["admin", "dispatcher", "officer"] },
    ],
  },
  {
    label: "Manage",
    items: [
      { id: "residents", label: "Residents", icon: Users,    roles: ["admin"] },
      { id: "officers",  label: "Personnel", icon: Shield,   roles: ["admin"] },
      { id: "config",    label: "Settings",  icon: Settings, roles: ["admin"] },
    ],
  },
];

export function Sidebar({ activeView, onViewChange, activeRole, userName, alertCount, onLogout }) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className="flex flex-col h-screen transition-all duration-300 flex-shrink-0"
      style={{
        width: collapsed ? 60 : 216,
        background: "var(--sidebar)",
        borderRight: "1px solid var(--sidebar-border)",
      }}
    >
      {/* Header */}
      <div className="flex items-center h-14 flex-shrink-0 px-3 gap-2"
        style={{ borderBottom: "1px solid var(--sidebar-border)" }}>
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="w-7 h-7 flex items-center justify-center rounded-md flex-shrink-0 transition-colors"
          style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>

        <div className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
          style={{ background: "#f59e0b" }}>
          <Eye size={14} color="#0c0f16" strokeWidth={2.5} />
        </div>

        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-white tracking-tight leading-none">LookOut</div>
            <div className="text-[11px] mt-0.5 truncate"
              style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              Barangay Tetuan
            </div>
          </div>
        )}
      </div>

      {/* Logged-in role — single pill, same style as the old toggle */}
      {!collapsed && (
        <div className="px-3 py-3" style={{ borderBottom: "1px solid var(--sidebar-border)" }}>
          <div className="flex rounded-md overflow-hidden" style={{ background: "var(--secondary)" }}>
            <div
              className="flex-1 py-1.5 text-[11px] font-medium text-center capitalize"
              style={{ background: "#f59e0b", color: "#0c0f16" }}
            >
              {activeRole}
            </div>
          </div>
          {userName && (
            <div className="text-[11px] text-center mt-2 truncate" style={{ color: "var(--muted-foreground)" }}>
              {userName}
            </div>
          )}
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3">
        {navGroups.map((group) => {
          const visible = group.items.filter((i) => i.roles.includes(activeRole));
          if (visible.length === 0) return null;
          return (
            <div key={group.label} className="mb-1">
              {!collapsed && (
                <div
                  className="px-4 py-1.5 text-[10px] font-semibold uppercase tracking-widest"
                  style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace", letterSpacing: "0.08em" }}
                >
                  {group.label}
                </div>
              )}
              {visible.map((item) => {
                const Icon = item.icon;
                const isActive = activeView === item.id;
                const showBadge = item.id === "alerts" && alertCount > 0;
                return (
                  <button
                    key={item.id}
                    onClick={() => onViewChange(item.id)}
                    title={collapsed ? item.label : undefined}
                    className="w-full flex items-center gap-2.5 transition-all duration-150"
                    style={{
                      padding: collapsed ? "9px 0" : "9px 12px",
                      justifyContent: collapsed ? "center" : "flex-start",
                      color: isActive ? "#f59e0b" : "var(--sidebar-foreground)",
                      background: isActive ? "rgba(245,158,11,0.08)" : "transparent",
                      borderLeft: isActive ? "2px solid #f59e0b" : "2px solid transparent",
                    }}
                  >
                    <div className="relative flex-shrink-0">
                      <Icon size={15} />
                      {showBadge && (
                        <span className="absolute -top-1 -right-1 w-3 h-3 rounded-full flex items-center justify-center text-[8px] font-bold text-white"
                          style={{ background: "#ef4444" }}>
                          {alertCount > 9 ? "9" : alertCount}
                        </span>
                      )}
                    </div>
                    {!collapsed && <span className="text-[13px] font-medium">{item.label}</span>}
                  </button>
                );
              })}
            </div>
          );
        })}
      </nav>

      {/* Bottom */}
      <div className="flex-shrink-0 p-3 space-y-2" style={{ borderTop: "1px solid var(--sidebar-border)" }}>
        {!collapsed && (
          <div className="flex items-center gap-2 px-2 py-2 rounded-md"
            style={{ background: "rgba(16,185,129,0.08)" }}>
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 flex-shrink-0 animate-pulse" />
            <div className="min-w-0 flex-1">
              <div className="text-[11px] font-medium text-emerald-400 leading-none">AI Active</div>
              <div className="text-[10px] mt-0.5"
                style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                4 cams · YOLOv8
              </div>
            </div>
          </div>
        )}
        <button
          onClick={onLogout}
          title="Sign out"
          className="w-full flex items-center gap-2 rounded-md py-2 px-2 transition-all duration-150"
          style={{ justifyContent: collapsed ? "center" : "flex-start", color: "var(--muted-foreground)" }}
          onMouseEnter={(e) => { e.currentTarget.style.color = "#ef4444"; e.currentTarget.style.background = "rgba(239,68,68,0.08)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}
        >
          <LogOut size={14} className="flex-shrink-0" />
          {!collapsed && <span className="text-[13px] font-medium">Sign out</span>}
        </button>
      </div>
    </aside>
  );
}