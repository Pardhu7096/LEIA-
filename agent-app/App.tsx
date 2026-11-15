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
import { resolveApiUrl } from "./src/lib/apiUrl";
import { createStaffSession } from "./src/lib/staffSession";
import { styles } from "./src/styles/auth.styles";

// Set EXPO_PUBLIC_API_URL (for example https://api.example.com) for staging and
// production builds. In development, match the browser host so SameSite refresh
// cookies work across local ports.
const API_URL = resolveApiUrl(
  process.env.EXPO_PUBLIC_API_URL,
  __DEV__,
  Platform.OS === "web" &&
    typeof window !== "undefined" &&
    window.location.hostname === "localhost"
    ? "http://localhost:8000"
    : "http://127.0.0.1:8000",
);

type StaffUser = {
  id: string;
  name: string;
  email: string;
  role: "agent" | "admin";
};

type StaffSession = ReturnType<typeof createStaffSession> & { user: StaffUser };

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

export default function App() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [session, setSession] = useState<StaffSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
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
      const loginResponse = await fetch(`${API_URL}/auth/staff/login`, {
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

      const staffSession = createStaffSession(API_URL, tokens.access_token);
      const staffResponse = await staffSession.authFetch("/auth/staff/me");
      const user: unknown = await readResponse(staffResponse);

      if (!isStaffUser(user)) {
        throw new Error("The server returned an invalid staff profile.");
      }

      setSession({ ...staffSession, user });

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
    if (!session || busyRef.current || workspaceBusy) return;

    busyRef.current = true;
    setBusy(true);
    setError("");

    try {
      await session.logout();
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
                authFetch={session.authFetch}
                role={session.user.role}
                onBusyChange={setWorkspaceBusy}
              />

              <Pressable
                onPress={logout}
                disabled={busy || workspaceBusy}
                accessibilityRole="button"
                accessibilityLabel="Sign out of LEIA agent workspace"
                style={[
                  styles.button,
                  (busy || workspaceBusy) && styles.disabled,
                ]}
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
