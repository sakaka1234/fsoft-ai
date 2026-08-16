"""
Truy cập bảng `card`. SQL thuần, không ORM.

Vector lưu dạng BLOB float32 little-endian ('<f4') — cố định bất kể kiến trúc
máy, để file SQLite copy sang máy khác vẫn đọc được.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from app.schemas.card import CardToStore, IndexRow, SourceCard, StoredCard
from app.store.db import Database

VECTOR_DTYPE = "<f4"


@dataclass(slots=True, frozen=True)
class CardFingerprint:
    """
    Đủ để syncer quyết định phải làm gì với một thẻ mà không cần đọc cả bảng.

    `content_hash` chỉ phủ text đem đi embed. Ba field còn lại KHÔNG nằm trong
    text đó, nên phải so riêng — nếu không, thẻ chuyển deck sẽ giữ nguyên
    deck_id cũ trong index và bị tìm thấy sai phạm vi.
    """

    content_hash: str
    model_version: str
    deck_id: int
    deck_title: str | None
    audio_url: str | None


_UPSERT_SQL = """
INSERT INTO card (
    card_id, deck_id, deck_title, word, word_lower, phonetic, part_of_speech,
    meaning, definition_en, example_sentence, example_meaning, audio_url, note,
    source_updated_at, content_hash, model_version, vector, synced_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(card_id) DO UPDATE SET
    deck_id           = excluded.deck_id,
    deck_title        = excluded.deck_title,
    word              = excluded.word,
    word_lower        = excluded.word_lower,
    phonetic          = excluded.phonetic,
    part_of_speech    = excluded.part_of_speech,
    meaning           = excluded.meaning,
    definition_en     = excluded.definition_en,
    example_sentence  = excluded.example_sentence,
    example_meaning   = excluded.example_meaning,
    audio_url         = excluded.audio_url,
    note              = excluded.note,
    source_updated_at = excluded.source_updated_at,
    content_hash      = excluded.content_hash,
    model_version     = excluded.model_version,
    vector            = excluded.vector,
    synced_at         = excluded.synced_at
"""


def vector_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=VECTOR_DTYPE).tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=VECTOR_DTYPE)


class CardRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---------------------------------------------------------------
    # Ghi
    # ---------------------------------------------------------------

    async def upsert_many(self, cards: list[CardToStore]) -> None:
        if not cards:
            return

        synced_at = datetime.now(UTC).isoformat()

        rows = [
            (
                item.source.card_id,
                item.source.deck_id,
                item.source.deck_title,
                item.source.word,
                item.source.word.lower(),
                item.source.phonetic,
                item.source.part_of_speech,
                item.source.meaning,
                item.source.definition_en,
                item.source.example_sentence,
                item.source.example_meaning,
                item.source.audio_url,
                item.source.note,
                item.source.source_updated_at.isoformat(),
                item.content_hash,
                item.model_version,
                vector_to_blob(item.vector),
                synced_at,
            )
            for item in cards
        ]

        await self._db.execute_many(_UPSERT_SQL, rows)

    async def delete_many(self, card_ids: set[int]) -> None:
        if not card_ids:
            return

        await self._db.execute_many(
            "DELETE FROM card WHERE card_id = ?",
            [(card_id,) for card_id in card_ids],
        )

    # ---------------------------------------------------------------
    # Đọc
    # ---------------------------------------------------------------

    async def get_fingerprints(self) -> dict[int, CardFingerprint]:
        """
        Syncer dùng bảng này để bỏ qua thẻ không đổi mà không cần embed lại.

        Với dưới 50.000 thẻ thì nạp hết một lần rẻ hơn nhiều so với truy vấn
        từng thẻ một trong vòng lặp.
        """

        rows = await self._db.query(
            "SELECT card_id, content_hash, model_version, deck_id, deck_title, audio_url FROM card"
        )

        return {
            row["card_id"]: CardFingerprint(
                content_hash=row["content_hash"],
                model_version=row["model_version"],
                deck_id=row["deck_id"],
                deck_title=row["deck_title"],
                audio_url=row["audio_url"],
            )
            for row in rows
        }

    async def get_vectors(self, card_ids: list[int]) -> dict[int, np.ndarray]:
        """
        Lấy lại vector đã lưu để cập nhật thẻ mà không phải embed lại.

        Dùng khi chỉ deck/tiêu đề/audio đổi — nội dung không đổi thì vector cũ
        vẫn đúng nguyên.
        """

        if not card_ids:
            return {}

        placeholders = ",".join("?" * len(card_ids))

        rows = await self._db.query(
            f"SELECT card_id, vector FROM card WHERE card_id IN ({placeholders})",
            tuple(card_ids),
        )

        return {row["card_id"]: blob_to_vector(row["vector"]) for row in rows}

    async def all_ids(self) -> set[int]:
        rows = await self._db.query("SELECT card_id FROM card")

        return {row["card_id"] for row in rows}

    async def count(self) -> int:
        rows = await self._db.query("SELECT COUNT(*) AS n FROM card")

        return int(rows[0]["n"])

    async def load_index_rows(self) -> list[IndexRow]:
        """Nạp lại vector index từ SQLite lúc khởi động — không gọi nguồn."""

        rows = await self._db.query(
            "SELECT card_id, deck_id, word, vector FROM card ORDER BY card_id"
        )

        return [
            IndexRow(
                card_id=row["card_id"],
                deck_id=row["deck_id"],
                word=row["word"],
                vector=blob_to_vector(row["vector"]),
            )
            for row in rows
        ]

    async def load_all(self) -> list[StoredCard]:
        """
        Nạp toàn bộ thẻ kèm vector để dựng `SearchIndex` lúc khởi động.

        Đường nóng không chạm SQLite, nên mọi text cần cho phản hồi và cho
        prompt phải nằm sẵn trong RAM.
        """

        rows = await self._db.query("SELECT * FROM card ORDER BY card_id")

        return [
            StoredCard(
                card=SourceCard(
                    card_id=row["card_id"],
                    deck_id=row["deck_id"],
                    deck_title=row["deck_title"],
                    word=row["word"],
                    phonetic=row["phonetic"],
                    part_of_speech=row["part_of_speech"],
                    meaning=row["meaning"],
                    definition_en=row["definition_en"],
                    example_sentence=row["example_sentence"],
                    example_meaning=row["example_meaning"],
                    audio_url=row["audio_url"],
                    note=row["note"],
                    source_updated_at=row["source_updated_at"],
                ),
                vector=blob_to_vector(row["vector"]),
            )
            for row in rows
        ]
