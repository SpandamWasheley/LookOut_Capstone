import { Cigarette, Beer, Car, ShieldAlert, AlertTriangle } from "lucide-react";
import { getViolationTypes } from "../api.js";

// Scope is fixed to these four types. Curfew/waste/noise are explicitly out —
// an unmapped backend row (curfew or anything else) falls through to
// UNKNOWN_VIOLATION_TYPE below rather than being added here.
//
// Icons are reused from data/mockData.js's VIOLATION_CONFIG (same lucide
// imports) so the visual language doesn't fork. The API's own `color` and
// `icon` fields on ViolationType are intentionally ignored — the frontend
// owns visual identity, not the backend.
//
// `short` is an explicit field, never derived from `label` (e.g. via
// split(" ")[0]) — that's what used to turn "Illegal Parking / Obstruction"
// into "Illegal".
export const VIOLATION_TYPES = {
  smoking: {
    code: "smoking",
    label: "Smoking",
    short: "Smoking",
    icon: Cigarette,
    aliases: ["smoking", "smoking in public places", "smoking in public area"],
  },
  drinking: {
    code: "drinking",
    label: "Drinking",
    short: "Drinking",
    icon: Beer,
    aliases: ["drinking", "drinking in public area", "drinking in public place"],
  },
  parking: {
    code: "parking",
    label: "Parking Obstruction in Area",
    short: "Parking",
    icon: Car,
    aliases: [
      "parking",
      "illegal parking",
      "illegal parking / obstruction",
      "parking obstruction",
      "parking obstruction in area",
    ],
  },
  // watch_thief.py used to create a SECOND ViolationType row (code "thief")
  // alongside the seeded "theft" row, so a citation or alert could carry
  // either — see migration 0026, which merged every "thief" row into
  // "theft" at the source (watch_thief.py/watch_all.py/watch_merged.py now
  // all create code="theft" too). The old aliases stay below regardless, as
  // a safety net for any row/citation text created before that fix.
  theft: {
    code: "theft",
    label: "Holdup in Public Area",
    short: "Holdup",
    icon: ShieldAlert,
    aliases: [
      "theft", "thief", "theft violation", "theft / robbery", "theft (holdup)",
      "holdup in public area", "holdup",
    ],
  },
};

// Safe, non-crashing fallback for anything that doesn't resolve — including
// curfew rows, which are deliberately not mapped above.
export const UNKNOWN_VIOLATION_TYPE = {
  code: "unknown",
  label: "Unknown",
  short: "Unknown",
  icon: AlertTriangle,
  aliases: [],
};

function normalize(value) {
  return String(value ?? "").trim().toLowerCase();
}

const ENTRIES = Object.values(VIOLATION_TYPES);

function findByCode(code) {
  const normalized = normalize(code);
  if (!normalized) return undefined;
  return ENTRIES.find((t) => t.code === normalized || t.aliases.includes(normalized));
}

function findByLabel(label) {
  const normalized = normalize(label);
  if (!normalized) return undefined;
  return ENTRIES.find((t) => t.aliases.includes(normalized));
}

// id -> code, built once from GET /violation-types/ by loadViolationTypeIndex.
// Resolution is ID-first (stable PK) with label matching as the fallback for
// callers that only have free text (e.g. Citation.violation_labels).
let idToCode = null;
let loadPromise = null;

export function setViolationTypeIndex(apiTypes) {
  const map = new Map();
  for (const t of apiTypes ?? []) {
    if (t?.id != null && t.code) map.set(t.id, normalize(t.code));
  }
  idToCode = map;
}

// Fetches /violation-types/ once and caches the id -> code index for the
// lifetime of the page. Safe to call from multiple components; concurrent
// calls share the same in-flight request.
export function loadViolationTypeIndex() {
  if (idToCode) return Promise.resolve(idToCode);
  if (loadPromise) return loadPromise;
  loadPromise = getViolationTypes()
    .then((res) => {
      setViolationTypeIndex(res?.results ?? res);
      return idToCode;
    })
    .catch((err) => {
      loadPromise = null;
      throw err;
    });
  return loadPromise;
}

// Resolves a violation to one of the four VIOLATION_TYPES entries, or
// UNKNOWN_VIOLATION_TYPE if nothing matches. Never throws.
//   id    - stable ViolationType PK (preferred; requires loadViolationTypeIndex)
//   code  - ViolationType.code, used directly if id isn't resolvable
//   label - free text (e.g. violation_labels[]), fallback only
// Inline style for a chip, using the per-type CSS custom properties defined
// in index.css (--violation-{code}-bg/text/border/dot, in both the light and
// dark blocks). Centralized here so consumers don't hand-roll the var name.
export function violationChipStyle(type) {
  const code = type?.code ?? "unknown";
  return {
    background: `var(--violation-${code}-bg)`,
    color: `var(--violation-${code}-text)`,
    borderColor: `var(--violation-${code}-border)`,
  };
}

export function resolveViolationType({ id, code, label } = {}) {
  if (id != null && idToCode?.has(id)) {
    const resolved = findByCode(idToCode.get(id));
    if (resolved) return resolved;
  }
  if (code) {
    const resolved = findByCode(code);
    if (resolved) return resolved;
  }
  if (label) {
    const resolved = findByLabel(label);
    if (resolved) return resolved;
  }
  return UNKNOWN_VIOLATION_TYPE;
}

// Capitalizes a raw backend code/slug for display when nothing in
// VIOLATION_TYPES claims it (curfew/waste/noise — deliberately out of scope,
// see the top of this file — or a future type this map hasn't been taught
// yet), so a user sees "Curfew", never the raw "curfew", and never something
// as opaque as "thief". Pure string helper — does not change what
// resolveViolationType returns, so it can't affect callers that rely on its
// identity contract (e.g. ResidentLog.jsx's `!== UNKNOWN_VIOLATION_TYPE`).
export function humanizeType(raw) {
  const s = String(raw ?? "").trim();
  if (!s) return "Unknown";
  return s.replace(/[_-]+/g, " ").split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// Convenience for components that just need {label, icon, color} for an
// alert's type and must never leak its raw db code — wraps
// resolveViolationType + humanizeType so every caller gets the same safe
// fallback instead of re-deriving it (that re-deriving, with a hand-rolled
// `?? { label: alert.type, icon: AlertTriangle }`, is exactly what used to
// render "thief" directly on ALT-0097's card).
export function violationDisplay(code) {
  const resolved = resolveViolationType({ code });
  const c = resolved.code !== "unknown" ? resolved.code : "unknown";
  return {
    label: resolved.code !== "unknown" ? resolved.label : humanizeType(code),
    icon: resolved.icon,
    color: `var(--violation-${c}-dot)`,
    // Translucent tints for icon-chip backgrounds/hover borders — use these
    // instead of hand-rolling one from `color` (e.g. `${color}22`), which
    // breaks now that color is a CSS custom property string, not a hex
    // literal you can string-concat an alpha suffix onto.
    bg: `var(--violation-${c}-bg)`,
    border: `var(--violation-${c}-border)`,
  };
}
