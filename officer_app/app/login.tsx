import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { GUTTER, radius, space, type as T } from "@/constants/theme";
import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";

export default function LoginScreen() {
  const { login } = useAuth();
  const router = useRouter();
  const c = useColors();
  const insets = useSafeAreaInsets();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleLogin = async () => {
    if (!username.trim()) {
      setError("Enter your username.");
      return;
    }
    if (!password.trim()) {
      setError("Enter your password.");
      return;
    }
    setError("");
    setLoading(true);
    if (Platform.OS !== "web") Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    const result = await login(username.trim(), password);
    setLoading(false);
    if (result.ok) {
      router.replace("/(tabs)");
    } else {
      setError(result.error ?? "That username and password don't match.");
      if (Platform.OS !== "web") Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    }
  };

  return (
    <KeyboardAvoidingView
      style={[styles.root, { backgroundColor: c.background }]}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          {
            paddingTop: insets.top + (Platform.OS === "web" ? space.xxxl : 60),
            paddingBottom: insets.bottom + space.xxxl,
          },
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* The same eye as the launcher icon and the web dashboard's own logo,
            so the home screen, the sign-in and the desk all carry one mark. */}
        <View style={styles.brand}>
          <View style={[styles.mark, { backgroundColor: c.primary }]}>
            <Feather name="eye" size={34} color={c.primaryForeground} />
          </View>
          <Text style={[T.display, { color: c.foreground }]}>LookOut</Text>
          <Text style={[T.meta, { color: c.mutedForeground }]}>Barangay enforcement</Text>
        </View>

        <View
          style={[
            styles.card,
            { backgroundColor: c.card, borderColor: c.border, borderRadius: radius.card },
            c.lift(1),
          ]}
        >
          <View style={styles.head}>
            <Text style={[T.heading, { color: c.foreground }]}>Sign in</Text>
            <Text style={[T.body, { color: c.mutedForeground }]}>
              Use the account your barangay admin set up for you.
            </Text>
          </View>

          <Field
            label="Username"
            icon="user"
            placeholder="juan.delacruz123"
            value={username}
            onChangeText={(v) => {
              setUsername(v);
              setError("");
            }}
            autoCapitalize="none"
            autoCorrect={false}
            returnKeyType="next"
          />
          <Field
            label="Password"
            icon="lock"
            placeholder="Your password"
            value={password}
            onChangeText={(v) => {
              setPassword(v);
              setError("");
            }}
            secret
            returnKeyType="done"
            onSubmitEditing={handleLogin}
          />

          {/* The old box was a literal #fed7d7 pink with its text in
              c.primary — amber, back when amber was the brand. See Notice. */}
          {!!error && <Notice tone="error" message={error} />}

          <Button label="Sign in" onPress={handleLogin} icon="log-in" loading={loading} />

          <Pressable
            onPress={() => router.push("/forgot-password")}
            accessibilityRole="button"
            hitSlop={8}
            style={({ pressed }) => [styles.forgot, { opacity: pressed ? 0.6 : 1 }]}
          >
            <Text style={[T.label, { color: c.primary }]}>Forgot your password?</Text>
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  scroll: { flexGrow: 1, paddingHorizontal: GUTTER, gap: space.xxl },
  brand: { alignItems: "center", gap: space.sm },
  mark: {
    width: 76,
    height: 76,
    borderRadius: radius.card + 4,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: space.xs,
  },
  card: { borderWidth: 1, padding: space.xl, gap: space.base },
  head: { gap: space.xs, marginBottom: space.xs },
  forgot: { alignItems: "center", paddingVertical: space.xs },
});
