"""Schema cho POST /internal/v1/search. SPEC muc 8.3."""

from pydantic import BaseModel, Field

from app.schemas.chat import MatchType


class SearchRequest(BaseModel):
    query: str
    allowed_deck_ids: list[int]
    top_k: int = Field(default=5, ge=1, le=50)
    scope_deck_id: int | None = None


class SearchResult(BaseModel):
    card_id: int
    word: str
    meaning: str
    deck_id: int
    deck_title: str | None
    score: float
    match_type: MatchType


class SearchResponse(BaseModel):
    results: list[SearchResult]
    latency_ms: int
    candidate_count: int
