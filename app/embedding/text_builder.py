"""
Dựng text đưa vào embedding và tính content_hash. SPEC muc 7.1.

Template cố định và có version. Đổi template nghĩa là phải reindex toàn bộ,
nên version của template nằm luôn trong `model_version` ('...@t1').
"""

import hashlib

from app.schemas.card import SourceCard


def _clean(value: str | None) -> str | None:
    """Chuỗi rỗng và chuỗi chỉ có khoảng trắng coi như không có."""

    if value is None:
        return None

    stripped = value.strip()

    return stripped or None


def build_text(card: SourceCard) -> str:
    """
    Text để embed. Bỏ HẲN dòng nếu field rỗng — không bao giờ in chuỗi 'None'.

    resilient (adj) /rɪˈzɪliənt/
    Nghĩa: kiên cường, có khả năng phục hồi nhanh
    Định nghĩa: able to recover quickly from difficult conditions
    Ví dụ: She remained resilient despite repeated setbacks. — Cô ấy vẫn kiên cường...
    Ghi chú: ...
    """

    word = _clean(card.word) or ""
    part_of_speech = _clean(card.part_of_speech)
    phonetic = _clean(card.phonetic)

    head = word

    if part_of_speech:
        head += f" ({part_of_speech})"

    if phonetic:
        head += f" /{phonetic.strip('/')}/"

    lines = [head]

    meaning = _clean(card.meaning)
    if meaning:
        lines.append(f"Nghĩa: {meaning}")

    definition_en = _clean(card.definition_en)
    if definition_en:
        lines.append(f"Định nghĩa: {definition_en}")

    example_sentence = _clean(card.example_sentence)
    example_meaning = _clean(card.example_meaning)
    if example_sentence:
        example = f"Ví dụ: {example_sentence}"

        if example_meaning:
            example += f" — {example_meaning}"

        lines.append(example)

    note = _clean(card.note)
    if note:
        lines.append(f"Ghi chú: {note}")

    return "\n".join(lines)


def content_hash(text: str) -> str:
    """
    SHA-256 của text đã dựng.

    Tính TRƯỚC khi thêm prefix 'passage: '. Prefix là chi tiết của model, không
    phải của nội dung — trộn vào hash sẽ khiến mọi thẻ phải embed lại nếu sau
    này đổi prefix.
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_text_and_hash(card: SourceCard) -> tuple[str, str]:
    text = build_text(card)

    return text, content_hash(text)
