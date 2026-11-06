/**
 * The palette. One identity, two grounds.
 *
 * This file used to describe two different products. Dark mode was already the
 * web dashboard's palette to the hex (#0c1820 ground, #85b7d6 brand, #0f2030
 * card) — but light mode was a generic cold-grey chassis with an AMBER brand
 * and an unused red `tint`, so a dispatcher on the dashboard and an officer on
 * the phone were not obviously looking at the same system, and LookOut's brand
 * colour changed depending on the time of day.
 *
 * Both themes now sit on the dashboard's values (see lookout/src/index.css):
 * harbour #0b5471 and sky #85b7d6 on warm paper #f6f0e7 or deep night #0c1820.
 *
 * Three rules hold the whole thing together:
 *
 *   1. The brand is NEVER a status. Harbour/sky marks actions and the active
 *      state and nothing else.
 *   2. A status always means the same thing in both themes — `urgent` is an
 *      open violation waiting on this officer, `pending` is in progress,
 *      `closed` is resolved or dismissed. Amber was the light-mode brand AND
 *      the pending colour, which is why a Save button and a pending badge used
 *      to be indistinguishable; amber is now only ever pending.
 *   3. Nothing is coloured for decoration.
 *
 * Contrast is checked against the ground, not eyeballed, because this app is
 * read at arm's length on a mid-range phone in Zamboanga daylight. That is the
 * one place the dashboard's own numbers were not good enough — see
 * `mutedForeground` below.
 */

const colors = {
  light: {
    // --- ground and ink ---------------------------------------------------
    /** Warm paper. The dashboard's --background. */
    background: "#f6f0e7",
    /** Headers, cards and sheets sit just above the paper. */
    card: "#fefaf3",
    cardForeground: "#0b1e2c",
    foreground: "#0b1e2c",
    /**
     * Secondary text. The dashboard uses #797c82, which is 3.7:1 on paper —
     * under the 4.5:1 floor for text this size. This is the single most-used
     * colour in the app (timestamps, hints, labels, placeholder text), and the
     * dashboard is read indoors on a monitor while this is read outdoors in
     * direct sun, so it is darkened to 5.2:1. The one deliberate departure.
     */
    mutedForeground: "#5c6672",

    // --- brand ------------------------------------------------------------
    /** Harbour. 7.5:1 on paper. Actions and active state only. */
    primary: "#0b5471",
    primaryForeground: "#f6f0e7",
    /** Sky. Supporting accent — never a primary action in light mode, where
     *  it is far too pale to carry text. */
    accent: "#85b7d6",
    accentForeground: "#0b1e2c",

    // --- fills ------------------------------------------------------------
    /** Chips, search fields, segmented tracks. */
    secondary: "#ede7dc",
    secondaryForeground: "#0b5471",
    /** Inputs and pills — one step deeper than `secondary`. */
    muted: "#e4ddd2",
    /** A surface that must read as lifted off the paper. */
    surface: "#ffffff",

    // --- structure --------------------------------------------------------
    /** Tinted toward the brand rather than neutral grey: a hairline the same
     *  hue as the ink reads as part of the design, a grey one reads as a
     *  leftover default. */
    border: "rgba(11, 84, 113, 0.14)",
    input: "#ede7dc",

    // --- status (rule 2) --------------------------------------------------
    /** Open and waiting on this officer. */
    urgent: "#c0392b",
    urgentSoft: "rgba(192, 57, 43, 0.10)",
    /** In progress, not yet closed. Text-safe amber — raw #f59e0b is 1.9:1 on
     *  paper and unreadable as type, so the dark gold carries the words and
     *  `pendingMark` carries the dots and spines. */
    pending: "#8a5a00",
    pendingMark: "#d98500",
    pendingSoft: "rgba(217, 133, 0, 0.12)",
    /** Resolved or dismissed. */
    closed: "#15795b",
    closedSoft: "rgba(21, 121, 91, 0.10)",

    // --- legacy aliases ---------------------------------------------------
    // The names ~40 existing styles already reference, pointed at the three
    // statuses above so nothing had to be renamed in one pass. Prefer the
    // status names in new code.
    destructive: "#c0392b",
    destructiveForeground: "#ffffff",
    danger: "#c0392b",
    dangerLight: "rgba(192, 57, 43, 0.10)",
    warning: "#8a5a00",
    warningForeground: "#ffffff",
    warningLight: "rgba(217, 133, 0, 0.12)",
    success: "#15795b",
    successForeground: "#ffffff",
    successLight: "rgba(21, 121, 91, 0.10)",
    info: "#2e6b87",
    infoLight: "rgba(46, 107, 135, 0.10)",
    text: "#0b1e2c",
  },

  dark: {
    // --- ground and ink ---------------------------------------------------
    /** Deep night. Unchanged — it already matched the dashboard. */
    background: "#0c1820",
    card: "#0f2030",
    cardForeground: "#f6f0e7",
    /** Warm off-white, not pure white: a night shift is most of this app's
     *  working life and #ffffff on #0c1820 glares. */
    foreground: "#f6f0e7",
    /** Raised from the dashboard's #797c82, which is only 4.0:1 on the card
     *  ground. 6.4:1. */
    mutedForeground: "#94a3ab",

    // --- brand ------------------------------------------------------------
    /** Sky. 8.5:1 on night. Harbour is unreadable on this ground, which is
     *  why the brand swaps to its lighter half here rather than staying put. */
    primary: "#85b7d6",
    primaryForeground: "#0b1a22",
    accent: "#0b5471",
    accentForeground: "#f6f0e7",

    // --- fills ------------------------------------------------------------
    secondary: "#152a3a",
    secondaryForeground: "#85b7d6",
    muted: "#142634",
    /** In dark mode a surface is lifted by being LIGHTER than its ground, not
     *  by a shadow — a black shadow on a near-black ground says nothing. See
     *  `elevation()` in constants/theme.ts. */
    surface: "#0f2030",

    // --- structure --------------------------------------------------------
    border: "rgba(133, 183, 214, 0.14)",
    input: "#152a3a",

    // --- status (rule 2) --------------------------------------------------
    urgent: "#ef4444",
    urgentSoft: "rgba(239, 68, 68, 0.15)",
    /** Amber can carry text on this ground, so there is one value rather than
     *  the text/mark split light mode needs. */
    pending: "#f0b429",
    pendingMark: "#f0b429",
    pendingSoft: "rgba(240, 180, 41, 0.15)",
    closed: "#10b981",
    closedSoft: "rgba(16, 185, 129, 0.15)",

    // --- legacy aliases ---------------------------------------------------
    destructive: "#ef4444",
    destructiveForeground: "#ffffff",
    danger: "#ef4444",
    dangerLight: "rgba(239, 68, 68, 0.15)",
    warning: "#f0b429",
    warningForeground: "#0b1a22",
    warningLight: "rgba(240, 180, 41, 0.15)",
    success: "#10b981",
    successForeground: "#ffffff",
    successLight: "rgba(16, 185, 129, 0.15)",
    info: "#85b7d6",
    infoLight: "rgba(133, 183, 214, 0.12)",
    text: "#f6f0e7",
  },
};

export default colors;
