"""Xác thực X-Internal-Token. SPEC muc 8."""

import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.core.errors import Unauthorized

# Khai báo dưới dạng security scheme chứ không phải `Header()` thường. Khác
# biệt duy nhất về hành vi là KHÔNG có — nhưng Swagger UI thì khác hẳn: nó hiện
# nút "Authorize", dán token một lần là dùng cho mọi endpoint, thay vì bắt gõ
# lại header ở từng ô "Try it out".
#
# `auto_error=False` để tự ném `Unauthorized` của mình, giữ đúng hình dạng
# {"error": {"code": ..., "message": ...}} ở SPEC muc 8.6 thay vì để FastAPI trả
# {"detail": "Not authenticated"} lạc quẻ.
internal_token_scheme = APIKeyHeader(
    name="X-Internal-Token",
    scheme_name="X-Internal-Token",
    description=(
        "Khớp với biến môi trường `AI_INTERNAL_TOKEN`.\n\n"
        "**Chỉ dán phần SAU dấu `=`.** Copy cả dòng trong `.env` là lỗi hay gặp nhất "
        "ở bước này, và nó trả về `401` giống hệt như khi sai token thật:\n\n"
        "```\n"
        "Trong .env:  AI_INTERNAL_TOKEN=c5d4e254195e0d01...\n"
        "Dán vào đây:                   c5d4e254195e0d01...\n"
        "```\n\n"
        "Không dán dấu nháy, không dán khoảng trắng thừa ở hai đầu.\n\n"
        "Đây **không phải** `AI_BACKEND_TOKEN`. Hai token đi ngược chiều nhau: "
        "`AI_INTERNAL_TOKEN` để backend Java gọi vào fsoft-ai, còn `AI_BACKEND_TOKEN` "
        "để fsoft-ai gọi ngược sang backend Java."
    ),
    auto_error=False,
)


def require_internal_token(
    request: Request,
    token: Annotated[str | None, Depends(internal_token_scheme)] = None,
) -> None:
    """
    So sánh bằng `secrets.compare_digest` chứ không phải `==`.

    `==` trên chuỗi thoát sớm ở ký tự khác nhau đầu tiên, để lộ độ dài tiền tố
    đúng qua thời gian phản hồi.

    So trên BYTES chứ không trên str: `compare_digest` ném
    `TypeError: comparing strings with non-ASCII characters is not supported`,
    mà header HTTP được decode theo latin-1 nên một client gửi
    `X-Internal-Token: café` sẽ làm endpoint trả 500 thay vì 401. Encode sang
    UTF-8 vẫn giữ nguyên tính so sánh thời gian hằng.
    """

    expected = request.app.state.service.settings.ai_internal_token

    if not token or not secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise Unauthorized("Thiếu hoặc sai X-Internal-Token")
