import {
  DarkTheme,
  DefaultTheme,
  Stack,
  ThemeProvider,
} from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  useColorScheme,
} from "react-native";

import { AnimatedSplashOverlay } from "@/components/animated-icon";
import { AuthProvider, useAuth } from "../context/AuthContext";

void SplashScreen.preventAutoHideAsync();

function SessionNotice() {
  const { notice, dismissNotice } = useAuth();
  if (!notice) return null;

  return (
    <View style={styles.notice} accessibilityRole="alert">
      <Text style={styles.noticeText}>{notice}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Dismiss message"
        onPress={dismissNotice}
        hitSlop={8}
      >
        <Text style={styles.noticeDismiss}>OK</Text>
      </Pressable>
    </View>
  );
}

function Navigation() {
  const { token, ready, restoreError, retryRestore, continueToSignIn } = useAuth();

  if (!ready) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator size="large" color="#355E48" />
        <Text style={styles.loadingText}>Connecting to LEIA…</Text>
      </View>
    );
  }

  if (restoreError) {
    // A saved session exists but could not be checked; never treat that as signed out.
    return (
      <View style={styles.loading}>
        <Text accessibilityRole="alert" style={styles.errorText}>{restoreError}</Text>
        <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={retryRestore}>
          <Text style={styles.primaryButtonText}>Try again</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.linkButton} onPress={continueToSignIn}>
          <Text style={styles.linkButtonText}>Sign in with another account</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Protected guard={!token}>
        <Stack.Screen name="login" />
        <Stack.Screen name="register" />
      </Stack.Protected>

      <Stack.Protected guard={!!token}>
        <Stack.Screen name="index" />
        <Stack.Screen name="explore" />
        <Stack.Screen name="profile" />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  const colorScheme = useColorScheme();

  return (
    <ThemeProvider
      value={colorScheme === "dark" ? DarkTheme : DefaultTheme}
    >
      <AuthProvider>
        <AnimatedSplashOverlay />
        <Navigation />
        <SessionNotice />
      </AuthProvider>
    </ThemeProvider>
  );
}

const styles = StyleSheet.create({
  loading: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#F8FAF5",
    paddingHorizontal: 24,
    gap: 16,
  },
  loadingText: { color: "#355E48", fontSize: 15 },
  errorText: { color: "#1F2D25", fontSize: 16, textAlign: "center", lineHeight: 22 },
  primaryButton: {
    backgroundColor: "#355E48",
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 28,
  },
  primaryButtonText: { color: "#FFFFFF", fontSize: 16, fontWeight: "600" },
  linkButton: { paddingVertical: 8 },
  linkButtonText: { color: "#355E48", fontSize: 15, textDecorationLine: "underline" },
  notice: {
    position: "absolute",
    left: 16,
    right: 16,
    bottom: 32,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: "#1F2D25",
    borderRadius: 12,
    padding: 14,
  },
  noticeText: { flex: 1, color: "#FFFFFF", fontSize: 14, lineHeight: 20 },
  noticeDismiss: { color: "#BFE3CC", fontSize: 15, fontWeight: "700" },
});