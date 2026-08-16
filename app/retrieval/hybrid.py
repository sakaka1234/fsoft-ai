"""
Retrieval lai ba tầng. SPEC muc 11.3 mục 1.

  Tầng 1 — Khớp chính xác  (0 token, ~1ms)  tra _word_index trong RAM
  Tầng 2 — Lexical BM25                     bắt được từ hiếm, sai chính tả nhẹ
  Tầng 3 — Semantic                         bắt được câu hỏi diễn đạt vòng vo

Hợp nhất bằng RRF: score(d) = Σ 1/(k + rank_i(d)).

Vì sao RRF chứ không cộng điểm thô: điểm cosine của E5 nằm trong dải 0.80-0.90
còn điểm BM25 chạy từ 0 tới vài chục — không có cách chuẩn hoá nào ổn định
giữa hai thang đó. RRF chỉ dùng THỨ HẠNG nên miễn nhiễm với chuyện này
(docs/M0_FINDINGS.md muc 2.5 giải thích vì sao ngưỡng tuyệt đối vô dụng).
"""

import re
from dataclasses import dataclass

import numpy as np

from app.retrieval.search_index import SearchIndex
from app.retrieval.stopwords import QUERY_STOPWORDS
from app.schemas.card import SourceCard
from app.schemas.chat import MatchType

# Token tiếng Anh: chỉ chữ cái Latin không dấu, tối thiểu 2 ký tự.
# Dùng để dò từ vựng tiếng Anh trong câu hỏi tiếng Việt.
#
# Bộ dò này không phân biệt được âm tiết tiếng Việt với từ tiếng Anh: "gia"
# trong "gia đình" cũng lọt qua. Không sao, vì `lookup_word` chỉ trả về thẻ
# khi `word` khớp tuyệt đối — âm tiết tiếng Việt đơn giản là không tra được
# gì. Tầng BM25 mới là nơi chúng gây hại, và nó tự lọc hư từ riêng.
_ENGLISH_TOKEN_RE = re.compile(r"\b[a-zA-Z]{2,}\b")


@dataclass(slots=True)
class RetrievalHit:
    card: SourceCard
    score: float
    match_type: MatchType
    rank: int


def extract_english_tokens(query: str) -> list[str]:
    """Rút các token có thể là từ vựng tiếng Anh cần tra."""

    return [
        token.lower()
        for token in _ENGLISH_TOKEN_RE.findall(query)
        if token.lower() not in QUERY_STOPWORDS
    ]


def reciprocal_rank_fusion(ranked_lists: list[list[int]], k: int) -> dict[int, float]:
    """score(d) = Σ 1/(k + rank(d)), rank bắt đầu từ 1."""

    fused: dict[int, float] = {}

    for ranked in ranked_lists:
        for position, card_id in enumerate(ranked, start=1):
            fused[card_id] = fused.get(card_id, 0.0) + 1.0 / (k + position)

    return fused


class HybridRetriever:
    def __init__(
        self,
        index: SearchIndex,
        *,
        rrf_k: int = 60,
        lexical_candidates: int = 20,
        semantic_candidates: int = 20,
        min_score: float = 0.83,
    ) -> None:
        self._index = index
        self._rrf_k = rrf_k
        self._lexical_candidates = lexical_candidates
        self._semantic_candidates = semantic_candidates
        self._min_score = min_score

    def retrieve(
        self,
        query: str,
        query_vector: np.ndarray,
        allowed_deck_ids: list[int],
        top_k: int,
    ) -> list[RetrievalHit]:
        # Fail đóng. Danh sách rỗng nghĩa là không được phép gì, không phải
        # "không lọc" (SPEC muc 11.3).
        if not allowed_deck_ids or top_k <= 0:
            return []

        exact_ids = self._exact(query, allowed_deck_ids)

        lexical = self._index.lexical_search(query, allowed_deck_ids, self._lexical_candidates)
        semantic = self._index.semantic_search(
            query_vector, allowed_deck_ids, self._semantic_candidates
        )

        # Cổng lọc liên quan. Không có nó thì mọi câu hỏi đều trả về đúng top_k
        # thẻ, kể cả khi bộ thẻ hoàn toàn không chứa câu trả lời — người dùng
        # hỏi "deforestation" trong bộ TOEIC sẽ nhận về 5 từ ngẫu nhiên.
        #
        # Ngưỡng lấy từ số đo trên tests/eval/retrieval_golden.json, không lấy
        # từ phỏng đoán. Hai phân bố CHỒNG LẤN nhau (positive thấp nhất 0.8277,
        # negative cao nhất 0.8297) nên không ngưỡng nào tách sạch được — 0.83
        # đổi một positive yếu lấy cả năm negative.
        semantic = [(card_id, score) for card_id, score in semantic if score >= self._min_score]

        lexical_ids = [card_id for card_id, _ in lexical]
        semantic_ids = [card_id for card_id, _ in semantic]

        # Không tầng nào có bằng chứng đủ mạnh -> nói thẳng là không có, thay vì
        # bịa ra kết quả. M4 dựa vào đây để trả lời "chưa có trong bộ thẻ".
        if not exact_ids and not semantic_ids:
            return []

        fused = reciprocal_rank_fusion([lexical_ids, semantic_ids], self._rrf_k)

        # Thẻ khớp chính xác luôn được ghim lên đầu, bất kể RRF nói gì.
        # Người dùng gõ đúng một từ trong bộ thẻ thì đó chắc chắn là thứ họ hỏi.
        ordered = list(exact_ids)
        ordered += [
            card_id
            for card_id in sorted(fused, key=lambda cid: -fused[cid])
            if card_id not in set(exact_ids)
        ]

        in_lexical = set(lexical_ids)
        in_semantic = set(semantic_ids)

        hits: list[RetrievalHit] = []

        for rank, card_id in enumerate(ordered[:top_k], start=1):
            card = self._index.get(card_id)

            if card is None:
                continue

            hits.append(
                RetrievalHit(
                    card=card,
                    score=1.0 if card_id in set(exact_ids) else fused.get(card_id, 0.0),
                    match_type=_match_type(card_id, exact_ids, in_lexical, in_semantic),
                    rank=rank,
                )
            )

        return hits

    def _exact(self, query: str, allowed_deck_ids: list[int]) -> list[int]:
        found: list[int] = []

        for token in extract_english_tokens(query):
            for card_id in self._index.lookup_word(token, allowed_deck_ids):
                if card_id not in found:
                    found.append(card_id)

        return found


def _match_type(
    card_id: int,
    exact_ids: list[int],
    in_lexical: set[int],
    in_semantic: set[int],
) -> MatchType:
    if card_id in exact_ids:
        return MatchType.EXACT

    if card_id in in_lexical and card_id in in_semantic:
        return MatchType.HYBRID

    if card_id in in_lexical:
        return MatchType.LEXICAL

    return MatchType.SEMANTIC
