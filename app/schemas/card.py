"""
Kiểu dữ liệu cho thẻ từ vựng.

`SourceCard` là thứ `CardSource` trả về — đã ở dạng snake_case. Việc chuyển
camelCase sang snake_case diễn ra đúng một chỗ: `app/sync/http_source.py`.
Không chỗ nào khác trong codebase được biết backend Java dùng camelCase.
"""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from pydantic import BaseModel


class SourceCard(BaseModel):
    """Một thẻ như nguồn dữ liệu cung cấp. Chưa embed."""

    card_id: int
    deck_id: int
    deck_title: str | None = None
    word: str
    phonetic: str | None = None
    part_of_speech: str | None = None
    meaning: str
    definition_en: str | None = None
    example_sentence: str | None = None
    example_meaning: str | None = None
    audio_url: str | None = None
    note: str | None = None
    source_updated_at: datetime


@dataclass(slots=True)
class CardToStore:
    """Một thẻ đã embed xong, sẵn sàng ghi xuống SQLite."""

    source: SourceCard
    content_hash: str
    model_version: str
    vector: np.ndarray


@dataclass(slots=True)
class IndexRow:
    """Đủ dữ liệu để dựng lại vector index từ SQLite lúc khởi động."""

    card_id: int
    deck_id: int
    word: str
    vector: np.ndarray


@dataclass(slots=True)
class StoredCard:
    """
    Một thẻ đã embed, đọc lên từ SQLite.

    Đường nóng (retrieval, chat, quiz) đọc hoàn toàn từ RAM, không chạm SQLite
    (SPEC muc 5.3), nên `SearchIndex` phải giữ đủ text để dựng phản hồi và
    dựng ngữ cảnh cho prompt — không chỉ vector.
    """

    card: SourceCard
    vector: np.ndarray
