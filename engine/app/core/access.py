"""Who may use the engine. On this computer (loopback) anyone may: that's the desktop app,
and CLI agents' bridge. From anywhere else (server mode, services/server.py) a request needs
the access code, given once as ``?access=<code>`` (kept in a cookie), in an
``Authorization: Bearer`` header, or typed into the sign-in page this serves.
"""

from __future__ import annotations

import hmac
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlencode

COOKIE = "pantheon_access"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_code: dict[str, str] = {"value": ""}


def set_code(code: str) -> None:
    _code["value"] = code


def valid(given: str | None) -> bool:
    code = _code["value"]
    return bool(code and given) and hmac.compare_digest(str(given).encode(), code.encode())


SIGN_IN = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Pantheon</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d0e12;color:#e8e8ee;
font:15px system-ui,sans-serif}form{width:min(340px,90vw);display:grid;gap:12px}
input,button{font:inherit;padding:10px 12px;border-radius:8px;border:1px solid #2c2e38;
background:#16171d;color:inherit}button{background:#5b4cf0;border:0;cursor:pointer}
p{color:#9a9bab;margin:0;font-size:13px}.bad{color:#f87171}</style></head><body>
<form method="post" action="/access"><strong>Pantheon</strong>
<p>Enter the access code shown in Pantheon's Settings, under Server.</p>%s
<input name="code" autocomplete="current-password" autofocus placeholder="Access code">
<button>Open</button></form></body></html>"""


class AccessMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket") or _local(scope):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        cookie = SimpleCookie(headers.get("cookie", "")).get(COOKIE)
        bearer = headers.get("authorization", "")
        query = parse_qs(scope.get("query_string", b"").decode())
        if valid(cookie.value if cookie else None) or (
                bearer.lower().startswith("bearer ") and valid(bearer[7:].strip())):
            return await self.app(scope, receive, send)
        given = (query.get("access") or [None])[0]
        if scope["type"] == "websocket":
            if valid(given):
                return await self.app(scope, receive, send)
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 4401})
            return
        path, method = scope.get("path", "/"), scope.get("method", "GET")
        if valid(given):  # a link with the code: remember it and drop it from the address
            rest = {k: v for k, v in query.items() if k != "access"}
            target = path + ("?" + urlencode(rest, doseq=True) if rest else "")
            return await _respond(send, 303, b"", [(b"location", target.encode()), _cookie(given)])
        if path == "/access" and method == "POST":
            body = parse_qs((await _body(receive)).decode())
            code = (body.get("code") or [""])[0].strip()
            if valid(code):
                return await _respond(send, 303, b"", [(b"location", b"/"), _cookie(code)])
            return await _page(send, 401, '<p class="bad">That code isn\'t right.</p>')
        if path.startswith("/api/"):
            return await _respond(send, 401, b'{"detail":"an access code is needed"}',
                                  [(b"content-type", b"application/json")])
        return await _page(send, 401, "")


def _local(scope: dict) -> bool:
    client = scope.get("client")
    return client is None or client[0] in LOOPBACK


def _cookie(code: str) -> tuple[bytes, bytes]:
    return (b"set-cookie", f"{COOKIE}={code}; Path=/; Max-Age=31536000; HttpOnly; "
                           "SameSite=Lax".encode())


async def _body(receive: Any) -> bytes:
    chunks = []
    while True:
        msg = await receive()
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body") or sum(map(len, chunks)) > 8192:
            return b"".join(chunks)


async def _page(send: Any, status: int, note: str) -> None:
    await _respond(send, status, (SIGN_IN % note).encode(),
                   [(b"content-type", b"text/html; charset=utf-8")])


async def _respond(send: Any, status: int, body: bytes, headers: list[tuple[bytes, bytes]]) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [*headers, (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
