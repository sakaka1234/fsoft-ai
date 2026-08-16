"""
Serialize thẻ thành ngữ cảnh cho prompt. SPEC muc 11.3 mục 3.

Dạng gọn, KHÔNG dump JSON đầy đủ — tiết kiệm khoảng 220 token mỗi lượt
(SPEC muc 4.1):

    [#101] resilient (adj) /rɪˈzɪliənt/ — kiên cường, có khả năng phục hồi nhanh
      EN: able to recover quickly from difficult conditions
      VD: She remained resilient despite repeated setbacks.

Đây cũng là một trong bốn lớp chống prompt injection (SPEC muc 11.5): thẻ do
người dùng tự tạo, và thẻ 109 trong fixture chứa nội dung tấn công thật.
"""

import re
import unicodedata

from app.schemas.card import SourceCard

# Ký tự điều khiển, cộng các ký tự VÔ HÌNH hay bị dùng để giấu chỉ thị trong
# text: zero-width space/joiner, dấu đảo chiều LTR-RTL, BOM.
#
# Viết bằng escape \u chứ không dán ký tự thật vào mã nguồn. Ký tự vô hình nằm
# trong source chính là trò mà đoạn regex này sinh ra để chặn — và U+2028 còn
# được Python coi là dấu xuống dòng, đủ sức làm hỏng công cụ đọc chính file này.
_CONTROL_RE = re.compile(
    "[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f"  # điều khiển ASCII và C1
    "\\u200b-\\u200f"  # zero-width space, ZWNJ, ZWJ, LRM, RLM
    "\\u2028-\\u202e"  # ngắt dòng/đoạn, LRE, RLE, PDF, LRO, RLO
    "\\u2060-\\u2064"  # word joiner và các ký tự vô hình khác
    "\\ufeff]"  # BOM
)


def sanitize(text: str, max_chars: int) -> str:
    """
    Làm sạch một field trước khi đưa vào prompt.

    Ba việc: chuẩn hoá Unicode, bỏ ký tự điều khiển và ký tự vô hình, cắt độ
    dài. Cắt độ dài không chỉ để tiết kiệm token — nó chặn luôn một thẻ có
    `note` dài hàng nghìn ký tự nuốt trọn ngân sách ngữ cảnh.
    """

    cleaned = unicodedata.normalize("NFC", text)
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "…"

    return cleaned


def serialize_card(card: SourceCard, max_chars_per_field: int) -> str:
    def clean(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None

        return sanitize(value, max_chars_per_field)

    head = f"[#{card.card_id}] {clean(card.word)}"

    part_of_speech = clean(card.part_of_speech)
    if part_of_speech:
        head += f" ({part_of_speech})"

    phonetic = clean(card.phonetic)
    if phonetic:
        head += f" /{phonetic.strip('/')}/"

    meaning = clean(card.meaning)
    if meaning:
        head += f" — {meaning}"

    lines = [head]

    definition_en = clean(card.definition_en)
    if definition_en:
        lines.append(f"  EN: {definition_en}")

    example_sentence = clean(card.example_sentence)
    if example_sentence:
        lines.append(f"  VD: {example_sentence}")

    note = clean(card.note)
    if note:
        lines.append(f"  Ghi chú: {note}")

    return "\n".join(lines)


def build_context(cards: list[SourceCard], max_chars_per_field: int) -> str:
    """Rỗng thì trả chuỗi rỗng — prompt sẽ tự nói 'không tìm thấy trong bộ thẻ'."""

    return "\n".join(serialize_card(card, max_chars_per_field) for card in cards)
