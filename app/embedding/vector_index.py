"""
Vector index trong RAM bằng numpy. SPEC muc 5.6.

Không dựng vector DB. 50.000 vector đã L2-normalize, một phép nhân ma trận
(N, 384) @ (384,) mất dưới 5ms. HNSW ở quy mô này chỉ thêm phức tạp.

Vector đã normalize nên cosine = dot product. Không chia norm.
"""

import threading
from typing import Protocol

import numpy as np

from app.schemas.card import IndexRow


class VectorIndexProtocol(Protocol):
    """Tách sẵn để sau này thay bằng Qdrant chỉ là thêm một class."""

    def upsert(self, rows: list[IndexRow]) -> None: ...

    def delete(self, card_ids: set[int]) -> None: ...

    def search(
        self, query_vector: np.ndarray, allowed_deck_ids: list[int], top_k: int
    ) -> list[tuple[int, float]]: ...

    def stats(self) -> dict: ...


class VectorIndex:
    def __init__(self, dim: int) -> None:
        self._dim = dim
        self._lock = threading.RLock()

        self._matrix: np.ndarray = np.zeros((0, dim), dtype=np.float32)
        self._card_ids: np.ndarray = np.zeros((0,), dtype=np.int64)
        self._deck_ids: np.ndarray = np.zeros((0,), dtype=np.int64)

        self._row_of: dict[int, int] = {}
        self._rows_of_deck: dict[int, np.ndarray] = {}
        self._word_index: dict[str, list[int]] = {}

        self._words: list[str] = []

    # ---------------------------------------------------------------
    # Cấu trúc dẫn xuất
    # ---------------------------------------------------------------

    def _rebuild_derived(self) -> None:
        """
        Dựng lại _row_of, _rows_of_deck, _word_index từ ba mảng gốc.

        O(N) trên toàn index. Với dưới 50.000 thẻ thì chỉ vài ms, mà đổi lại
        không bao giờ có chuyện cấu trúc dẫn xuất lệch khỏi ma trận — kể cả khi
        một thẻ đổi deck.
        """

        self._row_of = {int(card_id): row for row, card_id in enumerate(self._card_ids)}

        rows_of_deck: dict[int, list[int]] = {}
        word_index: dict[str, list[int]] = {}

        for row, (card_id, deck_id) in enumerate(zip(self._card_ids, self._deck_ids)):
            rows_of_deck.setdefault(int(deck_id), []).append(row)
            word_index.setdefault(self._words[row].lower(), []).append(int(card_id))

        self._rows_of_deck = {
            deck_id: np.asarray(rows, dtype=np.int64) for deck_id, rows in rows_of_deck.items()
        }
        self._word_index = word_index

    # ---------------------------------------------------------------
    # Ghi
    # ---------------------------------------------------------------

    def upsert(self, rows: list[IndexRow]) -> None:
        if not rows:
            return

        with self._lock:
            new_rows: list[IndexRow] = []

            for item in rows:
                existing = self._row_of.get(item.card_id)

                if existing is None:
                    new_rows.append(item)
                    continue

                # Thẻ đã có: ghi đè tại chỗ. deck_id có thể đổi -> _rebuild_derived
                # bên dưới sẽ cập nhật _rows_of_deck theo.
                self._matrix[existing] = item.vector
                self._deck_ids[existing] = item.deck_id
                self._words[existing] = item.word

            if new_rows:
                self._matrix = np.vstack(
                    [self._matrix, np.asarray([r.vector for r in new_rows], dtype=np.float32)]
                )
                self._card_ids = np.concatenate(
                    [self._card_ids, np.asarray([r.card_id for r in new_rows], dtype=np.int64)]
                )
                self._deck_ids = np.concatenate(
                    [self._deck_ids, np.asarray([r.deck_id for r in new_rows], dtype=np.int64)]
                )
                self._words.extend(r.word for r in new_rows)

            self._rebuild_derived()

    def delete(self, card_ids: set[int]) -> None:
        if not card_ids:
            return

        with self._lock:
            rows = [self._row_of[cid] for cid in card_ids if cid in self._row_of]

            if not rows:
                return

            keep = np.ones(len(self._card_ids), dtype=bool)
            keep[rows] = False

            self._matrix = self._matrix[keep]
            self._card_ids = self._card_ids[keep]
            self._deck_ids = self._deck_ids[keep]
            self._words = [w for w, k in zip(self._words, keep) if k]

            self._rebuild_derived()

    def rebuild(self, rows: list[IndexRow]) -> None:
        """Dựng lại từ đầu — dùng khi nạp index từ SQLite lúc khởi động."""

        with self._lock:
            self._matrix = (
                np.asarray([r.vector for r in rows], dtype=np.float32)
                if rows
                else np.zeros((0, self._dim), dtype=np.float32)
            )
            self._card_ids = np.asarray([r.card_id for r in rows], dtype=np.int64)
            self._deck_ids = np.asarray([r.deck_id for r in rows], dtype=np.int64)
            self._words = [r.word for r in rows]

            self._rebuild_derived()

    # ---------------------------------------------------------------
    # Đọc
    # ---------------------------------------------------------------

    def search(
        self, query_vector: np.ndarray, allowed_deck_ids: list[int], top_k: int
    ) -> list[tuple[int, float]]:
        """
        Trả [(card_id, score)] xếp giảm dần.

        `allowed_deck_ids` rỗng nghĩa là KHÔNG ĐƯỢC PHÉP GÌ, không phải "tất cả".
        Fail đóng, không fail mở — SPEC muc 11.3 gọi đây là lỗi kinh điển.
        """

        if not allowed_deck_ids or top_k <= 0:
            return []

        with self._lock:
            if len(self._card_ids) == 0:
                return []

            # Lọc phạm vi qua _rows_of_deck, KHÔNG np.isin trên toàn mảng mỗi query.
            deck_rows = [
                self._rows_of_deck[deck_id]
                for deck_id in allowed_deck_ids
                if deck_id in self._rows_of_deck
            ]

            if not deck_rows:
                return []

            rows = np.concatenate(deck_rows)

            # Vector đã L2-normalize -> dot product chính là cosine.
            scores = self._matrix[rows] @ np.asarray(query_vector, dtype=np.float32)

            k = min(top_k, len(scores))
            top = np.argpartition(-scores, k - 1)[:k]
            top = top[np.argsort(-scores[top])]

            return [(int(self._card_ids[rows[i]]), float(scores[i])) for i in top]

    def vector_of(self, card_id: int) -> np.ndarray | None:
        with self._lock:
            row = self._row_of.get(card_id)

            return None if row is None else self._matrix[row].copy()

    def neighbors(
        self, card_id: int, allowed_deck_ids: list[int], top_k: int
    ) -> list[tuple[int, float]]:
        """
        Láng giềng gần nhất theo embedding, KHÔNG tính chính nó.

        Đây là ứng dụng đắt giá thứ hai của embedding sau retrieval: nhiễu quiz
        chọn theo láng giềng vừa đủ giống để khó, vừa đủ khác để không mơ hồ
        (SPEC muc 11.6).
        """

        vector = self.vector_of(card_id)

        if vector is None:
            return []

        found = self.search(vector, allowed_deck_ids, top_k + 1)

        return [(cid, score) for cid, score in found if cid != card_id][:top_k]

    def lookup_word(self, word: str, allowed_deck_ids: list[int]) -> list[int]:
        """Tầng khớp chính xác của M2. Trả card_id trong phạm vi cho phép."""

        if not allowed_deck_ids:
            return []

        with self._lock:
            candidates = self._word_index.get(word.lower(), [])
            allowed = set(allowed_deck_ids)

            return [
                card_id
                for card_id in candidates
                if int(self._deck_ids[self._row_of[card_id]]) in allowed
            ]

    def stats(self) -> dict:
        with self._lock:
            return {
                "index_size": len(self._card_ids),
                "deck_count": len(self._rows_of_deck),
                "dim": self._dim,
            }

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._card_ids)
