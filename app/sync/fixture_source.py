"""
Nguồn dữ liệu từ file JSON — cho test và dev offline hoàn toàn.

Mô phỏng đúng hành vi của endpoint backend: lọc theo `since` với toán tử `>=`,
sắp xếp `(updatedAt ASC, cardId ASC)`, phân trang.
"""

import json
from datetime import datetime
from pathlib import Path

import anyio

from app.schemas.card import SourceCard
from app.sync.source import parse_source_card


class FixtureCardSource:
    def __init__(self, fixture_path: Path) -> None:
        self._path = fixture_path

    def _load_sync(self) -> list[SourceCard]:
        """
        Đọc lại file mỗi lần gọi, CỐ Ý không cache.

        Acceptance của M1 yêu cầu sửa/xoá thẻ trong fixture rồi đồng bộ lại và
        thấy thay đổi. Cache ở đây sẽ làm những test đó xanh giả.
        """

        raw = json.loads(self._path.read_text(encoding="utf-8"))

        cards = [parse_source_card(item) for item in raw]

        # Sắp xếp đúng như endpoint backend bắt buộc phải làm (SPEC muc 6.2).
        # Thiếu tie-break bằng id thì phân trang không ổn định và bỏ sót bản ghi.
        cards.sort(key=lambda c: (c.source_updated_at, c.card_id))

        return cards

    async def _load(self) -> list[SourceCard]:
        return await anyio.to_thread.run_sync(self._load_sync)

    # ---------------------------------------------------------------
    # CardSource
    # ---------------------------------------------------------------

    async def fetch_changed_since(
        self, since: datetime | None, page: int, size: int
    ) -> tuple[list[SourceCard], bool]:
        cards = await self._load()

        if since is not None:
            # `>=` chứ không phải `>`: fsoft-ai đã tự lùi mốc 5 giây và tự khử
            # trùng bằng content_hash.
            cards = [c for c in cards if c.source_updated_at >= since]

        start = (page - 1) * size  # page 1-based, đúng quy ước deck của backend
        chunk = cards[start : start + size]
        is_last = start + size >= len(cards)

        return chunk, is_last

    async def fetch_all_ids(self) -> set[int]:
        cards = await self._load()

        return {c.card_id for c in cards}

    async def health(self) -> bool:
        return await anyio.to_thread.run_sync(self._path.exists)
