import { useEffect, useRef, useState } from "react";
import { Filter, ChevronDown } from "lucide-react";
import { VIOLATION_TYPES } from "./constants/violationTypes";

const ALL_TYPES = Object.values(VIOLATION_TYPES);

// Selecting every filterable type is identical to selecting none — it's a
// redundant, confusing state — so the last one is disabled with a tooltip
// explaining why, rather than silently doing nothing or letting the count
// climb to "all". Derived from VIOLATION_TYPES so a 5th type later doesn't
// need this number touched by hand.
const MAX_SELECTABLE = ALL_TYPES.length - 1;

// Shared by the Violations tab (AlertFeed) and the Violator Log — one
// component so the two can't drift back into different filter UIs/rules.
// `selected` is a Set of VIOLATION_TYPES codes; `onToggle`/`onClear` mutate
// whatever the caller backs the selection with (local state or URL params).
export function TypeFilterDropdown({ selected, onToggle, onClear }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  return (
    <div className="relative flex-shrink-0" ref={rootRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium transition-all"
        style={{
          background: selected.size > 0 ? "var(--primary)" : "var(--secondary)",
          color: selected.size > 0 ? "var(--primary-foreground)" : "var(--muted-foreground)",
          border: `1px solid ${selected.size > 0 ? "var(--primary)" : "var(--border)"}`,
        }}
      >
        <Filter size={11} /> Type
        {selected.size > 0 && (
          <span className="w-4 h-4 flex items-center justify-center rounded-full text-[10px] font-semibold"
            style={{ background: "var(--primary-foreground)", color: "var(--primary)" }}>
            {selected.size}
          </span>
        )}
        <ChevronDown size={11} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s" }} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full mt-2 w-fit rounded-xl overflow-hidden shadow-2xl z-50 py-1"
            style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
            {ALL_TYPES.map((t) => {
              const checked = selected.has(t.code);
              const disabled = !checked && selected.size >= MAX_SELECTABLE;
              const TypeIcon = t.icon;
              const color = `var(--violation-${t.code}-dot)`;
              return (
                <label
                  key={t.code}
                  className="flex items-center gap-2.5 px-3.5 py-1 transition-colors"
                  style={{ cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.4 : 1 }}
                  title={disabled
                    ? `Selecting all ${ALL_TYPES.length} types is the same as no filter — leave at least one unselected.`
                    : undefined}
                  onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.background = "var(--secondary)"; }}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={() => onToggle(t.code)}
                    className="w-3.5 h-3.5 rounded"
                    style={{ accentColor: color }}
                  />
                  <TypeIcon size={13} style={{ color }} />
                  <span className="text-[13px] font-medium" style={{ color: "var(--foreground)" }}>
                    {t.short}
                  </span>
                </label>
              );
            })}
            <button
              onClick={onClear}
              disabled={selected.size === 0}
              className="w-full text-left px-3.5 py-1 text-[12px] font-medium transition-colors disabled:opacity-40 disabled:cursor-default"
              style={{ color: "var(--primary)", borderTop: "1px solid var(--border)" }}
            >
              Clear all
            </button>
          </div>
        </>
      )}
    </div>
  );
}
