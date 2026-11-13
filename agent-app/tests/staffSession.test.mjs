import assert from "node:assert/strict";
import test from "node:test";
import { createStaffSession } from "../src/lib/staffSession.ts";

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
