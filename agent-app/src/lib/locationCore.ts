// On-demand, foreground-only GPS capture for property drafts (GEO-M1, hardened in GEO-M1.1).
// Platform-independent: no React, Expo or React Native imports, so it is tested
// directly with Node. The device adapter lives in deviceLocation.ts.
//
// Privacy and integrity rules:
// - A fix is taken only when the agent taps the button. Nothing watches position,
//   runs in the background, or stores movement history.
// - Only a FRESH fix from this request is accepted. There is no last-known/cached
//   fallback, and a fix whose own timestamp is older than FRESH_MAX_AGE_MS is refused.
// - A fix the device reports as simulated (mock location) is refused.
// - Results that arrive after a timeout or cancellation are ignored.

export const ACCURACY_GOOD_M = 30;
// Fixes less precise than this cannot be saved as GPS (manual entry stays available).
export const ACCURACY_MAX_M = 100;
export const DEFAULT_TIMEOUT_MS = 20_000;
// Freshness is judged against the device's own clock (same clock as the fix
// timestamp), so a wrong device clock cannot make a fresh fix look stale or vice versa.
export const FRESH_MAX_AGE_MS = 60_000;
export const FUTURE_TOLERANCE_MS = 60_000;
export const COORDINATE_DECIMALS = 6; // about 0.11 m

export type RawFix = {
  latitude: number;
  longitude: number;
  accuracy: number | null;
  timestamp: number; // epoch ms from the device
  mocked?: boolean;
};

export type Fix = {
  latitude: number;
  longitude: number;
  accuracyM: number;
  capturedAt: string; // ISO 8601, the device's timestamp for this fix
};

export type Quality = "good" | "fair" | "poor";

export type CaptureResult =
  | { kind: "ok"; fix: Fix; quality: "good" | "fair" }
  | { kind: "poor_accuracy"; accuracyM: number | null }
  | { kind: "mocked" }
  | { kind: "stale" }
  | { kind: "denied"; canAskAgain: boolean }
  | { kind: "gps_off" }
  | { kind: "timeout" }
  | { kind: "cancelled" }
  | { kind: "unavailable"; reason: string };

export type LocationDeps = {
  servicesEnabled(): Promise<boolean>;
  requestPermission(): Promise<{ granted: boolean; canAskAgain: boolean }>;
  getCurrent(): Promise<RawFix>;
  now(): number;
};

export type CaptureOptions = {
  timeoutMs?: number;
  signal?: AbortSignal;
  // Injected in tests; defaults to setTimeout.
  delay?: (ms: number) => Promise<void>;
};

export function classifyAccuracy(accuracyM: number | null): Quality {
  if (accuracyM === null || !Number.isFinite(accuracyM) || accuracyM < 0) return "poor";
  if (accuracyM <= ACCURACY_GOOD_M) return "good";
  if (accuracyM <= ACCURACY_MAX_M) return "fair";
  return "poor";
}

export function formatCoordinate(value: number): string {
  // Fixed precision, no trailing zeros, no exponent notation.
  return String(Number(value.toFixed(COORDINATE_DECIMALS)));
}

function validCoordinates(raw: RawFix): boolean {
  const { latitude, longitude } = raw;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return false;
  if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) return false;
  // (0, 0) is what some providers report when they have no fix.
  return !(latitude === 0 && longitude === 0);
}

function roundAccuracy(accuracy: number | null): number | null {
  if (accuracy === null || !Number.isFinite(accuracy) || accuracy < 0) return null;
  return Math.round(accuracy * 10) / 10;
}

/** Applies every acceptance rule to a fix returned for this request. */
export function judgeFix(raw: RawFix, now: number): CaptureResult {
  if (raw.mocked) return { kind: "mocked" };
  if (!validCoordinates(raw)) {
    return { kind: "unavailable", reason: "The device returned an invalid position." };
  }
  if (!Number.isFinite(raw.timestamp) || raw.timestamp <= 0) {
    return { kind: "unavailable", reason: "The device returned a position without a valid time." };
  }
  if (raw.timestamp > now + FUTURE_TOLERANCE_MS) {
    return { kind: "unavailable", reason: "The device returned a position with an inconsistent time." };
  }
  if (now - raw.timestamp > FRESH_MAX_AGE_MS) return { kind: "stale" };
  const accuracyM = roundAccuracy(raw.accuracy);
  if (accuracyM === null || classifyAccuracy(accuracyM) === "poor") {
    return { kind: "poor_accuracy", accuracyM };
  }
  return {
    kind: "ok",
    quality: classifyAccuracy(accuracyM) === "good" ? "good" : "fair",
    fix: {
      latitude: Number(raw.latitude.toFixed(COORDINATE_DECIMALS)),
      longitude: Number(raw.longitude.toFixed(COORDINATE_DECIMALS)),
      accuracyM,
      capturedAt: new Date(raw.timestamp).toISOString(),
    },
  };
}

const TIMED_OUT = Symbol("timed-out");
const CANCELLED = Symbol("cancelled");
const defaultDelay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function abortPromise(signal?: AbortSignal): Promise<typeof CANCELLED> {
  return new Promise((resolve) => {
    if (!signal) return; // never settles
    if (signal.aborted) {
      resolve(CANCELLED);
      return;
    }
    signal.addEventListener("abort", () => resolve(CANCELLED), { once: true });
  });
}

/**
 * Takes one fresh position fix after an explicit user action.
 * Order: location services, then foreground permission, then one current fix
 * bounded by timeout and cancellation. No cached or last-known fallback.
 */
export async function captureLocation(
  deps: LocationDeps,
  options: CaptureOptions = {},
): Promise<CaptureResult> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const delay = options.delay ?? defaultDelay;
  const aborted = () => Boolean(options.signal?.aborted);
  if (aborted()) return { kind: "cancelled" };

  let enabled: boolean;
  try {
    enabled = await deps.servicesEnabled();
  } catch {
    return { kind: "unavailable", reason: "Location services could not be checked." };
  }
  if (aborted()) return { kind: "cancelled" };
  if (!enabled) return { kind: "gps_off" };

  let permission: { granted: boolean; canAskAgain: boolean };
  try {
    permission = await deps.requestPermission();
  } catch {
    return { kind: "unavailable", reason: "Location permission could not be requested." };
  }
  if (aborted()) return { kind: "cancelled" };
  if (!permission.granted) return { kind: "denied", canAskAgain: permission.canAskAgain };

  const current = deps.getCurrent();
  // A fix (or failure) that settles after the race is decided is ignored.
  current.catch(() => undefined);
  let outcome: RawFix | typeof TIMED_OUT | typeof CANCELLED;
  try {
    outcome = await Promise.race([
      current,
      delay(timeoutMs).then((): typeof TIMED_OUT => TIMED_OUT),
      abortPromise(options.signal),
    ]);
  } catch {
    return { kind: "unavailable", reason: "The device could not determine its location." };
  }
  if (outcome === CANCELLED || aborted()) return { kind: "cancelled" };
  if (outcome === TIMED_OUT) return { kind: "timeout" };
  return judgeFix(outcome, deps.now());
}

/** User-facing explanation for each outcome ("" for a silent cancellation). */
export function describeCapture(result: CaptureResult): string {
  switch (result.kind) {
    case "ok":
      return `Current location captured (±${Math.round(result.fix.accuracyM)} m). Review the coordinates before saving.`;
    case "poor_accuracy":
      return `Location accuracy is too low (±${result.accuracyM === null ? "unknown" : Math.round(result.accuracyM)} m; ${ACCURACY_MAX_M} m or better is needed). The coordinates were not changed. Move outdoors or near a window and try again, or enter them manually.`;
    case "mocked":
      return "This device is reporting a simulated (mock) location, so it cannot be used. The coordinates were not changed. Turn off any mock-location app and try again, or enter the coordinates manually.";
    case "stale":
      return "The device returned an old location instead of a fresh one, so it was not used. The coordinates were not changed. Try again (outdoors works best), or enter them manually.";
    case "denied":
      return result.canAskAgain
        ? "Location permission was not granted. Tap the button again to allow it, or enter the coordinates manually."
        : "Location permission is turned off for LEIA. Enable it in the app's settings, or enter the coordinates manually.";
    case "gps_off":
      return "Location services are turned off. Turn on Location in your device settings, or enter the coordinates manually.";
    case "timeout":
      return "Could not get a fresh location fix in time. The coordinates were not changed. Try again (outdoors works best), or enter them manually.";
    case "cancelled":
      return "";
    case "unavailable":
      return `${result.reason} The coordinates were not changed. Enter them manually or try again.`;
  }
}
