import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { nativeSessionDeps } from "../lib/nativeSessionDeps";
import {
  classifyStatus,
  createNativeSession,
  IN_SESSION_RETRY,
  sendWithRetry,
  STARTUP_RETRY,
  UNREACHABLE_MESSAGE,
  type RestoreResult,
  type RetryPolicy,
} from "../lib/sessionCore";


export const API_URL =
  process.env.EXPO_PUBLIC_API_URL ||
  "https://leia-backend-staging.onrender.com";

type AuthContextValue = {
  token: string | null;
  ready: boolean;
  // Set when a saved session could not be checked because LEIA was unreachable.
  restoreError: string | null;
  // Non-blocking message, e.g. secure storage could not save the sign-in.
  notice: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  authFetch: (path: string, options?: RequestInit) => Promise<Response>;
  retryRestore: () => void;
  continueToSignIn: () => void;
  dismissNotice: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

// Android/iOS: refresh token in secure storage, sent to /auth/native/*.
// Web: unchanged HttpOnly cookie session on /auth/*.
const nativeSession = nativeSessionDeps
  ? createNativeSession({
      apiUrl: API_URL,
      fetch: (url, init) => fetch(url, init),
      ...nativeSessionDeps,
    })
  : null;

async function errorDetail(response: Response, fallback: string) {
  try {
    const data = await response.json();
    return typeof data?.detail === "string" ? data.detail : fallback;
  } catch {
    return fallback;
  }
}

async function refreshCookieSession(policy: RetryPolicy): Promise<RestoreResult> {
  const outcome = await sendWithRetry(
    (signal) =>
      fetch(`${API_URL}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "X-LEIA-CSRF": "1" },
        signal,
      }),
    policy,
  );

  if (outcome.kind === "unreachable") {
    return { kind: "unreachable", message: UNREACHABLE_MESSAGE };
  }

  const { response } = outcome;
  const status = classifyStatus(response.status);
  if (status === "invalid") return { kind: "signed_out" };
  if (status !== "ok") {
    return { kind: "unreachable", message: await errorDetail(response, UNREACHABLE_MESSAGE) };
  }

  const data = await response.json().catch(() => null);
  if (typeof data?.access_token !== "string" || !data.access_token) {
    return { kind: "unreachable", message: UNREACHABLE_MESSAGE };
  }
  return { kind: "restored", accessToken: data.access_token };
}

// Share simultaneous refresh attempts so a cookie rotates only once.
let cookieRefresh: Promise<RestoreResult> | null = null;

function refreshSession(policy: RetryPolicy): Promise<RestoreResult> {
  if (nativeSession) return nativeSession.refresh(policy);

  if (!cookieRefresh) {
    cookieRefresh = refreshCookieSession(policy).finally(() => {
      cookieRefresh = null;
    });
  }
  return cookieRefresh;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [restoreRun, setRestoreRun] = useState(0);
  const signingOut = useRef(false);
  const sessionVersion = useRef(0);

  useEffect(() => {
    let active = true;

    refreshSession(STARTUP_RETRY)
      .catch((): RestoreResult => ({ kind: "unreachable", message: UNREACHABLE_MESSAGE }))
      .then((result) => {
        if (!active) return;
        if (result.kind === "restored") {
          setToken(result.accessToken);
          setRestoreError(null);
          if (result.warning) setNotice(result.warning);
        } else if (result.kind === "signed_out") {
          setToken(null);
          setRestoreError(null);
          if (result.message) setNotice(result.message);
        } else {
          // Keep saved credentials; offer Retry instead of the login screen.
          setRestoreError(result.message);
        }
      })
      .finally(() => {
        if (active) setReady(true);
      });

    return () => {
      active = false;
    };
  }, [restoreRun]);

  function retryRestore() {
    setReady(false);
    setRestoreError(null);
    setRestoreRun((run) => run + 1);
  }

  function continueToSignIn() {
    // The saved credential is kept; a successful login replaces it.
    setRestoreError(null);
    setToken(null);
  }

  async function login(email: string, password: string) {
    if (nativeSession) {
      const result = await nativeSession.login(email, password);
      setRestoreError(null);
      setNotice(result.warning ?? null);
      setToken(result.accessToken);
      return;
    }

    const response = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        typeof data.detail === "string"
          ? data.detail
          : "Login failed. Check your details.",
      );
    }

    setRestoreError(null);
    setToken(data.access_token);
  }

  async function logout() {
    if (signingOut.current) return;

    signingOut.current = true;
    try {
      if (nativeSession) {
        // Always signs out locally; server revocation is best effort.
        const result = await nativeSession.logout();
        sessionVersion.current += 1;
        setToken(null);
        setNotice(result.error ?? null);
        return;
      }

      // Revoke the newest cookie if a request was already rotating it.
      await cookieRefresh?.catch(() => null);

      const response = await fetch(`${API_URL}/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: { "X-LEIA-CSRF": "1" },
      });

      if (!response.ok) {
        throw new Error("Could not sign out. Please try again.");
      }

      sessionVersion.current += 1;
      setToken(null);
    } finally {
      signingOut.current = false;
    }
  }

  async function authFetch(
    path: string,
    options: RequestInit = {},
  ): Promise<Response> {
    const version = sessionVersion.current;

    function checkSession() {
      if (signingOut.current || version !== sessionVersion.current) {
        throw new Error("Please log in again.");
      }
    }

    async function renewSession() {
      checkSession();
      const result = await refreshSession(IN_SESSION_RETRY);
      checkSession();

      if (result.kind === "unreachable") {
        // Temporary failure: keep the session and let the caller show the error.
        throw new Error(result.message);
      }

      if (result.kind === "signed_out") {
        setToken(null);
        return null;
      }

      if (result.warning) setNotice(result.warning);
      setToken(result.accessToken);
      return result.accessToken;
    }

    checkSession();

    async function send(accessToken: string) {
      const headers = new Headers(options.headers);
      headers.set("Authorization", `Bearer ${accessToken}`);

      return fetch(`${API_URL}${path}`, {
        ...options,
        headers,
        credentials: "include",
      });
    }

    let accessToken = token;

    if (!accessToken) {
      accessToken = await renewSession();
    }

    if (!accessToken) {
      throw new Error("Please log in again.");
    }

    let response = await send(accessToken);

    // Renew once when the access token has expired.
    if (response.status === 401) {
      accessToken = await renewSession();

      if (!accessToken) {
        throw new Error("Your session expired. Please log in again.");
      }

      response = await send(accessToken);

      if (response.status === 401) {
        checkSession();
        setToken(null);
        throw new Error("Please log in again.");
      }
    }

    return response;
  }

  return (
    <AuthContext.Provider
      value={{
        token,
        ready,
        restoreError,
        notice,
        login,
        logout,
        authFetch,
        retryRestore,
        continueToSignIn,
        dismissNotice: () => setNotice(null),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be inside AuthProvider.");
  }

  return context;
}
