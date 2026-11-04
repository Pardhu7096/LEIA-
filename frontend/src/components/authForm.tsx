import { Link, router } from "expo-router";
import { useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { API_URL, useAuth } from "../context/AuthContext";

export default function AuthForm({
  mode,
}: {
  mode: "login" | "register";
}) {
  const registering = mode === "register";
  const { login } = useAuth();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
  if (busy) return;

  if (
    !email.trim() ||
    !password ||
    (registering && !name.trim())
  ) {
    setError("Please complete all fields.");
    return;
  }

  if (registering && password.length < 12) {
    setError("Use a password with at least 12 characters.");
    return;
  }

  setBusy(true);
  setError("");

  try {
    if (registering) {
      const response = await fetch(`${API_URL}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          email: email.trim(),
          password,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "Check your email and registration details.",
        );
      }

      // Account created. Go to login without creating a session.
      router.replace("/login");
      return;
    }

    // Only the login form creates a session.
    await login(email.trim(), password);
  } catch (err) {
    setError(
      err instanceof Error ? err.message : "Please try again.",
    );
  } finally {
    setBusy(false);
  }
}

  return (
    <ScrollView
      contentContainerStyle={s.page}
      keyboardShouldPersistTaps="handled"
    >
      <View style={s.card}>
        <Text style={s.brand}>LEIA ✦</Text>
        <Text style={s.heading}>
          {registering ? "Find a place that feels like you." : "Welcome back."}
        </Text>
        <Text style={s.subtitle}>
          {registering ? "Create your account to begin." : "Log in to continue."}
        </Text>

        {registering && (
          <TextInput
            style={s.input}
            placeholder="Your name"
            accessibilityLabel="Your name"
            value={name}
            onChangeText={setName}
            editable={!busy}
            maxLength={100}
            autoComplete="name"
          />
        )}

        <TextInput
          style={s.input}
          placeholder="Email"
          accessibilityLabel="Email"
          value={email}
          onChangeText={setEmail}
          editable={!busy}
          keyboardType="email-address"
          autoCapitalize="none"
          autoCorrect={false}
          autoComplete="email"
        />

        <TextInput
          style={s.input}
          placeholder={registering ? "Password (12+ characters)" : "Password"}
          accessibilityLabel="Password"
          value={password}
          onChangeText={setPassword}
          editable={!busy}
          secureTextEntry
          maxLength={128}
          autoComplete={registering ? "new-password" : "current-password"}
          onSubmitEditing={submit}
        />

        {!!error && (
          <Text style={s.error} accessibilityRole="alert">
            {error}
          </Text>
        )}

        <Pressable
          style={[s.button, busy && { opacity: 0.5 }]}
          onPress={submit}
          disabled={busy}
          accessibilityRole="button"
        >
          <Text style={s.buttonText}>
            {busy ? "Please wait…" : registering ? "Create account" : "Log in"}
          </Text>
        </Pressable>

        {!busy && (
          <Link
            href={registering ? "/login" : "/register"}
            style={s.link}
          >
            {registering
              ? "Already have an account? Log in"
              : "New to LEIA? Create an account"}
          </Link>
        )}
      </View>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  page: {
    flexGrow: 1,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#F8FAF5",
    padding: 24,
  },
  card: { width: "100%", maxWidth: 420, gap: 16 },
  brand: { fontSize: 28, fontWeight: "700", color: "#284F40" },
  heading: { fontSize: 30, fontWeight: "600", color: "#284F40" },
  subtitle: { color: "#627467", marginBottom: 12 },
  input: {
    backgroundColor: "#FFFFFF",
    borderWidth: 1,
    borderColor: "#D9E2D4",
    borderRadius: 12,
    padding: 16,
    color: "#284F40",
    fontSize: 16,
  },
  button: {
    backgroundColor: "#365F4C",
    padding: 16,
    borderRadius: 12,
    alignItems: "center",
  },
  buttonText: { color: "#FFFFFF", fontWeight: "600" },
  link: { color: "#365F4C", textAlign: "center", padding: 8 },
  error: { color: "#A82D2D" },
});