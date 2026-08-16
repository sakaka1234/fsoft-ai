"""
POST /internal/v1/chat và /chat/stream. SPEC muc 8.1 và 8.2.

Backend Java gọi hai endpoint này. fsoft-ai không biết gì về người dùng —
`allowed_deck_ids` là toàn bộ ranh giới bảo mật, và backend chịu trách nhiệm
tính đúng danh sách đó.
"""

import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import require_internal_token
from app.chat.orchestrator import mark_used_citations
from app.core.errors import AppError
from app.core.logging import get_logger
from app.schemas.chat import (
    AnswerSource,
    ChatRequest,
    ChatResponse,
    CitationOut,
    UsageOut,
)

log = get_logger(__name__)

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Chat"],
    dependencies=[Depends(require_internal_token)],
)


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    service = request.app.state.service

    outcome = await service.orchestrator.chat(
        query=body.query,
        allowed_deck_ids=body.allowed_deck_ids,
        scope_deck_id=body.scope_deck_id,
        history=[item.model_dump() for item in body.history],
        top_k=body.options.top_k,
        ready=service.is_ready,
    )

    return ChatResponse(
        answer=outcome.answer,
        intent=outcome.intent,
        answer_source=outcome.answer_source,
        rewritten_query=outcome.rewritten_query,
        citations=[CitationOut(**c.to_dict()) for c in outcome.citations],
        usage=UsageOut(
            model=outcome.model,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            latency_ms=outcome.latency_ms,
        ),
    )


@router.post("/chat/stream")
async def chat_stream(request: Request, body: ChatRequest) -> StreamingResponse:
    service = request.app.state.service

    async def events():
        started = time.perf_counter()

        try:
            prepared = await service.orchestrator.prepare(
                query=body.query,
                allowed_deck_ids=body.allowed_deck_ids,
                scope_deck_id=body.scope_deck_id,
                history=[item.model_dump() for item in body.history],
                top_k=body.options.top_k,
                ready=service.is_ready,
            )

        except AppError as exc:
            yield sse("error", {"code": exc.code, "message": exc.message})
            return

        source = prepared.early_source or AnswerSource.RAG

        yield sse(
            "meta",
            {
                "intent": prepared.intent.value,
                "answer_source": source.value,
                "rewritten_query": prepared.rewritten_query,
            },
        )

        # SPEC muc 8.2: citations PHẢI phát trước token đầu tiên. Giao diện
        # hiện nguồn ngay, tạo cảm giác phản hồi nhanh hơn hẳn.
        yield sse("citations", [c.to_dict() for c in prepared.citations])

        if prepared.early_answer is not None:
            yield sse("token", {"t": prepared.early_answer})
            yield sse(
                "done",
                {
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                    }
                },
            )
            return

        pieces: list[str] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}

        try:
            async for chunk in service.llm.stream(
                task="CHAT",
                system=prepared.system_prompt,
                user=prepared.user_prompt,
                max_tokens=body.options.max_output_tokens,
                intent=prepared.intent.value,
                answer_source=AnswerSource.RAG.value,
            ):
                if chunk.done:
                    if chunk.usage:
                        usage = {
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                            "latency_ms": chunk.usage.latency_ms,
                        }

                    continue

                pieces.append(chunk.text)

                yield sse("token", {"t": chunk.text})

        except AppError as exc:
            yield sse("error", {"code": exc.code, "message": exc.message})
            return

        answer = "".join(pieces)

        mark_used_citations(answer, prepared.citations)

        service.cache.put(
            prepared.query_vector,
            prepared.scope,
            answer,
            [c.to_dict() for c in prepared.citations],
        )

        yield sse("done", {"usage": usage})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
