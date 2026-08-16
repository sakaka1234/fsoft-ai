"""Enum hợp đồng cho chat và retrieval. SPEC muc 8.1."""

from enum import StrEnum


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
