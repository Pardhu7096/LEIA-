const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1"]);

/**
 * Resolve the API base URL from EXPO_PUBLIC_API_URL.
 *
 * Development builds fall back to the local backend. Release builds must be
 * given an explicit HTTPS URL, so a store or web build can never silently
 * point at localhost or send sign-in details over plain HTTP.
 */
export function resolveApiUrl(
  configured: string | undefined,
  isDevelopment: boolean,
  developmentFallback: string,
): string {
  const value = (configured ?? "").trim().replace(/\/+$/, "");
  if (!value) {
    if (isDevelopment) return developmentFallback;
    throw new Error("EXPO_PUBLIC_API_URL is required for release builds.");
  }
  const match = /^(https?):\/\/([^/:?#\s]+)(?::\d+)?$/i.exec(value);
  if (!match) {
    throw new Error("EXPO_PUBLIC_API_URL must look like https://api.example.com (no path).");
  }
  const secure = match[1].toLowerCase() === "https";
  if (!secure && !isDevelopment && !LOCAL_HOSTS.has(match[2].toLowerCase())) {
    throw new Error("EXPO_PUBLIC_API_URL must use https:// in release builds.");
  }
  return value;
}
