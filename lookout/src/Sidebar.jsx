import { useState, useEffect } from "react";
import {
  LayoutDashboard, Camera, Bell, Users, Settings,
  ChevronLeft, ChevronRight, Eye, LogOut, Shield,
  Archive, TrendingUp, Sun, Moon,
} from "lucide-react";

const navGroups = [
  {
    label: "Monitor",
    items: [
      { id: "dashboard",  label: "Overview",     icon: LayoutDashboard, roles: ["admin", "dispatcher", "both"] },
      { id: "cameras",    label: "Live Feeds",   icon: Camera,          roles: ["admin", "dispatcher", "both"] },
      { id: "alerts",     label: "Violations",   icon: Bell,            roles: ["admin", "dispatcher", "both"] },
      { id: "records",    label: "Records",      icon: Archive,         roles: ["admin", "dispatcher", "both"] },
      { id: "residentlog",label: "Resident Log", icon: TrendingUp,      roles: ["admin", "dispatcher"] },
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

export function Sidebar({ activeView, onViewChange, activeRole, onRoleChange, alertCount, onLogout }) {
  const [collapsed, setCollapsed] = useState(false);

  const [isLight, setIsLight] = useState(() => {
    const saved = localStorage.getItem("lookout-theme");
    return saved !== "dark";
  });

  useEffect(() => {
    if (isLight) {
      document.documentElement.classList.remove("dark");
      localStorage.setItem("lookout-theme", "light");
    } else {
      document.documentElement.classList.add("dark");
      localStorage.setItem("lookout-theme", "dark");
    }
  }, [isLight]);

  const sidebarBg     = "var(--sidebar)";
  const sidebarBorder = "var(--sidebar-border)";
  const activeBg      = "rgba(234,243,242,0.14)";
  const activeColor   = "var(--sidebar-primary)";
  const mutedColor    = "var(--sidebar-foreground)";

  return (
    <aside
      className="flex flex-col h-screen transition-all duration-300 flex-shrink-0 relative"
      style={{
        width: collapsed ? 60 : 220,
        background: sidebarBg,
        borderRight: `1px solid ${sidebarBorder}`,
        overflow: "visible",
      }}
    >
      {/* ── Collapse handle — sits on the right edge, half outside ── */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        title={collapsed ? "Expand" : "Collapse"}
        className="absolute z-20 flex items-center justify-center transition-all duration-150"
        style={{
          top: "14px",
          right: "-13px",
          width: 26,
          height: 26,
          background: sidebarBg,
          border: `1px solid ${sidebarBorder}`,
          borderLeft: "none",
          borderRadius: "0 8px 8px 0",
          color: mutedColor,
          cursor: "pointer",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.color = activeColor; }}
        onMouseLeave={(e) => { e.currentTarget.style.color = mutedColor; }}
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
      </button>

      {/* Header */}
      <div
        className="flex items-center h-14 flex-shrink-0 px-3 gap-2.5"
        style={{ borderBottom: `1px solid ${sidebarBorder}` }}
      >
        <div
          className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
          style={{ background: "var(--sidebar-primary)" }}
        >
          <Eye size={14} color="var(--sidebar-primary-foreground)" strokeWidth={2.5} />
        </div>
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold leading-none" style={{ color: "var(--sidebar-primary)" }}>
              LookOut
            </div>
            <div className="text-[11px] mt-0.5 truncate"
              style={{ color: mutedColor, fontFamily: "'DM Mono', monospace" }}>
              Barangay Tetuan
            </div>
          </div>
        )}
      </div>

      {/* Role tabs */}
      {!collapsed && onRoleChange && (
        <div className="px-3 py-3" style={{ borderBottom: `1px solid ${sidebarBorder}` }}>
          <div className="flex rounded-md overflow-hidden" style={{ background: "rgba(0,0,0,0.18)" }}>
            {["admin", "dispatcher"].map((role) => (
              <button
                key={role}
                onClick={() => onRoleChange(role)}
                className="flex-1 py-1.5 text-[11px] font-medium transition-all duration-150 capitalize"
                style={{
                  background: activeRole === role ? "var(--sidebar-primary)" : "transparent",
                  color: activeRole === role ? "var(--sidebar-primary-foreground)" : mutedColor,
                }}
              >
                {role === "dispatcher" ? "Dispatcher" : "Admin"}
              </button>
            ))}
          </div>
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
                  className="px-4 py-1.5 text-[10px] font-semibold uppercase"
                  style={{ color: mutedColor, fontFamily: "'DM Mono', monospace", letterSpacing: "0.08em", opacity: 0.6 }}
                >
                  {group.label}
                </div>
              )}
              {visible.map((item) => {
                const Icon = item.icon;
                const isActive = activeView === item.id;
                const showBadge = item.id === "alerts" && alertCount > 0;
                return (
                  <div key={item.id} className="px-2 py-0.5">
                    <button
                      onClick={() => onViewChange(item.id)}
                      title={collapsed ? item.label : undefined}
                      className="w-full flex items-center gap-2.5 transition-all duration-150 rounded-lg"
                      style={{
                        padding: collapsed ? "8px 0" : "8px 12px",
                        justifyContent: collapsed ? "center" : "flex-start",
                        color: isActive ? activeColor : mutedColor,
                        background: isActive ? activeBg : "transparent",
                      }}
                    >
                      <div className="relative flex-shrink-0 flex items-center">
                        <Icon size={15} />
                        {showBadge && collapsed && (
                          <span
                            className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full text-[9px] font-bold flex items-center justify-center"
                            style={{ background: "#0E7C86", color: "#fff" }}
                          >
                            {alertCount > 9 ? "9+" : alertCount}
                          </span>
                        )}
                      </div>
                      {!collapsed && (
                        <span className="text-[13px] font-medium flex-1 text-left">{item.label}</span>
                      )}
                      {showBadge && !collapsed && (
                        <span
                          className="ml-auto text-[10px] font-bold w-5 h-5 rounded-full flex items-center justify-center"
                          style={{ background: "#0E7C86", color: "#fff" }}
                        >
                          {alertCount > 9 ? "9+" : alertCount}
                        </span>
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
          );
        })}
      </nav>

      {/* Bottom: AI status + theme toggle + sign out */}
      <div className="flex-shrink-0 p-3 space-y-1.5" style={{ borderTop: `1px solid ${sidebarBorder}` }}>
        {!collapsed && (
          <div className="flex items-center gap-2 px-2 py-2 rounded-md"
            style={{ background: "rgba(46,139,115,0.18)" }}>
            <div className="w-1.5 h-1.5 rounded-full flex-shrink-0 animate-pulse"
              style={{ background: "#2ECC71" }} />
            <div className="min-w-0 flex-1">
              <div className="text-[11px] font-medium leading-none" style={{ color: "#2ECC71" }}>AI Active</div>
              <div className="text-[10px] mt-0.5"
                style={{ color: mutedColor, fontFamily: "'DM Mono', monospace" }}>
                4 cams · YOLOv8
              </div>
            </div>
          </div>
        )}

        <button
          onClick={() => setIsLight((v) => !v)}
          title={isLight ? "Switch to dark mode" : "Switch to light mode"}
          className="w-full flex items-center gap-2 rounded-md py-2 px-2 transition-all duration-150"
          style={{ justifyContent: collapsed ? "center" : "flex-start", color: mutedColor, background: "transparent" }}
          onMouseEnter={(e) => { e.currentTarget.style.background = activeBg; e.currentTarget.style.color = activeColor; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = mutedColor; }}
        >
          {isLight ? <Moon size={14} className="flex-shrink-0" /> : <Sun size={14} className="flex-shrink-0" />}
          {!collapsed && <span className="text-[13px] font-medium">{isLight ? "Dark mode" : "Light mode"}</span>}
        </button>

        <button
          onClick={onLogout}
          title="Sign out"
          className="w-full flex items-center gap-2 rounded-md py-2 px-2 transition-all duration-150"
          style={{ justifyContent: collapsed ? "center" : "flex-start", color: mutedColor }}
          onMouseEnter={(e) => { e.currentTarget.style.color = "#ef4444"; e.currentTarget.style.background = "rgba(239,68,68,0.12)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.color = mutedColor; e.currentTarget.style.background = "transparent"; }}
        >
          <LogOut size={14} className="flex-shrink-0" />
          {!collapsed && <span className="text-[13px] font-medium">Sign out</span>}
        </button>
      </div>
    </aside>
  );
}
