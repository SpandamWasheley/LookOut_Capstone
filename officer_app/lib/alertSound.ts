import * as Haptics from "expo-haptics";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { CHANNEL_ID } from "@/lib/notifications";

/**
 * The sound a new assignment makes when the app is the one that found it.
 *
 * Push already makes a sound: core/push.py sends `sound: "default"` and the
 * assignments channel is created at HIGH importance, so a dispatched
 * violation arrives with a chime and a heads-up banner. That covers the
 * officer whose phone is in their pocket.
 *
 * It does not cover the officer standing there with the app open, because
 * push is allowed to fail and routinely does: a declined permission, a build
 * without FCM credentials, a simulator, an expired token the server has not
 * been told about. In every one of those cases AssignmentContext's four-second
 * poll still finds the assignment and the screen still updates — silently.
 * An officer looking at the map while a holdup is assigned to them learned
 * about it by glancing back at the list.
 *
 * WHY THIS IS A NOTIFICATION AND NOT A SOUND FILE: playing audio needs
 * expo-audio, a native module, and this app ships to officers as an EAS
 * Update over an existing binary — adding one would mean a new build on every
 * phone before anybody heard anything. expo-notifications is already in the
 * binary, and presenting locally through it is better than a bare sound
 * anyway: it uses the chime the officer's own channel is set to, it obeys
 * their Do Not Disturb and their channel settings, it leaves a line in the
 * shade if they missed it, and tapping it routes to the violation through the
 * same listener a real push uses. The haptic beside it is for the phone that
 * is on silent, where a sound is not the channel that reaches anyone.
 */

// How long after a real push we stay quiet. The push itself made the sound,
// and the poll is certain to find the same assignment within four seconds of
// it, so without this every dispatched violation would be announced twice to
// an officer holding their phone.
//
// The opposite race — the poll finding it before the push lands — is not
// handled, because it cannot be: an OS-presented push cannot be called back.
// It needs push to be slower than the poll, and the result is one extra
// chime, not a missed one.
const PUSH_GRACE_MS = 12000;

let lastPushAt = 0;

/** Called when a REAL push lands, so the poll does not say it again. */
export function markPushArrived() {
  lastPushAt = Date.now();
}

/** Data flag on the notifications this module presents, so the push listener
 *  can tell its own echo from an actual push. */
export const LOCAL_FLAG = "lookoutLocal";

export interface AnnounceInput {
  /** How many assignments arrived in this poll. */
  count: number;
  /** The first one, which the notification names and a tap opens. */
  id: string;
  label: string;
  location: string;
}

/**
 * Announce assignments the poll found.
 *
 * Never throws and never awaits anything the caller depends on: it is called
 * from inside the poll's response handler, and a phone that cannot present a
 * notification must not take the assignment list down with it.
 */
export async function announceNewAssignments({ count, id, label, location }: AnnounceInput) {
  if (count < 1) return;
  if (Date.now() - lastPushAt < PUSH_GRACE_MS) return;

  // Fires even where a notification cannot be presented (permission declined
  // is the common one, and it is also the case that needs this most), so it
  // goes first and separately.
  try {
    await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
  } catch {
    // No vibrator, or a web build. Nothing to do about it.
  }

  // A burst is ONE notification. One incident can assign several violations
  // inside a minute, and a phone buzzing five times says nothing the count
  // does not say once.
  const many = count > 1;
  try {
    await Notifications.scheduleNotificationAsync({
      content: {
        title: many ? `${count} violations assigned to you` : `${label} assigned to you`,
        body: many ? "Open LookOut to see them" : location,
        sound: true,
        // The officer taps this to go somewhere, not to clear a count.
        data: { alertId: id, [LOCAL_FLAG]: true },
      },
      // An object with only a channelId is expo-notifications' "deliver now"
      // trigger. The channel is the one core/push.py names, so a local
      // announcement and a real push look and sound identical, and silencing
      // one in Android settings silences both — which is what an officer who
      // turns the channel down is asking for.
      trigger: Platform.OS === "android" ? { channelId: CHANNEL_ID } : null,
    });
  } catch {
    // Permission declined, or no notification service on this device. The
    // haptic above has already fired and the list on screen is correct.
  }
}
