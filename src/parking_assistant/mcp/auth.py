"""Bearer authentication middleware for the local MCP HTTP endpoint."""

import secrets
from typing import cast

from pydantic import SecretStr
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class MCPBearerAuthMiddleware:
    """Protect every MCP transport request with one configured bearer token."""

    def __init__(self, app: ASGIApp, token: SecretStr) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith("/mcp"):
            await self._app(scope, receive, send)
            return
        supplied = _bearer_token(scope)
        expected = self._token.get_secret_value().encode("utf-8")
        if supplied is None or not secrets.compare_digest(supplied, expected):
            await _unauthorized(send)
            return
        await self._app(scope, receive, send)


def _bearer_token(scope: Scope) -> bytes | None:
    headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
    for key, value in headers:
        if key.lower() != b"authorization":
            continue
        scheme, separator, credentials = value.partition(b" ")
        if separator and scheme.lower() == b"bearer" and credentials:
            return credentials
    return None


async def _unauthorized(send: Send) -> None:
    body = b'{"detail":"MCP authentication required"}'
    start: Message = {
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"www-authenticate", b"Bearer"),
        ],
    }
    await send(start)
    await send({"type": "http.response.body", "body": body})
