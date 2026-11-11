"""The officer app's light/dark preference.

Source-level checks, because the mobile app has no test runner wired up in this
repo and these are the properties most likely to be broken by a well-meaning
edit — a screen reading the OS scheme directly, or the three-way preference
being "simplified" into a boolean switch.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase

APP = Path(settings.BASE_DIR).parent / "officer_app"


def read(*parts):
    return (APP.joinpath(*parts)).read_text(encoding="utf-8")


def code(*parts):
    """`read`, with comments stripped.

    These files EXPLAIN what they deliberately avoid — "stored in AsyncStorage,
    not the SecureStore wrapper", "not react-native's useColorScheme". A plain
    substring search cannot tell that from a use, and would fail on the very
    comment documenting the rule it is checking.
    """
    source = read(*parts)
    source = re.sub(r"/[*].*?[*]/", "", source, flags=re.S)   # block comments
    return re.sub(r"(?<!:)//.*", "", source)                 # line comments


class ThemePreferenceTests(TestCase):
    def test_the_context_exists(self):
        self.assertTrue((APP / "context" / "ThemeContext.tsx").is_file())

    def test_the_preference_is_three_way_not_a_boolean(self):
        """"Follow the phone" is a real third option. A two-position switch
        cannot express it, and whichever way it booted would silently pin the
        app to one theme on the first tap."""
        source = read("context", "ThemeContext.tsx")
        self.assertIn('"system" | "light" | "dark"', source)

    def test_system_is_the_default(self):
        """An officer who never opens the setting should still get a phone
        that dims itself at dusk."""
        source = read("context", "ThemeContext.tsx")
        self.assertIn('useState<ThemePreference>("system")', source)

    def test_the_choice_is_persisted(self):
        source = read("context", "ThemeContext.tsx")
        self.assertIn("AsyncStorage.setItem", source)
        self.assertIn("AsyncStorage.getItem", source)

    def test_it_is_not_put_in_the_keychain(self):
        """lib/api.ts's SecureStore wrapper is for tokens. Which theme somebody
        likes is not a secret, and a read failure there must not matter."""
        source = code("context", "ThemeContext.tsx")
        self.assertNotIn("SecureStore", source)
        self.assertNotIn("expo-secure-store", source)

    def test_a_storage_failure_is_survivable(self):
        """"system" is a correct answer, just not necessarily the last one the
        officer picked — not a reason to fail to boot."""
        source = read("context", "ThemeContext.tsx")
        self.assertIn(".catch(", source)

    def test_toggle_flips_what_is_actually_on_screen(self):
        """Resolved against the CURRENT scheme, so one tap always visibly
        changes something. Toggling to the system's own scheme would look like
        a dead button."""
        source = read("context", "ThemeContext.tsx")
        self.assertIn('scheme === "dark" ? "light" : "dark"', source)


class ThemeWiringTests(TestCase):
    def test_use_colors_reads_the_preference_not_the_os(self):
        """This single hook is what carries the override to every screen."""
        source = code("hooks", "useColors.ts")
        self.assertIn("useTheme", source)
        # The thing that must not come back is react-native's useColorScheme,
        # which reads the OS and so ignores the in-app preference. Banning the
        # module by name was too blunt: a TYPE-only import (ViewStyle) is
        # erased at compile time and reads nothing at all, so the test failed
        # on a file that was behaving exactly as intended.
        self.assertNotIn("useColorScheme", source)
        for line in source.splitlines():
            if 'from "react-native"' in line:
                self.assertIn("import type", line, line.strip())

    def test_the_provider_wraps_the_whole_app(self):
        source = read("app", "_layout.tsx")
        self.assertIn("<ThemeProvider>", source)

    def test_it_wraps_auth_so_the_login_screen_is_themed(self):
        """Login and forgot-password render before there is a session, and
        they need a palette too — otherwise the app changes theme the moment
        the officer signs in."""
        source = read("app", "_layout.tsx")
        self.assertLess(source.index("<ThemeProvider>"), source.index("<AuthProvider>"))

    def test_no_screen_reads_the_os_scheme_behind_the_context(self):
        """The regression this stops: one screen on useColorScheme ignores the
        officer's choice, and the app is half-dark."""
        for pattern in ("app/**/*.tsx", "components/**/*.tsx"):
            for path in APP.glob(pattern):
                with self.subTest(screen=str(path.relative_to(APP))):
                    self.assertNotIn(
                        "useColorScheme",
                        code(*path.relative_to(APP).parts),
                        "read the scheme from ThemeContext, not the OS")

    def test_the_status_bar_follows_the_theme(self):
        """On a forced-dark app under a light phone the OS keeps drawing dark
        icons onto the dark header, where they cannot be seen."""
        source = read("app", "_layout.tsx")
        self.assertIn("StatusBar", source)
        self.assertIn('scheme === "dark" ? "light" : "dark"', source)


class ThemeControlTests(TestCase):
    def test_the_profile_screen_offers_all_three(self):
        source = read("app", "(tabs)", "profile.tsx")
        for choice in ('value: "system"', 'value: "light"', 'value: "dark"'):
            self.assertIn(choice, source)

    def test_system_is_offered_first(self):
        source = read("app", "(tabs)", "profile.tsx")
        self.assertLess(source.index('value: "system"'), source.index('value: "light"'))

    def test_the_control_says_which_state_it_is_in(self):
        """"Dark" selected and "following your phone, which is dark" look
        identical on screen otherwise."""
        source = read("app", "(tabs)", "profile.tsx")
        self.assertIn("Following your phone", source)

    def test_the_options_are_announced_to_a_screen_reader(self):
        source = read("app", "(tabs)", "profile.tsx")
        self.assertIn('accessibilityRole="radio"', source)
        self.assertIn("accessibilityState={{ selected: active }}", source)


class PaletteTests(TestCase):
    def test_both_palettes_define_the_same_keys(self):
        """A key present in one and missing from the other is `undefined`
        passed to a style prop — which renders as a silent black or
        transparent, not as an error."""
        import re
        source = read("constants", "colors.ts")
        light = source[source.index("light: {"):source.index("dark: {")]
        dark = source[source.index("dark: {"):]
        keys = lambda block: set(re.findall(r"^\s{4}(\w+):", block, re.M))
        self.assertEqual(keys(light), keys(dark))
