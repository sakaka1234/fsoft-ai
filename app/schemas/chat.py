"""Hợp đồng cho chat và retrieval. SPEC muc 8.1."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Intent(StrEnum):
    VOCAB_LOOKUP = "VOCAB_LOOKUP"
    EXAMPLE_REQUEST = "EXAMPLE_REQUEST"
    TRANSLATE = "TRANSLATE"
    GRAMMAR_QA = "GRAMMAR_QA"
    QUIZ_REQUEST = "QUIZ_REQUEST"
    SMALLTALK = "SMALLTALK"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class AnswerSource(StrEnum):
    DIRECT_LOOKUP = "DIRECT_LOOKUP"
    CACHE = "CACHE"
    RAG = "RAG"
    LLM_ONLY = "LLM_ONLY"
    CANNED = "CANNED"


class MatchType(StrEnum):
    EXACT = "EXACT"
    LEXICAL = "LEXICAL"
    SEMANTIC = "SEMANTIC"
    HYBRID = "HYBRID"


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatOptions(BaseModel):
    top_k: int | None = Field(default=None, ge=1, le=20)
    max_output_tokens: int | None = Field(default=None, ge=16, le=4096)


class ChatRequest(BaseModel):
    query: str
    allowed_deck_ids: list[int]
    scope_deck_id: int | None = None
    history: list[HistoryMessage] = Field(default_factory=list)
    options: ChatOptions = Field(default_factory=ChatOptions)


class CitationOut(BaseModel):
    card_id: int
    word: str
    deck_id: int
    deck_title: str | None = None
    score: float
    rank: int
    used_in_answer: bool


class UsageOut(BaseModel):
    provider: str = "groq"
    model: str | None = None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class ChatResponse(BaseModel):
    answer: str
    intent: Intent
    answer_source: AnswerSource
    rewritten_query: str | None = None
    citations: list[CitationOut]
    usage: UsageOut
