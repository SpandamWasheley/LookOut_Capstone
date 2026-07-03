import { useEffect, useRef, useState } from "react";
import { Save, RotateCcw, Moon, Volume2, Trash2, Clock, AlertTriangle, Loader2 } from "lucide-react";
import { getSettings, saveSettings } from "./api";

const trimSeconds = (t) => (t ? t.slice(0, 5) : t);

// Map noise dB ↔ sensitivity index (0=Very Low … 4=Very High)
const SENSITIVITY_DB = [80, 70, 65, 55, 45];
const dbToSensitivity = (db) => {
  let best = 0, bestDiff = Infinity;
  SENSITIVITY_DB.forEach((v, i) => { const d = Math.abs(v - db); if (d < bestDiff) { bestDiff = d; best = i; } });
  return best;
};

function fromApi(s) {
  return {
    curfewStart: trimSeconds(s.curfew_start),
    curfewEnd: trimSeconds(s.curfew_end),
    curfewAge: s.curfew_age,
    curfewConf: s.curfew_confidence,
    curfewDwell: s.curfew_dwell,
    guardianCheck: s.guardian_check,
    unknownAlert: s.unknown_alert,
    noiseEnabled: s.noise_enabled,
    noiseDb: s.noise_threshold_db,
    noiseSensitivity: dbToSensitivity(s.noise_threshold_db),
    noiseDur: s.noise_duration,
    wasteEnabled: s.waste_enabled,
    wasteConf: s.waste_confidence,
    wasteDwell: s.waste_dwell,
    wasteCollectionStart: trimSeconds(s.waste_collection_start),
    wasteCollectionEnd: trimSeconds(s.waste_collection_end),
    cooldown: s.alert_cooldown,
    retention: s.evidence_retention_days,
    autoDispatch: s.auto_dispatch,
    emailAlerts: s.email_alerts,
    smsAlerts: s.sms_alerts,
  };
}

function toApi(f) {
  return {
    curfew_start: f.curfewStart,
    curfew_end: f.curfewEnd,
    curfew_age: f.curfewAge,
    curfew_confidence: f.curfewConf,
    curfew_dwell: f.curfewDwell,
    guardian_check: f.guardianCheck,
    unknown_alert: f.unknownAlert,
    noise_enabled: f.noiseEnabled,
    noise_threshold_db: SENSITIVITY_DB[f.noiseSensitivity],
    noise_duration: f.noiseDur,
    waste_enabled: f.wasteEnabled,
    waste_confidence: f.wasteConf,
    waste_dwell: f.wasteDwell,
    waste_collection_start: f.wasteCollectionStart,
    waste_collection_end: f.wasteCollectionEnd,
    alert_cooldown: f.cooldown,
    evidence_retention_days: f.retention,
    auto_dispatch: f.autoDispatch,
    email_alerts: f.emailAlerts,
    sms_alerts: f.smsAlerts,
  };
}

const sections = [
  { id: "curfew", label: "Curfew", icon: Moon,    color: "#f59e0b" },
  { id: "noise",  label: "Noise",  icon: Volume2, color: "#a78bfa" },
  { id: "waste",  label: "Waste",  icon: Trash2,  color: "#84cc16" },
  // { id: "system", label: "System", icon: Clock,   color: "#3b82f6" },
];

// ── Slider ────────────────────────────────────────────────────────────────────
function Slider({ label, value, min, max, unit, desc, onChange }) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="space-y-2.5">
      <div className="flex justify-between items-center">
        <span className="text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>{label}</span>
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
          type="range" min={min} max={max} value={value}
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
      {desc && <p className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{desc}</p>}
    </div>
  );
}

// ── SensitivitySelector ───────────────────────────────────────────────────────
const SENSITIVITY_LEVELS = ["Very Low", "Low", "Medium", "High", "Very High"];

function SensitivitySelector({ label, value, onChange }) {
  return (
    <div className="space-y-2.5">
      <div className="flex justify-between items-center">
        <span className="text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>{label}</span>
        <span className="text-xs font-semibold px-2 py-0.5 rounded-md"
          style={{ color: "var(--primary)", background: "var(--secondary)" }}>
          {SENSITIVITY_LEVELS[value]}
        </span>
      </div>
      <div className="flex gap-1.5">
        {SENSITIVITY_LEVELS.map((lvl, i) => (
          <button
            key={lvl}
            onClick={() => onChange(i)}
            className="flex-1 py-2 rounded-lg text-[11px] font-medium transition-all"
            style={{
              background: value === i ? "var(--primary)" : "var(--secondary)",
              color: value === i ? "var(--primary-foreground)" : "var(--muted-foreground)",
              border: `1px solid ${value === i ? "var(--primary)" : "var(--border)"}`,
            }}
          >
            {lvl}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Toggle ────────────────────────────────────────────────────────────────────
function Toggle({ label, desc, value, onChange }) {
  return (
    <div className="flex items-center justify-between py-3" style={{ borderBottom: "1px solid var(--border)" }}>
      <div>
        <div className="text-[13px] font-medium" style={{ color: "var(--foreground)" }}>{label}</div>
        <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{desc}</div>
      </div>
      <button
        onClick={() => onChange(!value)}
        className="relative w-9 h-5 rounded-full transition-all duration-200 flex-shrink-0 ml-4"
        style={{ background: value ? "var(--primary)" : "var(--secondary)", border: "1px solid var(--border)" }}
      >
        <div
          className="absolute top-0.5 w-4 h-4 rounded-full transition-all duration-200"
          style={{ background: value ? "#0c0f16" : "var(--muted-foreground)", left: value ? "calc(100% - 18px)" : "2px" }}
        />
      </button>
    </div>
  );
}

// ── TimeInput ─────────────────────────────────────────────────────────────────
function TimeInput({ label, value, onChange }) {
  const inputRef = useRef(null);
  const [focused, setFocused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const active = focused || hovered;

  const openPicker = () => {
    const el = inputRef.current;
    if (!el) return;
    if (typeof el.showPicker === "function") el.showPicker();
    else el.focus();
  };

  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>{label}</label>
      <div
        onClick={openPicker}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="flex items-center gap-2 w-full px-3 py-2 rounded-lg cursor-pointer transition-all"
        style={{
          background: active ? "var(--secondary)" : "var(--secondary)",
          border: `1px solid ${active ? "var(--primary)" : "var(--border)"}`,
          boxShadow: focused ? "0 0 0 3px rgba(var(--primary-rgb, 11 84 113) / 0.15)" : "none",
        }}
      >
        <Clock size={14} className="flex-shrink-0 transition-colors"
          style={{ color: active ? "var(--primary)" : "var(--muted-foreground)" }} />
        <input
          ref={inputRef}
          type="time" value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          className="flex-1 bg-transparent text-sm outline-none cursor-pointer"
          style={{ color: "var(--primary)", fontFamily: "'DM Mono', monospace" }}
        />
      </div>
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────
export function SystemConfig() {
  const [active, setActive] = useState("curfew");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [curfewStart, setCurfewStart] = useState("22:00");
  const [curfewEnd, setCurfewEnd] = useState("06:00");
  const [curfewAge, setCurfewAge] = useState(18);
  const [curfewConf, setCurfewConf] = useState(75);
  const [curfewDwell, setCurfewDwell] = useState(5);
  const [guardianCheck, setGuardianCheck] = useState(true);
  const [unknownAlert, setUnknownAlert] = useState(true);
  const [noiseEnabled, setNoiseEnabled] = useState(true);
  const [noiseSensitivity, setNoiseSensitivity] = useState(2);
  const [noiseDur, setNoiseDur] = useState(10);
  const [wasteEnabled, setWasteEnabled] = useState(true);
  const [wasteConf, setWasteConf] = useState(70);
  const [wasteDwell, setWasteDwell] = useState(8);
  const [wasteCollectionStart, setWasteCollectionStart] = useState("06:00");
  const [wasteCollectionEnd, setWasteCollectionEnd] = useState("09:00");
  const [cooldown, setCooldown] = useState(120);
  const [retention, setRetention] = useState(30);
  const [autoDispatch, setAutoDispatch] = useState(false);
  const [emailAlerts, setEmailAlerts] = useState(true);
  const [smsAlerts, setSmsAlerts] = useState(true);

  const applySettings = (f) => {
    setCurfewStart(f.curfewStart);
    setCurfewEnd(f.curfewEnd);
    setCurfewAge(f.curfewAge);
    setCurfewConf(f.curfewConf);
    setCurfewDwell(f.curfewDwell);
    setGuardianCheck(f.guardianCheck);
    setUnknownAlert(f.unknownAlert);
    setNoiseEnabled(f.noiseEnabled);
    setNoiseSensitivity(f.noiseSensitivity);
    setNoiseDur(f.noiseDur);
    setWasteEnabled(f.wasteEnabled);
    setWasteConf(f.wasteConf);
    setWasteDwell(f.wasteDwell);
    setWasteCollectionStart(f.wasteCollectionStart);
    setWasteCollectionEnd(f.wasteCollectionEnd);
    setCooldown(f.cooldown);
    setRetention(f.retention);
    setAutoDispatch(f.autoDispatch);
    setEmailAlerts(f.emailAlerts);
    setSmsAlerts(f.smsAlerts);
  };

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      applySettings(fromApi(await getSettings()));
    } catch (err) {
      setLoadError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true);
    try {
      const updated = await saveSettings(toApi({
        curfewStart, curfewEnd, curfewAge, curfewConf, curfewDwell,
        guardianCheck, unknownAlert, noiseEnabled, noiseSensitivity, noiseDur,
        wasteEnabled, wasteConf, wasteDwell, wasteCollectionStart, wasteCollectionEnd,
        cooldown, retention, autoDispatch, emailAlerts, smsAlerts,
      }));
      applySettings(fromApi(updated));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      alert(`Failed to save settings: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const content = {
    curfew: (
      <div className="space-y-6">
        <div className="grid grid-cols-2 gap-4">
          <TimeInput label="Curfew start" value={curfewStart} onChange={setCurfewStart} />
          <TimeInput label="Curfew end" value={curfewEnd} onChange={setCurfewEnd} />
        </div>
        <Slider label="Minor age threshold" value={curfewAge} min={15} max={20} unit=" yrs" onChange={setCurfewAge} />
        <Slider
          label="Detection confidence" value={curfewConf} min={20} max={90} unit="%"
          desc="Face-match similarity score — a genuine match typically scores 35–70%. Values above ~75% are rarely reached."
          onChange={setCurfewConf}
        />
        <Slider label="Dwell time before alert" value={curfewDwell} min={2} max={30} unit="s" onChange={setCurfewDwell} />
        <div>
          <Toggle label="Guardian co-presence check" desc="Verify adult accompaniment — applies Ordinance No. 636 exemptions" value={guardianCheck} onChange={setGuardianCheck} />
          <Toggle label="Unknown person alert" desc="Alert for faces not in the resident database" value={unknownAlert} onChange={setUnknownAlert} />
        </div>
      </div>
    ),
    noise: (
      <div className="space-y-6">
        <Toggle label="Noise detection enabled" desc="Monitor ambient audio levels via camera microphones" value={noiseEnabled} onChange={setNoiseEnabled} />
        <SensitivitySelector label="Detection sensitivity" value={noiseSensitivity} onChange={setNoiseSensitivity} />
        <Slider label="Sustained duration before alert" value={noiseDur} min={5} max={60} unit="s" onChange={setNoiseDur} />
        <div className="rounded-xl p-4 text-xs leading-relaxed"
          style={{ background: "rgba(167,139,250,0.06)", border: "1px solid rgba(167,139,250,0.15)", color: "var(--muted-foreground)" }}>
          Quiet hours run <strong style={{ color: "#a78bfa" }}>22:00 – 06:00</strong>. Noise sustained above the threshold during these hours triggers a violation alert.
        </div>
      </div>
    ),
    waste: (
      <div className="space-y-6">
        <Toggle label="Waste detection enabled" desc="Detect improper disposal outside collection hours" value={wasteEnabled} onChange={setWasteEnabled} />
        <Slider label="Detection confidence" value={wasteConf} min={50} max={99} unit="%" onChange={setWasteConf} />
        <Slider label="Object dwell time" value={wasteDwell} min={3} max={30} unit="s" onChange={setWasteDwell} />
        <div className="grid grid-cols-2 gap-4">
          <TimeInput label="Collection hours start" value={wasteCollectionStart} onChange={setWasteCollectionStart} />
          <TimeInput label="Collection hours end" value={wasteCollectionEnd} onChange={setWasteCollectionEnd} />
        </div>
      </div>
    ),
    system: (
      <div className="space-y-6">
        <Slider label="Alert cooldown period" value={cooldown} min={30} max={600} unit="s" onChange={setCooldown} />
        <Slider label="Evidence retention" value={retention} min={7} max={90} unit=" days" onChange={setRetention} />
        <div>
          <Toggle label="Auto-dispatch on critical alert" desc="Notify nearest on-duty officer automatically" value={autoDispatch} onChange={setAutoDispatch} />
          <Toggle label="Email notifications" desc="Send alert emails to administrators" value={emailAlerts} onChange={setEmailAlerts} />
          <Toggle label="SMS notifications" desc="Send SMS to dispatchers and officers" value={smsAlerts} onChange={setSmsAlerts} />
        </div>
        <div className="rounded-xl p-4 space-y-2" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
          <div className="text-xs font-medium mb-3" style={{ color: "var(--foreground)" }}>System info</div>
          {[
            ["Model",         "YOLOv8n — fine-tuned v2.4.1"],
            ["Face backend",  "ArcFace · DeepFace 0.0.93"],
            ["RTSP streams",  "4 active"],
            ["Data residency","Local · RA 10173"],
            ["Last retrain",  "2025-05-18"],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between text-xs">
              <span style={{ color: "var(--muted-foreground)" }}>{k}</span>
              <span style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>{v}</span>
            </div>
          ))}
        </div>
      </div>
    ),
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <h1 className="text-[15px] font-semibold" style={{ color: "var(--foreground)" }}>Settings</h1>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>Admin only · changes apply immediately</span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={load}
            disabled={loading || saving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}
          >
            <RotateCcw size={11} /> Reset
          </button>
          <button
            onClick={save}
            disabled={loading || saving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-50"
            style={{ background: saved ? "#10b981" : "var(--primary)", color: saved ? "#fff" : "var(--primary-foreground)" }}
          >
            <Save size={11} /> {saved ? "Saved!" : saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Section nav */}
        <div className="w-48 flex-shrink-0 p-3 space-y-0.5 overflow-y-auto"
          style={{ borderRight: "1px solid var(--border)" }}>
          {sections.map((s) => {
            const Icon = s.icon;
            const isActive = active === s.id;
            return (
              <button
                key={s.id}
                onClick={() => setActive(s.id)}
                className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left transition-all text-[13px] font-medium"
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
        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-md">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-16 gap-2">
                <Loader2 size={24} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
                <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Loading settings…</div>
              </div>
            ) : loadError ? (
              <div className="flex flex-col items-center justify-center py-16 gap-2">
                <AlertTriangle size={24} style={{ color: "#ef4444" }} />
                <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Failed to load settings</div>
                <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
              </div>
            ) : (
              content[active] ?? (
                <div className="text-sm text-center py-16" style={{ color: "var(--muted-foreground)" }}>
                  Configuration for {active} coming soon
                </div>
              )
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
