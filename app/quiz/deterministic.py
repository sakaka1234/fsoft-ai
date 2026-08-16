"""
Sinh câu hỏi không cần LLM. SPEC muc 11.6.

BA TRONG BỐN dạng quiz không cần LLM. Đây là chế độ dự phòng cho ngày bảo vệ:
`use_ai_context=false` cho 100% deterministic, không chạm Groq lần nào.
"""

import random

from app.quiz.distractors import pick_distractors
from app.retrieval.search_index import SearchIndex
from app.schemas.card import SourceCard
from app.schemas.quiz import GeneratedBy, MatchingPair, QuestionType, QuizQuestion

# Trắc nghiệm cần 1 đáp án đúng + 3 nhiễu.
OPTIONS_PER_QUESTION = 4
DISTRACTORS_NEEDED = OPTIONS_PER_QUESTION - 1

MATCHING_PAIRS = 5


def build_multiple_choice(
    index: SearchIndex,
    card: SourceCard,
    *,
    index_number: int,
    max_cosine: float,
    rng: random.Random,
) -> QuizQuestion | None:
    """Từ -> nghĩa. Đáp án đúng là `meaning`, nhiễu là `meaning` của láng giềng."""

    distractors = pick_distractors(
        index, card, count=DISTRACTORS_NEEDED, max_cosine=max_cosine, field="meaning"
    )

    if len(distractors) < DISTRACTORS_NEEDED:
        return None

    options = [card.meaning] + [d.meaning for d in distractors]
    rng.shuffle(options)

    return QuizQuestion(
        index=index_number,
        type=QuestionType.MULTIPLE_CHOICE,
        card_id=card.card_id,
        prompt=f'"{card.word}" nghĩa là gì?',
        options=options,
        correct_index=options.index(card.meaning),
        explanation=_explain(card),
        generated_by=GeneratedBy.DETERMINISTIC,
    )


def build_listening(
    index: SearchIndex,
    card: SourceCard,
    *,
    index_number: int,
    max_cosine: float,
    rng: random.Random,
) -> QuizQuestion | None:
    """
    Nghe -> chọn từ. Bốn lựa chọn là `word`.

    Thẻ không có `audio_url` thì KHÔNG sinh được — thẻ 402 (`tuition`) trong
    fixture cố ý để `null` đúng cho tình huống này.
    """

    if not card.audio_url:
        return None

    distractors = pick_distractors(
        index, card, count=DISTRACTORS_NEEDED, max_cosine=max_cosine, field="word"
    )

    if len(distractors) < DISTRACTORS_NEEDED:
        return None

    options = [card.word] + [d.word for d in distractors]
    rng.shuffle(options)

    return QuizQuestion(
        index=index_number,
        type=QuestionType.LISTENING,
        card_id=card.card_id,
        prompt="Nghe và chọn từ bạn nghe được.",
        options=options,
        correct_index=options.index(card.word),
        explanation=_explain(card),
        generated_by=GeneratedBy.DETERMINISTIC,
        audio_url=card.audio_url,
    )


def build_matching(
    cards: list[SourceCard], *, index_number: int, rng: random.Random
) -> QuizQuestion | None:
    """Nối từ với nghĩa. Lấy tối đa 5 thẻ, xáo trộn cột nghĩa."""

    pool = [card for card in cards if card.meaning]

    if len(pool) < 2:
        return None

    selected = pool[:MATCHING_PAIRS]

    meanings = [card.meaning for card in selected]
    rng.shuffle(meanings)

    return QuizQuestion(
        index=index_number,
        type=QuestionType.MATCHING,
        prompt="Nối mỗi từ với nghĩa đúng của nó.",
        options=meanings,
        explanation="Đối chiếu từ ở cột trái với nghĩa tiếng Việt ở cột phải.",
        generated_by=GeneratedBy.DETERMINISTIC,
        matching=[
            MatchingPair(
                card_id=card.card_id,
                word=card.word,
                correct_option_index=meanings.index(card.meaning),
            )
            for card in selected
        ],
    )


def build_fallback_multiple_choice(
    index: SearchIndex,
    card: SourceCard,
    *,
    index_number: int,
    max_cosine: float,
    rng: random.Random,
) -> QuizQuestion | None:
    """
    Thay thế khi LLM sinh hỏng. SPEC muc 11.6.

    Không bao giờ trả lỗi cho người dùng chỉ vì LLM sinh sai — người học không
    quan tâm câu hỏi được sinh bằng cách nào.
    """

    return build_multiple_choice(
        index, card, index_number=index_number, max_cosine=max_cosine, rng=rng
    )


def _explain(card: SourceCard) -> str:
    parts = [f"{card.word}: {card.meaning}."]

    if card.definition_en:
        parts.append(f"Tiếng Anh: {card.definition_en}.")

    if card.example_sentence:
        parts.append(f"Ví dụ: {card.example_sentence}")

    return " ".join(parts)
