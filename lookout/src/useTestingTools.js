import { useEffect, useState } from "react";
import { getSettings } from "./api";

// "Show testing tools" (Settings -> System, admin only, default off). Non-admins never see the
// tools, so only an admin asks. Re-reads when Settings is saved.
export function useTestingTools(isAdmin) {
  const [on, setOn] = useState(false);

  useEffect(() => {
    if (!isAdmin) return undefined;
    let alive = true;
    const read = () =>
      getSettings()
        .then((s) => { if (alive) setOn(!!s.show_testing_tools); })
        .catch(() => {});
    read();
    window.addEventListener("lookout:settings-saved", read);
    return () => {
      alive = false;
      window.removeEventListener("lookout:settings-saved", read);
    };
  }, [isAdmin]);

  return isAdmin && on;
}
