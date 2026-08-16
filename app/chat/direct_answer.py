"""
Trả lời tra từ bằng template, 0 token. SPEC muc 11.5 bước 6.

Đây là kỹ thuật tiết kiệm token lớn nhất trong cả hệ thống: khoảng 40-50% lưu
lượng là tra từ khớp chính xác, và toàn bộ dữ liệu cần để trả lời đã nằm sẵn
trong thẻ. Gọi LLM để đọc lại một thứ mình đã có là lãng phí thuần tuý.
"""

import re

from app.schemas.card import SourceCard

# Câu hỏi tra từ "đơn giản" là câu chỉ hỏi nghĩa, không kèm yêu cầu gì thêm.
# "resilient nghĩa là gì"            -> đơn giản, trả template được
# "resilient dùng khác gì diligent"  -> KHÔNG, cần LLM so sánh
_SIMPLE_LOOKUP_RE = re.compile(
    r"^\s*(từ\s+)?[\w\s]{0,20}?"
    r"(nghĩa là gì|là gì|có nghĩa gì|nghĩa của (từ )?\w+|định nghĩa( của)?)"
    r"[\s?.!]*$",
    re.IGNORECASE,
)

# Dấu hiệu người dùng muốn nhiều hơn một định nghĩa.
_NEEDS_LLM_RE = re.compile(
    r"\bkhác (gì|nhau)\b"
    r"|\bso sánh\b"
    r"|\bphân biệt\b"
    r"|\btại sao\b"
    r"|\bvì sao\b"
    r"|\bngữ cảnh\b"
    r"|\bchi tiết\b"
    r"|\bthêm\b",
    re.IGNORECASE,
)

MAX_WORDS = 8


def is_simple_lookup(query: str) -> bool:
    if _NEEDS_LLM_RE.search(query):
        return False

    if len(query.split()) > MAX_WORDS:
        return False

    return bool(_SIMPLE_LOOKUP_RE.search(query))


def build_direct_answer(card: SourceCard) -> str:
    """
    Dựng câu trả lời từ chính dữ liệu thẻ.

    Vẫn ghi mã [#id] ở cuối như khi LLM trả lời, để giao diện hiển thị nguồn
    thống nhất giữa hai nhánh.
    """

    head = f"**{card.word}**"

    if card.phonetic:
        head += f" /{card.phonetic.strip('/')}/"

    if card.part_of_speech:
        head += f" ({card.part_of_speech})"

    lines = [head, f"Nghĩa: {card.meaning}"]

    if card.definition_en:
        lines.append(f"Định nghĩa: {card.definition_en}")

    if card.example_sentence:
        lines.append(f"Ví dụ: {card.example_sentence}")

        if card.example_meaning:
            lines.append(f"→ {card.example_meaning}")

    lines.append(f"[#{card.card_id}]")

    return "\n".join(lines)
