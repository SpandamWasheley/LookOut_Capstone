import * as Updates from "expo-updates";
import { useEffect, useRef } from "react";

/**
 * Take a published update on THIS launch instead of the next one.
 *
 * expo-updates' default is `checkAutomatically: ON_LOAD` with no wait: a launch
 * finds the update, downloads it in the background, and the officer goes on
 * running the old bundle until they happen to cold-start again. Two launches,
 * both of which must be real process kills — swiping a phone out of recents
 * usually is not one. In practice that means a fix can sit published and
 * undelivered for days while the app looks broken, which is exactly what
 * happened with the assignment-list fix: the server served the manifest
 * correctly and the handset simply never asked twice.
 *
 * This closes the gap by doing the second launch itself. On startup it asks,
 * fetches if there is something, and reloads straight into it.
 *
 * Deliberate choices:
 *
 * ON LAUNCH ONLY, never on resume. `reloadAsync` restarts the app from
 * scratch. At startup there is nothing to lose; mid-shift it would throw away
 * whatever an officer was part-way through — a half-typed violator name on a
 * citation they are standing in front of someone to file. A notification about
 * an available update would be the alternative, and an officer tapping
 * "restart now" during a callout is not a thing to design for.
 *
 * SILENT ON EVERY FAILURE. No signal, Expo unreachable, a corrupt download:
 * the officer still has the bundle that is already on the phone, which works.
 * An update is an improvement, never a precondition for doing the job, so
 * nothing here is allowed to block, warn or retry.
 *
 * NOT IN DEVELOPMENT. `Updates.checkForUpdateAsync` throws in an Expo Go or
 * dev-client session, where the bundle comes from Metro and there is no update
 * to find. `isEnabled` is false in exactly those cases.
 *
 * ONCE PER PROCESS. The ref guards against a re-render or a Fast Refresh
 * running it twice, which on a slow connection would mean two downloads of the
 * same bundle.
 */
export function useAutoUpdate() {
  const asked = useRef(false);

  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    // False in Expo Go, in a dev client, and in any build made with updates
    // switched off. Calling through anyway is an exception, not a no-op.
    if (!Updates.isEnabled) return;

    (async () => {
      try {
        const { isAvailable } = await Updates.checkForUpdateAsync();
        if (!isAvailable) return;
        const { isNew } = await Updates.fetchUpdateAsync();
        // `isNew` false means what was fetched is what is already running —
        // reloading on that would be a restart loop at every launch.
        if (isNew) await Updates.reloadAsync();
      } catch {
        // See above: the officer keeps the bundle they have.
      }
    })();
  }, []);
}
