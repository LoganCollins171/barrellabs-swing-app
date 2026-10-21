// BL-008C.22 -- isolated, network-free unit test for the pure logic in
// cloudflare_proxy_worker.js. Run with: node deploy/cloudflare_proxy_worker.test.mjs
//
// This does NOT and CANNOT verify the worker's actual behavior against a
// real Cloudflare edge, Streamlit Cloud's origin, or a real WebSocket
// upgrade -- see that file's LIMITATIONS section for what remains
// unverified. This only proves the two pure helper functions do what
// their comments claim, using Node's built-in fetch-compatible
// Request/Headers/URL globals (no network call is made).
import assert from "node:assert/strict";
import { originRequestInit, rewriteLocation } from "./cloudflare_proxy_worker.js";

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`ok - ${name}`);
  } catch (err) {
    console.error(`FAIL - ${name}`);
    console.error(err);
    process.exitCode = 1;
  }
}

test("GET requests never get a body in the origin init (the bug this session fixed)", () => {
  const req = new Request("https://app.barrellabsai.com/", { method: "GET" });
  const init = originRequestInit(req);
  assert.equal(init.method, "GET");
  assert.ok(!("body" in init) || init.body === undefined || init.body === null,
    "GET must not carry a body key, or the real Workers runtime throws");
});

test("HEAD requests never get a body in the origin init", () => {
  const req = new Request("https://app.barrellabsai.com/", { method: "HEAD" });
  const init = originRequestInit(req);
  assert.ok(!("body" in init) || init.body === undefined || init.body === null);
});

test("POST requests DO carry their body through", () => {
  const req = new Request("https://app.barrellabsai.com/upload", {
    method: "POST", body: "fake-bytes", headers: { "content-type": "video/mp4" },
  });
  const init = originRequestInit(req);
  assert.equal(init.method, "POST");
  assert.notEqual(init.body, undefined);
});

test("redirects are followed manually, not automatically (so Location can be inspected/rewritten)", () => {
  const req = new Request("https://app.barrellabsai.com/", { method: "GET" });
  const init = originRequestInit(req);
  assert.equal(init.redirect, "manual");
});

test("a redirect pointing at the Streamlit origin is rewritten to the incoming hostname", () => {
  const rewritten = rewriteLocation(
    "https://barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app/some/path?x=1",
    "app.barrellabsai.com",
  );
  assert.equal(rewritten, "https://app.barrellabsai.com/some/path?x=1");
});

test("a redirect pointing somewhere else entirely is left untouched", () => {
  const original = "https://accounts.google.com/oauth/callback";
  const rewritten = rewriteLocation(original, "app.barrellabsai.com");
  assert.equal(rewritten, original);
});

test("a relative redirect is left untouched (already correct as-is)", () => {
  const original = "/dashboard";
  const rewritten = rewriteLocation(original, "app.barrellabsai.com");
  assert.equal(rewritten, original);
});

test("an empty/missing Location is passed through unchanged", () => {
  assert.equal(rewriteLocation(null, "app.barrellabsai.com"), null);
  assert.equal(rewriteLocation(undefined, "app.barrellabsai.com"), undefined);
  assert.equal(rewriteLocation("", "app.barrellabsai.com"), "");
});

console.log(`\n${passed} passed`);
if (process.exitCode) {
  console.error("SOME TESTS FAILED");
}
