import { Feather } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import React, { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useColorScheme,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useAuth } from "@/context/AuthContext";
import { useColors } from "@/hooks/useColors";

export default function LoginScreen() {
  const { login } = useAuth();
  const router = useRouter();
  const c = useColors();
  const insets = useSafeAreaInsets();
  const scheme = useColorScheme();
  const isDark = scheme === "dark";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleLogin = async () => {
    if (!username.trim()) {
      setError("Enter your username");
      return;
    }
    if (!password.trim()) {
      setError("Enter your password");
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
      setError(result.error ?? "Invalid username or password.");
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
          styles.container,
          {
            paddingTop: insets.top + (Platform.OS === "web" ? 40 : 60),
            paddingBottom: insets.bottom + 40,
          },
        ]}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.brand}>
          <View style={[styles.logoWrap, { backgroundColor: c.primary }]}>
            <Feather name="eye" size={36} color="#fff" />
          </View>
          <Text style={[styles.appName, { color: c.foreground }]}>LookOut</Text>
          <Text style={[styles.tagline, { color: c.mutedForeground }]}>
            Barangay Enforcement System
          </Text>
        </View>

        <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }]}>
          <Text style={[styles.cardTitle, { color: c.foreground }]}>Officer Sign In</Text>
          <Text style={[styles.cardSubtitle, { color: c.mutedForeground }]}>
            Enter your credentials to access the dispatch board
          </Text>

          <View style={styles.fields}>
            <View>
              <Text style={[styles.label, { color: c.mutedForeground }]}>Username</Text>
              <View style={[styles.inputRow, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="shield" size={16} color={c.mutedForeground} />
                <TextInput
                  style={[styles.input, { color: c.foreground }]}
                  placeholder="e.g. juan.delacruz123"
                  placeholderTextColor={c.mutedForeground}
                  value={username}
                  onChangeText={(v) => { setUsername(v); setError(""); }}
                  autoCapitalize="none"
                  autoCorrect={false}
                  returnKeyType="next"
                />
              </View>
            </View>

            <View>
              <Text style={[styles.label, { color: c.mutedForeground }]}>Password</Text>
              <View style={[styles.inputRow, { backgroundColor: c.muted, borderColor: c.border }]}>
                <Feather name="lock" size={16} color={c.mutedForeground} />
                <TextInput
                  style={[styles.input, { color: c.foreground }]}
                  placeholder="Enter password"
                  placeholderTextColor={c.mutedForeground}
                  value={password}
                  onChangeText={(v) => { setPassword(v); setError(""); }}
                  secureTextEntry={!showPassword}
                  returnKeyType="done"
                  onSubmitEditing={handleLogin}
                />
                <Pressable onPress={() => setShowPassword((v) => !v)}>
                  <Feather name={showPassword ? "eye-off" : "eye"} size={16} color={c.mutedForeground} />
                </Pressable>
              </View>
            </View>
          </View>

          {!!error && (
            <View style={[styles.errorBox, { backgroundColor: isDark ? "#3b1219" : "#fed7d7" }]}>
              <Feather name="alert-circle" size={14} color={c.primary} />
              <Text style={[styles.errorText, { color: c.primary }]}>{error}</Text>
            </View>
          )}

          <Pressable
            style={({ pressed }) => [
              styles.loginBtn,
              { backgroundColor: c.primary, opacity: pressed || loading ? 0.8 : 1 },
            ]}
            onPress={handleLogin}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <>
                <Feather name="log-in" size={18} color="#fff" />
                <Text style={styles.loginBtnText}>Sign In</Text>
              </>
            )}
          </Pressable>

          <Pressable onPress={() => router.push("/forgot-password")} style={styles.forgotBtn}>
            <Text style={[styles.forgotText, { color: c.primary }]}>Forgot password?</Text>
          </Pressable>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  container: {
    flexGrow: 1,
    paddingHorizontal: 24,
    gap: 28,
  },
  brand: {
    alignItems: "center",
    gap: 10,
  },
  logoWrap: {
    width: 80,
    height: 80,
    borderRadius: 24,
    alignItems: "center",
    justifyContent: "center",
  },
  appName: {
    fontSize: 32,
    fontFamily: "Inter_700Bold",
    letterSpacing: -0.5,
  },
  tagline: {
    fontSize: 13,
    fontFamily: "Inter_400Regular",
    textAlign: "center",
  },
  card: {
    borderRadius: 16,
    borderWidth: 1,
    padding: 24,
    gap: 20,
  },
  cardTitle: {
    fontSize: 20,
    fontFamily: "Inter_700Bold",
  },
  cardSubtitle: {
    fontSize: 13,
    fontFamily: "Inter_400Regular",
    lineHeight: 19,
    marginTop: -12,
  },
  fields: {
    gap: 16,
  },
  label: {
    fontSize: 12,
    fontFamily: "Inter_600SemiBold",
    marginBottom: 6,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  inputRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 13,
  },
  input: {
    flex: 1,
    fontSize: 15,
    fontFamily: "Inter_400Regular",
    padding: 0,
  },
  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 12,
    borderRadius: 10,
  },
  errorText: {
    fontSize: 13,
    fontFamily: "Inter_500Medium",
    flex: 1,
  },
  loginBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
    paddingVertical: 15,
    borderRadius: 12,
  },
  loginBtnText: {
    color: "#fff",
    fontSize: 16,
    fontFamily: "Inter_600SemiBold",
  },
  forgotBtn: {
    alignItems: "center",
    paddingVertical: 4,
  },
  forgotText: {
    fontSize: 13,
    fontFamily: "Inter_600SemiBold",
  },
});
