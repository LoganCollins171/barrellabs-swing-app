/**
 * BL-008C.21/.22 -- Render-free hosting for app.barrellabsai.com
 *
 * PREPARED, NOT DEPLOYED. This file is not wired into any build; nothing
 * references it. It exists so the exact code is ready the moment the owner
 * approves the Cloudflare-based cutover described in the hosting report.
 * deploy/cloudflare_proxy_worker.test.mjs is a plain-Node, network-free
 * unit test of this file's pure request/response-transformation logic
 * (run with `node deploy/cloudflare_proxy_worker.test.mjs`) -- see the
 * LIMITATIONS note at the bottom of this file for exactly what that test
 * does and does NOT prove.
 *
 * WHAT THIS DOES
 * A thin reverse proxy: every request to app.barrellabsai.com is forwarded
 * to the existing Streamlit Community Cloud deployment
 * (barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app), preserving the
 * canonical hostname in the browser's address bar. Confirmed required
 * (not a plain CNAME) because Streamlit Community Cloud does not
 * terminate TLS for any hostname other than its own *.streamlit.app and
 * has no custom-domain support on the free tier.
 *
 * ALTERNATIVES RULED OUT THIS MILESTONE (BL-008C.22), with evidence:
 * Cloudflare's "Partial (CNAME) Setup" (subdomain-only proxying without
 * moving the whole zone's nameservers) requires a Business or Enterprise
 * plan -- confirmed via Cloudflare's own docs. Delegating just
 * "app.barrellabsai.com" to a separate Cloudflare-hosted child zone via
 * NS records also requires an Enterprise plan -- confirmed via
 * Cloudflare's own community/docs. Neither is available on the free
 * plan this task is constrained to. A full-zone migration (moving
 * barrellabsai.com's nameservers to Cloudflare entirely) is therefore
 * the ONLY Cloudflare-based way to keep app.barrellabsai.com as the
 * visible URL without a new paid plan -- still true, still not executed,
 * still requiring explicit owner approval (see HOSTING_CUTOVER.md).
 *
 * WebSockets: Cloudflare Workers forward a WebSocket Upgrade request to
 * the origin transparently via fetch() -- Cloudflare's own docs confirm
 * this is handled automatically, not something this code implements by
 * hand. This is required because Streamlit's entire reactive UI runs
 * over one persistent WebSocket after the initial page load.
 *
 * BL-008C.22 CRITICAL REVIEW -- bugs found and fixed in this revision
 * (none of this was deployed or could have broken anything live; this is
 * a pre-deployment code review of a file nothing references yet):
 *   1. `new Request(url, request)` with the ORIGINAL request as the
 *      init object throws a TypeError for GET/HEAD requests that have a
 *      non-null `body` property in some Workers runtime versions (the
 *      Fetch API forbids a body on GET/HEAD). The previous revision
 *      passed `request` directly as init for every method including
 *      GET/HEAD page loads -- the most common request this proxy would
 *      ever see. Fixed: body is only forwarded for methods that can
 *      carry one.
 *   2. No error handling around the origin fetch -- if the Streamlit
 *      Cloud origin is briefly unreachable (cold start, transient
 *      network blip), the previous revision let the exception propagate
 *      to an unstyled generic Cloudflare error page instead of a
 *      recognizable error. Fixed: wrapped in try/catch with a plain-text
 *      502 response.
 *   3. Redirect responses (3xx with a Location header) were passed
 *      through unmodified. If the origin ever issues a redirect to its
 *      own absolute https://barrellabs-swing-....streamlit.app/... URL,
 *      the browser would follow it there directly, breaking the
 *      canonical-hostname illusion this whole proxy exists to provide.
 *      Fixed: a same-origin redirect (scheme+host matching ORIGIN_HOST)
 *      is rewritten back to the proxy's own hostname before being
 *      returned; a redirect to anywhere else is left alone (nothing to
 *      fix, and rewriting an arbitrary third-party redirect target would
 *      be the wrong move).
 *   4. The request method itself was never validated -- forwarded
 *      verbatim. Not a security bug (Streamlit Cloud's own origin is the
 *      one that would reject something malformed), but worth noting:
 *      this proxy does not add or need any method allowlist of its own.
 *
 * NOT FIXED, FLAGGED INSTEAD (requires a real Cloudflare deployment to
 * verify, not guessable from here -- see LIMITATIONS):
 *   - Absolute streamlit.app URLs that might be embedded inside the
 *     page's own HTML/JS/CSS (not in a redirect header, inside the
 *     response BODY) are not rewritten. Streamlit's client bundle is
 *     designed to work behind arbitrary reverse proxies (it is commonly
 *     deployed behind nginx/Caddy in self-hosted setups) and uses
 *     relative/same-origin paths for its WebSocket and asset requests in
 *     every self-hosted deployment guide this session found -- but that
 *     is Streamlit's OPEN SOURCE behavior; Streamlit COMMUNITY CLOUD's
 *     specific build was not independently inspected (no access to its
 *     served JS bundle source map), so this is inferred, not verified.
 *   - Whether Streamlit Community Cloud's own edge validates the Origin
 *     header against an expected value and would reject this proxy's
 *     request. Cannot be tested without actually deploying the worker.
 *   - Actual wall-clock behavior of a long-lived WebSocket connection
 *     and a 150MB video upload streamed through a Worker -- Cloudflare's
 *     documented limits (subrequest/CPU time) describe the platform in
 *     general; this specific traffic shape was not load-tested.
 *   - Cache behavior: no Cache-Control override is applied here, so
 *     Cloudflare's default (respect origin headers, do not cache
 *     WebSocket/dynamic responses) should apply -- not independently
 *     confirmed against this specific origin's actual response headers.
 *
 * LOOP PREVENTION: ORIGIN_HOST is a hardcoded, different hostname from
 * this worker's own (app.barrellabsai.com vs. the .streamlit.app
 * address) -- there is no code path where this worker could call itself.
 *
 * PRIVATE DATA EXPOSURE: no new header or logging here carries anything
 * beyond what a normal reverse proxy forwards (client Host/Origin, for
 * Streamlit's own same-origin logic) -- no cookies, auth tokens, or
 * video content are read, logged, or altered by this code; they pass
 * through as opaque bytes exactly as received.
 *
 * SECURITY HEADERS: none are added or stripped here. Streamlit Cloud's
 * own origin response headers pass through unmodified (except the one
 * redirect-Location rewrite above). Adding e.g. HSTS/CSP here would be
 * an easy follow-up but was not required by this milestone and risks
 * breaking something about Streamlit's own page behavior sight unseen.
 *
 * DEPLOYMENT (owner action, not executed by this session): unchanged
 * from the BL-008C.21 report -- see deploy/HOSTING_CUTOVER.md for the
 * full, current step-by-step (including the free-plan-feasibility
 * findings above) and rollback plan.
 */

const ORIGIN_HOST = "barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app";
const BODYLESS_METHODS = new Set(["GET", "HEAD"]);

/** Pure, network-free: builds the origin-bound Request init object.
 * Exported for the isolated unit test; not meant to be called directly
 * in the Worker runtime (buildOriginRequest does that). */
export function originRequestInit(request) {
  const init = {
    method: request.method,
    headers: new Headers(request.headers),
    redirect: "manual",
  };
  if (!BODYLESS_METHODS.has(request.method)) {
    init.body = request.body;
  }
  return init;
}

/** Pure, network-free: given a Location header value and the incoming
 * request's own hostname, returns the rewritten value (or the original,
 * unchanged, if it doesn't point at ORIGIN_HOST). */
export function rewriteLocation(locationValue, incomingHostname) {
  if (!locationValue) return locationValue;
  let loc;
  try {
    loc = new URL(locationValue);
  } catch {
    return locationValue; // relative Location -- already correct as-is, nothing to rewrite
  }
  if (loc.hostname === ORIGIN_HOST) {
    loc.hostname = incomingHostname;
    return loc.toString();
  }
  return locationValue;
}

export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const originUrl = new URL(request.url);
    originUrl.hostname = ORIGIN_HOST;
    originUrl.protocol = "https:";

    const init = originRequestInit(request);
    init.headers.set("Host", ORIGIN_HOST);
    init.headers.set("X-Forwarded-Host", incoming.hostname);
    init.headers.set("Origin", originUrl.origin);
    const originRequest = new Request(originUrl.toString(), init);

    let response;
    try {
      response = await fetch(originRequest);
    } catch (err) {
      return new Response(
        "BarrelLabs is temporarily unreachable through this domain. Try again in a moment.",
        { status: 502, headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }

    if (response.status >= 300 && response.status < 400 && response.headers.has("location")) {
      const rewritten = rewriteLocation(response.headers.get("location"), incoming.hostname);
      if (rewritten !== response.headers.get("location")) {
        const headers = new Headers(response.headers);
        headers.set("location", rewritten);
        return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
      }
    }

    return response;
  },
};
