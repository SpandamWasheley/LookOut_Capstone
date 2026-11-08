/** What this app calls a violation, where that differs from what the system
 * calls it.
 *
 * The detector's name describes the thing it watches for; what an officer
 * reads on their phone -- and reads out loud when handing a citation over --
 * has to name the offence the way the ordinance does.
 *
 * This started life as a slip-only override in assignment/[id].tsx, which left
 * the app disagreeing with itself: the alert an officer opened said "Improper
 * Disposal" and the citation they wrote from it said "Abandoned Waste". One
 * map, applied wherever a label is shown, is what stops that.
 *
 * Display ONLY, and that is what keeps it safe. The code, the colour and the
 * icon are untouched, and nothing submitted to the API carries a label (the
 * slip posts violation IDs), so this cannot change WHICH violation anything
 * refers to -- only what it is called. The dashboard still shows the system's
 * own name, which is the one trade-off here.
 */
export const DISPLAY_LABELS: Record<string, string> = {
  disposal: "Abandoned Waste",
};

export const displayLabel = (type: { code: string; label: string }) =>
  DISPLAY_LABELS[type.code] ?? type.label;
