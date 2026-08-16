"""
`CardSource` — trừu tượng hoá then chốt. SPEC muc 9.1.

Nhờ Protocol này, M1 đến M5 chạy và test được hoàn toàn mà không cần backend
Java tồn tại. Đội Java làm xong endpoint thì chỉ đổi `AI_SOURCE_MODE`.

| Class             | Dùng khi                | Nguồn                        |
|-------------------|-------------------------|------------------------------|
| HttpCardSource    | production, dev có backend | GET /fsoft/internal/cards/** |
| FixtureCardSource | test, dev offline       | tests/fixtures/cards.json    |
"""

from datetime import datetime
from typing import Protocol

from app.schemas.card import SourceCard


class CardSource(Protocol):
    async def fetch_changed_since(
        self, since: datetime | None, page: int, size: int
    ) -> tuple[list[SourceCard], bool]:
        """Trả (danh sách thẻ, là_trang_cuối)."""
        ...

    async def fetch_all_ids(self) -> set[int]:
        """Toàn bộ card_id còn tồn tại ở nguồn — dùng để phát hiện thẻ bị xoá."""
        ...

    async def health(self) -> bool: ...


# ---------------------------------------------------------------------
# Ranh giới camelCase -> snake_case
# ---------------------------------------------------------------------
#
# SPEC muc 3.3: backend Java dùng camelCase, fsoft-ai dùng snake_case.
# Hàm dưới đây là NƠI DUY NHẤT biết điều đó. Fixture cũng đi qua đây vì nó
# cố ý mô phỏng đúng shape phản hồi của backend — nhờ vậy fixture vừa là dữ
# liệu test, vừa là hợp đồng đối chiếu với đội Java.

_FIELD_MAP = {
    "cardId": "card_id",
    "deckId": "deck_id",
    "deckTitle": "deck_title",
    "word": "word",
    "phonetic": "phonetic",
    "partOfSpeech": "part_of_speech",
    "meaning": "meaning",
    "definitionEn": "definition_en",
    "exampleSentence": "example_sentence",
    "exampleMeaning": "example_meaning",
    "audioUrl": "audio_url",
    "note": "note",
    "updatedAt": "source_updated_at",
}


def parse_source_card(raw: dict) -> SourceCard:
    """Một phần tử trong `data.content` của backend -> `SourceCard`."""

    return SourceCard.model_validate({snake: raw.get(camel) for camel, snake in _FIELD_MAP.items()})
