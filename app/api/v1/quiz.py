"""POST /internal/v1/quiz/generate. SPEC muc 8.4."""

from fastapi import APIRouter, Depends, Request

from app.api.deps import require_internal_token
from app.core.errors import IndexNotReady
from app.schemas.quiz import QuizRequest, QuizResponse

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Quiz"],
    dependencies=[Depends(require_internal_token)],
)


@router.post("/quiz/generate", response_model=QuizResponse)
async def generate_quiz(request: Request, body: QuizRequest) -> QuizResponse:
    service = request.app.state.service

    if not service.is_ready:
        raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

    questions, stats = await service.quiz.generate(body)

    return QuizResponse(questions=questions, stats=stats)
