import { useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StatusBar,
  Text,
  TextInput,
  View,
} from "react-native";
import PropertyWorkspace from "./src/components/propertyWorkspace";
import { styles } from "./src/styles/auth.styles";

// Local browser development.
// The customer app uses localhost:8000.
// Using 127.0.0.1 here keeps their browser refresh cookies separate.
const API_URL = "http://127.0.0.1:8000";

type StaffUser = {
  id: string;
  name: string;
  email: string;
  role: "agent" | "admin";
};

type StaffSession = {
  accessToken: string;
  user: StaffUser;
};

async function readResponse(response: Response) {
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : `Request failed (${response.status}). Please try again.`,
    );
  }

  if (data === null) {
    throw new Error("The server returned an invalid response.");
  }

  return data;
}

function isStaffUser(value: unknown): value is StaffUser {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const user = value as Record<string, unknown>;

  return (
    typeof user.id === "string" &&
    typeof user.name === "string" &&
    typeof user.email === "string" &&
    (user.role === "agent" || user.role === "admin")
  );
}

async function endSession() {
  const response = await fetch(`${API_URL}/auth/logout`, {
    method: "POST",
    credentials: "include",
    headers: {
      "X-LEIA-CSRF": "1",
    },
  });

  await readResponse(response);
}

export default function App() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [session, setSession] = useState<StaffSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const busyRef = useRef(false);

  async function login() {
    const normalizedEmail = email.trim().toLowerCase();

    if (busyRef.current || !normalizedEmail || !password) {
      return;
    }

    busyRef.current = true;
    setBusy(true);
    setError("");

    try {
      const loginResponse = await fetch(`${API_URL}/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          email: normalizedEmail,
          password,
        }),
      });

      const tokens = await readResponse(loginResponse);

      if (
        typeof tokens.access_token !== "string" ||
        !tokens.access_token.trim()
      ) {
        throw new Error("The server did not return a valid access token.");
      }

      // The backend checks the current database role.
      const staffResponse = await fetch(`${API_URL}/auth/staff/me`, {
        credentials: "include",
        headers: {
          Authorization: `Bearer ${tokens.access_token}`,
        },
      });

      if (staffResponse.status === 403) {
        await endSession();

        throw new Error(
          "This account has customer access. Ask your LEIA admin for agent access.",
        );
      }

      const user: unknown = await readResponse(staffResponse);

      if (!isStaffUser(user)) {
        throw new Error("The server returned an invalid staff profile.");
      }

      setSession({
        accessToken: tokens.access_token,
        user,
      });

      setPassword("");
    } catch (caught) {
      setError(
        caught instanceof TypeError
          ? "Could not reach LEIA. Check that the backend is running on port 8000."
          : caught instanceof Error
            ? caught.message
            : "Could not sign in. Please try again.",
      );
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function logout() {
    if (busyRef.current) return;

    busyRef.current = true;
    setBusy(true);
    setError("");

    try {
      await endSession();
      setSession(null);
      setPassword("");
    } catch {
      setError("Could not sign out. Check your connection and try again.");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  const canLogin = Boolean(email.trim() && password) && !busy;

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <StatusBar barStyle="dark-content" />

      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        <View
          style={[
            styles.card,
            session ? { width: "100%", maxWidth: 960 } : undefined,
          ]}
        >
          <Text style={styles.brand}>LEIA ✦</Text>
          <Text style={styles.eyebrow}>AGENT WORKSPACE</Text>

          {error ? (
            <Text style={styles.error} accessibilityRole="alert">
              {error}
            </Text>
          ) : null}

          {session ? (
            <>
              <Text style={styles.badge}>
                {session.user.role === "admin" ? "ADMIN" : "AGENT"}
              </Text>

              <Text style={styles.heading}>Welcome, {session.user.name}.</Text>

              <Text style={styles.email}>{session.user.email}</Text>
              <PropertyWorkspace
                apiUrl={API_URL}
                accessToken={session.accessToken}
                role={session.user.role}
              />

              <Pressable
                onPress={logout}
                disabled={busy}
                accessibilityRole="button"
                accessibilityLabel="Sign out of LEIA agent workspace"
                style={[styles.button, busy && styles.disabled]}
              >
                <Text style={styles.buttonText}>
                  {busy ? "Signing out…" : "Sign out"}
                </Text>
              </Pressable>
            </>
          ) : (
            <>
              <Text style={styles.heading}>Staff sign in</Text>

              <Text style={styles.subtitle}>
                Use your assigned agent or admin account to access LEIA.
              </Text>

              <Text style={styles.label}>Email address</Text>

              <TextInput
                value={email}
                onChangeText={setEmail}
                editable={!busy}
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
                autoComplete="email"
                accessibilityLabel="Email address"
                placeholder="Enter your email"
                placeholderTextColor="#879389"
                style={styles.input}
              />

              <Text style={styles.label}>Password</Text>

              <TextInput
                value={password}
                onChangeText={setPassword}
                editable={!busy}
                secureTextEntry
                autoCapitalize="none"
                autoCorrect={false}
                autoComplete="current-password"
                accessibilityLabel="Password"
                placeholder="Enter your password"
                placeholderTextColor="#879389"
                style={styles.input}
                returnKeyType="go"
                onSubmitEditing={login}
              />

              <Pressable
                onPress={login}
                disabled={!canLogin}
                accessibilityRole="button"
                accessibilityLabel="Sign in to LEIA agent workspace"
                style={[styles.button, !canLogin && styles.disabled]}
              >
                {busy ? (
                  <ActivityIndicator color="#FFFFFF" />
                ) : (
                  <Text style={styles.buttonText}>Sign in ↗</Text>
                )}
              </Pressable>

              <Text style={styles.notice}>
                Access is assigned by your LEIA administrator.
              </Text>
            </>
          )}
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
