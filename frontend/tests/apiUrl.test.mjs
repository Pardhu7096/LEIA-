import test from "node:test";
import assert from "node:assert/strict";
import { resolveApiUrl } from "../src/lib/apiUrl.ts";

const local = "http://localhost:8000";

test("development falls back to the local backend", () => {
  assert.equal(resolveApiUrl(undefined, true, local), local);
  assert.equal(resolveApiUrl("  ", true, local), local);
});
test("release builds refuse to fall back to localhost", () => {
  assert.throws(() => resolveApiUrl(undefined, false, local), /required for release builds/);
  assert.throws(() => resolveApiUrl("", false, local), /required for release builds/);
});
test("configured HTTPS API is used without a trailing slash", () => {
  assert.equal(resolveApiUrl("https://api.example.test/", false, local), "https://api.example.test");
  assert.equal(resolveApiUrl(" https://api.example.test:8443 ", false, local), "https://api.example.test:8443");
});
test("release builds refuse plain HTTP to a remote host", () => {
  assert.throws(() => resolveApiUrl("http://api.example.test", false, local), /must use https/);
  assert.throws(() => resolveApiUrl("http://203.0.113.10:8000", false, local), /must use https/);
});
test("plain HTTP stays available for local and development testing", () => {
  assert.equal(resolveApiUrl("http://192.0.2.5:8000", true, local), "http://192.0.2.5:8000");
  assert.equal(resolveApiUrl("http://127.0.0.1:8000", false, local), "http://127.0.0.1:8000");
});
test("malformed values are rejected instead of producing broken requests", () => {
  for (const value of ["api.example.test", "ftp://api.example.test", "https://api.example.test/v1", "https://"]) {
    assert.throws(() => resolveApiUrl(value, false, local), /must look like/);
  }
});
