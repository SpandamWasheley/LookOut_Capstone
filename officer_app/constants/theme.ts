import { Platform, TextStyle, ViewStyle } from "react-native";

/**
 * The non-colour half of the design system: type, spacing, radii, depth.
 *
 * Why this file exists: every screen used to invent its own numbers. Font
 * sizes ran 9, 10, 11, 12, 13, 14, 15, 18, 20, 22 and 24 with no scale behind
 * them, and radii ran 9, 10, 12, 16, 20, 24 and 36 — so two cards that meant
 * the same thing were rounded differently, and the `radius: 10` token in
 * constants/colors.ts was used almost nowhere. Nothing looks designed when the
 * measurements disagree with each other; it looks assembled.
 *
 * The scale is also tuned for WHERE this app is used, which is the one thing a
 * borrowed scale can't know: a tanod's phone, held one-handed, outdoors in
 * Zamboanga daylight or on a night shift. That is why `meta` is the smallest
 * type in the app at 12 and nothing goes below it, and why every tappable
 * thing is at least TOUCH_MIN tall.
 */

// ---------------------------------------------------------------------------
// Type
// ---------------------------------------------------------------------------

/** The four Inter weights loaded in app/_layout.tsx. Named so a style can't
 *  reference a weight that was never bundled — a missing family silently
 *  falls back to the system face, which is exactly the "default" look this
 *  pass is removing. */
export const font = {
  regular: "Inter_400Regular",
  medium: "Inter_500Medium",
  semibold: "Inter_600SemiBold",
  bold: "Inter_700Bold",
} as const;

/**
 * Display sizes get NEGATIVE tracking and small sizes get positive: large
 * Inter set at its default spacing reads loose and web-ish, and small Inter
 * set tight closes up on a cheap LCD in sunlight. This is the whole reason
 * the app can keep one typeface and still not look like a template — the
 * personality is in the setting, not in a second font download that a field
 * phone would have to fetch over barangay data.
 */
export const type = {
  /** Launch and login only — the app saying its own name. */
  display: { fontFamily: font.bold, fontSize: 30, lineHeight: 34, letterSpacing: -0.6 },
  /** Screen titles. */
  title: { fontFamily: font.bold, fontSize: 22, lineHeight: 27, letterSpacing: -0.3 },
  /** Card titles, sheet titles, the violation name. */
  heading: { fontFamily: font.semibold, fontSize: 17, lineHeight: 22, letterSpacing: -0.2 },
  /** Running text. */
  body: { fontFamily: font.regular, fontSize: 15, lineHeight: 21 },
  bodyStrong: { fontFamily: font.medium, fontSize: 15, lineHeight: 21 },
  /** Button faces and section headings. Sentence case — see SECTION below. */
  label: { fontFamily: font.semibold, fontSize: 13, lineHeight: 18 },
  /** The floor. Timestamps, hints, counts. Never smaller than this. */
  meta: { fontFamily: font.regular, fontSize: 12, lineHeight: 16 },
  metaStrong: { fontFamily: font.medium, fontSize: 12, lineHeight: 16 },
  /**
   * Alert codes (ALT-0042) and officer badges. Tabular figures so digits sit
   * in a fixed column: these are identifiers an officer reads aloud over
   * radio and compares character by character, and proportional digits make
   * 1s and 7s shift about between two codes stacked in a list.
   */
  code: {
    fontFamily: font.medium,
    fontSize: 12,
    lineHeight: 16,
    letterSpacing: 0.3,
    fontVariant: ["tabular-nums"],
  },
  /** Big single numbers — the profile counters. Tabular for the same reason. */
  figure: {
    fontFamily: font.bold,
    fontSize: 26,
    lineHeight: 30,
    letterSpacing: -0.5,
    fontVariant: ["tabular-nums"],
  },
} satisfies Record<string, TextStyle>;

/**
 * Section headings are SENTENCE CASE, deliberately.
 *
 * Every section in this app used to be a tracked-out capital label — DETAILS,
 * CONTACT, APPEARANCE, YOUR ASSIGNED VIOLATIONS · 3. Caps eyebrows are the
 * most recognisable piece of template chrome there is, they are measurably
 * slower to read than sentence case (no ascenders or descenders to shape the
 * word), and they were shouting six times on one screen. Hierarchy here comes
 * from weight, colour and the spine beside the heading instead.
 */
export const SECTION = type.label;

// ---------------------------------------------------------------------------
// Space
// ---------------------------------------------------------------------------

/** 4-based. Use the names, not the numbers, so rhythm survives edits. */
export const space = {
  xxs: 2,
  xs: 4,
  sm: 8,
  md: 12,
  base: 16,
  lg: 20,
  xl: 24,
  xxl: 32,
  xxxl: 40,
} as const;

/** The horizontal gutter every screen shares. One value, so nothing drifts. */
export const GUTTER = space.lg;

/**
 * Minimum tappable height. Android's own guidance is 48dp and this app is
 * used in a hurry, outdoors, sometimes by someone wearing work gloves — so
 * it is a floor, not a target, and several controls here are taller.
 */
export const TOUCH_MIN = 48;

// ---------------------------------------------------------------------------
// Radii
// ---------------------------------------------------------------------------

/**
 * Three tiers, each tied to a ROLE rather than to taste. Radius is hierarchy:
 * when a chip, a card and a bottom sheet are all rounded the same, nothing
 * tells you which one contains the others.
 */
export const radius = {
  /** Controls: inputs, buttons, chips, small swatches. */
  control: 8,
  /** Cards and anything that holds controls. */
  card: 14,
  /** Sheets and modals — the thing that holds the cards. */
  sheet: 22,
  /** Pills and avatars. */
  pill: 999,
} as const;

/** The violation spine: the one structural device this app is built around.
 *  Carried by the assignment card, the detail header, the active tab and
 *  every section heading, always coloured by what it identifies. */
export const SPINE = 4;

// ---------------------------------------------------------------------------
// Depth
// ---------------------------------------------------------------------------

/**
 * Two levels, and they work differently per theme on purpose.
 *
 * A drop shadow is light falling on paper: it reads on the warm paper ground
 * and says nothing at all on the deep-night ground, where a black shadow on
 * a near-black surface is invisible. So dark mode lifts a surface by being
 * LIGHTER than its ground (the `card` token already is) and keeps only a
 * hairline; light mode gets the shadow. Shadows are never used decoratively —
 * only on things that genuinely float above the page (sheets, the tab bar).
 */
export function elevation(level: 1 | 2, scheme: "light" | "dark"): ViewStyle {
  if (scheme === "dark") return {};
  const ios = level === 1
    ? { shadowColor: "#0b1e2c", shadowOpacity: 0.06, shadowRadius: 8, shadowOffset: { width: 0, height: 2 } }
    : { shadowColor: "#0b1e2c", shadowOpacity: 0.12, shadowRadius: 20, shadowOffset: { width: 0, height: -2 } };
  return Platform.OS === "android" ? { elevation: level === 1 ? 2 : 12 } : ios;
}

// ---------------------------------------------------------------------------
// Motion
// ---------------------------------------------------------------------------

/**
 * Durations, in ms. Short because every animation in this app answers a tap —
 * a sheet opening, a toggle moving, a dialog arriving. Nothing here animates
 * on its own except the spinner, which exists to say "still working".
 */
export const duration = { fast: 120, base: 180, sheet: 240 } as const;
