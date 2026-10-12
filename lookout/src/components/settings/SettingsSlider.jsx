import "./SettingsSlider.css";

// The one slider used on the Settings page. A native range input, restyled in
// SettingsSlider.css, with the filled part (left of the thumb) drawn by a
// gradient recomputed from the value on every render.
export function SettingsSlider({ label, value, min, max, step = 1, unit, desc, onChange }) {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;
  // The thumb's centre travels from 10px to (width - 10px), not 0 to width,
  // so shift the colour stop by the same amount or the fill would drift from
  // the thumb at the ends.
  const stop = `calc(${pct}% + ${10 - pct * 0.2}px)`;
  const background =
    `linear-gradient(to right, var(--slider-fill) 0%, var(--slider-fill) ${stop}, ` +
    `var(--slider-empty) ${stop}, var(--slider-empty) 100%)`;

  return (
    <div className="space-y-2">
      <div className="flex justify-between items-center">
        <span className="text-[14px] font-medium" style={{ color: "var(--foreground)" }}>{label}</span>
        <span className="text-xs font-semibold px-2 py-0.5 rounded-md"
          style={{ color: "var(--primary)", background: "var(--secondary)", fontFamily: "'DM Mono', monospace" }}>
          {value}{unit}
        </span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="settings-slider"
        style={{ "--slider-bg": background }}
      />
      {desc && <p className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{desc}</p>}
    </div>
  );
}
