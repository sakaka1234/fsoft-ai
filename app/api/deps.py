"""Xác thực X-Internal-Token. SPEC muc 8."""

import secrets
from typing import Annotated

from fastapi import Header, Request

from app.core.errors import Unauthorized


def require_internal_token(
    request: Request,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """
    So sánh bằng `secrets.compare_digest` chứ không phải `==`.

    `==` trên chuỗi thoát sớm ở ký tự khác nhau đầu tiên, để lộ độ dài tiền tố
    đúng qua thời gian phản hồi.
    """

    expected = request.app.state.service.settings.ai_internal_token

    if not x_internal_token or not secrets.compare_digest(x_internal_token, expected):
        raise Unauthorized("Thiếu hoặc sai X-Internal-Token")
