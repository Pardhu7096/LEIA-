// Platform-independent customer session logic.
// No React, Expo or React Native imports: tested directly with Node.

export type StoredSession = {
  refreshToken: string;
  // Set before a refresh is sent and kept until it succeeds, so a retry after
  // a lost response or app restart repeats the same attempt (see backend
  // native_sessions.py). Never reused once the new token is saved.
  pendingRequestId: string | null;
};

export type SessionStorage = {
  read(): Promise<StoredSession | null>;
  write(session: StoredSession): Promise<void>;
  clear(): Promise<void>;
};

export type RestoreResult =
  | { kind: "restored"; accessToken: string; warning?: string }
  | { kind: "signed_out"; message?: string }
  | { kind: "unreachable"; message: string };

export type LoginResult = { accessToken: string; warning?: string };

export type LogoutResult = { serverRevoked: boolean; error?: string };

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export type RetryPolicy = {
  timeoutMs: number;
  // One attempt per entry plus a first immediate attempt.
  delaysMs: number[];
};

// Covers a Render free-tier cold start (often 30-60 s) before giving up.
export const STARTUP_RETRY: RetryPolicy = {
  timeoutMs: 20_000,
  delaysMs: [2_000, 5_000, 10_000, 15_000],
};

// Shorter budget while the user is actively using the app.
export const IN_SESSION_RETRY: RetryPolicy = {
  timeoutMs: 15_000,
  delaysMs: [1_500],
};

export const UNREACHABLE_MESSAGE =
  "LEIA can't be reached right now. Your sign-in is saved. Check your connection and try again.";
export const STORAGE_WRITE_WARNING =
  "You're signed in, but this device couldn't save your sign-in securely. You may need to sign in again next time you open LEIA.";
export const STORAGE_READ_MESSAGE =
  "LEIA couldn't read your saved sign-in on this device. Please sign in again.";

export type StatusClass = "ok" | "invalid" | "transient" | "failed";

export function classifyStatus(status: number): StatusClass {
  if (status >= 200 && status < 300) return "ok";
  // 401: the server rejected the credential. 422: the stored value is malformed
  // and can never succeed. Only these remove saved credentials.
  if (status === 401 || status === 422) return "invalid";
  if (status === 408 || status === 429 || status >= 500) return "transient";
  return "failed";
}

export type AttemptOutcome =
  | { kind: "response"; response: Response }
  | { kind: "unreachable" };

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function retryAfterMs(response: Response, fallback: number) {
  const value = Number(response.headers.get("retry-after"));
  return Number.isFinite(value) && value > 0 ? Math.min(value * 1000, 30_000) : fallback;
}

/** Sends a request, retrying network errors, timeouts, 408, 429 and 5xx. */
export async function sendWithRetry(
  send: (signal: AbortSignal) => Promise<Response>,
  policy: RetryPolicy,
  sleep: (ms: number) => Promise<void> = defaultSleep,
): Promise<AttemptOutcome> {
  const delays = [0, ...policy.delaysMs];

  for (let attempt = 0; attempt < delays.length; attempt += 1) {
    let wait = delays[attempt + 1];
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), policy.timeoutMs);

    try {
      const response = await send(controller.signal);
      if (classifyStatus(response.status) !== "transient") {
        return { kind: "response", response };
      }
      if (wait !== undefined) wait = retryAfterMs(response, wait);
    } catch {
      // Network failure or timeout: the request may or may not have reached the server.
    } finally {
      clearTimeout(timer);
    }

    if (wait === undefined) break;
    await sleep(wait);
  }

  return { kind: "unreachable" };
}

async function errorDetail(response: Response, fallback: string) {
  try {
    const data = await response.json();
    return typeof data?.detail === "string" ? data.detail : fallback;
  } catch {
    return fallback;
  }
}

function isTokenBody(data: unknown): data is { access_token: string; refresh_token: string } {
  const body = data as { access_token?: unknown; refresh_token?: unknown } | null;
  return (
    typeof body?.access_token === "string" && body.access_token.length > 0 &&
    typeof body?.refresh_token === "string" && body.refresh_token.length > 0
  );
}

export type NativeSessionDeps = {
  apiUrl: string;
  fetch: FetchLike;
  storage: SessionStorage;
  // Must come from a cryptographically secure source (expo-crypto).
  randomId: () => string;
  sleep?: (ms: number) => Promise<void>;
};

/**
 * Android session: refresh token kept only in secure storage (and in memory
 * while running), sent in JSON bodies to /auth/native/*.
 */
export function createNativeSession(deps: NativeSessionDeps) {
  const json = { "Content-Type": "application/json" };
  // Holds the newest credential even when secure storage failed to save it.
  let memory: StoredSession | null = null;
  let inFlight: Promise<RestoreResult> | null = null;
  // Incremented by logout and login. A refresh that started in an older
  // generation must not write its token back after the user signed out.
  let generation = 0;
  let closing = false;

  async function persist(session: StoredSession): Promise<string | undefined> {
    memory = session;
    try {
      await deps.storage.write(session);
      return undefined;
    } catch {
      return STORAGE_WRITE_WARNING;
    }
  }

  async function forget() {
    memory = null;
    try {
      await deps.storage.clear();
    } catch {
      // The server already rejected this token, so a leftover copy is unusable.
    }
  }

  async function current(): Promise<StoredSession | null | "unreadable"> {
    if (memory) return memory;
    try {
      memory = await deps.storage.read();
      return memory;
    } catch {
      return "unreadable";
    }
  }

  async function login(email: string, password: string): Promise<LoginResult> {
    const response = await deps.fetch(`${deps.apiUrl}/auth/native/login`, {
      method: "POST",
      headers: json,
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      throw new Error(await errorDetail(response, "Login failed. Check your details."));
    }

    const data = await response.json();
    if (!isTokenBody(data)) throw new Error("The server returned an invalid sign-in response.");

    generation += 1;
    const warning = await persist({ refreshToken: data.refresh_token, pendingRequestId: null });
    return { accessToken: data.access_token, warning };
  }

  async function runRefresh(policy: RetryPolicy): Promise<RestoreResult> {
    const started = generation;
    const superseded = () => closing || generation !== started;
    const session = await current();
    if (session === "unreadable") {
      // Do not delete: a later read may succeed, and a new login overwrites it.
      return { kind: "signed_out", message: STORAGE_READ_MESSAGE };
    }
    if (!session) return { kind: "signed_out" };

    // Record the attempt id before sending so a restart can repeat this attempt.
    let attempt = session;
    if (!attempt.pendingRequestId) {
      attempt = { ...session, pendingRequestId: deps.randomId() };
      if (superseded()) return { kind: "signed_out" };
      await persist(attempt);
    }

    const outcome = await sendWithRetry(
      (signal) =>
        deps.fetch(`${deps.apiUrl}/auth/native/refresh`, {
          method: "POST",
          headers: json,
          body: JSON.stringify({
            refresh_token: attempt.refreshToken,
            request_id: attempt.pendingRequestId,
          }),
          signal,
        }),
      policy,
      deps.sleep,
    );

    if (outcome.kind === "unreachable") {
      return { kind: "unreachable", message: UNREACHABLE_MESSAGE };
    }

    const { response } = outcome;
    const status = classifyStatus(response.status);

    // Signed out (or signed in again) while this request was running:
    // drop its result so a revoked token is never written back.
    if (superseded()) return { kind: "signed_out" };

    if (status === "invalid") {
      await forget();
      return { kind: "signed_out", message: "Your session ended. Please sign in again." };
    }

    if (status !== "ok") {
      // Unexpected 4xx: keep credentials, report without signing out.
      return {
        kind: "unreachable",
        message: await errorDetail(response, UNREACHABLE_MESSAGE),
      };
    }

    let data: unknown;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!isTokenBody(data)) {
      // Keep the pending attempt: retrying it returns the same token.
      return { kind: "unreachable", message: UNREACHABLE_MESSAGE };
    }

    if (superseded()) return { kind: "signed_out" };
    const warning = await persist({ refreshToken: data.refresh_token, pendingRequestId: null });
    return { kind: "restored", accessToken: data.access_token, warning };
  }

  /** One refresh at a time; concurrent callers share the result. */
  function refresh(policy: RetryPolicy = IN_SESSION_RETRY): Promise<RestoreResult> {
    if (closing) return Promise.resolve({ kind: "signed_out" });
    if (!inFlight) {
      inFlight = runRefresh(policy).finally(() => {
        inFlight = null;
      });
    }
    return inFlight;
  }

  /**
   * Revokes the session on the server (best effort) and deletes the local copy.
   *
   * Safe outcomes:
   * - local copy deleted: signed out, even if the server was unreachable
   *   (no copy of the token remains; the server session expires by itself);
   * - server revoked but local delete failed: signed out with a warning
   *   (the leftover token is dead and is removed on the next 401);
   * - both failed: throws and keeps the session, because the saved token is
   *   still valid and would silently sign the user back in on next launch.
   */
  async function logout(): Promise<LogoutResult> {
    closing = true;
    try {
      // Let a running rotation finish so the newest token is revoked.
      await inFlight?.catch(() => undefined);
      generation += 1;
      const session = await current();
      const token = session && session !== "unreadable" ? session.refreshToken : null;
      let serverRevoked = false;

      if (token) {
        const outcome = await sendWithRetry(
          (signal) =>
            deps.fetch(`${deps.apiUrl}/auth/native/logout`, {
              method: "POST",
              headers: json,
              body: JSON.stringify({ refresh_token: token }),
              signal,
            }),
          IN_SESSION_RETRY,
          deps.sleep,
        );
        serverRevoked = outcome.kind === "response" && outcome.response.ok;
      }

      try {
        await deps.storage.clear();
      } catch {
        if (token && !serverRevoked) {
          throw new Error(
            "Sign-out didn't finish: LEIA couldn't be reached and this device couldn't remove the saved sign-in. Check your connection and try again.",
          );
        }
        memory = null;
        return {
          serverRevoked,
          error: "Signed out, but this device couldn't remove the saved sign-in.",
        };
      }

      memory = null;
      return { serverRevoked };
    } finally {
      closing = false;
    }
  }

  return { login, refresh, logout };
}

export type NativeSession = ReturnType<typeof createNativeSession>;

/** Validates what secure storage returned; anything else is treated as absent. */
export function parseStoredSession(raw: string | null): StoredSession | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<StoredSession>;
    if (typeof value.refreshToken !== "string" || !value.refreshToken) return null;
    const pending = value.pendingRequestId;
    return {
      refreshToken: value.refreshToken,
      pendingRequestId: typeof pending === "string" && pending ? pending : null,
    };
  } catch {
    return null;
  }
}
