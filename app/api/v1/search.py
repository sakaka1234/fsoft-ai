"""
POST /internal/v1/search — tìm kiếm ngữ nghĩa. SPEC muc 8.3.

Hoàn toàn KHÔNG tốn token LLM. Phục vụ U019 và đồng thời là công cụ debug
retrieval tốt nhất.
"""

import time

from fastapi import APIRouter, Depends, Request

from app.api.deps import require_internal_token
from app.core.errors import IndexNotReady, InvalidScope
from app.schemas.search import SearchRequest, SearchResponse, SearchResult

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Search"],
    dependencies=[Depends(require_internal_token)],
)


def validate_scope(allowed_deck_ids: list[int], scope_deck_id: int | None) -> list[int]:
    """
    Toàn bộ ranh giới bảo mật của service nằm ở đây.

    Danh sách rỗng phải FAIL ĐÓNG — hiểu thành "không được phép gì", tuyệt đối
    không hiểu thành "không lọc". SPEC muc 11.3 gọi đây là lỗi kinh điển.
    """

    if not allowed_deck_ids:
        raise InvalidScope("allowed_deck_ids không được rỗng.")

    if scope_deck_id is None:
        return allowed_deck_ids

    if scope_deck_id not in allowed_deck_ids:
        raise InvalidScope(f"scope_deck_id={scope_deck_id} không nằm trong allowed_deck_ids.")

    return [scope_deck_id]


@router.post("/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest) -> SearchResponse:
    service = request.app.state.service

    deck_ids = validate_scope(body.allowed_deck_ids, body.scope_deck_id)

    if not service.is_ready:
        raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

    started = time.perf_counter()

    query_vector = await service.encoder.embed_query(body.query)

    hits = service.retriever.retrieve(
        query=body.query,
        query_vector=query_vector,
        allowed_deck_ids=deck_ids,
        top_k=body.top_k,
    )

    return SearchResponse(
        results=[
            SearchResult(
                card_id=hit.card.card_id,
                word=hit.card.word,
                meaning=hit.card.meaning,
                deck_id=hit.card.deck_id,
                deck_title=hit.card.deck_title,
                score=round(hit.score, 4),
                match_type=hit.match_type,
            )
            for hit in hits
        ],
        latency_ms=int((time.perf_counter() - started) * 1000),
        candidate_count=len(hits),
    )
