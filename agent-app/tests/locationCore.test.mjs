import assert from "node:assert/strict";
import test from "node:test";
import {
  ACCURACY_MAX_M,
  captureLocation,
  classifyAccuracy,
  describeCapture,
  formatCoordinate,
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
function device(over = {}) {
  const calls = [];
  const deps = {
    servicesEnabled: async () => (calls.push("services"), true),
    requestPermission: async () => (calls.push("permission"), { granted: true, canAskAgain: true }),
    getCurrent: async () => (calls.push("current"), fix()),
    getLastKnown: async () => (calls.push("lastKnown"), null),
    now: () => NOW,
    ...over,
  };
  return { deps, calls };
}
const never = () => new Promise(() => {});
const instant = () => Promise.resolve();

test("successful capture returns rounded coordinates, accuracy and timestamp", async () => {
  const { deps, calls } = device();
  const result = await captureLocation(deps);
  assert.equal(result.kind, "ok");
  assert.equal(result.quality, "good");
  assert.deepEqual(result.fix, {
    latitude: 17.723105,
    longitude: 83.301248,
    accuracyM: 12.3,
    capturedAt: "2026-11-26T04:59:59.000Z",
    mocked: false,
    lastKnown: false,
  });
  assert.deepEqual(calls, ["services", "permission", "current"]);
  assert.match(describeCapture(result), /Current location captured \(±12 m\)/);
});

test("permission is requested only during capture, after location services are confirmed", async () => {
  const { deps, calls } = device({ servicesEnabled: async () => (calls.push("services"), false) });
  const result = await captureLocation(deps);
  assert.deepEqual(result, { kind: "gps_off" });
  assert.deepEqual(calls, ["services"], "no permission prompt and no fix when GPS is off");
  assert.match(describeCapture(result), /turned off/);
});

test("denied permission (can ask again) and permanently denied are distinguished", async () => {
  let r = await captureLocation(device({ requestPermission: async () => ({ granted: false, canAskAgain: true }) }).deps);
  assert.deepEqual(r, { kind: "denied", canAskAgain: true });
  assert.match(describeCapture(r), /Tap the button again/);
  r = await captureLocation(device({ requestPermission: async () => ({ granted: false, canAskAgain: false }) }).deps);
  assert.deepEqual(r, { kind: "denied", canAskAgain: false });
  assert.match(describeCapture(r), /settings/);
});

test("denied permission never reads a position", async () => {
  const { deps, calls } = device({ requestPermission: async () => (calls.push("permission"), { granted: false, canAskAgain: true }) });
  await captureLocation(deps);
  assert.ok(!calls.includes("current") && !calls.includes("lastKnown"));
});

test("timeout without a recent last-known fix reports timeout", async () => {
  const { deps, calls } = device({ getCurrent: never });
  const result = await captureLocation(deps, { delay: instant });
  assert.deepEqual(result, { kind: "timeout" });
  assert.ok(calls.includes("lastKnown"));
  assert.match(describeCapture(result), /in time/);
});

test("timeout falls back to a recent last-known fix, labelled as such", async () => {
  const { deps } = device({ getCurrent: never, getLastKnown: async () => fix({ timestamp: NOW - 60_000 }) });
  const result = await captureLocation(deps, { delay: instant });
  assert.equal(result.kind, "ok");
  assert.equal(result.fix.lastKnown, true);
  assert.match(describeCapture(result), /Last known location/);
});

test("a stale last-known fix is not used", async () => {
  const { deps } = device({ getCurrent: never, getLastKnown: async () => fix({ timestamp: NOW - 10 * 60_000 }) });
  assert.deepEqual(await captureLocation(deps, { delay: instant }), { kind: "timeout" });
});

test("poor accuracy cannot be applied but is shown", async () => {
  const { deps } = device({ getCurrent: async () => fix({ accuracy: 250 }) });
  const result = await captureLocation(deps);
  assert.equal(result.kind, "poor_accuracy");
  assert.equal(result.fix.accuracyM, 250);
  assert.match(describeCapture(result), new RegExp(`${ACCURACY_MAX_M} m or better`));
});

test("unknown accuracy is treated as poor", async () => {
  const { deps } = device({ getCurrent: async () => fix({ accuracy: null }) });
  assert.equal((await captureLocation(deps)).kind, "poor_accuracy");
});

test("fair accuracy (30-100 m) is accepted with a fair rating", async () => {
  const { deps } = device({ getCurrent: async () => fix({ accuracy: 64 }) });
  const result = await captureLocation(deps);
  assert.equal(result.kind, "ok");
  assert.equal(result.quality, "fair");
});

test("device errors become 'unavailable' (after trying last known)", async () => {
  const { deps, calls } = device({ getCurrent: async () => { throw new Error("Location provider unavailable"); } });
  const result = await captureLocation(deps);
  assert.equal(result.kind, "unavailable");
  assert.ok(calls.includes("lastKnown"));
  assert.match(describeCapture(result), /manually/);
});

test("invalid and (0,0) positions are rejected", async () => {
  for (const bad of [fix({ latitude: 0, longitude: 0 }), fix({ latitude: 91 }), fix({ longitude: Number.NaN })]) {
    const { deps } = device({ getCurrent: async () => bad });
    assert.equal((await captureLocation(deps)).kind, "unavailable");
  }
});

test("mock locations are flagged", async () => {
  const { deps } = device({ getCurrent: async () => fix({ mocked: true }) });
  const result = await captureLocation(deps);
  assert.equal(result.fix.mocked, true);
  assert.match(describeCapture(result), /mock/);
});

test("exceptions from services or permission checks are reported, not thrown", async () => {
  let r = await captureLocation(device({ servicesEnabled: async () => { throw new Error("x"); } }).deps);
  assert.equal(r.kind, "unavailable");
  r = await captureLocation(device({ requestPermission: async () => { throw new Error("x"); } }).deps);
  assert.equal(r.kind, "unavailable");
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
