"""
Hình dạng phản hồi lỗi, khai báo cho OpenAPI. SPEC muc 8.6.

Không có file này thì Swagger chỉ hiện đúng mã 200 và một ô 422 do FastAPI tự
sinh — người gọi không hề biết 400/401/429/503 tồn tại, cũng không biết chúng
có dạng gì. Mà đó lại chính là những mã họ phải xử lý.
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(
        description="Mã lỗi ổn định, dùng để rẽ nhánh trong code. Đừng khớp theo `message`.",
        examples=["INVALID_SCOPE"],
    )
    message: str = Field(
        description="Câu tiếng Việt hiển thị được cho người dùng cuối.",
        examples=["allowed_deck_ids không được rỗng."],
    )
    retry_after_seconds: int | None = Field(
        default=None,
        description="Chỉ có ở 429 và 503. Số giây nên đợi trước khi thử lại.",
        examples=[12],
    )


class ErrorResponse(BaseModel):
    """Mọi lỗi đều trả đúng hình dạng này, không bao giờ lộ traceback."""

    error: ErrorDetail


def _vi_du(code: str, message: str, **them: Any) -> dict:
    return {"value": {"error": {"code": code, "message": message, **them}}}


# Ánh xạ đúng bảng ở SPEC muc 8.6. Gắn vào từng endpoint qua tham số
# `responses=` để Swagger hiện đủ các mã lỗi có thật.

UNAUTHORIZED: dict = {
    401: {
        "model": ErrorResponse,
        "description": "Thiếu hoặc sai `X-Internal-Token`.",
        "content": {
            "application/json": {
                "examples": {
                    "thieu_token": _vi_du("UNAUTHORIZED", "Thiếu hoặc sai X-Internal-Token"),
                }
            }
        },
    }
}

SCOPE_ERRORS: dict = {
    400: {
        "model": ErrorResponse,
        "description": "Phạm vi deck không hợp lệ, hoặc tham số sai.",
        "content": {
            "application/json": {
                "examples": {
                    "danh_sach_rong": _vi_du(
                        "INVALID_SCOPE",
                        "allowed_deck_ids không được rỗng.",
                    ),
                    "ngoai_pham_vi": _vi_du(
                        "INVALID_SCOPE",
                        "scope_deck_id=9 không nằm trong allowed_deck_ids.",
                    ),
                }
            }
        },
    }
}

NOT_READY: dict = {
    503: {
        "model": ErrorResponse,
        "description": "Model chưa nạp xong hoặc index chưa sẵn sàng. Đợi `/readyz` trả 200.",
        "content": {
            "application/json": {
                "examples": {
                    "chua_san_sang": _vi_du(
                        "INDEX_NOT_READY",
                        "Model chưa nạp xong hoặc index chưa sẵn sàng.",
                    ),
                    "nha_cung_cap_hong": _vi_du(
                        "PROVIDER_UNAVAILABLE",
                        "Groq không phản hồi, vui lòng thử lại sau.",
                        retry_after_seconds=30,
                    ),
                }
            }
        },
    }
}

BUDGET: dict = {
    429: {
        "model": ErrorResponse,
        "description": (
            "Cạn ngân sách token của cả service (`AI_GLOBAL_TOKENS_PER_MINUTE`). "
            "Đây là hạn mức TOÀN CỤC, không phải theo người dùng — backend nên hiện "
            "thông báo chờ chứ đừng coi là lỗi của người gọi."
        ),
        "content": {
            "application/json": {
                "examples": {
                    "het_ngan_sach": _vi_du(
                        "BUDGET_EXHAUSTED",
                        "Hệ thống đang bận, vui lòng thử lại sau ít giây.",
                        retry_after_seconds=17,
                    ),
                }
            }
        },
    }
}
