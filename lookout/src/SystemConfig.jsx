import { useEffect, useState } from "react";
import { IndicatorTimings } from "./IndicatorTimings";
import { Save, RotateCcw, Car, Cigarette, Siren, Beer, AlertTriangle, Loader2, SlidersHorizontal } from "lucide-react";
import { getSettings, saveSettings } from "./api";

const trimSeconds = (t) => (t ? t.slice(0, 5) : t);

function fromApi(s) {
  return {
    parkingEnabled: s.parking_enabled,
    parkingConf: s.parking_confidence,
    parkingDwell: s.parking_dwell,
    parkingMove: s.parking_move_tolerance,
    smokingEnabled: s.smoking_enabled,
    smokingConf: s.smoking_confidence,
    smokingDwell: s.smoking_dwell,
    thiefEnabled: s.thief_enabled,
    thiefConf: s.thief_confidence,
    thiefDwell: s.thief_dwell,
    drinkingEnabled: s.drinking_enabled,
    drinkingConf: s.drinking_confidence,
    drinkingDwell: s.drinking_dwell,
    drinkingHeldDwell: s.drinking_held_dwell,
    drinkingEvidenceMaxAge: s.drinking_evidence_max_age,
    drinkingMouthProximity: s.drinking_mouth_proximity,
    drinkingCooldownDist: s.drinking_cooldown_center_dist,
    drinkingHoursEnabled: s.drinking_hours_enabled,
    drinkingStart: trimSeconds(s.drinking_start),
    drinkingEnd: trimSeconds(s.drinking_end),
    cooldown: s.alert_cooldown,
    retention: s.evidence_retention_days,
    autoPurge: s.evidence_auto_purge,
  };
}

function toApi(f) {
  return {
    parking_enabled: f.parkingEnabled,
    parking_confidence: f.parkingConf,
    parking_dwell: f.parkingDwell,
    parking_move_tolerance: f.parkingMove,
    smoking_enabled: f.smokingEnabled,
    smoking_confidence: f.smokingConf,
    smoking_dwell: f.smokingDwell,
    thief_enabled: f.thiefEnabled,
    thief_confidence: f.thiefConf,
    thief_dwell: f.thiefDwell,
    drinking_enabled: f.drinkingEnabled,
    drinking_confidence: f.drinkingConf,
    drinking_dwell: f.drinkingDwell,
    drinking_held_dwell: f.drinkingHeldDwell,
    drinking_evidence_max_age: f.drinkingEvidenceMaxAge,
    drinking_mouth_proximity: f.drinkingMouthProximity,
    drinking_cooldown_center_dist: f.drinkingCooldownDist,
    alert_cooldown: f.cooldown,
    evidence_retention_days: f.retention,
    evidence_auto_purge: f.autoPurge,
  };
}

const sections = [
  { id: "parking", label: "Parking", icon: Car,    color: "#ef4444" },
  { id: "smoking", label: "Smoking", icon: Cigarette, color: "#f97316" },
  { id: "thief",   label: "Holdup",   icon: Siren,  color: "#dc2626" },
  { id: "drinking", label: "Drinking", icon: Beer, color: "#8b5cf6" },
  { id: "system", label: "System", icon: SlidersHorizontal, color: "#3b82f6" },
];

// ── Slider ────────────────────────────────────────────────────────────────────
function Slider({ label, value, min, max, step = 1, unit, desc, onChange }) {
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

// ── Toggle ────────────────────────────────────────────────────────────────────
function Toggle({ label, desc, value, onChange }) {
  return (
    <div className="flex items-center justify-between py-3" style={{ borderBottom: "1px solid var(--border)" }}>
      <div>
        <div className="text-[15px] font-medium" style={{ color: "var(--foreground)" }}>{label}</div>
        <div className="text-[13px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{desc}</div>
      </div>
      <button
        onClick={() => onChange(!value)}
        className="relative w-9 h-5 rounded-full transition-all duration-200 flex-shrink-0 ml-4"
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

// ── TimeInput ─────────────────────────────────────────────────────────────────
export function SystemConfig() {
  const [active, setActive] = useState("parking");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [pendingAction, setPendingAction] = useState(null); // null | "reset" | "save"

  const [parkingEnabled, setParkingEnabled] = useState(true);
  const [parkingConf, setParkingConf] = useState(35);
  const [parkingDwell, setParkingDwell] = useState(60);
  const [parkingMove, setParkingMove] = useState(40);
  const [smokingEnabled, setSmokingEnabled] = useState(true);
  const [smokingConf, setSmokingConf] = useState(30);
  const [smokingDwell, setSmokingDwell] = useState(3);
  const [thiefEnabled, setThiefEnabled] = useState(true);
  const [thiefConf, setThiefConf] = useState(30);
  const [thiefDwell, setThiefDwell] = useState(3);
  const [drinkingEnabled, setDrinkingEnabled] = useState(true);
  const [drinkingConf, setDrinkingConf] = useState(35);
  const [drinkingDwell, setDrinkingDwell] = useState(8);
  const [drinkingHeldDwell, setDrinkingHeldDwell] = useState(24);
  const [drinkingEvidenceMaxAge, setDrinkingEvidenceMaxAge] = useState(12);
  const [drinkingMouthProximity, setDrinkingMouthProximity] = useState(3.0);
  const [drinkingCooldownDist, setDrinkingCooldownDist] = useState(1.5);
  const [drinkingHoursEnabled, setDrinkingHoursEnabled] = useState(false);
  const [drinkingStart, setDrinkingStart] = useState("22:00");
  const [drinkingEnd, setDrinkingEnd] = useState("05:00");
  const [cooldown, setCooldown] = useState(120);
  const [retention, setRetention] = useState(30);
  const [autoPurge, setAutoPurge] = useState(false);
  const [savedSnapshot, setSavedSnapshot] = useState(null);

  const applySettings = (f) => {
    setParkingEnabled(f.parkingEnabled);
    setParkingConf(f.parkingConf);
    setParkingDwell(f.parkingDwell);
    setParkingMove(f.parkingMove);
    setSmokingEnabled(f.smokingEnabled);
    setSmokingConf(f.smokingConf);
    setSmokingDwell(f.smokingDwell);
    setThiefEnabled(f.thiefEnabled);
    setThiefConf(f.thiefConf);
    setThiefDwell(f.thiefDwell);
    setDrinkingEnabled(f.drinkingEnabled);
    setDrinkingConf(f.drinkingConf);
    setDrinkingDwell(f.drinkingDwell);
    setDrinkingHeldDwell(f.drinkingHeldDwell);
    setDrinkingEvidenceMaxAge(f.drinkingEvidenceMaxAge);
    setDrinkingMouthProximity(f.drinkingMouthProximity);
    setDrinkingCooldownDist(f.drinkingCooldownDist);
    setDrinkingHoursEnabled(f.drinkingHoursEnabled);
    setDrinkingStart(f.drinkingStart);
    setDrinkingEnd(f.drinkingEnd);
    setCooldown(f.cooldown);
    setRetention(f.retention);
    setAutoPurge(f.autoPurge);
    setSavedSnapshot(f);
  };

  const isDirty = !!savedSnapshot && (
    parkingEnabled !== savedSnapshot.parkingEnabled ||
    parkingConf !== savedSnapshot.parkingConf ||
    parkingDwell !== savedSnapshot.parkingDwell ||
    parkingMove !== savedSnapshot.parkingMove ||
    smokingEnabled !== savedSnapshot.smokingEnabled ||
    smokingConf !== savedSnapshot.smokingConf ||
    smokingDwell !== savedSnapshot.smokingDwell ||
    thiefEnabled !== savedSnapshot.thiefEnabled ||
    thiefConf !== savedSnapshot.thiefConf ||
    thiefDwell !== savedSnapshot.thiefDwell ||
    drinkingEnabled !== savedSnapshot.drinkingEnabled ||
    drinkingConf !== savedSnapshot.drinkingConf ||
    drinkingDwell !== savedSnapshot.drinkingDwell ||
    drinkingHeldDwell !== savedSnapshot.drinkingHeldDwell ||
    drinkingEvidenceMaxAge !== savedSnapshot.drinkingEvidenceMaxAge ||
    drinkingMouthProximity !== savedSnapshot.drinkingMouthProximity ||
    drinkingCooldownDist !== savedSnapshot.drinkingCooldownDist ||
    drinkingHoursEnabled !== savedSnapshot.drinkingHoursEnabled ||
    drinkingStart !== savedSnapshot.drinkingStart ||
    drinkingEnd !== savedSnapshot.drinkingEnd ||
    cooldown !== savedSnapshot.cooldown ||
    retention !== savedSnapshot.retention ||
    autoPurge !== savedSnapshot.autoPurge
  );

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
        parkingEnabled, parkingConf, parkingDwell, parkingMove,
        smokingEnabled, smokingConf, smokingDwell,
        thiefEnabled, thiefConf, thiefDwell,
        drinkingEnabled, drinkingConf, drinkingDwell,
        drinkingHeldDwell, drinkingEvidenceMaxAge, drinkingMouthProximity, drinkingCooldownDist,
        drinkingHoursEnabled, drinkingStart, drinkingEnd,
        cooldown, retention, autoPurge,
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
    parking: (
      <div className="space-y-6">
        <Toggle label="Illegal parking detection enabled" desc="Flag vehicles parked / obstructing beyond the dwell time" value={parkingEnabled} onChange={setParkingEnabled} />
        <Slider
          label="Detection confidence" value={parkingConf} min={20} max={90} unit="%"
          desc="How sure the model must be that a box is a vehicle. Lower catches faint/blurry ones; higher reduces false detections."
          onChange={setParkingConf}
        />
        <Slider
          label="Dwell time before alert" value={parkingDwell} min={5} max={300} unit="s"
          desc="How long a vehicle must stay put to count as parked. A car merely driving through never reaches this."
          onChange={setParkingDwell}
        />
        <Slider
          label="Movement tolerance" value={parkingMove} min={10} max={120} unit="px"
          desc="How far a vehicle may drift and still be 'stationary'. Drift beyond this resets its dwell timer."
          onChange={setParkingMove}
        />
        <div className="rounded-xl p-4 text-xs leading-relaxed"
          style={{ background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.15)", color: "var(--muted-foreground)" }}>
          Detects <strong style={{ color: "#ef4444" }}>car, motorcycle, bus, truck</strong> (tricycles register as motorcycle). A vehicle stationary past the dwell time raises an <strong style={{ color: "#ef4444" }}>Illegal Parking</strong> alert.
        </div>
      </div>
    ),
    smoking: (
      <div className="space-y-6">
        <Toggle label="Smoking detection enabled" desc="Detect public smoking (cigarette / vape) on the smoking-monitor feed" value={smokingEnabled} onChange={setSmokingEnabled} />
        <Slider
          label="Detection confidence" value={smokingConf} min={10} max={90} unit="%"
          desc="How sure the model must be. The custom model scores genuine cigarettes/vapes ~30–90%; lower catches more at distance, higher reduces false hits."
          onChange={setSmokingConf}
        />
        <Slider
          label="Dwell time before alert" value={smokingDwell} min={2} max={30} unit="s"
          desc="How long smoking must stay visible before alerting. Filters one-frame false positives."
          onChange={setSmokingDwell}
        />
        <div className="rounded-xl p-4 text-xs leading-relaxed"
          style={{ background: "rgba(249,115,22,0.06)", border: "1px solid rgba(249,115,22,0.15)", color: "var(--muted-foreground)" }}>
          Detects <strong style={{ color: "#f97316" }}>cigarettes</strong>. Sustained presence past the dwell time raises a <strong style={{ color: "#f97316" }}>Public Smoking</strong> alert with an evidence snapshot.
        </div>
        <IndicatorTimings group="smoking" />
      </div>
    ),
    thief: (
      <div className="space-y-6">
        <Toggle label="Holdup detection enabled" desc="Detect Holdup indicators on the monitor feed" value={thiefEnabled} onChange={setThiefEnabled} />
        <Slider
          label="Detection confidence" value={thiefConf} min={10} max={90} unit="%"
          desc="How sure the model must be. Lower catches more (with more false hits); higher is stricter."
          onChange={setThiefConf}
        />
        <Slider
          label="Dwell time before alert" value={thiefDwell} min={2} max={30} unit="s"
          desc="How long a threat must stay visible before alerting. Kept short — an armed robbery should alert fast."
          onChange={setThiefDwell}
        />
        <div className="rounded-xl p-4 text-xs leading-relaxed"
          style={{ background: "rgba(220,38,38,0.06)", border: "1px solid rgba(220,38,38,0.15)", color: "var(--muted-foreground)" }}>
          Detects <strong style={{ color: "#dc2626" }}>Knife</strong> as anchor for the potential violation. Sustained presence past the dwell time raises a <strong style={{ color: "#dc2626" }}>Holdup</strong> alert with an evidence snapshot.
        </div>
        <IndicatorTimings group="holdup" />
      </div>
    ),
    drinking: (
      <div className="space-y-6">
        <Toggle label="Drinking detection enabled" desc="Detect public drinking on the drinking-monitor feed" value={drinkingEnabled} onChange={setDrinkingEnabled} />
        <Slider
          label="Detection confidence" value={drinkingConf} min={10} max={90} unit="%"
          desc="How sure the model must be. Lower catches more (with more false hits); higher is stricter."
          onChange={setDrinkingConf}
        />
        <Slider
          label="Dwell time before alert (raised to mouth)" value={drinkingDwell} min={3} max={60} unit="s"
          desc="How long a bottle must stay raised to the mouth before it counts as drinking, not just possession."
          onChange={setDrinkingDwell}
        />
        <Slider
          label="Dwell time before alert (held, not raised)" value={drinkingHeldDwell} min={10} max={120} unit="s"
          desc="How long a bottle merely held — or with no face visible to check posture at all, common at CCTV range — must stay with a person before it counts. Weaker evidence than a raised bottle, so this should stay well above the dwell above."
          onChange={setDrinkingHeldDwell}
        />
        <Slider
          label="Gathering evidence expiry" value={drinkingEvidenceMaxAge} min={5} max={60} unit="s"
          desc="A gathering's bottle sighting must be this recent to still count — stops a group from staying 'armed' to alert on one old bottle no longer in frame."
          onChange={setDrinkingEvidenceMaxAge}
        />
        <Slider
          label="Mouth proximity" value={drinkingMouthProximity} min={1.5} max={5} step={0.25} unit=" face-widths"
          desc="How close a bottle must be to the mouth to count as raised, in units of the detected face's width."
          onChange={setDrinkingMouthProximity}
        />
        <Slider
          label="Alert cooldown radius" value={drinkingCooldownDist} min={0.5} max={3} step={0.25} unit="x"
          desc="How close (as a fraction of person height) a new alert must be to a recent one to count as 'the same spot' and get suppressed by cooldown."
          onChange={setDrinkingCooldownDist}
        />
        <div className="rounded-xl p-4 text-xs leading-relaxed"
          style={{ background: "rgba(139,92,246,0.06)", border: "1px solid rgba(139,92,246,0.15)", color: "var(--muted-foreground)" }}>
          Detects <strong style={{ color: "#8b5cf6" }}>bottles</strong>. Sustained presence past the dwell time raises a <strong style={{ color: "#8b5cf6" }}>Public Drinking</strong> alert with an evidence snapshot, reflecting inuman culture practices.
        </div>
        <IndicatorTimings group="drinking" />
      </div>
    ),
    system: (
      <div className="space-y-6">
        <Slider label="Alert cooldown period" value={cooldown} min={30} max={600} unit="s" onChange={setCooldown} />
        <Slider
          label="Evidence retention" value={retention} min={7} max={90} unit=" days"
          desc="How long alert images and clips are kept before they may be purged (RA 10173 storage limitation). The alert records themselves are always kept."
          onChange={setRetention}
        />
        <Toggle
          label="Automatic purge of old evidence"
          desc="OFF by default. When on, a scheduled run of `manage.py purge_old_evidence --auto` deletes evidence older than the retention period. Nothing is deleted while this is off."
          value={autoPurge} onChange={setAutoPurge}
        />
        <IndicatorTimings group="all" />
        <IndicatorTimings group="ai" />
      </div>
    ),
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Settings</h1>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>Admin only · changes apply immediately</span>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setPendingAction("reset")}
            disabled={loading || saving || !isDirty}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}
          >
            <RotateCcw size={11} /> Reset
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
        <div className="w-48 flex-shrink-0 p-3 space-y-0.5 overflow-y-auto"
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
                <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
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

      {pendingAction && (() => {
        const config = pendingAction === "reset"
          ? {
              iconColor: "#ef4444", iconBg: "rgba(239,68,68,0.12)", Icon: RotateCcw,
              title: "Discard unsaved changes?",
              message: "This reloads the last saved settings from the server — any edits you haven't saved will be lost.",
              confirmLabel: "Yes, reset", confirmColor: "#ef4444",
              run: load,
            }
          : {
              iconColor: "#10b981", iconBg: "rgba(16,185,129,0.12)", Icon: Save,
              title: "Save these settings?",
              message: "Changes apply immediately across the system — the detectors will use these values right away.",
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
