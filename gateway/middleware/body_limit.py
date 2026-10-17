"""Bound request buffering for all routes, including JSON validation/auth."""

import asyncio
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from config import settings


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hosts = [
            value for key, value in scope.get("headers", []) if key.lower() == b"host"
        ]
        try:
            host = hosts[0].decode("ascii") if len(hosts) == 1 else ""
            parsed = urlsplit("//" + host)
            valid_host = (
                bool(parsed.hostname)
                and parsed.netloc == host
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
                and parsed.username is None
                and not any(char.isspace() or char == "\\" for char in host)
            )
            _ = parsed.port
        except (ValueError, UnicodeError):
            valid_host = False
        if not valid_host or not scope.get("path", "").startswith("/"):
            await JSONResponse(
                {"detail": "invalid request target or host"}, status_code=400
            )(scope, receive, send)
            return
        chunks = []
        size = 0
        try:
            while True:
                message = await asyncio.wait_for(receive(), timeout=30)
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > settings.max_request_body_bytes:
                    await JSONResponse(
                        {"detail": "request body too large"}, status_code=413
                    )(scope, receive, send)
                    return
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
        except TimeoutError:
            await JSONResponse({"detail": "request body timeout"}, status_code=408)(
                scope, receive, send
            )
            return
        body = b"".join(chunks)
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
