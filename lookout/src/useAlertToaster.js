import { useCallback, useEffect, useRef, useState } from "react";

import { mapAlert } from "./alertModel";

// A burst of arrivals is announced ONCE, as a count.
//
// Alerts do not trickle in evenly — one incident puts several on the board
// inside a minute, and a toast per alert would bury the screen at exactly the
// moment someone needs to look at it. Inside this window the toast counts up
// ("5 New Alerts") instead of stacking; past it, the next arrival starts a
// fresh one.
const BURST_WINDOW_MS = 90 * 1000;

// How long a toast stays up. Long enough to read and click, short enough that
// it is gone before it becomes furniture.
const VISIBLE_MS = 8000;

/** Arrival detection and toast state.
 *
 * `handleAlerts` is called with each freshly polled alert list, from inside
 * the fetch callback rather than from an effect — new alerts genuinely arrive
 * when the response does, and diffing there means no effect has to re-derive
 * "what changed" from a list it cannot tell apart from the last one.
 */
export function useAlertToaster() {
  const [toast, setToast] = useState(null);
  // null until the first list lands; a Set of alert codes after that.
  const seenRef = useRef(null);
  const burstRef = useRef({ startedAt: 0, count: 0, first: null });
  const suppressedRef = useRef(false);
  const hideTimerRef = useRef(null);

  const dismiss = useCallback(() => {
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = null;
    setToast(null);
  }, []);

  /** While true, arrivals are recorded but never shown. */
  const setSuppressed = useCallback((value) => { suppressedRef.current = value; }, []);

  const handleAlerts = useCallback((list) => {
    const active = list.filter((a) => a.status === "active");

    // The first list after a load is the backlog, not an arrival. Seed and say
    // nothing — otherwise every refresh would announce the whole queue as if
    // it had all just happened.
    if (seenRef.current === null) {
      seenRef.current = new Set(active.map((a) => a.code));
      return;
    }

    const fresh = active.filter((a) => !seenRef.current.has(a.code));
    if (fresh.length === 0) return;
    fresh.forEach((a) => seenRef.current.add(a.code));

    // Marked seen but never announced: on the Violations tab the list IS the
    // notification, and a popup there would be covering the thing it is
    // announcing. Recording it still matters, so leaving the tab later does
    // not replay what was already on screen.
    if (suppressedRef.current) return;

    const now = Date.now();
    const burst = burstRef.current;
    const continuing = now - burst.startedAt < BURST_WINDOW_MS;
    burstRef.current = {
      startedAt: continuing ? burst.startedAt : now,
      count: (continuing ? burst.count : 0) + fresh.length,
      // The first of the burst, not the latest: a toast that rewrites itself
      // to a different violation each time is harder to read than one that
      // names what started it and counts the rest.
      first: continuing && burst.first ? burst.first : mapAlert(fresh[0]),
    };

    setToast({ ...burstRef.current, shownAt: now });
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => setToast(null), VISIBLE_MS);
  }, []);

  // A pending hide must not fire into an unmounted component.
  useEffect(() => () => {
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
  }, []);

  return { toast, dismiss, handleAlerts, setSuppressed };
}
