import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import * as api from "@/lib/api";

/**
 * Getting this phone an address, and keeping the server's copy of it honest.
 *
 * Why any of this exists: AssignmentContext polls `/alerts/` every four
 * seconds, but that timer only runs while the app is open in the foreground.
 * An officer with the app backgrounded or killed — which is most officers,
 * most of the time — learned about an assignment when they next happened to
 * open it. A push notification is the only thing that reaches a phone nobody
 * is looking at.
 *
 * It stays a NUDGE, not a data channel: the poll remains the source of truth
 * for what an officer is assigned, and every failure path here is silent
 * because the app must keep working for an officer who declined the
 * permission, or is on a device that cannot receive push at all.
 */

/** Must match core/push.py's CHANNEL_ID, or Android files these under a
 *  default channel the officer never configured and may have silenced. */
export const CHANNEL_ID = "assignments";

/**
 * The token currently registered with the server, so sign-out can withdraw
 * the exact one it registered rather than asking Expo for it again — the app
 * may be offline by then, and Expo is a network call.
 *
 * PERSISTED, not just held in memory. A module variable is empty after a
 * restart, so an officer who relaunched and signed out before registration
 * had finished — or whose registration failed because they had no signal —
 * withdrew nothing, and the server went on delivering to a handset nobody
 * was signed into. On a shared duty phone that hands the next person the
 * type, place and evidence level of live violations.
 *
 * AsyncStorage rather than the SecureStore wrapper lib/api.ts uses for
 * tokens: an Expo push token is an address, not a credential. Holding it
 * lets you send that device a notification, not read anything from it.
 */
const TOKEN_KEY = "lookout.pushToken";

let registered: string | null = null;

async function remember(token: string | null) {
  registered = token;
  try {
    if (token) await AsyncStorage.setItem(TOKEN_KEY, token);
    else await AsyncStorage.removeItem(TOKEN_KEY);
  } catch {
    // Unreadable storage degrades this to the old in-memory behaviour
    // rather than failing a sign-in over it.
  }
}

async function recall(): Promise<string | null> {
  if (registered) return registered;
  try {
    return await AsyncStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/**
 * How a notification behaves when it lands while the app is already open.
 *
 * It still shows. An officer may well have the app open on the history tab,
 * or be part-way through a citation, when a new violation is assigned —
 * suppressing it then would mean the one moment they are definitely holding
 * the phone is the one moment they are not told.
 */
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    // The app has no unread count to mirror, and a badge nothing ever clears
    // is worse than no badge.
    shouldSetBadge: false,
  }),
});

/**
 * The Android channel this app's notifications belong to.
 *
 * Created client-side and named by the server in every message. Importance
 * HIGH is what makes it a heads-up banner with a sound rather than a silent
 * line in the shade — an assignment is a thing to act on now. The officer can
 * still turn that down in Android settings, which is exactly the point of
 * giving it its own channel.
 */
async function ensureChannel() {
  if (Platform.OS !== "android") return;
  await Notifications.setNotificationChannelAsync(CHANNEL_ID, {
    name: "Assignments",
    description: "When dispatch assigns you a violation.",
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: "#0b5471",
  });
}

/** The EAS project this build belongs to. getExpoPushTokenAsync cannot work
 *  it out on its own in a standalone build, and returns a confusing error
 *  rather than a clear one when it is missing. */
function projectId(): string | undefined {
  return (
    Constants.expoConfig?.extra?.eas?.projectId ??
    (Constants as unknown as { easConfig?: { projectId?: string } }).easConfig?.projectId
  );
}

export type PushStatus = "registered" | "denied" | "unsupported" | "failed";

/**
 * Ask for permission, get this install's Expo token, and tell the server.
 *
 * Called on every sign-in and every launch, because an Expo token is not
 * permanent — it can be reissued when the app updates or is restored onto a
 * new handset, and the server's copy would then point at nothing.
 *
 * Never throws. Returns what happened so the Profile screen can tell the
 * officer why their phone is quiet.
 */
export async function registerForPush(): Promise<PushStatus> {
  // A simulator has no push service behind it; asking produces an error that
  // looks like a bug rather than the expected answer.
  if (!Device.isDevice) return "unsupported";

  try {
    await ensureChannel();

    const existing = await Notifications.getPermissionsAsync();
    let granted = existing.granted;
    // Only ask if we may: once an officer has said no, asking again on every
    // launch is nagging, and on Android the OS stops showing the prompt
    // anyway. `canAskAgain` is how we tell the two apart.
    if (!granted && existing.canAskAgain) {
      granted = (await Notifications.requestPermissionsAsync()).granted;
    }
    if (!granted) return "denied";

    const { data: token } = await Notifications.getExpoPushTokenAsync({
      projectId: projectId(),
    });

    await api.registerPushToken(token, Platform.OS === "ios" ? "ios" : "android");
    await remember(token);
    return "registered";
  } catch {
    // No FCM credentials on the build, no network, or a server that rejected
    // the token. None of it should interrupt an officer signing in.
    return "failed";
  }
}

/**
 * What this phone's state is, without asking for anything.
 *
 * Push fails silently by design — an officer who declined the permission, or
 * whose build has no FCM credentials, simply never hears a sound. That is
 * correct behaviour and a terrible thing to leave invisible, so the Profile
 * screen reports it.
 */
export async function currentPushStatus(): Promise<PushStatus> {
  if (!Device.isDevice) return "unsupported";
  try {
    const { granted } = await Notifications.getPermissionsAsync();
    if (!granted) return "denied";
    // Permission is held but the server was never told — no credentials on
    // the build, or the registration call did not get through.
    return (await recall()) ? "registered" : "failed";
  } catch {
    return "failed";
  }
}

/**
 * Withdraw this device before the session ends.
 *
 * Has to happen while the access token is still usable, which is why
 * AuthContext calls it BEFORE api.logout(). Without it a handset passed to
 * the next officer on shift keeps receiving the previous one's assignments —
 * the server would have no way to know the account had gone.
 */
export async function forgetDevice() {
  const token = await recall();
  await remember(null);
  if (!token) return;
  try {
    await api.unregisterPushToken(token);
  } catch {
    // Offline sign-out: the request never left the phone. The server-side
    // backstop is /auth/logout/, which clears this account's devices from
    // the refresh token alone — but that is the same network, so it is gone
    // too. What remains is the upsert: the next officer to sign in on this
    // handset takes the token over, because the server keys on the token
    // rather than the account. A handset signed out of while offline and
    // then never used again is the one case still open.
  }
}
