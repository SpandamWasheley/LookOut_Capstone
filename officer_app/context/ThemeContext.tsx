import AsyncStorage from "@react-native-async-storage/async-storage";
import React, { createContext, useContext, useEffect, useState } from "react";
import { useColorScheme } from "react-native";

/**
 * Light / dark, chosen by the officer rather than only by the phone.
 *
 * THREE values, not a boolean. "system" is a real preference and the default:
 * a phone that dims itself at dusk should take the app with it, and an officer
 * who never opens this setting gets that for free. A plain on/off switch
 * cannot express it — whichever position it booted into would silently pin the
 * app to one theme the first time it was touched.
 *
 * Stored in AsyncStorage, not the SecureStore wrapper lib/api.ts uses for
 * tokens: which theme somebody likes is not a secret, and the Keychain is for
 * things that are. It also means a read failure is survivable — the preference
 * simply falls back to the system, which is what it would have been anyway.
 */
export type ThemePreference = "system" | "light" | "dark";

const STORAGE_KEY = "lookout.theme";

interface ThemeContextValue {
  /** What the officer asked for. */
  preference: ThemePreference;
  /** What is actually on screen, once "system" has been resolved. */
  scheme: "light" | "dark";
  setPreference: (next: ThemePreference) => void;
  /** Light <-> dark in one tap, for the header button. */
  toggle: () => void;
  /** False until the stored preference has been read. */
  ready: boolean;
}

const ThemeContext = createContext<ThemeContextValue>({
  preference: "system",
  scheme: "light",
  setPreference: () => {},
  toggle: () => {},
  ready: false,
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const system = useColorScheme();
  const [preference, setPreferenceState] = useState<ThemePreference>("system");
  const [ready, setReady] = useState(false);

  // Read once on mount. Until it lands the app renders on the SYSTEM scheme,
  // which is both the default and the most likely stored value — so the common
  // case has no flash of the wrong theme to correct.
  useEffect(() => {
    let cancelled = false;
    AsyncStorage.getItem(STORAGE_KEY)
      .then((stored) => {
        if (cancelled) return;
        if (stored === "light" || stored === "dark" || stored === "system") {
          setPreferenceState(stored);
        }
      })
      .catch(() => {
        // Unreadable storage is not worth failing over: "system" is a correct
        // answer, just not necessarily the officer's last one.
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const scheme: "light" | "dark" =
    preference === "system" ? (system === "dark" ? "dark" : "light") : preference;

  const setPreference = (next: ThemePreference) => {
    // Applied immediately and persisted in the background. A theme that waited
    // on a disk write before repainting would feel broken on a slow device,
    // and a failed write costs the officer one re-tap next launch.
    setPreferenceState(next);
    AsyncStorage.setItem(STORAGE_KEY, next).catch(() => {});
  };

  // Resolves "system" to the opposite of what is CURRENTLY showing, so one tap
  // always visibly changes something. Toggling to the system's own scheme
  // would otherwise look like a dead button.
  const toggle = () => setPreference(scheme === "dark" ? "light" : "dark");

  return (
    <ThemeContext.Provider value={{ preference, scheme, setPreference, toggle, ready }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
