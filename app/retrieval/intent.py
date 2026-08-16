"""
Phân loại intent — KHÔNG gọi LLM. SPEC muc 11.3 mục 2.

Hai bước:
  1. Rule regex. Bắt được thì dừng luôn, gần 0ms.
  2. Centroid của câu mẫu đã embed sẵn. Câu hỏi đã được embed cho retrieval
     rồi nên bước này tốn thêm đúng một phép nhân ma trận 7x384.

Tổng chi phí: 0 token, dưới 1ms. Tiết kiệm ~230 token mỗi lượt so với hỏi LLM
(SPEC muc 4.1).

VỀ NGƯỠNG: `AI_INTENT_THRESHOLD` KHÔNG dùng như ngưỡng tuyệt đối. E5 nén điểm
cosine query-query vào dải rất hẹp và rất cao — hai câu vô can vẫn đạt ~0.85
(docs/M0_FINDINGS.md muc 2.5). Vì vậy `OUT_OF_SCOPE` là một lớp có câu mẫu
riêng và quyết định bằng argmax tương đối, còn ngưỡng chỉ dùng cho biên độ
giữa hạng nhất và hạng nhì.
"""

import re
from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.core.logging import get_logger
from app.embedding.encoder import Encoder
from app.retrieval.intent_examples import INTENT_EXAMPLES
from app.schemas.chat import Intent

log = get_logger(__name__)


@dataclass(slots=True)
class IntentResult:
    intent: Intent
    method: Literal["RULE", "CENTROID"]
    score: float
    margin: float = 0.0


# ---------------------------------------------------------------------
# Bước 1 — rule
# ---------------------------------------------------------------------
#
# Thứ tự QUAN TRỌNG: câu "cho tôi ví dụ với từ resilient" chứa cả dấu hiệu của
# EXAMPLE_REQUEST lẫn của VOCAB_LOOKUP. Luật nào đặc thù hơn phải đứng trước.

_RULES: list[tuple[Intent, re.Pattern[str]]] = [
    (
        Intent.QUIZ_REQUEST,
        re.compile(
            r"\b(tạo|làm|sinh|cho)\b.{0,20}\b(quiz|bài kiểm tra|bài test|trắc nghiệm)\b"
            r"|\bkiểm tra (kiến thức|tôi|mình)\b"
            r"|\bthi thử\b"
            r"|\bôn tập\b",
            re.IGNORECASE,
        ),
    ),
    (
        Intent.EXAMPLE_REQUEST,
        re.compile(
            r"\bđặt câu\b"
            r"|\bví dụ\b"
            r"|\bexample\b"
            r"|\bdùng .{0,15}trong câu\b"
            r"|\bviết .{0,10}câu\b",
            re.IGNORECASE,
        ),
    ),
    (
        Intent.TRANSLATE,
        # "dịch" phải đứng như một hành động. Không bắt "tiếng anh là gì" —
        # đó là tra từ, không phải dịch câu (xem case R036 ở SPEC muc 11.3).
        re.compile(r"\bdịch\b|\btranslate\b", re.IGNORECASE),
    ),
    (
        Intent.GRAMMAR_QA,
        # "thì" là hư từ cực phổ biến trong tiếng Việt ("nếu... thì..."), nên
        # chỉ bắt khi nó đi kèm tên một thì cụ thể.
        re.compile(
            r"\bngữ pháp\b"
            r"|\bthì (hiện tại|quá khứ|tương lai)\b"
            r"|\bchia (thì|động từ)\b"
            r"|\bcâu (điều kiện|bị động|tường thuật|hỏi)\b"
            r"|\bmệnh đề\b"
            r"|\bgiới từ\b"
            r"|\bso sánh (hơn|nhất)\b"
            r"|\b(danh|tính|trạng) từ hoá\b"
            r"|\bmodal verb\b",
            re.IGNORECASE,
        ),
    ),
    (
        Intent.SMALLTALK,
        re.compile(
            r"^\s*(xin chào|chào bạn|chào buổi|hello|hi)\b"
            r"|\bcảm ơn\b"
            r"|\btạm biệt\b"
            r"|\bbạn (là ai|tên gì)\b",
            re.IGNORECASE,
        ),
    ),
    (
        Intent.VOCAB_LOOKUP,
        re.compile(
            r"\bnghĩa (là gì|của|gì)\b"
            r"|\blà gì\b"
            r"|\bcó nghĩa\b"
            r"|\bđịnh nghĩa\b"
            r"|\bphát âm\b"
            r"|\btừ loại\b"
            r"|\btừ nào\b"
            # "giải thích ... từ X". Luật GRAMMAR_QA chạy trước nên
            # "giải thích thì hiện tại" không rơi nhầm vào đây.
            r"|\bgiải thích\b.{0,25}\btừ\b"
            r"|\bwhat does\b.{0,30}\bmean\b",
            re.IGNORECASE,
        ),
    ),
]


def classify_by_rule(query: str) -> Intent | None:
    for intent, pattern in _RULES:
        if pattern.search(query):
            return intent

    return None


# ---------------------------------------------------------------------
# Bước 2 — centroid
# ---------------------------------------------------------------------


class IntentClassifier:
    def __init__(self, encoder: Encoder, min_margin: float = 0.0) -> None:
        self._encoder = encoder
        self._min_margin = min_margin

        self._intents: list[Intent] = []
        self._centroids: np.ndarray | None = None

    @property
    def is_ready(self) -> bool:
        return self._centroids is not None

    async def warmup(self) -> None:
        """Embed toàn bộ câu mẫu một lần lúc khởi động, rồi giữ centroid."""

        intents: list[Intent] = []
        centroids: list[np.ndarray] = []

        for intent, examples in INTENT_EXAMPLES.items():
            vectors = await self._encoder.embed_queries(examples)

            centroid = np.mean(np.asarray(vectors, dtype=np.float32), axis=0)
            norm = float(np.linalg.norm(centroid))

            # Chuẩn hoá để cosine với câu hỏi lại thành dot product thuần.
            centroids.append(centroid / norm if norm else centroid)
            intents.append(intent)

        self._intents = intents
        self._centroids = np.asarray(centroids, dtype=np.float32)

        log.info(
            "intent_centroids_ready",
            intents=len(intents),
            examples=sum(len(v) for v in INTENT_EXAMPLES.values()),
        )

    def classify(self, query: str, query_vector: np.ndarray) -> IntentResult:
        by_rule = classify_by_rule(query)

        if by_rule is not None:
            return IntentResult(intent=by_rule, method="RULE", score=1.0, margin=1.0)

        if self._centroids is None:
            # Chưa warmup: đoán an toàn nhất là tra từ, nhánh rẻ nhất và
            # không bao giờ gây hại.
            return IntentResult(intent=Intent.VOCAB_LOOKUP, method="CENTROID", score=0.0)

        scores = self._centroids @ np.asarray(query_vector, dtype=np.float32)
        order = np.argsort(-scores)

        best = int(order[0])
        margin = float(scores[best] - scores[int(order[1])])

        # Biên độ quá mỏng nghĩa là câu hỏi nằm giữa hai lớp. Nghiêng về
        # VOCAB_LOOKUP: đoán nhầm thành tra từ chỉ tốn một lần retrieval miễn
        # phí, còn đoán nhầm thành OUT_OF_SCOPE là từ chối trả lời người dùng.
        if margin < self._min_margin and self._intents[best] == Intent.OUT_OF_SCOPE:
            return IntentResult(
                intent=Intent.VOCAB_LOOKUP,
                method="CENTROID",
                score=float(scores[best]),
                margin=margin,
            )

        return IntentResult(
            intent=self._intents[best],
            method="CENTROID",
            score=float(scores[best]),
            margin=margin,
        )
