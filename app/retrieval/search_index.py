"""
Gom ba cấu trúc trong RAM mà đường nóng cần, sau một mặt tiền duy nhất:

  1. `VectorIndex`  — vector đã L2-normalize, lọc theo deck  (tầng semantic)
  2. BM25           — chỉ mục lexical                         (tầng lexical)
  3. `dict` thẻ     — text đầy đủ để dựng phản hồi và prompt

Vì sao gom lại: cả ba phải luôn nhất quán với nhau. Nếu syncer phải nhớ cập
nhật ba thứ riêng lẻ thì sớm muộn sẽ quên một, và triệu chứng là kết quả tìm
kiếm trỏ tới thẻ đã xoá — rất khó lần ra.

Đường nóng đọc hoàn toàn từ đây, KHÔNG chạm SQLite (SPEC muc 5.3).
"""

import re
import threading

import numpy as np
from rank_bm25 import BM25Okapi

from app.core.logging import get_logger
from app.embedding.vector_index import VectorIndex
from app.retrieval.stopwords import strip_stopwords
from app.schemas.card import IndexRow, SourceCard, StoredCard

log = get_logger(__name__)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """
    Tách token cho BM25 và cho tầng khớp chính xác.

    `\\w+` với cờ UNICODE giữ nguyên chữ tiếng Việt có dấu, nên "phục hồi" ra
    hai token thay vì vỡ vụn theo từng ký tự.
    """

    return _TOKEN_RE.findall(text.lower())


def _bm25_document(card: SourceCard) -> list[str]:
    """SPEC muc 11.3: BM25 dựng trên word + meaning + example_sentence."""

    parts = [card.word, card.meaning, card.example_sentence or ""]

    return tokenize(" ".join(parts))


class SearchIndex:
    def __init__(self, dim: int) -> None:
        self._lock = threading.RLock()
        self._vectors = VectorIndex(dim)
        self._cards: dict[int, SourceCard] = {}

        self._bm25: BM25Okapi | None = None
        self._bm25_ids: list[int] = []
        self._bm25_dirty = True

    # ---------------------------------------------------------------
    # Ghi
    # ---------------------------------------------------------------

    def rebuild(self, stored: list[StoredCard]) -> None:
        with self._lock:
            self._cards = {item.card.card_id: item.card for item in stored}
            self._vectors.rebuild([self._to_index_row(item) for item in stored])
            self._bm25_dirty = True

    def upsert(self, stored: list[StoredCard]) -> None:
        if not stored:
            return

        with self._lock:
            for item in stored:
                self._cards[item.card.card_id] = item.card

            self._vectors.upsert([self._to_index_row(item) for item in stored])
            self._bm25_dirty = True

    def delete(self, card_ids: set[int]) -> None:
        if not card_ids:
            return

        with self._lock:
            for card_id in card_ids:
                self._cards.pop(card_id, None)

            self._vectors.delete(card_ids)
            self._bm25_dirty = True

    @staticmethod
    def _to_index_row(item: StoredCard) -> IndexRow:
        return IndexRow(
            card_id=item.card.card_id,
            deck_id=item.card.deck_id,
            word=item.card.word,
            vector=item.vector,
        )

    def _ensure_bm25(self) -> None:
        """
        BM25Okapi tính sẵn IDF lúc khởi tạo nên không cập nhật từng phần được —
        phải dựng lại toàn bộ. Dựng lười: chỉ làm ở lần tìm kiếm đầu tiên sau
        khi có thay đổi, thay vì mỗi chu kỳ đồng bộ 120 giây.
        """

        if not self._bm25_dirty:
            return

        ids = sorted(self._cards)
        corpus = [_bm25_document(self._cards[card_id]) for card_id in ids]

        self._bm25_ids = ids
        self._bm25 = BM25Okapi(corpus) if corpus else None
        self._bm25_dirty = False

        log.info("bm25_rebuilt", documents=len(corpus))

    # ---------------------------------------------------------------
    # Đọc
    # ---------------------------------------------------------------

    def get(self, card_id: int) -> SourceCard | None:
        with self._lock:
            return self._cards.get(card_id)

    def get_many(self, card_ids: list[int]) -> list[SourceCard]:
        with self._lock:
            return [self._cards[cid] for cid in card_ids if cid in self._cards]

    def cards_in_decks(self, deck_ids: list[int]) -> list[SourceCard]:
        if not deck_ids:
            return []

        allowed = set(deck_ids)

        with self._lock:
            return [card for card in self._cards.values() if card.deck_id in allowed]

    def lookup_word(self, word: str, allowed_deck_ids: list[int]) -> list[int]:
        """Tầng khớp chính xác. Uỷ nhiệm nguyên vẹn cho VectorIndex."""

        return self._vectors.lookup_word(word, allowed_deck_ids)

    def semantic_search(
        self, query_vector: np.ndarray, allowed_deck_ids: list[int], top_k: int
    ) -> list[tuple[int, float]]:
        return self._vectors.search(query_vector, allowed_deck_ids, top_k)

    def lexical_search(
        self, query: str, allowed_deck_ids: list[int], top_k: int
    ) -> list[tuple[int, float]]:
        """BM25 trên toàn corpus, lọc phạm vi deck sau khi chấm điểm."""

        if not allowed_deck_ids or top_k <= 0:
            return []

        # Bỏ hư từ khỏi câu hỏi, giữ nguyên corpus. Xem app/retrieval/stopwords.py:
        # một hư từ hiếm gặp trong corpus nhỏ có IDF rất cao và một mình nó
        # quyết định toàn bộ thứ hạng lexical.
        tokens = strip_stopwords(tokenize(query))

        # Câu hỏi toàn hư từ thì tầng lexical không có gì để nói. Trả rỗng để
        # tầng semantic quyết định, thay vì bịa ra thứ hạng từ nhiễu.
        if not tokens:
            return []

        with self._lock:
            self._ensure_bm25()

            if self._bm25 is None:
                return []

            scores = self._bm25.get_scores(tokens)
            allowed = set(allowed_deck_ids)

            hits = [
                (card_id, float(score))
                for card_id, score in zip(self._bm25_ids, scores)
                if score > 0 and self._cards[card_id].deck_id in allowed
            ]

        hits.sort(key=lambda pair: -pair[1])

        return hits[:top_k]

    # ---------------------------------------------------------------
    # Trạng thái
    # ---------------------------------------------------------------

    @property
    def size(self) -> int:
        return self._vectors.size

    @property
    def vectors(self) -> VectorIndex:
        """Truy cập trực tiếp cho quiz (M5) cần láng giềng theo embedding."""

        return self._vectors

    def stats(self) -> dict:
        with self._lock:
            return self._vectors.stats() | {"card_count": len(self._cards)}
