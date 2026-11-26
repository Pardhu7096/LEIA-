import assert from "node:assert/strict";
import test from "node:test";
import {
  ACCURACY_MAX_M,
  FRESH_MAX_AGE_MS,
  captureLocation,
  classifyAccuracy,
  describeCapture,
  formatCoordinate,
  judgeFix,
} from "../src/lib/locationCore.ts";

const NOW = Date.parse("2026-11-26T05:00:00Z");
const fix = (over = {}) => ({
  latitude: 17.72310456,
  longitude: 83.30124789,
  accuracy: 12.34,
  timestamp: NOW - 1_000,
  mocked: false,
  ...over,
});

// Fake device. Records every call so tests can prove what was (not) asked.
// getLastKnown is present only to prove it is never used (GEO-M1.1, B2).
function device(over = {}) {
  const calls = [];
  const deps = {
    servicesEnabled: async () => (calls.push("services"), true),
    requestPermission: async () => (calls.push("permission"), { granted: true, canAskAgain: true }),
    getCurrent: async () => (calls.push("current"), fix()),
    getLastKnown: async () => (calls.push("lastKnown"), fix()),
    now: () => NOW,
    ...over,
  };
  return { deps, calls };
}
const never = () => new Promise(() => {});
const instant = () => Promise.resolve();

test("successful capture returns rounded coordinates, accuracy and the fix's own timestamp", async () => {
  const { deps, calls } = device();
  const result = await captureLocation(deps);
  assert.equal(result.kind, "ok");
  assert.equal(result.quality, "good");
  assert.deepEqual(result.fix, {
    latitude: 17.723105,
    longitude: 83.301248,
    accuracyM: 12.3,
    capturedAt: "2026-11-26T04:59:59.000Z",
  });
  assert.deepEqual(calls, ["services", "permission", "current"]);
  assert.match(describeCapture(result), /Current location captured \(±12 m\)/);
});

// ---- B1: mock locations --------------------------------------------------
test("B1: a mocked fix is refused and carries no coordinates", async () => {
  const { deps } = device({ getCurrent: async () => fix({ mocked: true }) });
  const result = await captureLocation(deps);
  assert.deepEqual(result, { kind: "mocked" });
  assert.ok(!("fix" in result), "no coordinates are exposed for a mocked fix");
  assert.match(describeCapture(result), /simulated \(mock\) location.*not changed.*manually/s);
});

test("B1: mocked is refused even when it is otherwise perfect", () => {
  assert.equal(judgeFix(fix({ mocked: true, accuracy: 3 }), NOW).kind, "mocked");
});

// ---- B2: cached / last-known positions ------------------------------------
test("B2: last-known positions are never requested, even on timeout or error", async () => {
  for (const getCurrent of [never, async () => { throw new Error("provider unavailable"); }]) {
    const { deps, calls } = device({ getCurrent });
    const result = await captureLocation(deps, { delay: instant });
    assert.ok(["timeout", "unavailable"].includes(result.kind));
    assert.ok(!calls.includes("lastKnown"), "no cached fallback");
  }
});

test("B2: a stale fix returned by the device is refused", async () => {
  const { deps } = device({ getCurrent: async () => fix({ timestamp: NOW - FRESH_MAX_AGE_MS - 1 }) });
  const result = await captureLocation(deps);
  assert.deepEqual(result, { kind: "stale" });
  assert.match(describeCapture(result), /old location.*not changed/s);
});

test("B2: a fix just inside the freshness window is accepted", () => {
  assert.equal(judgeFix(fix({ timestamp: NOW - FRESH_MAX_AGE_MS }), NOW).kind, "ok");
});

test("B2: a late fix that arrives after the timeout is ignored", async () => {
  let resolveLate;
  const late = new Promise((resolve) => { resolveLate = resolve; });
  const { deps } = device({ getCurrent: () => late });
  const result = await captureLocation(deps, { delay: instant });
  assert.deepEqual(result, { kind: "timeout" });
  resolveLate(fix());
  await late;
  assert.deepEqual(result, { kind: "timeout" }, "result does not change afterwards");
});

test("B2: a late failure after the timeout does not throw (no unhandled rejection)", async () => {
  let rejectLate;
  const late = new Promise((_, reject) => { rejectLate = reject; });
  const { deps } = device({ getCurrent: () => late });
  assert.deepEqual(await captureLocation(deps, { delay: instant }), { kind: "timeout" });
  rejectLate(new Error("late"));
  await new Promise((resolve) => setTimeout(resolve, 0));
});

test("cancellation before or during capture returns cancelled and applies nothing", async () => {
  const before = new AbortController();
  before.abort();
  const { deps: d1, calls: c1 } = device();
  assert.deepEqual(await captureLocation(d1, { signal: before.signal }), { kind: "cancelled" });
  assert.deepEqual(c1, [], "nothing is asked after cancellation");

  const during = new AbortController();
  const { deps: d2 } = device({ getCurrent: () => { setTimeout(() => during.abort(), 0); return never(); } });
  const result = await captureLocation(d2, { signal: during.signal, timeoutMs: 60_000 });
  assert.deepEqual(result, { kind: "cancelled" });
  assert.equal(describeCapture(result), "");
});

test("a fix whose timestamp is far in the device's future is refused", () => {
  const r = judgeFix(fix({ timestamp: NOW + 10 * 60_000 }), NOW);
  assert.equal(r.kind, "unavailable");
  assert.match(r.reason, /inconsistent time/);
  assert.equal(judgeFix(fix({ timestamp: 0 }), NOW).kind, "unavailable");
});

// ---- existing behaviour kept ------------------------------------------------
test("permission is requested only during capture, after location services are confirmed", async () => {
  const { deps, calls } = device({ servicesEnabled: async () => (calls.push("services"), false) });
  const result = await captureLocation(deps);
  assert.deepEqual(result, { kind: "gps_off" });
  assert.deepEqual(calls, ["services"], "no permission prompt and no fix when GPS is off");
});

test("denied permission (can ask again) and permanently denied are distinguished; no position read", async () => {
  for (const canAskAgain of [true, false]) {
    const { deps, calls } = device({ requestPermission: async () => ({ granted: false, canAskAgain }) });
    const r = await captureLocation(deps);
    assert.deepEqual(r, { kind: "denied", canAskAgain });
    assert.ok(!calls.includes("current"));
    assert.match(describeCapture(r), canAskAgain ? /Tap the button again/ : /settings/);
  }
});

test("timeout reports timeout and changes nothing", async () => {
  const r = await captureLocation(device({ getCurrent: never }).deps, { delay: instant });
  assert.deepEqual(r, { kind: "timeout" });
  assert.match(describeCapture(r), /fresh location fix in time.*not changed/s);
});

test("poor and unknown accuracy are refused without coordinates", async () => {
  for (const accuracy of [250, null]) {
    const r = await captureLocation(device({ getCurrent: async () => fix({ accuracy }) }).deps);
    assert.equal(r.kind, "poor_accuracy");
    assert.ok(!("fix" in r));
  }
  assert.match(describeCapture({ kind: "poor_accuracy", accuracyM: 250 }), new RegExp(`${ACCURACY_MAX_M} m or better`));
});

test("fair accuracy (30-100 m) is accepted with a fair rating", async () => {
  const r = await captureLocation(device({ getCurrent: async () => fix({ accuracy: 64 }) }).deps);
  assert.equal(r.kind, "ok");
  assert.equal(r.quality, "fair");
});

test("invalid and (0,0) positions are rejected", async () => {
  for (const bad of [fix({ latitude: 0, longitude: 0 }), fix({ latitude: 91 }), fix({ longitude: Number.NaN })]) {
    assert.equal((await captureLocation(device({ getCurrent: async () => bad }).deps)).kind, "unavailable");
  }
});

test("exceptions from services or permission checks are reported, not thrown", async () => {
  assert.equal((await captureLocation(device({ servicesEnabled: async () => { throw new Error("x"); } }).deps)).kind, "unavailable");
  assert.equal((await captureLocation(device({ requestPermission: async () => { throw new Error("x"); } }).deps)).kind, "unavailable");
});

test("accuracy classification boundaries", () => {
  assert.equal(classifyAccuracy(30), "good");
  assert.equal(classifyAccuracy(30.1), "fair");
  assert.equal(classifyAccuracy(100), "fair");
  assert.equal(classifyAccuracy(100.1), "poor");
  assert.equal(classifyAccuracy(-1), "poor");
});

test("coordinate formatting keeps 6 decimals without exponent or trailing zeros", () => {
  assert.equal(formatCoordinate(17.7231004), "17.7231");
  assert.equal(formatCoordinate(83.30124789), "83.301248");
  assert.equal(formatCoordinate(-0.0000001), "0");
});
