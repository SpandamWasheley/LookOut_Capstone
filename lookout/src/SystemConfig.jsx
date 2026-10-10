import { useEffect, useState } from "react";
import { Save, RotateCcw, Car, Cigarette, Siren, Beer, AlertTriangle, Loader2, SlidersHorizontal } from "lucide-react";
import { getSettings, saveSettings } from "./api";
import { readTestingTools, writeTestingTools } from "./useTestingTools";

// Settings. One form for the whole page and ONE save button (top right). Every panel is two
// columns on a wide screen: "Detection" on the left, "Timings" on the right; they stack on a
// narrow one. Each card has its own "Reset to default", which puts that card's fields back to
// their default values in the form (nothing is saved until "Save changes").
//
// Wording is for barangay staff: plain language, no jargon, no scoring terms.

const sections = [
  { id: "parking", label: "Parking", icon: Car,    color: "#ef4444" },
  { id: "smoking", label: "Smoking", icon: Cigarette, color: "#f97316" },
  { id: "thief",   label: "Holdup",   icon: Siren,  color: "#dc2626" },
  { id: "drinking", label: "Drinking", icon: Beer, color: "#8b5cf6" },
  { id: "system", label: "System", icon: SlidersHorizontal, color: "#3b82f6" },
];

// Defaults for fields the server does not publish a default for. Timings come from the server
// (`spec_defaults`) so the page and the detectors can never disagree about them.
const STATIC_DEFAULTS = {
  parking_enabled: true, parking_confidence: 35, parking_dwell: 60, parking_move_tolerance: 40,
  smoking_enabled: true, smoking_confidence: 30,
  thief_enabled: true, thief_confidence: 30,
  drinking_enabled: true, drinking_confidence: 35,
  drinking_mouth_proximity: 3.0, drinking_cooldown_center_dist: 1.5,
  alert_cooldown: 120, evidence_retention_days: 30, evidence_auto_purge: false, show_testing_tools: false, auto_start_detection: false,
  vlm_enabled: true, vlm_model: "qwen3-vl:2b-instruct", vlm_model_holdup: "qwen3-vl:4b-instruct", vlm_frames: 8,
};

const FIELD_KEYS = [
  ...Object.keys(STATIC_DEFAULTS),
  "object_confirm_seconds", "cue_hold_seconds", "monitoring_min_seconds", "drinking_group_duration", "drinking_min_group", "drinking_start", "drinking_end",
  "smoking_puff_count", "smoking_puff_window_minutes", "holdup_loiter_seconds", "holdup_near_person_heights",
];

const hhmm = (t) => (t ? String(t).slice(0, 5) : t);

function fromApi(s) {
  const f = {};
  FIELD_KEYS.forEach((k) => { f[k] = s[k]; });
  f.drinking_start = hhmm(f.drinking_start);
  f.drinking_end = hhmm(f.drinking_end);
  return f;
}

function defaultsFrom(s) {
  const d = { ...STATIC_DEFAULTS, ...(s?.spec_defaults ?? {}) };
  d.drinking_start = hhmm(d.drinking_start);
  d.drinking_end = hhmm(d.drinking_end);
  return d;
}

// ── Plain-language durations: "25 sec", "10 min", "2.5 min" — never 0.4166666 ──
const trimNum = (n) => String(Math.round(n * 10) / 10);

// ── Small controls ───────────────────────────────────────────────────────────
function Slider({ label, value, min, max, step = 1, unit, desc, onChange, differs }) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="space-y-2" style={differs ? { borderLeft: "2px solid #f59e0b88", paddingLeft: 10 } : undefined}>
      <div className="flex justify-between items-center">
        <span className="text-[14px] font-medium" style={{ color: "var(--foreground)" }}>{label}</span>
        <span className="text-xs font-semibold px-2 py-0.5 rounded-md"
          style={{ color: "var(--primary)", background: "var(--secondary)", fontFamily: "'DM Mono', monospace" }}>
          {value}{unit}
        </span>
      </div>
      <div className="relative h-5 flex items-center">
        <div className="absolute inset-x-0 h-1.5 rounded-full" style={{ background: "var(--secondary)" }} />
        <div className="absolute left-0 h-1.5 rounded-full transition-all"
          style={{ width: `${pct}%`, background: "var(--primary)" }} />
        <input
          type="range" min={min} max={max} step={step} value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="absolute inset-0 w-full opacity-0 cursor-pointer"
          style={{ height: "100%" }}
        />
        <div
          className="absolute w-5 h-5 rounded-full pointer-events-none transition-all"
          style={{
            left: `calc(${pct}% - ${pct * 0.2}px)`,
            background: "var(--card)",
            border: "2px solid var(--primary)",
            boxShadow: "0 1px 4px rgba(0,0,0,0.18)",
          }}
        />
      </div>
      {desc && <p className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{desc}</p>}
    </div>
  );
}

function Toggle({ label, desc, value, onChange, differs }) {
  return (
    <div className="flex items-center justify-between py-2.5 gap-3"
      style={differs ? { borderLeft: "2px solid #f59e0b88", paddingLeft: 10 } : undefined}>
      <div>
        <div className="text-[15px] font-medium" style={{ color: "var(--foreground)" }}>{label}</div>
        <div className="text-[13px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{desc}</div>
      </div>
      <button
        onClick={() => onChange(!value)}
        className="relative w-9 h-5 rounded-full transition-all duration-200 flex-shrink-0"
        style={{ background: value ? "var(--primary)" : "var(--secondary)", border: "1px solid var(--border)" }}
      >
        <div
          className="absolute top-0.5 w-3.5 h-3.5 rounded-full transition-all duration-200"
          style={{ background: value ? "#0c0f16" : "var(--muted-foreground)", left: value ? "calc(100% - 16px)" : "2px" }}
        />
      </button>
    </div>
  );
}

const inputStyle = { background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" };

// A labelled row: label + description on the left, the control on the right.
function Row({ label, desc, differs, children }) {
  return (
    <div className="flex items-start justify-between gap-4"
      style={differs ? { borderLeft: "2px solid #f59e0b88", paddingLeft: 10 } : undefined}>
      <div className="min-w-0">
        <div className="text-[14px] font-medium" style={{ color: "var(--foreground)" }}>{label}</div>
        {desc && <div className="text-[13px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{desc}</div>}
      </div>
      <div className="flex items-center gap-1.5 flex-shrink-0">{children}</div>
    </div>
  );
}

function NumberBox({ value, onChange, min, max, step = 1, unit, width = "w-20" }) {
  return (
    <>
      <input type="number" value={value ?? ""} min={min} max={max} step={step}
        onChange={(e) => { const n = parseFloat(e.target.value); if (!Number.isNaN(n)) onChange(n); }}
        className={`${width} px-2 py-1 rounded-lg text-[14px] outline-none`} style={inputStyle} />
      {unit && <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{unit}</span>}
    </>
  );
}

// A duration the user can type in seconds or minutes. The value is held in SECONDS by the caller.
function DurationBox({ seconds, onChange, version }) {
  const [unit, setUnit] = useState(seconds < 60 ? "sec" : "min");
  const shown = unit === "sec" ? trimNum(seconds) : trimNum(seconds / 60);
  return (
    <>
      <input key={`${version}-${unit}`} type="number" min={0} step={unit === "sec" ? 1 : 0.5} defaultValue={shown}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          if (!Number.isNaN(n) && n > 0) onChange(unit === "sec" ? n : n * 60);
        }}
        className="w-20 px-2 py-1 rounded-lg text-[14px] outline-none" style={inputStyle} />
      <select value={unit} onChange={(e) => setUnit(e.target.value)}
        className="px-1.5 py-1 rounded-lg text-[13px] outline-none" style={inputStyle}>
        <option value="sec">sec</option>
        <option value="min">min</option>
      </select>
    </>
  );
}

function TimeBox({ value, onChange }) {
  return (
    <input type="time" value={value ?? ""} onChange={(e) => onChange(e.target.value)}
      className="w-36 px-2 py-1 rounded-lg text-[14px] outline-none" style={inputStyle} />
  );
}

function Card({ title, note, onReset, children }) {
  return (
    <div className="rounded-xl p-4 min-w-0" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="text-[15px] font-semibold" style={{ color: "var(--foreground)" }}>{title}</div>
          {note && <div className="text-[13px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{note}</div>}
        </div>
        {onReset && (
          <button onClick={onReset}
            className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium flex-shrink-0"
            style={{ background: "var(--card)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
            <RotateCcw size={10} /> Reset to default
          </button>
        )}
      </div>
      <div className="space-y-4">{children}</div>
    </div>
  );
}

const TIMINGS_FOOTER = "Changes are saved with each alert for record-keeping.";

function InfoBox({ color, children }) {
  return (
    <div className="rounded-xl p-4 text-[13px] leading-relaxed"
      style={{ background: `${color}0f`, border: `1px solid ${color}26`, color: "var(--muted-foreground)" }}>
      {children}
    </div>
  );
}

const TwoCols = ({ left, right, below }) => (
  <div className="space-y-4">
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-start">{left}{right}</div>
    {below}
  </div>
);

// ── The page ─────────────────────────────────────────────────────────────────
export function SystemConfig() {
  const [active, setActive] = useState("parking");
  const [saving, setSaving] = useState(false);
  const [testingTools, setTestingTools] = useState(readTestingTools);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [pendingAction, setPendingAction] = useState(null); // null | "reset" | "save"
  const [form, setForm] = useState(null);
  const [server, setServer] = useState(null);       // last saved values
  const [defaults, setDefaults] = useState(STATIC_DEFAULTS);
  const [version, setVersion] = useState(0);        // bumped to re-sync uncontrolled inputs

  const adopt = (data) => {
    const f = fromApi(data);
    setForm(f);
    setServer(f);
    setDefaults(defaultsFrom(data));
    setVersion((v) => v + 1);
  };

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      adopt(await getSettings());
    } catch (err) {
      setLoadError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const changedKeys = form && server ? FIELD_KEYS.filter((k) => form[k] !== server[k]) : [];
  const isDirty = changedKeys.length > 0;

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const differs = (key) => form && defaults[key] !== undefined && form[key] !== defaults[key];
  const resetKeys = (keys) => {
    setForm((f) => ({ ...f, ...Object.fromEntries(keys.map((k) => [k, defaults[k]])) }));
    setVersion((v) => v + 1);
  };

  const save = async () => {
    setSaving(true);
    try {
      const patch = {};
      changedKeys.forEach((k) => { patch[k] = form[k]; });
      adopt(await saveSettings(patch));
      window.dispatchEvent(new Event("lookout:settings-saved"));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      alert(`Failed to save settings: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const detection = (prefix, enabledLabel, enabledDesc, confDesc, extra) => (
    <Card title="Detection" onReset={() => resetKeys([`${prefix}_enabled`, `${prefix}_confidence`, ...(extra?.keys ?? [])])}>
      <Toggle label={enabledLabel} desc={enabledDesc} value={form[`${prefix}_enabled`]}
        differs={differs(`${prefix}_enabled`)} onChange={(v) => set(`${prefix}_enabled`, v)} />
      <Slider label="Detection confidence" value={form[`${prefix}_confidence`]} min={10} max={90} unit="%"
        differs={differs(`${prefix}_confidence`)} desc={confDesc}
        onChange={(v) => set(`${prefix}_confidence`, v)} />
      {extra?.node}
    </Card>
  );

  const content = form && {
    parking: (
      <TwoCols
        left={detection("parking", "Illegal parking detection",
          "Flag vehicles parked or blocking the road for too long.",
          "How sure the system must be before it treats something as a vehicle. Lower catches more but makes more mistakes; higher is stricter.")}
        right={
          <Card title="Timings" onReset={() => resetKeys(["parking_dwell", "parking_move_tolerance"])}>
            <Row label="Time before it counts as parked"
              desc="How long a vehicle must stay put to count as parked. A car just driving through never reaches it."
              differs={differs("parking_dwell")}>
              <DurationBox seconds={form.parking_dwell} version={version} onChange={(s) => set("parking_dwell", Math.round(s))} />
            </Row>
            <Row label="Movement allowed while parked"
              desc="How far a vehicle may shift and still count as standing still."
              differs={differs("parking_move_tolerance")}>
              <NumberBox value={form.parking_move_tolerance} min={10} max={120} step={5} unit="px"
                onChange={(v) => set("parking_move_tolerance", Math.round(v))} />
            </Row>
          </Card>
        }
        below={<InfoBox color="#ef4444">
          The system watches for cars, motorcycles, buses and trucks (tricycles count as motorcycles).
          A vehicle that stays put beyond the set time is flagged as obstructing the road.
        </InfoBox>}
      />
    ),
    smoking: (
      <TwoCols
        left={detection("smoking", "Smoking detection", "Watch for people smoking in public.",
          "How sure the system must be before it reports a cigarette. Lower catches more at a distance; higher means fewer false alarms.")}
        right={
          <Card title="Timings" onReset={() => resetKeys(["smoking_puff_count", "smoking_puff_window_minutes"])}>
            <Row label="Repeated smoking motion"
              desc="How many hand-to-mouth motions, and in how much time, count as a repeated pattern."
              differs={differs("smoking_puff_count") || differs("smoking_puff_window_minutes")}>
              <NumberBox value={form.smoking_puff_count} min={2} max={10} step={1} unit="motions in" width="w-16"
                onChange={(v) => set("smoking_puff_count", Math.round(v))} />
              <DurationBox seconds={form.smoking_puff_window_minutes * 60} version={version}
                onChange={(s) => set("smoking_puff_window_minutes", Math.round((s / 60) * 100) / 100)} />
            </Row>
            <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{TIMINGS_FOOTER}</div>
          </Card>
        }
        below={<InfoBox color="#f97316">
          The system watches for a cigarette in someone&rsquo;s hand. A cigarette seen briefly is flagged for
          monitoring. It&rsquo;s raised further when the person is also seen bringing it to their mouth, again and again.
        </InfoBox>}
      />
    ),
    thief: (
      <TwoCols
        left={detection("thief", "Holdup detection", "Watch for a person holding a knife.",
          "How sure the system must be before it reports a knife. Lower catches more but makes more mistakes; higher is stricter.")}
        right={
          <Card title="Timings" onReset={() => resetKeys(["holdup_loiter_seconds", "holdup_near_person_heights"])}>
            <Row label="Lingering before the incident"
              desc="How long someone must hang around nearby before it's considered suspicious."
              differs={differs("holdup_loiter_seconds")}>
              <DurationBox seconds={form.holdup_loiter_seconds} version={version}
                onChange={(s) => set("holdup_loiter_seconds", Math.round(s))} />
            </Row>
            <Row label="How close the second person must be"
              desc="How near another person must be to the person holding the knife."
              differs={differs("holdup_near_person_heights")}>
              <NumberBox value={form.holdup_near_person_heights} min={0.5} max={4} step={0.25} unit="body heights"
                onChange={(v) => set("holdup_near_person_heights", v)} />
            </Row>
            <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{TIMINGS_FOOTER}</div>
          </Card>
        }
        below={<InfoBox color="#dc2626">
          A knife held briefly is flagged for monitoring. It&rsquo;s raised further when another person is close by.
        </InfoBox>}
      />
    ),
    drinking: (
      <TwoCols
        left={detection("drinking", "Drinking detection", "Watch for people drinking in public.",
          "How sure the system must be before it reports a bottle. Lower catches more but makes more mistakes; higher is stricter.",
          {
            keys: ["drinking_mouth_proximity", "drinking_cooldown_center_dist"],
            node: (
              <>
                <Slider label="Bottle-to-mouth distance" value={form.drinking_mouth_proximity} min={1.5} max={5} step={0.25}
                  unit=" face widths" differs={differs("drinking_mouth_proximity")}
                  desc="How close a bottle must be to someone's mouth to count as raised to drink."
                  onChange={(v) => set("drinking_mouth_proximity", v)} />
                <Slider label="Same-spot radius" value={form.drinking_cooldown_center_dist} min={0.5} max={3} step={0.25}
                  unit="x" differs={differs("drinking_cooldown_center_dist")}
                  desc="How near a new alert must be to a recent one to be treated as the same spot and held back."
                  onChange={(v) => set("drinking_cooldown_center_dist", v)} />
              </>
            ),
          })}
        right={
          <Card title="Timings"
            onReset={() => resetKeys(["drinking_group_duration", "drinking_min_group", "drinking_start", "drinking_end"])}>
            <Row label="Stay time that counts as a long stay"
              desc="How long a group must stay together to be treated as a long gathering."
              differs={differs("drinking_group_duration")}>
              <DurationBox seconds={form.drinking_group_duration} version={version}
                onChange={(s) => set("drinking_group_duration", Math.round(s))} />
            </Row>
            <Row label="Minimum group size" desc="How many people together count as a group."
              differs={differs("drinking_min_group")}>
              <NumberBox value={form.drinking_min_group} min={2} max={20} step={1} unit="people"
                onChange={(v) => set("drinking_min_group", Math.round(v))} />
            </Row>
            <Row label="Evening hours" desc="The hours when drinking in public is more likely."
              differs={differs("drinking_start") || differs("drinking_end")}>
              <TimeBox value={form.drinking_start} onChange={(v) => set("drinking_start", v)} />
              <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>to</span>
              <TimeBox value={form.drinking_end} onChange={(v) => set("drinking_end", v)} />
            </Row>
            <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{TIMINGS_FOOTER}</div>
          </Card>
        }
        below={<InfoBox color="#8b5cf6">
          The system watches for bottles. A bottle seen briefly is flagged for monitoring. It&rsquo;s raised further
          when people stay together as a group, stay at the spot for a long time, or raise the bottle to their mouth.
        </InfoBox>}
      />
    ),
    system: (
      <TwoCols
        left={
          <Card title="Alerts and records" onReset={() => resetKeys(["alert_cooldown", "evidence_retention_days", "evidence_auto_purge", "auto_start_detection"])}>
            <Slider label="Alert cooldown" value={form.alert_cooldown} min={30} max={600} unit=" sec"
              differs={differs("alert_cooldown")}
              desc="How long the system waits before reporting the same spot again."
              onChange={(v) => set("alert_cooldown", v)} />
            <Slider label="Evidence retention" value={form.evidence_retention_days} min={7} max={90} unit=" days"
              differs={differs("evidence_retention_days")}
              desc="How long alert images and clips are kept before they may be deleted (RA 10173 storage limitation). The alert records themselves are always kept."
              onChange={(v) => set("evidence_retention_days", v)} />
            <Toggle label="Delete old evidence automatically"
              desc="Off by default. When on, evidence older than the retention period is deleted on a schedule. Nothing is deleted while this is off."
              value={form.evidence_auto_purge} differs={differs("evidence_auto_purge")}
              onChange={(v) => set("evidence_auto_purge", v)} />
            <Toggle label="Start detection automatically when LookOut starts"
              desc="Off by default. When on, live monitoring of the camera starts by itself a few seconds after LookOut starts. You can still stop it from Live Feeds."
              value={form.auto_start_detection} differs={differs("auto_start_detection")}
              onChange={(v) => set("auto_start_detection", v)} />
            <Toggle label="Show testing tools"
              desc="Shows Run Detection, and Upload Video and History on Live Feeds, for trying the system on recorded footage. Leave off for normal use."
              value={testingTools} differs={testingTools}
              onChange={(v) => { writeTestingTools(v); setTestingTools(v); }} />
          </Card>
        }
        right={
          <div className="space-y-4">
            <Card title="Event confirmation" onReset={() => resetKeys(["object_confirm_seconds", "cue_hold_seconds", "monitoring_min_seconds"])}>
              <Row label="How long an object must be seen"
                desc="How long a bottle, cigarette or knife must stay in view before the system starts watching it."
                differs={differs("object_confirm_seconds")}>
                <DurationBox seconds={form.object_confirm_seconds} version={version}
                  onChange={(s) => set("object_confirm_seconds", Math.round(s * 2) / 2)} />
              </Row>
              <Row label="How long a behaviour still counts"
                desc="How long something like a hand raised to the mouth still counts after it was last seen. Keeps an alert steady instead of flickering."
                differs={differs("cue_hold_seconds")}>
                <DurationBox seconds={form.cue_hold_seconds} version={version}
                  onChange={(s) => set("cue_hold_seconds", Math.round(s * 2) / 2)} />
              </Row>
              <Row label="Shortest event worth keeping"
                desc="A Monitoring event shorter than this that never grew is dropped. Filters out vehicles and people passing through."
                differs={differs("monitoring_min_seconds")}>
                <DurationBox seconds={form.monitoring_min_seconds} version={version}
                  onChange={(s) => set("monitoring_min_seconds", Math.round(s * 2) / 2)} />
              </Row>
              <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{TIMINGS_FOOTER}</div>
            </Card>
            <Card title="AI checker"
              note="A local AI model looks at a short clip and describes the scene. It only adds context and a suggestion; it never changes an alert's status."
              onReset={() => resetKeys(["vlm_enabled", "vlm_model", "vlm_model_holdup", "vlm_frames"])}>
              <Toggle label="AI checker" desc="Everything stays on this computer. Nothing is sent anywhere."
                value={form.vlm_enabled} differs={differs("vlm_enabled")} onChange={(v) => set("vlm_enabled", v)} />
              <Row label="Model for smoking and drinking" desc="A smaller model is faster and fits alongside detection."
                differs={differs("vlm_model")}>
                <input type="text" value={form.vlm_model} onChange={(e) => set("vlm_model", e.target.value)}
                  className="w-48 px-2 py-1 rounded-lg text-[14px] outline-none" style={inputStyle} />
              </Row>
              <Row label="Model for holdup"
                desc="A larger model for holdups, where reading the scene correctly matters most. It is slower, so the AI note for a holdup appears a little later. Leave blank to use the model above."
                differs={differs("vlm_model_holdup")}>
                <input type="text" value={form.vlm_model_holdup} onChange={(e) => set("vlm_model_holdup", e.target.value)}
                  className="w-48 px-2 py-1 rounded-lg text-[14px] outline-none" style={inputStyle} />
              </Row>
              <Row label="Frames per check"
                desc="How many video frames the AI looks at each time. More frames give it more to go on but take longer."
                differs={differs("vlm_frames")}>
                <NumberBox value={form.vlm_frames} min={8} max={12} step={1} unit="frames"
                  onChange={(v) => set("vlm_frames", Math.min(12, Math.max(8, Math.round(v))))} />
              </Row>
            </Card>
          </div>
        }
      />
    ),
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Settings</h1>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>Admin only · changes apply within a few seconds</span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setPendingAction("reset")}
            disabled={loading || saving || !isDirty}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}
          >
            <RotateCcw size={11} /> Discard changes
          </button>
          <button
            onClick={() => setPendingAction("save")}
            disabled={loading || saving || !isDirty}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-50"
            style={{ background: saved ? "#10b981" : "var(--primary)", color: saved ? "#fff" : "var(--primary-foreground)" }}
          >
            <Save size={11} /> {saved ? "Saved!" : saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Section nav */}
        <div className="w-44 flex-shrink-0 p-3 space-y-0.5 overflow-y-auto"
          style={{ borderRight: "1px solid var(--border)" }}>
          {sections.map((s) => {
            const Icon = s.icon;
            const isActive = active === s.id;
            return (
              <button
                key={s.id}
                onClick={() => setActive(s.id)}
                className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left transition-all text-[15px] font-medium"
                style={{
                  background: isActive ? `${s.color}12` : "transparent",
                  color: isActive ? s.color : "var(--muted-foreground)",
                  border: `1px solid ${isActive ? s.color + "20" : "transparent"}`,
                }}
              >
                <Icon size={14} /> {s.label}
              </button>
            );
          })}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">
          <div className="max-w-5xl">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-16 gap-2">
                <Loader2 size={24} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
                <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Loading settings…</div>
              </div>
            ) : loadError ? (
              <div className="flex flex-col items-center justify-center py-16 gap-2">
                <AlertTriangle size={24} style={{ color: "#ef4444" }} />
                <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Failed to load settings</div>
                <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
              </div>
            ) : (
              content?.[active]
            )}
          </div>
        </div>
      </div>

      {pendingAction && (() => {
        const config = pendingAction === "reset"
          ? {
              iconColor: "#ef4444", iconBg: "rgba(239,68,68,0.12)", Icon: RotateCcw,
              title: "Discard unsaved changes?",
              message: "This reloads the last saved settings — any edits you haven't saved will be lost.",
              confirmLabel: "Yes, discard", confirmColor: "#ef4444",
              run: load,
            }
          : {
              iconColor: "#10b981", iconBg: "rgba(16,185,129,0.12)", Icon: Save,
              title: "Save these settings?",
              message: "Changes apply within a few seconds — the detectors will use these values right away.",
              confirmLabel: "Yes, save", confirmColor: "#10b981",
              run: save,
            };
        const Icon = config.Icon;
        return (
          <div
            className="fixed inset-0 z-[80] flex items-center justify-center p-4"
            style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
            onClick={() => setPendingAction(null)}
          >
            <div
              className="w-full max-w-xs rounded-2xl overflow-hidden shadow-2xl"
              style={{ background: "var(--card)", border: "1px solid var(--border)" }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="px-5 pt-5 pb-4 flex flex-col items-center text-center gap-3">
                <div className="w-10 h-10 rounded-full flex items-center justify-center"
                  style={{ background: config.iconBg }}>
                  <Icon size={18} style={{ color: config.iconColor }} />
                </div>
                <div>
                  <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>{config.title}</div>
                  <div className="text-[14px] mt-1" style={{ color: "var(--muted-foreground)" }}>{config.message}</div>
                </div>
              </div>
              <div className="flex items-center gap-2 px-5 pb-5">
                <button onClick={() => setPendingAction(null)}
                  className="flex-1 px-4 py-2 rounded-xl text-sm font-medium"
                  style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                  Cancel
                </button>
                <button onClick={() => { setPendingAction(null); config.run(); }}
                  className="flex-1 flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl text-sm font-medium"
                  style={{ background: config.confirmColor, color: "#fff" }}>
                  <Icon size={13} /> {config.confirmLabel}
                </button>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
