import { Bell, X, ArrowUpRight } from "lucide-react";

import { violationDisplay } from "./constants/violationTypes";

// The toast itself. Arrival detection, counting and timing all live in
// useAlertToaster — this only draws whatever it is handed.
export function AlertToast({ toast, onOpen, onDismiss }) {
  if (!toast) return null;

  const many = toast.count > 1;
  const vcfg = many ? null : violationDisplay(toast.first.type);
  const Icon = many ? Bell : vcfg.icon;
  const accent = many ? "#ef4444" : vcfg.color;

  return (
    <div
      // Above the dropdowns and the page, clear of the window edge. Fixed to
      // the viewport, so it holds the same corner whatever page is open.
      className="fixed bottom-5 right-5 z-[100] w-80 rounded-xl overflow-hidden shadow-2xl"
      style={{ background: "var(--card)", border: `1px solid ${accent}66` }}
      role="status"
      aria-live="polite"
    >
      <div
        className="flex items-start gap-3 p-4"
        style={{ background: many ? "rgba(239,68,68,0.08)" : "transparent" }}
      >
        <span
          className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ background: many ? "rgba(239,68,68,0.15)" : vcfg.bg }}
        >
          <Icon size={15} style={{ color: accent }} />
        </span>

        <button onClick={onOpen} className="min-w-0 flex-1 text-left">
          <span className="block text-sm font-semibold truncate" style={{ color: "var(--foreground)" }}>
            {many ? `${toast.count} New Alerts` : vcfg.label}
          </span>
          <span className="block text-[12px] mt-0.5 truncate" style={{ color: "var(--muted-foreground)" }}>
            {many
              ? "Waiting to be reviewed"
              : `${toast.first.id} · ${toast.first.cameraZone || toast.first.camera || "Unknown camera"}`}
          </span>
          <span className="flex items-center gap-1 text-[12px] font-medium mt-1.5" style={{ color: "var(--primary)" }}>
            Review <ArrowUpRight size={12} />
          </span>
        </button>

        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          className="p-1 rounded-md flex-shrink-0"
          style={{ color: "var(--muted-foreground)" }}
        >
          <X size={13} />
        </button>
      </div>
    </div>
  );
}
