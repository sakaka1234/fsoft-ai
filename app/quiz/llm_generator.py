"""
Sinh câu điền từ bằng LLM. SPEC muc 11.6.

Đây là dạng DUY NHẤT cần LLM: nó đòi một câu tiếng Anh MỚI, có ngữ cảnh, khác
câu ví dụ đã có trên thẻ. Ba dạng còn lại dựng thẳng từ dữ liệu thẻ.

Sinh THEO LÔ: một lời gọi cho 5 câu, không phải 5 lời gọi. Tiết kiệm khoảng
bốn lần token vì system prompt chỉ gửi một lần.
"""

import json
import random

from app.core.logging import get_logger
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.quiz.deterministic import DISTRACTORS_NEEDED, build_fallback_multiple_choice
from app.quiz.distractors import pick_distractors
from app.quiz.validator import validate_question
from app.retrieval.search_index import SearchIndex
from app.schemas.card import SourceCard
from app.schemas.quiz import GeneratedBy, QuestionType, QuizQuestion

log = get_logger(__name__)

MAX_ATTEMPTS = 3


class FillBlankGenerator:
    def __init__(
        self,
        *,
        index: SearchIndex,
        llm: LlmClient,
        prompts: PromptRegistry,
        max_cosine: float,
        model: str,
        rng: random.Random,
    ) -> None:
        self._index = index
        self._llm = llm
        self._prompts = prompts
        self._max_cosine = max_cosine
        self._model = model
        self._rng = rng

        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0

    async def generate(self, cards: list[SourceCard], start_index: int) -> list[QuizQuestion]:
        """
        Trả về đúng `len(cards)` câu hỏi.

        Câu nào LLM sinh hỏng thì THAY bằng câu trắc nghiệm deterministic —
        không bao giờ trả lỗi cho người dùng chỉ vì LLM sinh sai.
        """

        if not cards:
            return []

        distractor_map = {
            card.card_id: pick_distractors(
                self._index,
                card,
                count=DISTRACTORS_NEEDED,
                max_cosine=self._max_cosine,
                field="word",
            )
            for card in cards
        }

        by_card: dict[int, QuizQuestion] = {}

        for attempt in range(MAX_ATTEMPTS):
            missing = [card for card in cards if card.card_id not in by_card]

            if not missing:
                break

            parsed = await self._call_llm(missing, distractor_map)

            for card in missing:
                raw = parsed.get(card.card_id)

                if raw is None:
                    continue

                question = self._to_question(raw, card, index_number=0)

                if question is None:
                    continue

                result = validate_question(question, answer_word=card.word)

                if result.ok:
                    by_card[card.card_id] = question

                else:
                    log.warning(
                        "quiz_question_invalid",
                        card_id=card.card_id,
                        reason=result.reason,
                        attempt=attempt + 1,
                    )

        questions: list[QuizQuestion] = []

        for offset, card in enumerate(cards):
            question = by_card.get(card.card_id)

            if question is None:
                fallback = build_fallback_multiple_choice(
                    self._index,
                    card,
                    index_number=start_index + offset,
                    max_cosine=self._max_cosine,
                    rng=self._rng,
                )

                if fallback is None:
                    continue

                log.info("quiz_fallback_deterministic", card_id=card.card_id)

                questions.append(fallback)
                continue

            question.index = start_index + offset
            questions.append(question)

        return questions

    # ---------------------------------------------------------------

    async def _call_llm(
        self, cards: list[SourceCard], distractor_map: dict[int, list[SourceCard]]
    ) -> dict[int, dict]:
        lines = []

        for card in cards:
            nhieu = ", ".join(d.word for d in distractor_map.get(card.card_id, []))

            lines.append(
                f"- card_id={card.card_id} | từ: {card.word} | nghĩa: {card.meaning}"
                f" | từ nhiễu: {nhieu or '(không có)'}"
            )

        try:
            result = await self._llm.complete(
                task="QUIZ",
                system=self._prompts.render("quiz_fill_blank_v1", cards="\n".join(lines)),
                user="Soạn câu hỏi cho toàn bộ danh sách trên.",
                model=self._model,
                json_mode=True,
                max_tokens=250 * len(cards),
            )

        except Exception:
            log.warning("quiz_llm_call_failed", exc_info=True)

            return {}

        self.llm_calls += 1
        self.prompt_tokens += result.prompt_tokens
        self.completion_tokens += result.completion_tokens

        return self._parse(result.text)

    @staticmethod
    def _parse(text: str) -> dict[int, dict]:
        try:
            payload = json.loads(text)

        except json.JSONDecodeError:
            log.warning("quiz_json_invalid", preview=text[:200])

            return {}

        items = payload.get("questions") if isinstance(payload, dict) else payload

        if not isinstance(items, list):
            return {}

        parsed: dict[int, dict] = {}

        for item in items:
            if isinstance(item, dict) and isinstance(item.get("card_id"), int):
                parsed[item["card_id"]] = item

        return parsed

    @staticmethod
    def _to_question(raw: dict, card: SourceCard, index_number: int) -> QuizQuestion | None:
        try:
            return QuizQuestion(
                index=index_number,
                type=QuestionType.FILL_BLANK,
                card_id=card.card_id,
                prompt=str(raw.get("prompt", "")),
                options=[str(o) for o in raw.get("options", [])],
                correct_index=raw.get("correct_index"),
                explanation=str(raw.get("explanation", "")),
                generated_by=GeneratedBy.LLM,
            )

        except Exception:  # noqa: BLE001 - LLM trả gì cũng có thể, hỏng thì bỏ câu này
            return None
