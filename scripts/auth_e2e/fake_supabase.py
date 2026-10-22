"""BL-008C.32 -- local fake Supabase (GoTrue auth + minimal PostgREST) for
real-browser auth E2E tests of the REAL app.py. Never talks to the real
project. On first start it GENERATES fake accounts with random passwords
(fixtures.json) and the app's E2E secrets file (secrets_e2e.toml). Both are
gitignored and never committed: this repo is public.

GoTrue semantics reproduced (the ones the bug and fix depend on):
  * password grant -> new session with a refresh token
  * refresh_token grant -> rotation; the presented token becomes revoked
    (single use). A revoked token presented again within REUSE_INTERVAL_S
    returns the session's CURRENT token (GoTrue's reuse interval); after
    that it is rejected "Invalid Refresh Token: Already Used".
  * POST /logout?scope=local -> the session and all its tokens revoked
  * GET /user -> 403 once the session is revoked
Access tokens are unsigned JWT-shaped strings with a real `exp` (supabase-py
decodes the payload). ACCESS_TTL_S is short on purpose so expiry + refresh
happens inside a test run.

Every auth decision is appended to events.jsonl with token SERIAL numbers
only -- never token values.

Usage: python fake_supabase.py [port]
       then: streamlit run app.py --secrets.files scripts/auth_e2e/secrets_e2e.toml
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURES_PATH = os.path.join(HERE, "fixtures.json")
_SECRETS_PATH = os.path.join(HERE, "secrets_e2e.toml")
_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 54399


def _write_private(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def _ensure_local_files() -> None:
    """Generate (once) fake accounts and the E2E secrets file. Local only."""
    if not os.path.exists(_FIXTURES_PATH):
        users = []
        for label in ("alpha", "bravo"):
            uid = str(uuid.uuid4())
            email = f"e2e-{label}@example.test"
            users.append({"label": label, "id": uid, "email": email,
                          "password": "e2e-" + secrets.token_urlsafe(12),
                          "player": {"id": str(uuid.uuid4()), "user_id": uid,
                                     "name": f"E2E {label.title()}", "email": email,
                                     "handedness": "RIGHT", "created_at": "2026-01-01T00:00:00Z",
                                     "removed_at": None}})
        _write_private(_FIXTURES_PATH, json.dumps(
            {"_note": "LOCAL E2E FIXTURES ONLY (generated, gitignored).", "users": users}, indent=1))
    if not os.path.exists(_SECRETS_PATH):
        _write_private(_SECRETS_PATH,
                       "# LOCAL E2E ONLY (generated, gitignored): points the app at fake_supabase.py.\n"
                       f"[supabase]\nurl = \"http://127.0.0.1:{_PORT}\"\npublishable_key = \"e2e-anon-key\"\n")


_ensure_local_files()
FIXTURES = json.load(open(_FIXTURES_PATH))
EVENTS = os.path.join(HERE, "events.jsonl")
ACCESS_TTL_S = int(os.environ.get("E2E_ACCESS_TTL_S", "90"))
REUSE_INTERVAL_S = int(os.environ.get("E2E_REUSE_INTERVAL_S", "10"))

_lock = threading.Lock()
USERS = {u["email"]: u for u in FIXTURES["users"]}
SESSIONS: dict[str, dict] = {}      # session_id -> {user, revoked, current_rt}
TOKENS: dict[str, dict] = {}        # refresh token -> {serial, session_id, revoked, revoked_at}
_serial = [0]
OUTAGE = {"on": False}
USAGE: dict[str, int] = {}          # user id -> free_swings_used (charges)
SWINGS: list[dict] = []             # rows inserted into `swings`   # toggled via POST /__outage?on=1 to simulate a Supabase outage


def _event(**kw):
    kw["t"] = round(time.time(), 3)
    with open(EVENTS, "a") as f:
        f.write(json.dumps(kw) + "\n")


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


def _jwt(user: dict, session_id: str) -> str:
    now = int(time.time())
    payload = {"sub": user["id"], "email": user["email"], "role": "authenticated",
               "aud": "authenticated", "session_id": session_id,
               "iat": now, "exp": now + ACCESS_TTL_S}
    return _b64({"alg": "HS256", "typ": "JWT"}) + "." + _b64(payload) + ".e2esig"


def _user_json(user: dict) -> dict:
    return {"id": user["id"], "aud": "authenticated", "role": "authenticated",
            "email": user["email"], "app_metadata": {"provider": "email"},
            "user_metadata": {}, "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z", "identities": []}


def _new_rt(session_id: str) -> str:
    _serial[0] += 1
    rt = secrets.token_urlsafe(24)
    TOKENS[rt] = {"serial": _serial[0], "session_id": session_id,
                  "revoked": False, "revoked_at": None}
    SESSIONS[session_id]["current_rt"] = rt
    return rt


def _session_json(session_id: str) -> dict:
    s = SESSIONS[session_id]
    at = _jwt(s["user"], session_id)
    return {"access_token": at, "token_type": "bearer", "expires_in": ACCESS_TTL_S,
            "expires_at": int(time.time()) + ACCESS_TTL_S,
            "refresh_token": s["current_rt"], "user": _user_json(s["user"])}


def _claims(auth_header: str | None) -> dict | None:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    tok = auth_header.split(" ", 1)[1]
    parts = tok.split(".")
    if len(parts) != 3:
        return None
    try:
        pad = "=" * (-len(parts[1]) % 4)
        c = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:
        return None
    if "session_id" not in c:
        return None  # the anon key
    s = SESSIONS.get(c["session_id"])
    if not s or s["revoked"] or c["exp"] < time.time():
        return {"invalid": True}
    return c


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=None, headers=None):
        raw = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return None
        try:
            return json.loads(self.rfile.read(n))
        except Exception:
            return None

    def _auth_err(self, code, msg, error_code):
        self._send(code, {"code": code, "error_code": error_code, "msg": msg,
                          "error": error_code, "error_description": msg})

    # ------------------------------------------------------------------ auth
    def _token(self, q):
        body = self._body() or {}
        grant = (q.get("grant_type") or [""])[0]
        with _lock:
            if grant == "password":
                u = USERS.get((body.get("email") or "").lower())
                if not u or u["password"] != body.get("password"):
                    _event(ev="password_login", ok=False)
                    return self._auth_err(400, "Invalid login credentials", "invalid_credentials")
                sid = str(uuid.uuid4())
                SESSIONS[sid] = {"user": u, "revoked": False, "current_rt": None}
                _new_rt(sid)
                _event(ev="password_login", ok=True, user=u["label"],
                       session=sid[:8], rt_serial=TOKENS[SESSIONS[sid]["current_rt"]]["serial"])
                return self._send(200, _session_json(sid))
            if grant == "refresh_token":
                rt = body.get("refresh_token") or ""
                meta = TOKENS.get(rt)
                if not meta:
                    _event(ev="refresh", ok=False, reason="not_found")
                    return self._auth_err(400, "Invalid Refresh Token: Refresh Token Not Found",
                                          "refresh_token_not_found")
                s = SESSIONS[meta["session_id"]]
                if s["revoked"]:
                    _event(ev="refresh", ok=False, reason="session_revoked", rt_serial=meta["serial"])
                    return self._auth_err(400, "Invalid Refresh Token: Session Expired (Revoked by Newer Login)",
                                          "session_expired")
                if meta["revoked"]:
                    if time.time() - meta["revoked_at"] <= REUSE_INTERVAL_S:
                        _event(ev="refresh", ok=True, reason="reuse_interval", rt_serial=meta["serial"],
                               new_serial=TOKENS[s["current_rt"]]["serial"])
                        return self._send(200, _session_json(meta["session_id"]))
                    _event(ev="refresh", ok=False, reason="already_used", rt_serial=meta["serial"])
                    return self._auth_err(400, "Invalid Refresh Token: Already Used",
                                          "refresh_token_already_used")
                meta["revoked"], meta["revoked_at"] = True, time.time()
                new = _new_rt(meta["session_id"])
                _event(ev="refresh", ok=True, reason="rotated", rt_serial=meta["serial"],
                       new_serial=TOKENS[new]["serial"], user=s["user"]["label"])
                return self._send(200, _session_json(meta["session_id"]))
        return self._auth_err(400, "unsupported grant", "unsupported_grant_type")

    def _user(self):
        c = _claims(self.headers.get("Authorization"))
        if not c or c.get("invalid"):
            return self._auth_err(403, "Session from session_id claim in JWT does not exist",
                                  "session_not_found")
        return self._send(200, _user_json(USERS[c["email"]]))

    def _logout(self, q):
        c = _claims(self.headers.get("Authorization"))
        if not c or c.get("invalid"):
            _event(ev="logout", ok=False)
            return self._auth_err(403, "invalid JWT", "bad_jwt")
        with _lock:
            s = SESSIONS[c["session_id"]]
            s["revoked"] = True
            for meta in TOKENS.values():
                if meta["session_id"] == c["session_id"]:
                    meta["revoked"], meta["revoked_at"] = True, 0
        _event(ev="logout", ok=True, scope=(q.get("scope") or ["global"])[0],
               session=c["session_id"][:8], user=s["user"]["label"])
        self.send_response(204)
        self.end_headers()

    # ------------------------------------------------------------------ rest
    def _rest(self, method, path, q):
        c = _claims(self.headers.get("Authorization"))
        if c and c.get("invalid"):
            return self._send(401, {"code": "PGRST301", "message": "JWT expired"})
        uid = c["sub"] if c else None
        table = path.split("/rest/v1/", 1)[1]
        rows: list = []
        if table == "players" and method == "GET" and uid:
            u = next(x for x in USERS.values() if x["id"] == uid)
            rows = [u["player"]]
        elif table == "players" and method == "PATCH" and uid:
            # Like PostgREST with return=representation: the FULL updated row.
            u = next(x for x in USERS.values() if x["id"] == uid)
            b = self._body() or {}
            if isinstance(b, dict):
                u["player"].update({k: v for k, v in b.items() if k not in ("id", "user_id")})
            rows = [u["player"]]
        elif table == "v_my_plan" and method == "GET" and uid:
            rows = [{"user_id": uid, "plan": "free", "status": "active",
                     "free_swings_used": USAGE.get(uid, 0), "free_swing_limit": 3}]
        elif table == "rpc/increment_free_swing_usage_idempotent":
            # Mirrors production: the idempotency migration is NOT applied.
            return self._send(404, {"code": "PGRST202", "message":
                "Could not find the function public.increment_free_swing_usage_idempotent"})
        elif table == "rpc/increment_free_swing_usage" and uid:
            with _lock:
                USAGE[uid] = USAGE.get(uid, 0) + 1
                n = USAGE[uid]
            label = next(x["label"] for x in USERS.values() if x["id"] == uid)
            _event(ev="usage_charge", user=label, free_swings_used=n)
            return self._send(200, n)
        elif table.startswith("rpc/"):
            return self._send(200, None)
        elif table == "swings" and method == "POST" and uid:
            b = self._body()
            rows = b if isinstance(b, list) else ([b] if b else [])
            SWINGS.extend(rows)
            label = next(x["label"] for x in USERS.values() if x["id"] == uid)
            _event(ev="swing_record_insert", user=label,
                   legacy_fields=sorted(k for r in rows for k in r
                                        if k in ("edge_score", "mlb_match", "mlb_comp", "score")))
            return self._send(201, rows)
        elif table == "__usage":
            rows = [{"user_id": k, "free_swings_used": v} for k, v in USAGE.items()]
        elif method in ("POST", "PATCH"):
            b = self._body()
            rows = b if isinstance(b, list) else ([b] if b else [])
        accept = self.headers.get("Accept") or ""
        if "vnd.pgrst.object" in accept:
            if not rows:
                return self._send(406, {"code": "PGRST116", "message": "0 rows"})
            return self._send(200, rows[0])
        self._send(200, rows, {"Content-Range": f"0-{max(len(rows)-1,0)}/{len(rows)}"})

    def _dispatch(self, method):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/__outage" and method == "POST":
            OUTAGE["on"] = (q.get("on") or ["0"])[0] == "1"
            _event(ev="outage", on=OUTAGE["on"])
            return self._send(200, OUTAGE)
        if u.path == "/__revoke_user" and method == "POST":
            label = (q.get("label") or [""])[0]
            with _lock:
                for sid, sess in SESSIONS.items():
                    if sess["user"]["label"] == label:
                        sess["revoked"] = True
            _event(ev="admin_revoke_all_sessions", user=label)
            return self._send(200, {"revoked": label})
        if OUTAGE["on"] and u.path.startswith(("/auth/", "/rest/")):
            return self._send(503, {"message": "upstream connect error or disconnect/reset before headers"})
        if u.path == "/auth/v1/token" and method == "POST":
            return self._token(q)
        if u.path == "/auth/v1/user" and method == "GET":
            return self._user()
        if u.path == "/auth/v1/logout" and method == "POST":
            return self._logout(q)
        if u.path.startswith("/rest/v1/"):
            return self._rest(method, u.path, q)
        if u.path == "/__state":
            with _lock:
                return self._send(200, {
                    "sessions": {k[:8]: {"user": v["user"]["label"], "revoked": v["revoked"],
                                         "current_serial": TOKENS[v["current_rt"]]["serial"]}
                                 for k, v in SESSIONS.items()}})
        self._send(404, {"msg": "not found"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_HEAD(self):
        self._dispatch("GET")


if __name__ == "__main__":
    port = _PORT
    open(EVENTS, "w").close()
    print(f"fake supabase on 127.0.0.1:{port} access_ttl={ACCESS_TTL_S}s reuse={REUSE_INTERVAL_S}s", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
