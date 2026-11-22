import assert from "node:assert/strict";
import test from "node:test";
import { createStaffSession, restoreStaffSession } from "../src/lib/staffSession.ts";

test("overlapping expired requests share a refresh and retry the original draft", async (t) => {
  const writes = [];
  let refreshes = 0;

  t.mock.method(globalThis, "fetch", async (url, options) => {
    if (url.endsWith("/auth/staff/refresh")) {
      refreshes += 1;
      assert.equal(options.credentials, "include");
      assert.equal(options.headers["X-LEIA-CSRF"], "1");
      return Response.json({ access_token: "renewed" });
    }
    if (options.headers.get("Authorization") === "Bearer expired") {
      return new Response(null, { status: 401 });
    }
    if (options.method === "PUT") writes.push(options.body);
    return Response.json({ status: "draft" });
  });

  const { authFetch } = createStaffSession("http://localhost:8000", "expired");
  const body = JSON.stringify({ version: 3, title: "Unsaved edit" });
  const results = await Promise.all([
    authFetch("/staff/properties/1", { method: "PUT", body }),
    authFetch("/staff/properties"),
  ]);
  assert.ok(results.every((response) => response.ok));
  assert.equal(refreshes, 1);
  assert.deepEqual(writes, [body]);
});

test("a rejected refresh returns to the form without resending the save", async (t) => {
  let writes = 0;
  t.mock.method(globalThis, "fetch", async (url) => {
    if (!url.endsWith("/auth/staff/refresh")) writes += 1;
    return new Response(null, { status: 401 });
  });

  const { authFetch } = createStaffSession("http://localhost:8000", "expired");
  const result = await authFetch("/staff/properties/1", { method: "PUT" });
  assert.equal(result.status, 401);
  assert.equal(writes, 1);
});

const restoreApi = "https://example.test";

test("valid cookie restores staff authentication", async (t) => {
  t.mock.method(globalThis, "fetch", async (url, options) => {
    if (url.endsWith("/auth/staff/refresh")) {
      assert.equal(options.method, "POST");
      assert.equal(options.credentials, "include");
      assert.equal(options.headers["X-LEIA-CSRF"], "1");

      return Response.json({ access_token: "restored-token" });
    }

    assert.equal(
      options.headers.get("Authorization"),
      "Bearer restored-token",
    );

    return Response.json({ role: "admin" });
  });

  const session = await restoreStaffSession(restoreApi);
  assert.ok(session);

  const response = await session.authFetch("/auth/staff/me");
  assert.equal(response.status, 200);
});

test("missing or expired session returns null", async (t) => {
  t.mock.method(globalThis, "fetch", async () => {
    return new Response(null, { status: 401 });
  });

  assert.equal(await restoreStaffSession(restoreApi), null);
});

test("forbidden refresh rejects restoration", async (t) => {
  t.mock.method(globalThis, "fetch", async () => {
    return new Response(null, { status: 403 });
  });

  await assert.rejects(
    restoreStaffSession(restoreApi),
    /403/,
  );
});

test("server failure rejects restoration", async (t) => {
  t.mock.method(globalThis, "fetch", async () => {
    return new Response(null, { status: 500 });
  });

  await assert.rejects(
    restoreStaffSession(restoreApi),
    /500/,
  );
});

test("invalid token rejects restoration", async (t) => {
  t.mock.method(globalThis, "fetch", async () => {
    return Response.json({ access_token: "   " });
  });

  await assert.rejects(
    restoreStaffSession(restoreApi),
    /valid access token/,
  );
});

test("concurrent startup calls share one refresh", async (t) => {
  let refreshes = 0;

  t.mock.method(globalThis, "fetch", async () => {
    refreshes += 1;

    return Response.json({ access_token: "shared-token" });
  });

  const first = restoreStaffSession(restoreApi);
  const second = restoreStaffSession(restoreApi);

  assert.equal(first, second);

  const [a, b] = await Promise.all([first, second]);

  assert.ok(a);
  assert.equal(a, b);
  assert.equal(refreshes, 1);
});

test("failed restoration allows a later retry", async (t) => {
  let attempts = 0;

  t.mock.method(globalThis, "fetch", async () => {
    attempts += 1;

    if (attempts === 1) {
      throw new TypeError("Network unavailable");
    }

    return Response.json({ access_token: "retry-token" });
  });

  await assert.rejects(
    restoreStaffSession(restoreApi),
    /Network unavailable/,
  );

  assert.ok(await restoreStaffSession(restoreApi));
  assert.equal(attempts, 2);
});
