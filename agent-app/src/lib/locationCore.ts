// On-demand, foreground-only GPS capture for property drafts (GEO-M1).
// Platform-independent: no React, Expo or React Native imports, so it is tested
// directly with Node. The device adapter lives in deviceLocation.ts.
//
// Privacy: a fix is taken only when the agent taps the button. Nothing here
// watches position, runs in the background, or stores movement history.

export const ACCURACY_GOOD_M = 30;
// Fixes less precise than this cannot be saved as GPS (manual entry stays available).
export const ACCURACY_MAX_M = 100;
export const DEFAULT_TIMEOUT_MS = 20_000;
// A last known position is offered only if it is this recent.
export const LAST_KNOWN_MAX_AGE_MS = 2 * 60_000;
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
  accuracyM: number | null;
  capturedAt: string; // ISO 8601
  mocked: boolean;
  lastKnown: boolean;
};

export type Quality = "good" | "fair" | "poor";

export type CaptureResult =
  | { kind: "ok"; fix: Fix; quality: "good" | "fair" }
  | { kind: "poor_accuracy"; fix: Fix }
  | { kind: "denied"; canAskAgain: boolean }
  | { kind: "gps_off" }
  | { kind: "timeout" }
  | { kind: "unavailable"; reason: string };

export type LocationDeps = {
  servicesEnabled(): Promise<boolean>;
  requestPermission(): Promise<{ granted: boolean; canAskAgain: boolean }>;
  getCurrent(): Promise<RawFix>;
  getLastKnown(maxAgeMs: number): Promise<RawFix | null>;
  now(): number;
};

export type CaptureOptions = {
  timeoutMs?: number;
  lastKnownMaxAgeMs?: number;
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

function toFix(raw: RawFix, lastKnown: boolean, now: number): Fix {
  const timestamp = Number.isFinite(raw.timestamp) && raw.timestamp > 0 ? raw.timestamp : now;
  return {
    latitude: Number(raw.latitude.toFixed(COORDINATE_DECIMALS)),
    longitude: Number(raw.longitude.toFixed(COORDINATE_DECIMALS)),
    accuracyM:
      raw.accuracy === null || !Number.isFinite(raw.accuracy) || raw.accuracy < 0
        ? null
        : Math.round(raw.accuracy * 10) / 10,
    capturedAt: new Date(timestamp).toISOString(),
    mocked: Boolean(raw.mocked),
    lastKnown,
  };
}

function judge(raw: RawFix, lastKnown: boolean, now: number): CaptureResult {
  if (!validCoordinates(raw)) {
    return { kind: "unavailable", reason: "The device returned an invalid position." };
  }
  const fix = toFix(raw, lastKnown, now);
  const quality = classifyAccuracy(fix.accuracyM);
  if (quality === "poor") return { kind: "poor_accuracy", fix };
  return { kind: "ok", fix, quality };
}

const TIMED_OUT = Symbol("timed-out");
const defaultDelay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Takes one position fix after an explicit user action.
 * Order: location services → foreground permission → current fix (bounded by
 * timeout) → recent last-known fix as a labelled fallback.
 */
export async function captureLocation(
  deps: LocationDeps,
  options: CaptureOptions = {},
): Promise<CaptureResult> {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const maxAge = options.lastKnownMaxAgeMs ?? LAST_KNOWN_MAX_AGE_MS;
  const delay = options.delay ?? defaultDelay;

  let enabled: boolean;
  try {
    enabled = await deps.servicesEnabled();
  } catch {
    return { kind: "unavailable", reason: "Location services could not be checked." };
  }
  if (!enabled) return { kind: "gps_off" };

  let permission: { granted: boolean; canAskAgain: boolean };
  try {
    permission = await deps.requestPermission();
  } catch {
    return { kind: "unavailable", reason: "Location permission could not be requested." };
  }
  if (!permission.granted) return { kind: "denied", canAskAgain: permission.canAskAgain };

  let failure: "timeout" | "error" = "error";
  try {
    const outcome = await Promise.race([
      deps.getCurrent(),
      delay(timeoutMs).then(() => TIMED_OUT),
    ]);
    if (outcome !== TIMED_OUT) return judge(outcome as RawFix, false, deps.now());
    failure = "timeout";
  } catch {
    failure = "error";
  }

  // Fallback: a recent last-known position, clearly labelled as such.
  try {
    const last = await deps.getLastKnown(maxAge);
    if (last && deps.now() - last.timestamp <= maxAge) {
      return judge(last, true, deps.now());
    }
  } catch {
    // Ignore and report the original failure.
  }
  return failure === "timeout"
    ? { kind: "timeout" }
    : { kind: "unavailable", reason: "The device could not determine its location." };
}

/** User-facing explanation for each outcome. */
export function describeCapture(result: CaptureResult): string {
  switch (result.kind) {
    case "ok": {
      const source = result.fix.lastKnown ? "Last known location (under 2 minutes old)" : "Current location";
      const accuracy = result.fix.accuracyM === null ? "" : ` (±${Math.round(result.fix.accuracyM)} m)`;
      const mocked = result.fix.mocked ? " Warning: the device reports a simulated (mock) location." : "";
      return `${source} captured${accuracy}. Review the coordinates before saving.${mocked}`;
    }
    case "poor_accuracy":
      return `Location accuracy is too low (±${result.fix.accuracyM === null ? "unknown" : Math.round(result.fix.accuracyM)} m; ${ACCURACY_MAX_M} m or better is needed). Move outdoors or near a window and try again, or enter the coordinates manually.`;
    case "denied":
      return result.canAskAgain
        ? "Location permission was not granted. Tap the button again to allow it, or enter the coordinates manually."
        : "Location permission is turned off for LEIA. Enable it in the app's settings, or enter the coordinates manually.";
    case "gps_off":
      return "Location services are turned off. Turn on Location in your device settings, or enter the coordinates manually.";
    case "timeout":
      return "Could not get a location fix in time. Try again (outdoors works best), or enter the coordinates manually.";
    case "unavailable":
      return `${result.reason} Enter the coordinates manually or try again.`;
  }
}
