// Run: node --experimental-strip-types --test tests/sessionCore.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  classifyStatus,
  createNativeSession,
  parseStoredSession,
  sendWithRetry,
  STARTUP_RETRY,
  STORAGE_READ_MESSAGE,
  STORAGE_WRITE_WARNING,
} from "../src/lib/sessionCore.ts";

const API = "https://api.test";

/** Mirrors backend/native_sessions.py rotation, retry and reuse rules. */
function fakeServer() {
  const server = {
    sessions: [],
    calls: [],
    script: [], // queued overrides: "network" | "lose-response" | status number
    issued: 0,
  };
  const issue = () => `token-${++server.issued}-${"x".repeat(48)}`;
  const respond = (status, body = {}, headers = {}) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json", ...headers },
    });

  server.fetch = async (url, init) => {
    const path = url.replace(API, "");
    const body = init.body ? JSON.parse(init.body) : {};
    server.calls.push({ path, body });
    const override = server.script.shift();
    if (override === "network") throw new TypeError("Network request failed");
    if (typeof override === "number") {
      return respond(override, { detail: "scripted" }, override === 429 ? { "Retry-After": "3" } : {});
    }

    let result;
    if (path === "/auth/native/login") {
      if (body.password !== "right-password") return respond(401, { detail: "Incorrect email or password." });
      const session = { current: issue(), previous: null, rid: null, revoked: false };
      server.sessions.push(session);
      result = respond(200, { access_token: "access", refresh_token: session.current });
    } else if (path === "/auth/native/refresh") {
      const token = body.refresh_token;
      const session = server.sessions.find((s) => s.current === token || s.previous === token);
      if (!session || session.revoked) {
        result = respond(401, { detail: "Session expired or invalid." });
      } else if (session.current === token) {
        session.previous = token;
        session.rid = body.request_id;
        session.current = `${token.slice(0, 12)}-${body.request_id}`; // deterministic per attempt
        result = respond(200, { access_token: "access", refresh_token: session.current });
      } else if (session.rid === body.request_id) {
        result = respond(200, { access_token: "access", refresh_token: session.current });
      } else {
        session.revoked = true;
        result = respond(401, { detail: "Session expired or invalid." });
      }
    } else if (path === "/auth/native/logout") {
      for (const s of server.sessions) {
        if (s.current === body.refresh_token || s.previous === body.refresh_token) s.revoked = true;
      }
      result = respond(200, { message: "Logged out." });
    } else {
      result = respond(404);
    }

    if (override === "lose-response") throw new TypeError("Network request failed");
    return result;
  };
  return server;
}

function memoryStorage() {
  const storage = {
    value: null,
    failRead: false,
    failWrite: false,
    failClear: false,
    clears: 0,
    async read() {
      if (storage.failRead) throw new Error("keystore unavailable");
      return storage.value ? { ...storage.value } : null;
    },
    async write(session) {
      if (storage.failWrite) throw new Error("keystore write failed");
      storage.value = { ...session };
    },
    async clear() {
      storage.clears += 1;
      if (storage.failClear) throw new Error("keystore delete failed");
      storage.value = null;
    },
  };
  return storage;
}

function setup(server = fakeServer(), storage = memoryStorage()) {
  let ids = 0;
  const sleeps = [];
  const make = () =>
    createNativeSession({
      apiUrl: API,
      fetch: (url, init) => server.fetch(url, init),
      storage,
      randomId: () => `request-${++ids}-${"r".repeat(24)}`,
      sleep: async (ms) => {
        sleeps.push(ms);
      },
    });
  return { server, storage, sleeps, session: make(), restart: make };
}

test("login stores the refresh token in secure storage only via the storage port", async () => {
  const { storage, session, server } = setup();
  const result = await session.login("a@example.com", "right-password");
  assert.equal(result.accessToken, "access");
  assert.equal(result.warning, undefined);
  assert.deepEqual(storage.value, { refreshToken: server.sessions[0].current, pendingRequestId: null });
});

test("wrong password throws the server message and stores nothing", async () => {
  const { storage, session } = setup();
  await assert.rejects(session.login("a@example.com", "nope"), /Incorrect email or password/);
  assert.equal(storage.value, null);
});

test("app restart restores the session and rotates the stored token", async () => {
  const { storage, session, restart, server } = setup();
  await session.login("a@example.com", "right-password");
  const before = storage.value.refreshToken;

  const result = await restart().refresh(STARTUP_RETRY);
  assert.equal(result.kind, "restored");
  assert.notEqual(storage.value.refreshToken, before);
  assert.equal(storage.value.pendingRequestId, null);
  assert.equal(server.sessions[0].revoked, false);
});

test("no saved session means signed out without any network call", async () => {
  const { session, server } = setup();
  assert.deepEqual(await session.refresh(STARTUP_RETRY), { kind: "signed_out" });
  assert.equal(server.calls.length, 0);
});

test("401 clears the saved token", async () => {
  const { session, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  server.script.push(401);
  const result = await session.refresh(STARTUP_RETRY);
  assert.equal(result.kind, "signed_out");
  assert.equal(storage.value, null);
});

test("cold start: network errors are retried with the same request id", async () => {
  const { session, server, sleeps } = setup();
  await session.login("a@example.com", "right-password");
  server.script.push("network", "network", 502);
  const result = await session.refresh(STARTUP_RETRY);
  assert.equal(result.kind, "restored");
  const ids = server.calls.filter((c) => c.path === "/auth/native/refresh").map((c) => c.body.request_id);
  assert.equal(ids.length, 4);
  assert.equal(new Set(ids).size, 1);
  assert.deepEqual(sleeps, [2000, 5000, 10000]);
});

test("persistent 5xx/429 keeps credentials; the next launch repeats the same attempt", async () => {
  const { session, restart, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  const token = storage.value.refreshToken;
  server.script.push(503, 429, 500, 503, 504);

  const offline = await session.refresh(STARTUP_RETRY);
  assert.equal(offline.kind, "unreachable");
  assert.equal(storage.value.refreshToken, token);
  const pending = storage.value.pendingRequestId;
  assert.ok(pending);
  assert.equal(storage.clears, 0);

  const recovered = await restart().refresh(STARTUP_RETRY);
  assert.equal(recovered.kind, "restored");
  assert.equal(server.calls.at(-1).body.request_id, pending);
});

test("429 Retry-After is honoured (capped)", async () => {
  const { session, server, sleeps } = setup();
  await session.login("a@example.com", "right-password");
  server.script.push(429);
  assert.equal((await session.refresh(STARTUP_RETRY)).kind, "restored");
  assert.deepEqual(sleeps, [3000]);
});

test("lost response after server rotation is recovered without revocation", async () => {
  const { session, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  server.script.push("lose-response");
  const result = await session.refresh(STARTUP_RETRY);
  assert.equal(result.kind, "restored");
  assert.equal(server.sessions[0].revoked, false);
  assert.equal(storage.value.refreshToken, server.sessions[0].current);
});

test("unexpected 4xx keeps credentials and does not sign out", async () => {
  const { session, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  server.script.push(403);
  const result = await session.refresh(STARTUP_RETRY);
  assert.equal(result.kind, "unreachable");
  assert.ok(storage.value.refreshToken);
});

test("failed secure-storage write at login: signed in with an explicit warning", async () => {
  const { session, storage } = setup();
  storage.failWrite = true;
  const result = await session.login("a@example.com", "right-password");
  assert.equal(result.warning, STORAGE_WRITE_WARNING);
  assert.equal(storage.value, null);
  // The in-memory credential keeps this run working.
  assert.equal((await session.refresh()).kind, "restored");
});

test("failed write after rotation: next launch recovers via the saved pending attempt", async () => {
  const { session, restart, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  const realWrite = storage.write;
  let writes = 0;
  storage.write = async (value) => {
    writes += 1;
    if (writes === 2) throw new Error("keystore write failed"); // the post-rotation save
    return realWrite(value);
  };
  const result = await session.refresh(STARTUP_RETRY);
  assert.equal(result.kind, "restored");
  assert.equal(result.warning, STORAGE_WRITE_WARNING);
  assert.ok(storage.value.pendingRequestId);

  storage.write = realWrite;
  const recovered = await restart().refresh(STARTUP_RETRY);
  assert.equal(recovered.kind, "restored");
  assert.equal(server.sessions[0].revoked, false);
});

test("unreadable secure storage signs out with a message but does not delete", async () => {
  const { session, storage, server } = setup();
  storage.value = { refreshToken: "t".repeat(64), pendingRequestId: null };
  storage.failRead = true;
  const result = await session.refresh(STARTUP_RETRY);
  assert.deepEqual(result, { kind: "signed_out", message: STORAGE_READ_MESSAGE });
  assert.equal(storage.clears, 0);
  assert.equal(server.calls.length, 0);
});

test("token replay by another holder revokes the session for everyone", async () => {
  const { session, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  const stolen = storage.value.refreshToken;
  await session.refresh();
  const replay = await server.fetch(`${API}/auth/native/refresh`, {
    method: "POST",
    body: JSON.stringify({ refresh_token: stolen, request_id: "attacker-request-id-0000000" }),
  });
  assert.equal(replay.status, 401);
  const result = await session.refresh();
  assert.equal(result.kind, "signed_out");
  assert.equal(storage.value, null);
});

test("concurrent refreshes share one request", async () => {
  const { session, server } = setup();
  await session.login("a@example.com", "right-password");
  const [a, b, c] = await Promise.all([session.refresh(), session.refresh(), session.refresh()]);
  assert.equal(server.calls.filter((call) => call.path === "/auth/native/refresh").length, 1);
  assert.equal(a, b);
  assert.equal(b, c);
});

test("logout revokes on the server and clears secure storage", async () => {
  const { session, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  const result = await session.logout();
  assert.deepEqual(result, { serverRevoked: true });
  assert.equal(storage.value, null);
  assert.equal(server.sessions[0].revoked, true);
  assert.deepEqual(await session.refresh(), { kind: "signed_out" });
});

test("logout while offline still signs out locally", async () => {
  const { session, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  server.script.push("network", "network");
  const result = await session.logout();
  assert.equal(result.serverRevoked, false);
  assert.equal(storage.value, null);
});

test("logout reports a failed secure-storage delete", async () => {
  const { session, storage } = setup();
  await session.login("a@example.com", "right-password");
  storage.failClear = true;
  const result = await session.logout();
  assert.equal(result.serverRevoked, true);
  assert.match(result.error, /couldn't remove the saved sign-in/);
});

test("logout fails visibly when the server and the local delete both fail", async () => {
  const { session, storage, server } = setup();
  await session.login("a@example.com", "right-password");
  const token = storage.value.refreshToken;
  server.script.push("network", "network");
  storage.failClear = true;

  await assert.rejects(session.logout(), /Sign-out didn't finish/);
  // Nothing claims a sign-out: the still-valid credential is kept and usable.
  assert.equal(storage.value.refreshToken, token);
  assert.equal(server.sessions[0].revoked, false);
  storage.failClear = false;
  assert.deepEqual(await session.logout(), { serverRevoked: true });
  assert.equal(storage.value, null);
});

test("a refresh racing with logout never writes a token back", async () => {
  const server = fakeServer();
  const { session, storage } = setup(server);
  await session.login("a@example.com", "right-password");

  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const realFetch = server.fetch;
  server.fetch = async (url, init) => {
    if (url.endsWith("/auth/native/refresh")) await gate;
    return realFetch(url, init);
  };

  const refreshing = session.refresh();
  await new Promise((resolve) => setImmediate(resolve));
  const loggingOut = session.logout();
  // While logout is running, new refreshes are refused.
  assert.deepEqual(await session.refresh(), { kind: "signed_out" });
  release();

  assert.equal((await refreshing).kind, "signed_out");
  assert.deepEqual(await loggingOut, { serverRevoked: true });
  assert.equal(storage.value, null);
  assert.equal(server.sessions[0].revoked, true);
});

test("sendWithRetry aborts a hung request at the timeout", async () => {
  let aborted = 0;
  const outcome = await sendWithRetry(
    (signal) =>
      new Promise((_, reject) => {
        signal.addEventListener("abort", () => {
          aborted += 1;
          reject(new Error("aborted"));
        });
      }),
    { timeoutMs: 10, delaysMs: [0] },
    async () => {},
  );
  assert.deepEqual(outcome, { kind: "unreachable" });
  assert.equal(aborted, 2);
});

test("status classification", () => {
  assert.equal(classifyStatus(200), "ok");
  assert.equal(classifyStatus(401), "invalid");
  assert.equal(classifyStatus(422), "invalid");
  for (const status of [408, 429, 500, 502, 503]) assert.equal(classifyStatus(status), "transient");
  for (const status of [400, 403, 404]) assert.equal(classifyStatus(status), "failed");
});

test("stored session parsing rejects malformed values", () => {
  assert.equal(parseStoredSession(null), null);
  assert.equal(parseStoredSession("not json"), null);
  assert.equal(parseStoredSession(JSON.stringify({ refreshToken: "" })), null);
  assert.deepEqual(parseStoredSession(JSON.stringify({ refreshToken: "abc", pendingRequestId: 5 })), {
    refreshToken: "abc",
    pendingRequestId: null,
  });
});
