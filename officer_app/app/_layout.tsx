import {
  Inter_400Regular,
  Inter_500Medium,
  Inter_600SemiBold,
  Inter_700Bold,
  useFonts,
} from "@expo-google-fonts/inter";
import { Stack, useRouter, useSegments } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import React, { useEffect } from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AssignmentProvider } from "@/context/AssignmentContext";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { ThemeProvider, useTheme } from "@/context/ThemeContext";
import { useAssignmentNotifications } from "@/hooks/useAssignmentNotifications";
import { useColors } from "@/hooks/useColors";

SplashScreen.preventAutoHideAsync();

function NavigationGuard() {
  const { officer, isLoading } = useAuth();
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    const segment0 = segments[0];
    const isProtected = segment0 === "(tabs)" || segment0 === "assignment";
    const isAuthScreen = segment0 === "login" || segment0 === "forgot-password";
    const isChangePasswordScreen = segment0 === "change-password";

    if (!officer) {
      if (isProtected || isChangePasswordScreen || segment0 === undefined) {
        router.replace("/login");
      }
      return;
    }

    if (officer.mustChangePassword) {
      if (!isChangePasswordScreen) router.replace("/change-password");
      return;
    }

    if (isAuthScreen || isChangePasswordScreen || segment0 === undefined) {
      router.replace("/(tabs)");
    }
  }, [officer, isLoading, segments]);

  return null;
}

function RootLayoutNav() {
  const c = useColors();
  const { scheme } = useTheme();
  // Registers this phone for push and opens the assignment behind a
  // tapped notification. Here rather than in a screen for the same
  // reason NavigationGuard is: there is exactly one of it.
  useAssignmentNotifications();
  return (
    <>
      <NavigationGuard />
      {/* Inverted against the background, and driven by the THEME rather than
          the phone: on a forced-dark app under a light phone the OS would
          otherwise keep drawing dark icons onto the dark header, where they
          are invisible. */}
      <StatusBar style={scheme === "dark" ? "light" : "dark"} />
      {/* contentStyle paints the scene background with the theme color so the
          stack push/pop transition doesn't flash the default (white) background
          before a screen renders — that flash read as a flicker when opening an
          assignment, especially in dark mode. */}
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: c.background } }}>
        <Stack.Screen name="login" />
        <Stack.Screen name="forgot-password" />
        <Stack.Screen name="change-password" />
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="assignment/[id]" />
      </Stack>
    </>
  );
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
  });

  useEffect(() => {
    if (fontsLoaded || fontError) {
      SplashScreen.hideAsync();
    }
  }, [fontsLoaded, fontError]);

  if (!fontsLoaded && !fontError) return null;

  return (
    <SafeAreaProvider>
      {/* Outermost of the three: the login and forgot-password screens are
          rendered before there is a session, and they need a palette too. */}
      <ThemeProvider>
        <AuthProvider>
          <AssignmentProvider>
            <GestureHandlerRootView style={{ flex: 1 }}>
              <RootLayoutNav />
            </GestureHandlerRootView>
          </AssignmentProvider>
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
