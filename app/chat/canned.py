"""
Câu trả lời mẫu cố định. SPEC muc 11.5 bước 5.

SMALLTALK và OUT_OF_SCOPE không cần LLM. Đây là phần dễ nhất của mục tiêu
"≥ 40% lưu lượng không tốn token" ở SPEC muc 11.8.
"""

from app.schemas.chat import Intent

_SMALLTALK = (
    "Chào bạn! Mình là trợ lý học từ vựng tiếng Anh. "
    "Bạn có thể hỏi mình nghĩa của một từ trong bộ thẻ, xin ví dụ đặt câu, "
    "hỏi ngữ pháp, hoặc nhờ mình tạo bài kiểm tra."
)

_OUT_OF_SCOPE = (
    "Xin lỗi, mình chỉ hỗ trợ việc học tiếng Anh thôi — nghĩa của từ, cách "
    "dùng, ngữ pháp và luyện tập với bộ thẻ của bạn. Bạn thử hỏi mình về một "
    "từ vựng nhé."
)

CANNED_ANSWERS: dict[Intent, str] = {
    Intent.SMALLTALK: _SMALLTALK,
    Intent.OUT_OF_SCOPE: _OUT_OF_SCOPE,
}


def canned_answer(intent: Intent) -> str | None:
    return CANNED_ANSWERS.get(intent)
