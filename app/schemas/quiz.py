"""Schema cho POST /internal/v1/quiz/generate. SPEC muc 8.4."""

from enum import StrEnum

from pydantic import BaseModel, Field


class QuestionType(StrEnum):
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    FILL_BLANK = "FILL_BLANK"
    LISTENING = "LISTENING"
    MATCHING = "MATCHING"


class GeneratedBy(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    LLM = "LLM"


class MatchingPair(BaseModel):
    card_id: int
    word: str
    correct_option_index: int


class QuizQuestion(BaseModel):
    index: int
    type: QuestionType
    card_id: int | None = None
    prompt: str
    options: list[str] = Field(default_factory=list)
    correct_index: int | None = None
    explanation: str
    generated_by: GeneratedBy

    # Chỉ dạng LISTENING dùng.
    audio_url: str | None = None

    # Chỉ dạng MATCHING dùng: cột trái và đáp án nối sang `options`.
    matching: list[MatchingPair] | None = None


class QuizStats(BaseModel):
    deterministic_count: int
    llm_count: int
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class QuizRequest(BaseModel):
    deck_id: int
    allowed_deck_ids: list[int]
    question_count: int = Field(default=10, ge=1, le=50)
    types: list[QuestionType] = Field(default_factory=lambda: [QuestionType.MULTIPLE_CHOICE])
    card_ids: list[int] = Field(default_factory=list)
    use_ai_context: bool = False


class QuizResponse(BaseModel):
    questions: list[QuizQuestion]
    stats: QuizStats
