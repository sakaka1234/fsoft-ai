"""
Chọn đáp án nhiễu bằng embedding. SPEC muc 11.6.

Vì sao không chọn ngẫu nhiên: hỏi `resilient` mà nhiễu là `deforestation` thì
ai cũng loại được, câu hỏi thành vô nghĩa. Vì sao không để LLM bịa: tốn token
và hay trùng lặp.

Láng giềng embedding cho nhiễu VỪA ĐỦ GIỐNG ĐỂ KHÓ, VỪA ĐỦ KHÁC ĐỂ KHÔNG MƠ HỒ.
Với fixture, hỏi `resilient` sẽ ra nhiễu `diligent`, `meticulous`, `empathetic`
— cùng là tính từ mô tả con người.
"""

import re
import unicodedata

from app.retrieval.search_index import SearchIndex
from app.schemas.card import SourceCard

# Lấy láng giềng hạng 2-12. Hạng 1 chính là thẻ đang hỏi.
NEIGHBOUR_POOL = 12


def normalize_meaning(text: str) -> str:
    """Chuẩn hoá để so trùng: bỏ dấu câu, gộp khoảng trắng, về chữ thường."""

    cleaned = unicodedata.normalize("NFC", text).lower()
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)

    return re.sub(r"\s+", " ", cleaned).strip()


def _too_similar(a: str, b: str) -> bool:
    """Trùng nhau, hoặc cái này chứa cái kia — cả hai đều tạo ra hai đáp án đúng."""

    x, y = normalize_meaning(a), normalize_meaning(b)

    if not x or not y:
        return True

    return x == y or x in y or y in x


def pick_distractors(
    index: SearchIndex,
    correct: SourceCard,
    *,
    count: int,
    max_cosine: float,
    field: str = "meaning",
) -> list[SourceCard]:
    """
    Chọn `count` thẻ nhiễu cho `correct`, lấy TRONG CÙNG DECK.

    Ba luật loại, theo SPEC muc 11.6:
      1. Cosine trên `max_cosine` -> nguy cơ đồng nghĩa, sẽ có hai đáp án đúng.
         Với fixture, `apprehensive` (102) và `anxious` (108) rất dễ vượt ngưỡng
         này — đó chính là lý do luật tồn tại.
      2. Nội dung trùng hoặc chứa nhau sau khi chuẩn hoá.
      3. Trùng với nhiễu đã chọn trước đó.
    """

    chosen: list[SourceCard] = []
    used = [getattr(correct, field) or ""]

    for card_id, score in index.vectors.neighbors(
        correct.card_id, [correct.deck_id], NEIGHBOUR_POOL
    ):
        if len(chosen) >= count:
            break

        if score > max_cosine:
            continue

        card = index.get(card_id)

        if card is None:
            continue

        value = getattr(card, field) or ""

        if not value or any(_too_similar(value, existing) for existing in used):
            continue

        chosen.append(card)
        used.append(value)

    return chosen
