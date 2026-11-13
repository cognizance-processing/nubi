/**
 * safeImageSrc.test.ts — allowlist for image widget <img src>.
 *
 * Run with:
 *   node --import tsx --test src/dashboards/safeImageSrc.test.ts
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { safeImageSrc } from "./safeImageSrc.js";

const HEX = "0123456789abcdef0123456789abcdef";

// Mirrors resolveImageUrl() with a same-origin BASE ('/api/v1'); lib/api.ts
// reads import.meta.env at load, so it is not imported here.
function resolveSameOrigin(raw: string): string {
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw)) return raw;
  return `/api/v1${raw.replace(/^\/api\/v1/, "")}`;
}

test("safeImageSrc: allows http(s) URLs and stored image paths", () => {
  assert.equal(
    safeImageSrc("https://cdn.example.com/a.png?v=1"),
    "https://cdn.example.com/a.png?v=1",
  );
  assert.equal(safeImageSrc("http://example.com/x.jpg"), "http://example.com/x.jpg");
  assert.equal(safeImageSrc(`/api/v1/images/${HEX}`), `/api/v1/images/${HEX}`);
  assert.equal(
    safeImageSrc(`/api/v1/images/${HEX.toUpperCase()}`),
    `/api/v1/images/${HEX.toUpperCase()}`,
  );
  assert.equal(
    safeImageSrc(`https://api.example.com/api/v1/images/${HEX}`),
    `https://api.example.com/api/v1/images/${HEX}`,
  );
  assert.equal(safeImageSrc(`  /api/v1/images/${HEX}  `), `/api/v1/images/${HEX}`);
  assert.equal(
    safeImageSrc(resolveSameOrigin(`/api/v1/images/${HEX}`)),
    `/api/v1/images/${HEX}`,
  );
});

test("safeImageSrc: rejects everything else", () => {
  for (const bad of [
    "",
    "   ",
    "javascript:alert(1)",
    " JavaScript:alert(1)",
    "data:image/png;base64,AAAA",
    "data:image/svg+xml,<svg onload=alert(1)>",
    "vbscript:x",
    "//evil.com/x.png",
    "/\\evil.com",
    "ftp://example.com/x.png",
    "https://exa\nmple.com/x.png",
    "java\tscript:alert(1)",
    `/api/v1/images/${HEX}\u0000`,
    `/api/v1/images/${HEX.slice(1)}`,
    `/api/v1/images/${HEX}0`,
    "/api/v1/images/zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
    `/api/v1/images/${HEX}/../x`,
    `/api/v1/images/${HEX}?x=1`,
    "/api/v1/queries/1",
    "/logo.png",
    "relative/path.png",
  ]) {
    assert.equal(safeImageSrc(bad), null, `should block ${JSON.stringify(bad)}`);
  }
});

test("safeImageSrc: resolved forms of junk input are rejected", () => {
  for (const raw of [
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "//evil.com/x.png",
    "/logo.png",
  ]) {
    assert.equal(
      safeImageSrc(resolveSameOrigin(raw)),
      null,
      `should block resolved ${JSON.stringify(raw)}`,
    );
  }
});
