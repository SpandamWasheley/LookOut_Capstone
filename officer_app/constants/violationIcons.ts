import { Feather } from "@expo/vector-icons";

// Maps the short icon keys stored on ViolationType.icon (backend) to a
// real Feather glyph, replacing what used to be raw emoji characters.
export const VIOLATION_ICON_MAP: Record<string, keyof typeof Feather.glyphMap> = {
  moon: "moon",
  trash: "trash-2",
  noise: "volume-2",
  ban: "slash",
  alert: "alert-triangle",
  shield: "shield",
};

export function getViolationIconName(key: string): keyof typeof Feather.glyphMap {
  return VIOLATION_ICON_MAP[key] ?? "alert-circle";
}
