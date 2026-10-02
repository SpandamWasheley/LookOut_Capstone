import { useEffect, useState } from "react";
import { Loader2, RotateCcw, Save } from "lucide-react";
import { getSettings, saveSettings, resetSpecDefaults } from "./api";

// Adjustable indicator TIMINGS and CONDITIONS (scoring spec v6), one card per violation, each with
// its spec default shown beside it and a "Reset to spec defaults" button. The points per
// indicator and the 55 / 75 status cutoffs are fixed by the spec and are deliberately not here:
// changing them would change what "Possible" and "Likely" mean.
//
// The active values are logged with every alert, so a status can always be traced back to the
// timings that produced it. Detectors pick up a change within a few seconds.

const GROUPS = {
  all: {
    title: "Object confirmation",
    note: "How long an object must be seen before the event starts as Monitoring.",
    fields: [
      { key: "object_confirm_seconds", label: "Object seen for", unit: "seconds", step: 0.5,
        help: "About 2 seconds in the spec. Longer is stricter: fewer brief false detections, slower to appear on the watchlist." },
    ],
  },
  drinking: {
    title: "Drinking timings",
    note: "Indicators: stayed 10+ minutes, group of 2+, evening band.",
    fields: [
      { key: "drinking_group_duration", label: "Stay time that counts as a long stay", unit: "minutes", scale: 60, step: 1,
        help: "A group that has stayed this long earns the long-stay indicator." },
      { key: "drinking_min_group", label: "Minimum group size", unit: "people", step: 1,
        help: "How many people together count as a group." },
      { key: "drinking_start", label: "Evening band starts", type: "time", help: "Time of day when the evening indicator begins." },
      { key: "drinking_end", label: "Evening band ends", type: "time", help: "00:00 means midnight." },
    ],
  },
  smoking: {
    title: "Smoking timings",
    note: "Indicator: repeated puff pattern, and the puff-only path (no cigarette seen).",
    fields: [
      { key: "smoking_puff_count", label: "Puffs needed", unit: "puffs", step: 1,
        help: "Hand-to-mouth movements that make a repeated pattern." },
      { key: "smoking_puff_window_minutes", label: "Within", unit: "minutes", step: 0.5,
        help: "The puffs must happen inside this window." },
    ],
  },
  holdup: {
    title: "Holdup timings",
    note: "Indicators: someone loitering first, and a second person near the knife holder.",
    fields: [
      { key: "holdup_loiter_seconds", label: "Loitering time", unit: "seconds", step: 5,
        help: "How long someone must linger before the loitering indicator counts." },
      { key: "holdup_near_person_heights", label: "Nearby person distance", unit: "holder heights", step: 0.25,
        help: "A second person within this distance of the knife holder is 'nearby'. Measured in the holder's own height, so it works at any camera distance." },
    ],
  },
  ai: {
    title: "AI checker",
    note: "A local vision model describes the scene and suggests a status. It never changes the official status.",
    noReset: true,
    fields: [
      { key: "vlm_enabled", label: "AI checker enabled", type: "bool" },
      { key: "vlm_model", label: "Model (smoking, drinking)", type: "text",
        help: "An Ollama model tag. The 2B model is fast and fits beside detection on the GPU." },
      { key: "vlm_model_holdup", label: "Model (holdup)", type: "text",
        help: "The 4B model reads a holdup correctly where the 2B did not. It is slower and shares the GPU, so a holdup's AI card appears later. Leave blank to use the model above." },
      { key: "vlm_frames", label: "Frames per check", unit: "frames", step: 1, min: 8, max: 12,
        help: "8 to 12, bunched around the moment of detection." },
    ],
  },
};

const timeValue = (v) => (v ? String(v).slice(0, 5) : "");

function display(field, raw) {
  if (raw === undefined || raw === null) return "";
  if (field.type === "time") return timeValue(raw);
  if (field.scale) return raw / field.scale;
  return raw;
}

function parse(field, text) {
  if (field.type === "time" || field.type === "text") return text;
  if (field.type === "bool") return text;
  const n = parseFloat(text);
  if (Number.isNaN(n)) return null;
  return field.scale ? Math.round(n * field.scale) : n;
}

export function IndicatorTimings({ group }) {
  const spec = GROUPS[group];
  const [server, setServer] = useState(null);     // last saved values
  const [form, setForm] = useState({});
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const adopt = (data) => {
    setServer(data);
    setForm(Object.fromEntries(spec.fields.map((f) => [f.key, data[f.key]])));
  };

  useEffect(() => {
    getSettings().then(adopt).catch((e) => setMsg({ error: e.message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group]);

  if (!server) {
    return (
      <div className="flex items-center gap-2 text-[13px] py-3" style={{ color: "var(--muted-foreground)" }}>
        {msg?.error ? msg.error : <><Loader2 size={13} className="animate-spin" /> Loading…</>}
      </div>
    );
  }

  const dirty = spec.fields.some((f) => form[f.key] !== server[f.key]
    && !(f.type === "time" && timeValue(form[f.key]) === timeValue(server[f.key])));
  const defaults = server.spec_defaults ?? {};

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const patch = {};
      spec.fields.forEach((f) => { if (form[f.key] !== server[f.key]) patch[f.key] = form[f.key]; });
      adopt(await saveSettings(patch));
      setMsg({ ok: "Saved. Detectors pick this up within a few seconds." });
    } catch (e) {
      setMsg({ error: e.message });
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true); setMsg(null);
    try {
      adopt(await resetSpecDefaults(group));
      setMsg({ ok: "Restored to the spec defaults." });
    } catch (e) {
      setMsg({ error: e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl p-4 mt-6" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
      <div className="text-[15px] font-semibold" style={{ color: "var(--foreground)" }}>{spec.title}</div>
      <div className="text-[13px] mt-0.5 mb-3" style={{ color: "var(--muted-foreground)" }}>{spec.note}</div>

      <div className="space-y-4">
        {spec.fields.map((f) => {
          const changedFromDefault = defaults[f.key] !== undefined
            && String(form[f.key]).slice(0, 5) !== String(defaults[f.key]).slice(0, 5);
          return (
            <div key={f.key}>
              <div className="flex items-center justify-between gap-3">
                <label className="text-[14px]" style={{ color: "var(--foreground)" }}>{f.label}</label>
                {f.type === "bool" ? (
                  <input type="checkbox" checked={!!form[f.key]}
                    onChange={(e) => setForm({ ...form, [f.key]: e.target.checked })} />
                ) : (
                  <div className="flex items-center gap-1.5">
                    <input
                      type={f.type === "time" ? "time" : f.type === "text" ? "text" : "number"}
                      step={f.step} min={f.min} max={f.max}
                      value={f.type === "text" ? (form[f.key] ?? "") : display(f, form[f.key])}
                      onChange={(e) => {
                        const v = parse(f, e.target.value);
                        if (v !== null) setForm({ ...form, [f.key]: v });
                      }}
                      className={`${f.type === "text" ? "w-44" : "w-24"} px-2 py-1 rounded-lg text-[14px] outline-none`}
                      style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                    />
                    {f.unit && <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{f.unit}</span>}
                  </div>
                )}
              </div>
              {f.help && <div className="text-[12px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{f.help}</div>}
              {defaults[f.key] !== undefined && (
                <div className="text-[12px] mt-0.5" style={{ color: changedFromDefault ? "#f59e0b" : "var(--muted-foreground)" }}>
                  Spec default: {f.type === "time" ? timeValue(defaults[f.key]) : display(f, defaults[f.key])}
                  {f.unit && f.type !== "time" ? ` ${f.unit}` : ""}{changedFromDefault ? " · changed" : ""}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2 mt-4">
        <button onClick={save} disabled={!dirty || busy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
          style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}>
          <Save size={11} /> Save {spec.title.toLowerCase()}
        </button>
        {!spec.noReset && (
          <button onClick={reset} disabled={busy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
            style={{ background: "var(--card)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
            <RotateCcw size={11} /> Reset to spec defaults
          </button>
        )}
        {msg?.ok && <span className="text-[13px]" style={{ color: "#10b981" }}>{msg.ok}</span>}
        {msg?.error && <span className="text-[13px]" style={{ color: "#ef4444" }}>{msg.error}</span>}
      </div>
      {!spec.noReset && (
        <div className="text-[12px] mt-3" style={{ color: "var(--muted-foreground)" }}>
          Points per indicator and the 55 / 75 status cutoffs are fixed by the spec and cannot be changed here.
          The active values are logged with every alert.
        </div>
      )}
    </div>
  );
}
