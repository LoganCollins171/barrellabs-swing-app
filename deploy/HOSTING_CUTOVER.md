# BL-008C.21 -- Render-free hosting for app.barrellabsai.com

Prepared, not executed. No DNS, no deploy, no new accounts. Read-only
investigation performed 2026-10-21.

## Current verified DNS (read-only, via `dig`)

| Record | Type | Value |
|---|---|---|
| `barrellabsai.com` | NS (authoritative) | `dns1-4.p09.nsone.net` (**NS1**, not Cloudflare, not Netlify) |
| `barrellabsai.com` | A | `18.208.88.157`, `98.84.224.111` (Amazon-announced IPs, fronted by **Netlify Edge** -- confirmed via `cache-status: "Netlify Edge"` response header) |
| `www.barrellabsai.com` | A | same two IPs, same Netlify front |
| `app.barrellabsai.com` | CNAME | `barrellabs-swing-app.onrender.com` -> Render's own edge (which happens to front with Cloudflare as Render's internal CDN -- that is Render's infrastructure, not an account the owner controls) |

**Correction to a prior session's assumption:** earlier work described
`app.barrellabsai.com`'s DNS as routing "through Cloudflare." That was
imprecise -- the hostname Render's CNAME resolves to
(`gcp-us-west1-1.origin.onrender.com.cdn.cloudflare.net`) is Render using
Cloudflare as its own backend CDN. The owner does not have a Cloudflare
account authoritative for this domain today. The actual DNS provider is
**NS1**.

## Why a plain CNAME to the Streamlit Cloud app does not work

Confirmed via Streamlit's own documentation and community support threads:
Streamlit Community Cloud (the free tier this app runs on) does not support
custom domains at all. Even if `app.barrellabsai.com` CNAMEd directly to
`barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app`, two independent
failures would occur: (1) Streamlit's edge has no TLS certificate covering
`app.barrellabsai.com`, so HTTPS fails outright; (2) Streamlit's multi-tenant
routing identifies which app to serve by the request's Host header matching
a known `*.streamlit.app` name -- an unregistered custom hostname has no
route to this specific app even if the cert problem were solved.

## Why Netlify (already in use for the marketing site) can't proxy the app

Netlify is already an existing, zero-additional-cost account on this
domain. It was evaluated first for exactly that reason. Confirmed via
Netlify's own support forum: **Netlify does not support WebSocket
proxying**, and its redirect/rewrite proxy has a hard 26-second response
timeout. Streamlit's entire UI (every button, every page change, swing
analysis progress) runs over one persistent WebSocket opened after the
initial page load -- without it the app loads a blank shell and never
becomes interactive. Netlify is therefore unusable as the actual app
host, full stop. It remains useful for one thing: a plain HTTP redirect
(see "interim fallback" below), which is request/response only, not a
persistent connection.

## Recommendation: Cloudflare Worker reverse proxy (not deployed)

The only Render-free path that (a) keeps `app.barrellabsai.com` as the real
URL in the browser, (b) gets a valid HTTPS certificate for that hostname,
and (c) supports Streamlit's WebSocket, is a reverse proxy running on
infrastructure that can both terminate TLS for a custom domain AND forward
WebSocket upgrades. Cloudflare Workers do this natively and are free at
this traffic volume (Workers free tier: 100,000 requests/day).

The exact script is prepared at `deploy/cloudflare_proxy_worker.js` in this
repo, with full deployment steps in its header comment. Summary:

**This requires moving `barrellabsai.com`'s DNS zone from NS1 to
Cloudflare.** Workers Custom Domains only attach to zones Cloudflare is
authoritative for -- there is no way to point a Worker at a domain whose
DNS lives elsewhere. This is a nameserver delegation change at the
registrar, which is why it is flagged as the one consequential approval
needed, not a routine config edit: it affects every record on the domain,
not just "app" (the marketing site's Netlify A records and any mail
records must be re-created in Cloudflare first, or those break too during
the cutover window).

### Exact record changes (prepared, NOT applied)

**Before moving the zone (if the owner approves the Cloudflare path):**
1. Create a free Cloudflare account (or confirm one already exists outside
   what this session could detect) and add `barrellabsai.com` as a zone.
2. Inside that new Cloudflare zone, recreate the CURRENT records exactly,
   so nothing breaks the moment nameservers cut over:
   - `A barrellabsai.com -> 18.208.88.157, 98.84.224.111` (or switch to
     Netlify's documented ALIAS/CNAME flattening target for the apex --
     confirm the exact value in the Netlify dashboard's own domain
     instructions before recreating it, do not just copy the IPs blind,
     they can change)
   - `A/CNAME www.barrellabsai.com -> ` same Netlify target
   - any existing MX/TXT (SPF/DKIM) records for email on this domain --
     not inventoried this session (no mail config visible from this repo)
3. Remove the `app.barrellabsai.com` CNAME to Render (it would otherwise
   keep pointing at a permanently suspended service).
4. Add the Worker's Custom Domain binding for `app.barrellabsai.com` (this
   creates Cloudflare's own internal record for it -- no manual CNAME
   needed for "app" specifically).
5. At the domain registrar, change `barrellabsai.com`'s nameservers from
   NS1's four `dns*.p09.nsone.net` to the two Cloudflare-assigned
   nameservers (Cloudflare shows these when the zone is added).

**After cutover, DNS should read:**
| Record | Type | Value |
|---|---|---|
| `barrellabsai.com` NS | NS | Cloudflare-assigned pair (replaces NS1) |
| `barrellabsai.com` | A/ALIAS | Netlify's documented apex target (unchanged destination, new provider) |
| `www.barrellabsai.com` | CNAME/A | Netlify (unchanged destination) |
| `app.barrellabsai.com` | Cloudflare Worker Custom Domain (not a plain CNAME) | routes to the Worker, which proxies to `barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app` |

### HTTPS / WebSocket verification plan (post-cutover, not yet run)

1. `curl -sI https://app.barrellabsai.com` -- expect `HTTP/2 200` and a
   `server` header consistent with Cloudflare, with the page body matching
   the Streamlit app's HTML shell (compare against a direct fetch of the
   `.streamlit.app` URL).
2. `openssl s_client -connect app.barrellabsai.com:443 -servername app.barrellabsai.com | openssl x509 -noout -subject -dates` --
   expect a Cloudflare-issued cert covering `app.barrellabsai.com`, not
   expired.
3. Open `https://app.barrellabsai.com` in a real browser, confirm the app
   becomes interactive (not stuck on "Please wait...") -- this is the
   actual WebSocket proof; a blank/stuck page means the Upgrade didn't
   reach the origin.
4. Log in, upload one disposable test video, confirm the manual-review
   flow (the one re-verified end-to-end in BL-008C.20) still completes
   through the proxy, not just the direct `.streamlit.app` URL.
5. DNS propagation: TTL on the current records is 3600s (1 hour, per
   `dig`'s returned TTL on the `app` CNAME); worst case the cutover takes
   up to an hour to be visible everywhere, not instant.

### Rollback

Nothing about this proxy touches the Streamlit Cloud app itself or its
code. Two independent rollback levers:
- Remove the Worker's Custom Domain binding -- `app.barrellabsai.com`
  stops resolving through Cloudflare for that specific record.
- If the whole zone migration itself needs to be undone, revert the
  nameservers at the registrar back to NS1's four `dns*.p09.nsone.net`
  servers -- this is the same kind of change as the cutover itself, just
  in reverse, and carries the same "affects the whole domain, not just
  app" caution.
Either lever leaves `barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app`
itself completely unaffected and still reachable directly.

## Interim fallback (zero new accounts, zero DNS zone migration)

If the Cloudflare/nameserver migration is not approved right now, the
smallest safe interim step is a plain HTTP redirect from
`app.barrellabsai.com` to the live `.streamlit.app` URL, added to
whichever repository hosts the Netlify marketing site's `_redirects` /
`netlify.toml` (not this repository -- its source was not located this
session; see Blockers). Exact rule:

```
# netlify.toml, on the marketing-site repo
[[redirects]]
  from = "https://app.barrellabsai.com/*"
  to = "https://barrellabs-swing-yjaqcxes8gr2srqc35anny.streamlit.app/:splat"
  status = 301
  force = true
```

This gets the friend a working link immediately with zero infrastructure
change beyond adding that file to an existing Netlify site and pointing
`app`'s DNS (still on NS1, no migration needed) at Netlify instead of
Render. It does **not** keep `app.barrellabsai.com` visible in the browser
after the redirect fires -- per the BL-008C.21 instructions, this is
reported as the temporary fallback it is, not a substitute for the
Cloudflare path above.
