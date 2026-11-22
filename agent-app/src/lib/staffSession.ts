const sessionRequest = {
  method: "POST",
  credentials: "include",
  headers: { "X-LEIA-CSRF": "1" },
} satisfies RequestInit;

export type StaffFetch = (path: string, options?: RequestInit) => Promise<Response>;

export function createStaffSession(apiUrl: string, initialToken: string) {
  let token = initialToken;
  let refreshInFlight: Promise<Response> | null = null;
  let closing = false;

  function refresh() {
    if (!refreshInFlight) {
      refreshInFlight = (async () => {
        const response = await fetch(`${apiUrl}/auth/staff/refresh`, sessionRequest);
        if (response.ok) {
          const data = await response.json();
          if (typeof data?.access_token !== "string" || !data.access_token) {
            throw new Error("The server did not return a valid access token.");
          }
          token = data.access_token;
        }
        return response;
      })().finally(() => {
        refreshInFlight = null;
      });
    }
    return refreshInFlight;
  }

  const authFetch: StaffFetch = async (path, options) => {
    function send(accessToken: string) {
      const headers = new Headers(options?.headers);
      headers.set("Authorization", `Bearer ${accessToken}`);
      return fetch(`${apiUrl}${path}`, { ...options, headers });
    }

    if (closing) throw new Error("Your session is closing.");
    const rejectedToken = token;
    const response = await send(rejectedToken);
    if (response.status !== 401 || closing || options?.signal?.aborted) {
      return response;
    }

    // Another request may already have rotated the token.
    if (rejectedToken === token) {
      const refreshed = await refresh();
      if (!refreshed.ok) return refreshed.clone();
    }

    if (closing || options?.signal?.aborted) return response;
    return send(token);
  };

  async function logout() {
    closing = true;
    try {
      // Let the browser receive the rotated cookie before revoking it.
      await refreshInFlight?.catch(() => undefined);
      const response = await fetch(`${apiUrl}/auth/staff/logout`, sessionRequest);
      if (!response.ok) throw new Error("Could not sign out.");
    } catch (error) {
      closing = false;
      throw error;
    }
  }

  return { authFetch, logout };
}

async function requestStaffSession(apiUrl: string) {
  const response = await fetch(
    `${apiUrl}/auth/staff/refresh`,
    sessionRequest,
  );

  if (response.status === 401) {
    return null;
  }

  if (!response.ok) {
    throw new Error(
      `Session restoration failed (${response.status}).`,
    );
  }

  const data = await response.json();

  if (
    typeof data?.access_token !== "string" ||
    !data.access_token.trim()
  ) {
    throw new Error(
      "The server did not return a valid access token.",
    );
  }

  return createStaffSession(apiUrl, data.access_token);
}

const startupRefreshes = new Map<
  string,
  Promise<ReturnType<typeof createStaffSession> | null>
>();

export function restoreStaffSession(apiUrl: string) {
  const existing = startupRefreshes.get(apiUrl);

  if (existing) {
    return existing;
  }

  const pending = requestStaffSession(apiUrl).finally(() => {
    startupRefreshes.delete(apiUrl);
  });

  startupRefreshes.set(apiUrl, pending);

  return pending;
}
