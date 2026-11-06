import colors from "@/constants/colors";
import { elevation, radius } from "@/constants/theme";
import { useTheme } from "@/context/ThemeContext";
import type { ViewStyle } from "react-native";

type Palette = typeof colors.light & typeof colors.dark & {
  /** Which ground is on screen. Needed by anything whose treatment differs
   *  between the two rather than just its colour — depth, most of all. */
  scheme: "light" | "dark";
  radius: typeof radius;
  /** Depth, already resolved for the active scheme. Light mode gets a shadow;
   *  dark mode gets nothing, because a black shadow on a near-black ground
   *  says nothing and a surface there is lifted by being lighter instead. */
  lift: (level: 1 | 2) => ViewStyle;
};

/**
 * The active palette, plus the few tokens that depend on which one is active.
 *
 * Reads ThemeContext rather than react-native's useColorScheme, so the
 * officer's own light/dark choice wins over the phone's. The context resolves
 * "system" back to useColorScheme, so nothing is lost by going through it —
 * and every screen picks the override up without touching any of them.
 *
 * Everything that does NOT depend on the theme — the type scale, spacing,
 * radii — is imported straight from constants/theme.ts instead of being
 * threaded through here, so a style that only needs a font size doesn't have
 * to subscribe to theme changes to get one.
 */
export function useColors(): Palette {
  const { scheme } = useTheme();
  const palette = scheme === "dark" ? colors.dark : colors.light;
  return {
    ...palette,
    scheme,
    radius,
    lift: (level: 1 | 2) => elevation(level, scheme),
  };
}
