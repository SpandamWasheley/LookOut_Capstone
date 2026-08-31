import { useState } from "react";
import { VIOLATION_TYPES, UNKNOWN_VIOLATION_TYPE, violationChipStyle } from "./violationTypes.js";

// Dev-only visual check for the four violation-type chips against the
// light/dark CSS custom properties in index.css. Not wired into any route —
// see main.jsx for the temporary render used to view this.
function Chip({ type }) {
  const Icon = type.icon;
  return (
    <span
      className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[13px] font-medium border"
      style={violationChipStyle(type)}
    >
      <Icon size={13} style={{ color: `var(--violation-${type.code}-dot)` }} />
      {type.label}
    </span>
  );
}

export function ViolationChipPreview() {
  const [dark, setDark] = useState(false);
  const allTypes = [...Object.values(VIOLATION_TYPES), UNKNOWN_VIOLATION_TYPE];

  return (
    <div className={dark ? "dark" : ""} style={{ minHeight: "100vh", background: "var(--background)" }}>
      <div style={{ padding: 32, fontFamily: "Inter, system-ui, sans-serif" }}>
        <button
          onClick={() => setDark((v) => !v)}
          className="mb-6 px-3 py-1.5 rounded-md text-sm font-medium border"
          style={{ background: "var(--secondary)", color: "var(--foreground)", borderColor: "var(--border)" }}
        >
          Switch to {dark ? "light" : "dark"}
        </button>

        <h2 style={{ color: "var(--foreground)", marginBottom: 16 }}>Chips ({dark ? "dark" : "light"})</h2>
        <div className="flex flex-wrap gap-3 mb-10">
          {allTypes.map((t) => (
            <Chip key={t.code} type={t} />
          ))}
        </div>

        <h2 style={{ color: "var(--foreground)", marginBottom: 16 }}>On a card</h2>
        <div
          className="flex flex-wrap gap-3 p-6 rounded-xl border"
          style={{ background: "var(--card)", borderColor: "var(--border)" }}
        >
          {allTypes.map((t) => (
            <Chip key={t.code} type={t} />
          ))}
        </div>

        <h2 style={{ color: "var(--foreground)", margin: "40px 0 16px" }}>Short labels</h2>
        <div className="flex flex-wrap gap-4">
          {allTypes.map((t) => (
            <div key={t.code} style={{ color: "var(--muted-foreground)", fontSize: 12 }}>
              {t.label} → <strong style={{ color: "var(--foreground)" }}>{t.short}</strong>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
