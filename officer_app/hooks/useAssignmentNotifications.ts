import * as Notifications from "expo-notifications";
import { useRouter } from "expo-router";
import { useEffect, useRef, useState } from "react";

import { useAssignments } from "@/context/AssignmentContext";
import { useAuth } from "@/context/AuthContext";
import { registerForPush } from "@/lib/notifications";

/**
 * Registers this phone for push, and opens the right assignment when one is
 * tapped.
 *
 * Mounted once, in app/_layout.tsx, next to NavigationGuard — the same reason
 * that guard lives there applies here: there is exactly one of each, and
 * neither belongs to any single screen.
 */
export function useAssignmentNotifications() {
  const { officer } = useAuth();
  const { refreshAssignments } = useAssignments();
  const router = useRouter();

  // An alert id from a tapped notification, held until it is safe to navigate.
  const [pending, setPending] = useState<string | null>(null);
  // The cold-start response is returned by getLastNotificationResponseAsync on
  // every call, not just the first, so without this it would re-navigate on
  // every re-render that re-ran the effect.
  const coldStartHandled = useRef(false);

  // Register on sign-in and on every launch with a session already restored.
  // Keyed on the username so switching accounts on a shared handset
  // re-registers the token against whoever is now signed in.
  useEffect(() => {
    if (!officer) return;
    registerForPush();
  }, [officer?.username]);

  useEffect(() => {
    const take = (response: Notifications.NotificationResponse | null) => {
      const id = response?.notification.request.content.data?.alertId;
      if (typeof id === "string" && id) setPending(id);
    };

    // Tapped while the app was dead. This is the common case for an officer
    // who was not holding their phone — which is the whole point of this
    // feature — so it cannot be left to the listener below, which only sees
    // taps that arrive while the app is already running.
    if (!coldStartHandled.current) {
      coldStartHandled.current = true;
      Notifications.getLastNotificationResponseAsync().then(take);
    }

    const tapped = Notifications.addNotificationResponseReceivedListener(take);
    // Arriving (not tapped) while the app is open: pull the list straight away
    // so the new violation is already there if they switch to it themselves.
    const arrived = Notifications.addNotificationReceivedListener(() => {
      refreshAssignments();
    });

    return () => {
      tapped.remove();
      arrived.remove();
    };
  }, [refreshAssignments]);

  // Navigate only once there is somewhere to navigate to. A tap can land
  // before the session has been restored, and NavigationGuard would redirect
  // on top of us mid-flight; holding the id until the officer is actually
  // signed in and past the forced password change avoids racing it.
  useEffect(() => {
    if (!pending || !officer || officer.mustChangePassword) return;
    setPending(null);
    router.push(`/assignment/${pending}`);
  }, [pending, officer, router]);
}
