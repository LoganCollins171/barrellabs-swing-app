"""BL-008C.32 -- local stand-in for Streamlit Community Cloud's proxy, which
"filters some headers and almost all cookies" before requests reach the app
(Streamlit staff, discuss.streamlit.io/t/80597). Forwards HTTP and the
/_stcore/stream websocket to a local Streamlit server, dropping every request
cookie except Streamlit's own `_streamlit_xsrf`.

Without this, local tests pass for code that can never work in production:
st.context.cookies sees the cookie locally and never on Community Cloud.

Uses only packages already in the app's venv (starlette, uvicorn, httpx,
websockets). Usage: python cookie_stripping_proxy.py <listen_port> <upstream_port>
"""
from __future__ import annotations

import asyncio
import sys

import httpx
import uvicorn
import websockets
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

KEEP = {"_streamlit_xsrf"}
LISTEN, UPSTREAM = int(sys.argv[1]), int(sys.argv[2])
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
       "content-encoding"}
STRIPPED = {"count": 0}


def _strip(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    parts = [c.strip() for c in cookie_header.split(";") if c.strip()]
    kept = [c for c in parts if c.split("=", 1)[0] in KEEP]
    if len(kept) != len(parts):
        STRIPPED["count"] += 1
    return "; ".join(kept) or None


def _fwd(headers) -> list[tuple[str, str]]:
    out = [(k, v) for k, v in headers.items()
           if k.lower() not in HOP and k.lower() != "cookie"
           and not k.lower().startswith("sec-websocket")]
    c = _strip(headers.get("cookie"))
    if c:
        out.append(("cookie", c))
    return out


client = httpx.AsyncClient(timeout=600, follow_redirects=False)


async def http_proxy(request: Request) -> Response:
    url = f"http://127.0.0.1:{UPSTREAM}{request.url.path}"
    if request.url.query:
        url += "?" + request.url.query
    body = await request.body()
    r = await client.request(request.method, url, headers=_fwd(request.headers), content=body)
    headers = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in HOP]
    resp = Response(content=r.content, status_code=r.status_code)
    for k, v in headers:
        resp.headers.append(k, v)
    return resp


async def ws_proxy(ws: WebSocket):
    protos = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    uri = f"ws://127.0.0.1:{UPSTREAM}{ws.url.path}"
    if ws.url.query:
        uri += "?" + ws.url.query
    up = await websockets.connect(uri, subprotocols=protos or None,
                                  additional_headers=_fwd(ws.headers), max_size=None)
    await ws.accept(subprotocol=up.subprotocol)

    async def down():
        try:
            async for m in up:
                if isinstance(m, bytes):
                    await ws.send_bytes(m)
                else:
                    await ws.send_text(m)
        except Exception:
            pass

    async def upward():
        try:
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    break
                if m.get("bytes") is not None:
                    await up.send(m["bytes"])
                elif m.get("text") is not None:
                    await up.send(m["text"])
        except (WebSocketDisconnect, Exception):
            pass

    t1, t2 = asyncio.create_task(down()), asyncio.create_task(upward())
    await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in (t1, t2):
        t.cancel()
    await up.close()
    try:
        await ws.close()
    except Exception:
        pass


async def stats(request: Request) -> Response:
    return Response(f'{{"requests_with_cookies_stripped": {STRIPPED["count"]}}}',
                    media_type="application/json")


app = Starlette(routes=[
    Route("/__proxy_stats", stats),
    WebSocketRoute("/_stcore/stream", ws_proxy),
    Route("/{path:path}", http_proxy, methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"]),
])

if __name__ == "__main__":
    print(f"cookie-stripping proxy 127.0.0.1:{LISTEN} -> :{UPSTREAM} (keeps {sorted(KEEP)})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=LISTEN, log_level="warning", ws="websockets")
