"""
Lỗi nghiệp vụ và định dạng phản hồi lỗi. SPEC muc 8.6.

Mọi lỗi ra ngoài đều có dạng:

    { "error": { "code": ..., "message": ..., "retry_after_seconds": ... } }

Không bao giờ để traceback lọt ra response.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

log = get_logger(__name__)


class AppError(Exception):
    """Lỗi có mã, ánh xạ thẳng sang bảng ở SPEC muc 8.6."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds

    def to_dict(self) -> dict:
        error: dict = {"code": self.code, "message": self.message}

        if self.retry_after_seconds is not None:
            error["retry_after_seconds"] = self.retry_after_seconds

        return {"error": error}


class InvalidScope(AppError):
    code = "INVALID_SCOPE"
    http_status = 400


class InvalidRequest(AppError):
    code = "INVALID_REQUEST"
    http_status = 400


class Unauthorized(AppError):
    code = "UNAUTHORIZED"
    http_status = 401


class BudgetExhausted(AppError):
    code = "BUDGET_EXHAUSTED"
    http_status = 429


class ProviderUnavailable(AppError):
    code = "PROVIDER_UNAVAILABLE"
    http_status = 503


class IndexNotReady(AppError):
    code = "INDEX_NOT_READY"
    http_status = 503


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log đầy đủ ở phía server, nhưng KHÔNG trả traceback ra response.
        # Starlette gọi handler này từ trong khối except nên .exception() bắt
        # được traceback thật.
        log.exception(
            "unhandled_exception",
            path=request.url.path,
            error=str(exc),
            error_type=type(exc).__name__,
        )

        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Lỗi nội bộ, vui lòng thử lại sau.",
                }
            },
        )
