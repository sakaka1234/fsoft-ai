"""
Luồng chat RAG 12 bước. SPEC muc 11.5.

Thứ tự các bước KHÔNG được đổi. Mỗi bước đứng trước là một cơ hội thoát sớm
mà không tốn token, và mục tiêu ≥ 40% lưu lượng miễn phí ở SPEC muc 11.8 phụ
thuộc hoàn toàn vào việc các nhánh rẻ được thử trước.

 1. Kiểm allowed_deck_ids          -> rỗng thì 400
 2. Có cần viết lại câu hỏi không  -> LLM 8b, chỉ khi thật sự cần
 3. embed_query MỘT lần            -> dùng cho cả bước 4 và 6
 4. Phân loại intent               -> 0 token
 5. Rẽ nhánh theo intent           -> SMALLTALK/OUT_OF_SCOPE dừng ở đây
 6. Retrieval                      -> tra từ đơn giản dừng ở đây, 0 token
 7. Semantic cache                 -> câu lặp dừng ở đây, 0 token
 8. Kiểm ngân sách token           -> cạn thì 429
 9. Dựng prompt
10. Gọi LLM
11. Đánh dấu used_in_answer
12. Ghi usage_log và cache
"""

import re
from dataclasses import dataclass, field

import numpy as np

from app.chat.canned import canned_answer
from app.chat.direct_answer import build_direct_answer, is_simple_lookup
from app.chat.semantic_cache import SemanticCache, scope_hash
from app.config import Settings
from app.core.errors import IndexNotReady, InvalidScope
from app.core.logging import get_logger
from app.embedding.encoder import Encoder
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.retrieval.context import build_context
from app.retrieval.hybrid import HybridRetriever, RetrievalHit
from app.retrieval.intent import IntentClassifier
from app.schemas.chat import AnswerSource, Intent

log = get_logger(__name__)

# Đại từ chỉ định: dấu hiệu câu hỏi phụ thuộc lượt trước và cần viết lại.
_PRONOUN_RE = re.compile(
    r"\b(này|đó|kia|ấy|nó|vậy|thế|chúng|họ|trên|vừa rồi|it|this|that|them|these|those)\b",
    re.IGNORECASE,
)

# Mã thẻ [#101] xuất hiện trong câu trả lời.
_CITATION_RE = re.compile(r"\[#(\d+)\]")

REWRITE_MAX_WORDS = 8
REWRITE_MAX_TOKENS = 120

# Intent bỏ qua retrieval hoàn toàn: kiến thức chung, không nằm trong bộ thẻ.
_SKIP_RETRIEVAL = {Intent.GRAMMAR_QA, Intent.TRANSLATE}


@dataclass(slots=True)
class Citation:
    card_id: int
    word: str
    deck_id: int
    deck_title: str | None
    score: float
    rank: int
    used_in_answer: bool = False

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "word": self.word,
            "deck_id": self.deck_id,
            "deck_title": self.deck_title,
            "score": round(self.score, 4),
            "rank": self.rank,
            "used_in_answer": self.used_in_answer,
        }


@dataclass
class ChatOutcome:
    answer: str
    intent: Intent
    answer_source: AnswerSource
    citations: list[Citation] = field(default_factory=list)
    rewritten_query: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


@dataclass(slots=True)
class PreparedTurn:
    """Kết quả các bước 1-9: mọi thứ cần để gọi LLM, hoặc câu trả lời đã có sẵn."""

    intent: Intent
    citations: list[Citation]
    rewritten_query: str | None
    question: str
    query_vector: np.ndarray
    scope: str
    system_prompt: str = ""
    user_prompt: str = ""
    early_answer: str | None = None
    early_source: AnswerSource | None = None


def needs_rewrite(query: str, history: list[dict]) -> bool:
    """
    SPEC muc 11.5 bước 2.

    Viết lại tốn khoảng 150 token, nên chỉ làm khi thật sự cần: câu hỏi ngắn
    hoặc có đại từ chỉ định thì mới có khả năng phụ thuộc lượt trước.
    """

    if not history:
        return False

    # Câu dài mà không có đại từ thì đã tự đủ nghĩa, viết lại chỉ tốn token.
    return not (len(query.split()) > REWRITE_MAX_WORDS and not _PRONOUN_RE.search(query))


def format_history(history: list[dict], max_messages: int) -> str:
    recent = history[-max_messages:]

    return "\n".join(
        f"{'Người dùng' if item.get('role') == 'user' else 'Trợ lý'}: {item.get('content', '')}"
        for item in recent
    )


def mark_used_citations(answer: str, citations: list[Citation]) -> None:
    """SPEC muc 11.5 bước 11: thẻ nào có mã [#id] trong câu trả lời là đã dùng."""

    mentioned = {int(m) for m in _CITATION_RE.findall(answer)}

    for citation in citations:
        citation.used_in_answer = citation.card_id in mentioned


def to_citations(hits: list[RetrievalHit]) -> list[Citation]:
    return [
        Citation(
            card_id=hit.card.card_id,
            word=hit.card.word,
            deck_id=hit.card.deck_id,
            deck_title=hit.card.deck_title,
            score=hit.score,
            rank=hit.rank,
        )
        for hit in hits
    ]


class ChatOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        encoder: Encoder,
        retriever: HybridRetriever,
        intent_classifier: IntentClassifier,
        llm: LlmClient,
        prompts: PromptRegistry,
        cache: SemanticCache,
    ) -> None:
        self._settings = settings
        self._encoder = encoder
        self._retriever = retriever
        self._intent = intent_classifier
        self._llm = llm
        self._prompts = prompts
        self._cache = cache

    # ---------------------------------------------------------------
    # Bước 1-9
    # ---------------------------------------------------------------

    async def prepare(
        self,
        *,
        query: str,
        allowed_deck_ids: list[int],
        scope_deck_id: int | None = None,
        history: list[dict] | None = None,
        top_k: int | None = None,
        ready: bool = True,
    ) -> PreparedTurn:
        history = history or []
        top_k = top_k or self._settings.ai_top_k

        # --- 1. Phạm vi ---
        deck_ids = self._validate_scope(allowed_deck_ids, scope_deck_id)

        if not ready:
            raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

        # --- 2. Viết lại câu hỏi ---
        rewritten: str | None = None
        question = query

        if needs_rewrite(query, history):
            rewritten = await self._rewrite(query, history)
            question = rewritten or query

        # --- 3. Embed MỘT lần, dùng cho cả bước 4 và 6 ---
        query_vector = await self._encoder.embed_query(question)

        # --- 4. Intent, 0 token ---
        intent = self._intent.classify(question, query_vector).intent

        # --- 5. Rẽ nhánh ---
        canned = canned_answer(intent)

        if canned is not None:
            return PreparedTurn(
                intent=intent,
                citations=[],
                rewritten_query=rewritten,
                question=question,
                query_vector=query_vector,
                scope=scope_hash(deck_ids, []),
                early_answer=canned,
                early_source=AnswerSource.CANNED,
            )

        hits: list[RetrievalHit] = []

        if intent not in _SKIP_RETRIEVAL:
            # --- 6. Retrieval ---
            hits = self._retriever.retrieve(
                query=question,
                query_vector=query_vector,
                allowed_deck_ids=deck_ids,
                top_k=top_k,
            )

            direct = self._try_direct_answer(question, intent, hits)

            if direct is not None:
                citations = to_citations(hits[:1])
                mark_used_citations(direct, citations)

                return PreparedTurn(
                    intent=intent,
                    citations=citations,
                    rewritten_query=rewritten,
                    question=question,
                    query_vector=query_vector,
                    scope=scope_hash(deck_ids, [hits[0].card.card_id]),
                    early_answer=direct,
                    early_source=AnswerSource.DIRECT_LOOKUP,
                )

        citations = to_citations(hits)
        scope = scope_hash(deck_ids, [hit.card.card_id for hit in hits])

        # --- 7. Semantic cache ---
        cached = self._cache.get(query_vector, scope)

        if cached is not None:
            restored = [Citation(**item) for item in cached.citations]

            log.info("cache_hit", intent=intent.value, scope=scope[:12])

            return PreparedTurn(
                intent=intent,
                citations=restored,
                rewritten_query=rewritten,
                question=question,
                query_vector=query_vector,
                scope=scope,
                early_answer=cached.answer,
                early_source=AnswerSource.CACHE,
            )

        # --- 8 và 9. Dựng prompt (ngân sách kiểm ngay trong LlmClient) ---
        context = build_context(
            [hit.card for hit in hits], self._settings.ai_context_max_chars_per_field
        )

        return PreparedTurn(
            intent=intent,
            citations=citations,
            rewritten_query=rewritten,
            question=question,
            query_vector=query_vector,
            scope=scope,
            system_prompt=self._prompts.render("chat_system_v1"),
            user_prompt=self._prompts.render(
                "chat_user_v1",
                context=context or "(Bộ thẻ của người dùng không có thẻ nào khớp câu hỏi này.)",
                question=self._build_question_block(question, history),
            ),
        )

    # ---------------------------------------------------------------
    # Bước 10-12, chế độ blocking
    # ---------------------------------------------------------------

    async def chat(self, **kwargs) -> ChatOutcome:
        prepared = await self.prepare(**kwargs)

        if prepared.early_answer is not None:
            return ChatOutcome(
                answer=prepared.early_answer,
                intent=prepared.intent,
                answer_source=prepared.early_source or AnswerSource.CANNED,
                citations=prepared.citations,
                rewritten_query=prepared.rewritten_query,
            )

        result = await self._llm.complete(
            task="CHAT",
            system=prepared.system_prompt,
            user=prepared.user_prompt,
            intent=prepared.intent.value,
            answer_source=AnswerSource.RAG.value,
        )

        mark_used_citations(result.text, prepared.citations)

        self._cache.put(
            prepared.query_vector,
            prepared.scope,
            result.text,
            [citation.to_dict() for citation in prepared.citations],
        )

        return ChatOutcome(
            answer=result.text,
            intent=prepared.intent,
            answer_source=(AnswerSource.RAG if prepared.citations else AnswerSource.LLM_ONLY),
            citations=prepared.citations,
            rewritten_query=prepared.rewritten_query,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            latency_ms=result.latency_ms,
        )

    # ---------------------------------------------------------------

    def _validate_scope(self, allowed_deck_ids: list[int], scope_deck_id: int | None) -> list[int]:
        if not allowed_deck_ids:
            raise InvalidScope("allowed_deck_ids không được rỗng.")

        if scope_deck_id is None:
            return allowed_deck_ids

        if scope_deck_id not in allowed_deck_ids:
            raise InvalidScope(f"scope_deck_id={scope_deck_id} không nằm trong allowed_deck_ids.")

        return [scope_deck_id]

    async def _rewrite(self, query: str, history: list[dict]) -> str | None:
        """Rút gọn ngữ cảnh bằng model 8b. Hỏng thì dùng câu gốc, không làm sập lượt chat."""

        prompt = self._prompts.render(
            "query_rewrite_v1",
            history=format_history(history, self._settings.ai_history_max_messages),
            question=query,
        )

        try:
            result = await self._llm.complete(
                task="REWRITE",
                system=self._prompts.render("query_rewrite_system_v1"),
                user=prompt,
                model=self._settings.ai_model_rewrite,
                max_tokens=REWRITE_MAX_TOKENS,
            )

            return result.text.strip().strip('"') or None

        except Exception:
            log.warning("rewrite_failed_dung_cau_goc", exc_info=True)

            return None

    def _try_direct_answer(
        self, question: str, intent: Intent, hits: list[RetrievalHit]
    ) -> str | None:
        """Tầng 1 trúng + VOCAB_LOOKUP + câu hỏi đơn giản -> trả template, 0 token."""

        if intent != Intent.VOCAB_LOOKUP or not hits:
            return None

        from app.schemas.chat import MatchType

        if hits[0].match_type != MatchType.EXACT:
            return None

        if not is_simple_lookup(question):
            return None

        return build_direct_answer(hits[0].card)

    def _build_question_block(self, question: str, history: list[dict]) -> str:
        if not history:
            return question

        recent = format_history(history, self._settings.ai_history_max_messages)

        return f"{recent}\nNgười dùng: {question}"
