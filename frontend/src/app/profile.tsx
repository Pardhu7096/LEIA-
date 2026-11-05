import { router } from "expo-router";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useAuth } from "../context/AuthContext";
import { styles as chatStyles } from "../styles/chat.styles";

type Profile = {
  name: string;
  email: string;
};

export default function ProfileScreen() {
  const { authFetch, logout } = useAuth();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loadError, setLoadError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState("");
  const signOutInProgress = useRef(false);

  const loadProfile = useEffectEvent(async (signal: AbortSignal) => {
    const response = await authFetch("/auth/me", { signal });
    if (!response.ok) throw new Error("Profile request failed.");
    return (await response.json()) as Profile;
  });

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    loadProfile(signal)
      .then((data) => {
        if (!signal.aborted) setProfile(data);
      })
      .catch(() => {
        if (!signal.aborted) {
          setLoadError("Could not load your profile. Please try again.");
        }
      });

    return () => controller.abort();
  }, [attempt]);

  async function signOut() {
    if (signOutInProgress.current) return;

    signOutInProgress.current = true;
    setSigningOut(true);
    setSignOutError("");

    try {
      await logout();
    } catch {
      setSignOutError("Could not sign out. Please try again.");
      signOutInProgress.current = false;
      setSigningOut(false);
    }
  }

  return (
    <ScrollView
      style={chatStyles.screen}
      contentContainerStyle={styles.content}
    >
      <View style={styles.card}>
        <Pressable
          onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))}
          accessibilityRole="button"
          disabled={signingOut}
          style={[chatStyles.newSearchButton, styles.backButton]}
        >
          <Text style={chatStyles.newSearchText}>Back to search</Text>
        </Pressable>

        <Text accessibilityRole="header" style={styles.title}>Profile</Text>

        {profile ? (
          <View style={styles.details}>
            <Text style={styles.label}>Name</Text>
            <Text style={styles.value}>{profile.name}</Text>
            <Text style={styles.label}>Email address</Text>
            <Text style={styles.value}>{profile.email}</Text>
          </View>
        ) : loadError ? (
          <View style={styles.details}>
            <Text accessibilityRole="alert" style={styles.error}>{loadError}</Text>
            <Pressable
              onPress={() => {
                setLoadError("");
                setAttempt((value) => value + 1);
              }}
              accessibilityRole="button"
              disabled={signingOut}
              style={[chatStyles.newSearchButton, styles.backButton]}
            >
              <Text style={chatStyles.newSearchText}>Try again</Text>
            </Pressable>
          </View>
        ) : (
          <ActivityIndicator accessibilityLabel="Loading profile" color="#355E48" />
        )}

        {signOutError ? (
          <Text accessibilityRole="alert" style={styles.error}>{signOutError}</Text>
        ) : null}

        <Pressable
          onPress={signOut}
          accessibilityRole="button"
          disabled={signingOut}
          style={[styles.signOutButton, signingOut && chatStyles.buttonDisabled]}
        >
          <Text style={styles.signOutText}>
            {signingOut ? "Signing out…" : "Sign out"}
          </Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: {
    flexGrow: 1,
    justifyContent: "center",
    padding: 20,
  },
  card: {
    width: "100%",
    maxWidth: 480,
    alignSelf: "center",
    gap: 20,
    padding: 24,
    borderWidth: 1,
    borderColor: "#E3E8DF",
    borderRadius: 16,
    backgroundColor: "#FFFFFF",
  },
  backButton: {
    alignSelf: "flex-start",
  },
  title: {
    color: "#294638",
    fontSize: 26,
    fontWeight: "700",
  },
  details: {
    gap: 8,
  },
  label: {
    color: "#416C54",
    fontSize: 13,
    fontWeight: "600",
  },
  value: {
    color: "#294638",
    fontSize: 16,
    marginBottom: 6,
  },
  error: {
    color: "#963E33",
  },
  signOutButton: {
    borderRadius: 8,
    alignItems: "center",
    padding: 14,
    backgroundColor: "#355E48",
  },
  signOutText: {
    color: "#FFFFFF",
    fontWeight: "700",
  },
});
