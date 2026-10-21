/**
 * BL-008C.21 -- Render-free hosting for app.barrellabsai.com
 *
 * PREPARED, NOT DEPLOYED. This file is not wired into any build; nothing
 * references it. It exists so the exact code is ready the moment the owner
 * approves the Cloudflare-based cutover described in the BL-008C.21 report.
 *
 * WHAT THIS DOES
 * A thin reverse proxy: every request to app.barrellabsai.com is forwarded
 * to the existing Streamlit Community Cloud deployment
 * (barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app), preserving the
 * canonical hostname in the browser's address bar. This is the ONLY
 * Render-free option that keeps app.barrellabsai.com as the real, visible
 * URL (a plain CNAME cannot work here -- Streamlit Community Cloud does not
 * terminate TLS for any hostname other than its own *.streamlit.app, and
 * does not support custom domains on the free tier; see the report).
 *
 * WebSockets: Cloudflare Workers forward a WebSocket Upgrade request to the
 * origin transparently via fetch() -- no special-casing needed below. This
 * is required because Streamlit's entire reactive UI runs over one
 * persistent WebSocket after the initial page load; a platform that can't
 * proxy WebSockets (Netlify's redirect/proxy engine, confirmed via Netlify's
 * own support forum, cannot) cannot serve this app at all past the loading
 * screen.
 *
 * DEPLOYMENT (owner action, not executed by this session):
 *   1. This REQUIRES barrellabsai.com's DNS to be managed by Cloudflare
 *      (Workers Routes/Custom Domains only attach to zones Cloudflare is
 *      authoritative for). The zone is currently on NS1
 *      (dns[1-4].p09.nsone.net) -- confirmed via `dig barrellabsai.com NS`
 *      this session -- NOT Cloudflare. Moving the zone to Cloudflare's free
 *      plan is itself the one consequential step here: it's a nameserver
 *      delegation change at the domain registrar, and it affects every
 *      record on the domain (the marketing site's Netlify records too),
 *      not just "app". It needs to be done carefully, with every existing
 *      record (apex, www, app, any mail records) re-created in Cloudflare
 *      BEFORE the nameserver cutover, or the marketing site and any email
 *      briefly break too.
 *   2. In the Cloudflare dashboard: Workers & Pages -> Create Worker ->
 *      paste this file's contents.
 *   3. Workers & Pages -> (this worker) -> Settings -> Domains & Routes ->
 *      Add Custom Domain -> app.barrellabsai.com. Cloudflare provisions the
 *      TLS certificate for that hostname automatically (it owns the zone).
 *   4. No DNS record for "app" needs to be created manually -- adding the
 *      Custom Domain in step 3 creates/manages it.
 *
 * ROLLBACK: remove the Custom Domain binding in step 3 (or just repoint
 * "app"'s DNS record back to the Render CNAME, if the zone stays on
 * Cloudflare, or back to NS1 if the whole zone is reverted). Nothing about
 * this worker touches the Streamlit Cloud app itself, so rollback never
 * risks that deployment.
 */

const ORIGIN_HOST = "barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app";

export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const originUrl = new URL(request.url);
    originUrl.hostname = ORIGIN_HOST;
    originUrl.protocol = "https:";

    // Rebuild the request against the real origin. Keep method/body/
    // headers as-is; only the Host-affecting bits (url, and the Host
    // header itself) change. This also carries the WebSocket Upgrade
    // header straight through -- Cloudflare's fetch() implementation
    // special-cases Upgrade: websocket and hands back a proper
    // WebSocket-upgraded Response, it is not something this code has to
    // implement by hand.
    const originRequest = new Request(originUrl.toString(), request);
    originRequest.headers.set("Host", ORIGIN_HOST);
    // Streamlit Cloud's own edge may check these for its own routing/
    // CORS logic; forwarding the real client-facing values (rather than
    // leaving them as Cloudflare's edge-to-origin values) keeps Streamlit's
    // own same-origin assumptions intact.
    originRequest.headers.set("X-Forwarded-Host", incoming.hostname);
    originRequest.headers.set("Origin", originUrl.origin);

    const response = await fetch(originRequest);

    // Pass the origin's response straight back. Do not rewrite
    // Set-Cookie/Location here: Streamlit Community Cloud does not set a
    // domain-scoped cookie that would break under a different hostname
    // (confirmed by this app working correctly today at its own
    // *.streamlit.app URL with no custom-domain cookie configuration), and
    // rewriting response bodies (e.g. absolute streamlit.app URLs in
    // inline JS) is deliberately NOT done here -- that is exactly the kind
    // of fragile, hard-to-verify workaround the BL-008C.21 instructions
    // warn against. If real traffic through this worker surfaces a broken
    // absolute-URL reference, that is a concrete, testable bug to fix
    // then, not something to guess-fix in advance.
    return response;
  },
};
