import colors from "@/constants/colors";
import { useTheme } from "@/context/ThemeContext";

/**
 * The active palette.
 *
 * Reads ThemeContext rather than react-native's useColorScheme, so the
 * officer's own light/dark choice wins over the phone's. The context resolves
 * "system" back to useColorScheme, so nothing is lost by going through it --
 * and every screen picks the override up without touching any of them.
 */
export function useColors() {
  const { scheme } = useTheme();
  const palette = scheme === "dark" ? colors.dark : colors.light;
  return { ...palette, radius: colors.radius };
}
