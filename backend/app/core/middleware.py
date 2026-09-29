"""Pure-ASGI middleware: request correlation, access logging, security headers, body limits."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body, internal_error_body
from app.core.logging import request_id_var

access_logger = logging.getLogger("veritas.access")
error_logger = logging.getLogger("veritas.errors")

REQUEST_ID_HEADER = "x-request-id"
# Client-supplied correlation IDs are accepted only if they are short and inert.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_DOCS_PATHS = ("/api/docs", "/api/redoc", "/api/openapi.json")

_BASE_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
)
_API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")


async def _send_json(send: Send, status: int, body: dict[str, Any], request_id: str) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (REQUEST_ID_HEADER.encode(), request_id.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class RequestContextMiddleware:
    """Assigns a correlation ID, emits one structured access log line per request,
    and converts unhandled exceptions into a generic, correlated 500 response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_holder = {"code": 500, "started": False}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                status_holder["started"] = True
                headers = [h for h in message.get("headers", []) if h[0].lower() != b"x-request-id"]
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            error_logger.error(
                "unhandled exception",
                exc_info=exc,
                extra={"event": "unhandled_exception", "exception_type": type(exc).__name__},
            )
            if not status_holder["started"]:
                status_holder["code"] = 500
                await _send_json(send, 500, internal_error_body(), request_id)
        finally:
            route = scope.get("route")
            access_logger.info(
                "request",
                extra={
                    "event": "http_request",
                    "method": scope.get("method"),
                    # Route template (e.g. /api/v1/cases/{case_id}) — never the query string.
                    "route": getattr(route, "path", "unmatched"),
                    "status_code": status_holder["code"],
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id_var.reset(token)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope.get("path", "").startswith(_DOCS_PATHS)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_BASE_SECURITY_HEADERS)
                if not is_docs:
                    # API docs (development only) need their own assets; everything else is JSON.
                    headers.append(_API_CSP)
                    headers.append((b"cache-control", b"no-store"))
                if self.hsts:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than ``max_bytes`` (declared or streamed)."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = True
            if too_large:
                await _send_json(
                    send,
                    413,
                    error_body("request_too_large", "Request body exceeds the permitted size"),
                    request_id_var.get() or "",
                )
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await _send_json(
                send,
                413,
                error_body("request_too_large", "Request body exceeds the permitted size"),
                request_id_var.get() or "",
            )


class _BodyTooLarge(Exception):
    pass
