"""ASGI request-body size guard.

FastAPI/Starlette parses multipart bodies before the route handler runs, and
Starlette does not apply ``max_part_size`` to file parts.  A route-level
``MAX_UPLOAD_MB`` check therefore happens after the whole upload has already
been spooled to disk.  This middleware rejects oversized bodies before (or
while) they are read, including chunked requests without Content-Length.
"""

from __future__ import annotations

import json

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyTooLarge(Exception):
    """Raised by the wrapped receive callable when the limit is exceeded."""


class BodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_multipart_bytes: int,
        max_json_bytes: int,
    ) -> None:
        self.app = app
        self.max_multipart_bytes = max(1, int(max_multipart_bytes))
        self.max_json_bytes = max(1, int(max_json_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        if method in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_type = headers.get("content-type", "").lower()
        is_multipart = content_type.startswith("multipart/form-data")
        limit = self.max_multipart_bytes if is_multipart else self.max_json_bytes

        content_length = headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > limit:
                await self._reject(send, limit)
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge()
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except BodyTooLarge:
            # If the downstream already started a response there is nothing
            # useful we can add; otherwise send a normal JSON 413.
            if not response_started:
                await self._reject(send, limit)

    @staticmethod
    async def _reject(send: Send, limit: int) -> None:
        payload = json.dumps(
            {
                "detail": (
                    "请求体超过大小限制"
                    f"（{limit // (1024 * 1024)}MB），请压缩文件或拆分后重试。"
                )
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
