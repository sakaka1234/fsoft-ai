"""
Điều phối sinh quiz. SPEC muc 8.4 và muc 11.6.

`use_ai_context=false` -> 100% deterministic, không chạm Groq lần nào. Đây là
chế độ dự phòng cho ngày bảo vệ (SPEC muc 14.1).
"""

import random
import time

from app.config import Settings
from app.core.errors import InvalidRequest, InvalidScope
from app.core.logging import get_logger
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.quiz.deterministic import (
    OPTIONS_PER_QUESTION,
    build_listening,
    build_matching,
    build_multiple_choice,
)
from app.quiz.llm_generator import FillBlankGenerator
from app.retrieval.search_index import SearchIndex
from app.schemas.card import SourceCard
from app.schemas.quiz import QuestionType, QuizQuestion, QuizRequest, QuizStats

log = get_logger(__name__)

# Một lời gọi LLM cho 5 câu điền từ (SPEC muc 11.6).
LLM_BATCH_DEFAULT = 5


class QuizGenerator:
    def __init__(
        self,
        *,
        settings: Settings,
        index: SearchIndex,
        llm: LlmClient,
        prompts: PromptRegistry,
    ) -> None:
        self._settings = settings
        self._index = index
        self._llm = llm
        self._prompts = prompts

    async def generate(
        self, request: QuizRequest, rng: random.Random | None = None
    ) -> tuple[list[QuizQuestion], QuizStats]:
        started = time.perf_counter()
        rng = rng or random.Random()

        cards = self._select_cards(request)

        plan = self._plan_types(request, len(cards))

        questions: list[QuizQuestion] = []
        fill_blank_cards: list[SourceCard] = []

        for position, (card, kind) in enumerate(zip(cards, plan), start=1):
            if kind == QuestionType.FILL_BLANK:
                fill_blank_cards.append(card)
                continue

            question = self._build_deterministic(kind, card, cards, position, rng)

            # Không sinh được dạng đã định (thẻ thiếu audio, thiếu nhiễu...) thì
            # lùi về trắc nghiệm thay vì bỏ trống câu hỏi.
            if question is None and kind != QuestionType.MULTIPLE_CHOICE:
                question = build_multiple_choice(
                    self._index,
                    card,
                    index_number=position,
                    max_cosine=self._settings.ai_quiz_distractor_max_cosine,
                    rng=rng,
                )

            if question is not None:
                questions.append(question)

        prompt_tokens = 0
        completion_tokens = 0
        llm_count = 0

        if fill_blank_cards:
            generator = FillBlankGenerator(
                index=self._index,
                llm=self._llm,
                prompts=self._prompts,
                max_cosine=self._settings.ai_quiz_distractor_max_cosine,
                model=self._settings.ai_model_quiz,
                rng=rng,
            )

            batch_size = self._settings.ai_quiz_llm_batch_size or LLM_BATCH_DEFAULT

            for offset in range(0, len(fill_blank_cards), batch_size):
                batch = fill_blank_cards[offset : offset + batch_size]

                questions.extend(await generator.generate(batch, start_index=0))

            prompt_tokens = generator.prompt_tokens
            completion_tokens = generator.completion_tokens
            llm_count = sum(1 for q in questions if q.generated_by.value == "LLM")

        if not questions:
            raise InvalidRequest(
                "Không sinh được câu hỏi nào từ bộ thẻ này. "
                "Bộ thẻ cần ít nhất 4 thẻ có nghĩa khác nhau."
            )

        for position, question in enumerate(questions, start=1):
            question.index = position

        return questions, QuizStats(
            deterministic_count=len(questions) - llm_count,
            llm_count=llm_count,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ---------------------------------------------------------------

    def _select_cards(self, request: QuizRequest) -> list[SourceCard]:
        if not request.allowed_deck_ids:
            raise InvalidScope("allowed_deck_ids không được rỗng.")

        if request.deck_id not in request.allowed_deck_ids:
            raise InvalidScope(f"deck_id={request.deck_id} không nằm trong allowed_deck_ids.")

        pool = self._index.cards_in_decks([request.deck_id])

        if request.card_ids:
            # card_ids do backend Java chọn theo thẻ đến hạn ôn SRS. fsoft-ai
            # không biết gì về SRS, chỉ lọc lại theo phạm vi cho an toàn.
            wanted = set(request.card_ids)
            pool = [card for card in pool if card.card_id in wanted]

        if len(pool) < OPTIONS_PER_QUESTION:
            raise InvalidRequest(
                f"Bộ thẻ chỉ có {len(pool)} thẻ trong phạm vi, "
                f"cần ít nhất {OPTIONS_PER_QUESTION} thẻ để tạo câu trắc nghiệm."
            )

        pool.sort(key=lambda card: card.card_id)

        return pool[: request.question_count]

    def _plan_types(self, request: QuizRequest, count: int) -> list[QuestionType]:
        types = list(request.types) or [QuestionType.MULTIPLE_CHOICE]

        if not request.use_ai_context:
            # Chế độ dự phòng: bỏ hẳn dạng cần LLM.
            types = [t for t in types if t != QuestionType.FILL_BLANK]

            if not types:
                types = [QuestionType.MULTIPLE_CHOICE]

        return [types[i % len(types)] for i in range(count)]

    def _build_deterministic(
        self,
        kind: QuestionType,
        card: SourceCard,
        all_cards: list[SourceCard],
        position: int,
        rng: random.Random,
    ) -> QuizQuestion | None:
        max_cosine = self._settings.ai_quiz_distractor_max_cosine

        if kind == QuestionType.LISTENING:
            return build_listening(
                self._index, card, index_number=position, max_cosine=max_cosine, rng=rng
            )

        if kind == QuestionType.MATCHING:
            others = [c for c in all_cards if c.card_id != card.card_id]

            return build_matching([card, *others], index_number=position, rng=rng)

        return build_multiple_choice(
            self._index, card, index_number=position, max_cosine=max_cosine, rng=rng
        )
