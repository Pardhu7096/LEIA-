import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";


export const API_URL = "http://localhost:8000";

type TokenData = {
  access_token: string;
};

type AuthContextValue = {
  token: string | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  authFetch: (path: string, options?: RequestInit) => Promise<Response>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

// Share simultaneous refresh attempts so a cookie rotates only once.
let refreshPromise: Promise<string | null> | null = null;

async function refreshToken(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const response = await fetch(`${API_URL}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "X-LEIA-CSRF": "1" },
      });

      if (response.status === 401) return null;

      if (!response.ok) {
        throw new Error("Could not restore your session.");
      }

      const data: TokenData = await response.json();
      return data.access_token;
    })().finally(() => {
      refreshPromise = null;
    });
  }

  return refreshPromise;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const signingOut = useRef(false);
  const sessionVersion = useRef(0);

  useEffect(() => {
    let active = true;

    refreshToken()
      .then((value) => {
        if (active) setToken(value);
      })
      .catch(() => {
        // Show login if the backend cannot restore the session.
        // The existing refresh cookie is left untouched.
      })
      .finally(() => {
        if (active) setReady(true);
      });

    return () => {
      active = false;
    };
  }, []);

  async function login(email: string, password: string) {
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

    setToken(data.access_token);
  }

  async function logout() {
    if (signingOut.current) return;

    signingOut.current = true;
    try {
      // Revoke the newest cookie if a request was already rotating it.
      await refreshPromise?.catch(() => null);

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
      const accessToken = await refreshToken();
      checkSession();
      setToken(accessToken);
      return accessToken;
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
      value={{ token, ready, login, logout, authFetch }}
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