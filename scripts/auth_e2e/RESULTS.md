# BL-008C.32 — real-browser auth + experimental-path evidence (2026-10-21, local)

All runs: the REAL `app.py` in Chrome (claude-in-chrome), Streamlit 1.57.0,
against `fake_supabase.py` (GoTrue + PostgREST stand-in: real single-use
refresh-token rotation, 10 s reuse interval, logout/revocation, usage RPCs
mirroring production — idempotency migration NOT applied). Fake `@example.test`
accounts generated locally at startup (gitignored, never committed). No
production account, data, or quota used.

**Cookie-stripping proxy.** Every auth run below went through
`cookie_stripping_proxy.py`, which drops all request cookies except
`_streamlit_xsrf` — what Streamlit Community Cloud's proxy is documented to do
("filters some headers and almost all cookies in the proxy layer", Streamlit
staff, discuss.streamlit.io/t/80597). Without it, local tests pass for code
that cannot work in production.

## Root cause, reproduced

| Code | Login | Refresh | Backend events |
|---|---|---|---|
| production 9cae39c, behind proxy | dashboard, cookie set in browser | **login form** | 1 password login, **0 refresh grants** |
| fixed, behind proxy | dashboard, cookie write acked | **dashboard** | login, then a rotation on each restore |

Production Supabase logs (2026-10-21 23:30 → 10-22 01:45 UTC) show the same
signature as the reproduced failure: 6 password-grant requests (5 succeeded),
**zero** refresh_token grants, and each of those sessions kept a single
never-exchanged refresh token.

## Persistent-login matrix (fixed code, behind proxy)

| Case | Result |
|---|---|
| login → refresh | BLANK → RESTORING → APP (never the login form) |
| navigate (LIBRARY, SESSIONS) → refresh | still signed in |
| app server restart (≈ redeploy) → reload | restored |
| new tab | restored straight into APP |
| access token expiry mid-session | 1 refresh, conditional cookie write, ack `landed=True`, no hidden timer rotation |
| token rotated during "Analyze swing" | analysis completes, swing saved, 1 charge (regression found and fixed, see below) |
| two tabs; tab 2 rotates tab 1's token; tab 1 used after expiry | tab 1 refresh fails (`already_used`), resets, restores from the newer cookie in ≈200 ms; tab 2 refresh → APP |
| explicit logout → refresh | server `logout scope=local`, clear ack `landed=True`, login form, no restore attempt |
| account switch (logout A, login B) | B's identity only; no A data |
| corrupted cookie | rejected (`not_found`) → conditional clear → login form (≈170 ms) |
| server-side revocation | rejected (`session_revoked`) → cookie cleared → login form |
| Supabase outage during restore | "couldn't reach the sign-in service" notice, cookie KEPT; after recovery → restored |

## Defects found by these runs and fixed (not visible in unit tests)

1. Production-code process: the SDK's background auto-refresh timer kept
   rotating a closed tab's tokens in pairs every ~80 s, forever (source of the
   887-rotation session seen in production).
2. Bridge v1 re-applied a late write over a newer token from another tab →
   compare-and-set writes/clears.
3. "Restoring" screen could hang after a rejected cookie → one guarded
   delivery rerun.
4. Bridge reports triggered reruns that cut short an in-flight "Analyze swing"
   → reports only while the auth gate needs them.

## Experimental path (ffprobe absent from the app's PATH)

- Real upload of a disposable copy of the owner's `IMG_9005.MOV` → "Select &
  confirm manually (experimental)" → decoded (103 frames, 30 fps from PTS) →
  interval selection page. No ffprobe error. (Every selection on this
  backlit clip is then blocked as "multiple movements" by the engine's own
  validation — no charge.)
- Full flow through the real page in AppTest:
  `tests/test_experimental_path_integration.py` (MP4 + MOV container; with
  the pre-hotfix decoder swapped in it renders the owner's exact error).
- Normal "Analyze swing" (sample clip): report rendered, "Saved to your
  Sessions", 1 swing record, 1 charge; refresh afterwards → still signed in,
  no second charge.

Original footage hashes (SHA-256) before = after:
the owner's original `IMG_9005.MOV` a2c3cf68…181e, `assets/sample_swing.mp4` 1c939a2f…d0a6.
