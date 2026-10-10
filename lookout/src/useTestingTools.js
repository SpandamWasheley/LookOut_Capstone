import { useEffect, useState } from "react";

// "Show testing tools" (Settings -> System, admin only, default off). It is a per-browser choice:
// it is kept in this browser only, so turning it on here does not show the tools on anyone else's
// computer. Non-admins never see the tools.
const KEY = "lookout:show-testing-tools";
const EVENT = "lookout:testing-tools-changed";

export function readTestingTools() {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function writeTestingTools(on) {
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* storage blocked: stays off */ }
  window.dispatchEvent(new Event(EVENT));
}

export function useTestingTools(isAdmin) {
  const [on, setOn] = useState(readTestingTools);

  useEffect(() => {
    const read = () => setOn(readTestingTools());
    window.addEventListener(EVENT, read);
    window.addEventListener("storage", read);
    return () => {
      window.removeEventListener(EVENT, read);
      window.removeEventListener("storage", read);
    };
  }, []);

  return isAdmin && on;
}
