/**
 * Making a backend-chosen colour readable on whichever ground is showing.
 *
 * Violation-type colours come from the server (ViolationType.color) as one hex
 * per type — #6b8cae smoking, #f59e0b parking, #22c55e disposal — chosen once,
 * with no idea which theme they will land on. Used raw they are wrong at both
 * ends: parking's amber on warm paper is about 1.9:1 and unreadable as a
 * glyph, and smoking's slate blue sinks into the night ground.
 *
 * So the server's colour stays the source of truth for IDENTITY — the spine,
 * the dot, which violation this is — and these two helpers adapt it to the job
 * it is doing: `readable` for anything carrying a shape or a word, `wash` for
 * a fill sitting behind one.
 */

const INK = [11, 30, 44]; // #0b1e2c
const PAPER = [246, 240, 231]; // #f6f0e7

function parse(hex: string): [number, number, number] | null {
  const raw = hex.replace("#", "").trim();
  const full = raw.length === 3 ? raw.split("").map((ch) => ch + ch).join("") : raw;
  if (full.length !== 6 || /[^0-9a-f]/i.test(full)) return null;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

function mix(rgb: [number, number, number], towards: number[], amount: number): string {
  const out = rgb.map((v, i) => Math.round(v + (towards[i] - v) * amount));
  return "#" + out.map((v) => v.toString(16).padStart(2, "0")).join("");
}

/**
 * The type's colour pulled far enough toward the ink (light) or the paper
 * (dark) to carry a glyph or a word. The hue survives, so the violation stays
 * recognisable by colour — it just stops being invisible.
 *
 * Falls through unchanged on anything it cannot parse, because a violation
 * type with a malformed colour should still render.
 */
export function readable(hex: string, scheme: "light" | "dark"): string {
  const rgb = parse(hex);
  if (!rgb) return hex;
  return scheme === "dark" ? mix(rgb, PAPER, 0.3) : mix(rgb, INK, 0.38);
}

/**
 * The same colour as a fill to sit behind a glyph. Alpha rather than a mix, so
 * it tints whatever surface it lands on instead of assuming one.
 */
export function wash(hex: string, scheme: "light" | "dark"): string {
  return hex + (scheme === "dark" ? "33" : "26");
}
