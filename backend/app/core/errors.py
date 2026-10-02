"""Uniform, production-safe error responses.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

No stack traces, exception messages from internals, submitted input values, or
configuration are ever returned to clients.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var


class VeritasError(Exception):
    """Base class for expected, client-facing domain errors."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(VeritasError):
    status_code = 404
    code = "not_found"


class AuthenticationUnavailableError(VeritasError):
    status_code = 401
    code = "authentication_unavailable"


class AuthenticationFailedError(VeritasError):
    status_code = 401
    code = "invalid_credentials"


class AccessDeniedError(VeritasError):
    status_code = 403
    code = "access_denied"


class DomainRuleViolation(VeritasError):
    status_code = 409
    code = "domain_rule_violation"


class EvidenceConflictError(VeritasError):
    status_code = 409
    code = "evidence_state_conflict"


class EvidenceUploadTooLargeError(VeritasError):
    status_code = 413
    code = "evidence_too_large"


class EvidenceUploadRequestError(VeritasError):
    status_code = 400
    code = "invalid_evidence_upload"


class EvidenceUnsupportedUploadError(VeritasError):
    status_code = 415
    code = "unsupported_evidence_upload_media_type"


class EvidenceStorageUnavailableError(VeritasError):
    status_code = 503
    code = "evidence_storage_unavailable"


class ExaminationConflictError(VeritasError):
    """The request is valid but the examination cannot be created or changed as asked."""

    status_code = 409
    code = "examination_conflict"


class MethodUnavailableError(ExaminationConflictError):
    code = "method_unavailable"


class MethodInapplicableError(ExaminationConflictError):
    code = "method_inapplicable"


class EvidenceObjectNotPreservedError(ExaminationConflictError):
    code = "evidence_object_not_preserved"


class DemonstrationExaminationError(ExaminationConflictError):
    code = "examination_not_permitted_for_demonstration"


class IdempotencyConflictError(ExaminationConflictError):
    code = "idempotency_conflict"


class InvalidLifecycleTransitionError(ExaminationConflictError):
    code = "invalid_lifecycle_transition"


class InvalidMethodParametersError(VeritasError):
    status_code = 422
    code = "invalid_method_parameters"


def error_body(
    code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id_var.get(),
    }
    if details:
        body["details"] = details
    return {"error": body}


_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "access_denied",
    404: "not_found",
    405: "method_not_allowed",
    413: "request_too_large",
    415: "unsupported_media_type",
    429: "rate_limited",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(VeritasError)
    async def _domain_error(_: Request, exc: VeritasError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        # Only framework-generated generic phrases are returned, never internal detail.
        try:
            message = HTTPStatus(exc.status_code).phrase
        except ValueError:
            message = "Error"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Submitted values ("input") are deliberately omitted: they may contain sensitive data.
        details = [
            {
                "location": [str(part) for part in err.get("loc", ())],
                "type": err.get("type", "invalid"),
                "message": err.get("msg", "Invalid value"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "Request validation failed", details),
        )


def internal_error_body() -> dict[str, Any]:
    return error_body("internal_error", "An internal error occurred")
