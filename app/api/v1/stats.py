"""
Quan trắc. SPEC muc 11.8.

Endpoint này không dành cho người dùng cuối mà dành cho người vận hành: nó trả
lời đúng một câu hỏi — hệ thống có đang đốt token vào việc mà dữ liệu cục bộ
làm được miễn phí hay không.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import require_internal_token
from app.core.errors import InvalidRequest
from app.schemas.common import MetricTarget, UsageStatsResponse
from app.store.usage_repo import UsageStats

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Quan trắc"],
    dependencies=[Depends(require_internal_token)],
)

DEFAULT_WINDOW_HOURS = 24

# Ngưỡng ở SPEC muc 11.8. Để nguyên đây chứ không đưa vào .env: đây là mục tiêu
# thiết kế của hệ thống, không phải thứ chỉnh theo môi trường. Ai muốn đổi thì
# phải sửa SPEC trước.
TARGET_FREE_RATIO = 0.40
TARGET_AVG_TOKENS_PER_CHAT = 1200.0
TARGET_LATENCY_P95_MS = 3000.0
TARGET_ERROR_RATE = 0.02


def _parse(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback

    try:
        # Từ Python 3.11, fromisoformat đọc được hậu tố 'Z' trực tiếp.
        parsed = datetime.fromisoformat(value)

    except ValueError as exc:
        raise InvalidRequest(
            f"Không đọc được mốc thời gian {value!r}. Cần ISO-8601, ví dụ 2026-08-16T00:00:00Z."
        ) from exc

    # Thiếu offset thì hiểu là UTC, đúng như định dạng service tự ghi ra.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _at_least(value: float, target: float) -> MetricTarget:
    return MetricTarget(value=round(value, 4), target=target, ok=value >= target, comparison=">=")


def _below(value: float, target: float) -> MetricTarget:
    return MetricTarget(value=round(value, 4), target=target, ok=value < target, comparison="<")


def build_response(stats: UsageStats) -> UsageStatsResponse:
    free_ratio = _at_least(stats.free_ratio, TARGET_FREE_RATIO)
    avg_tokens = _below(stats.avg_tokens_per_chat, TARGET_AVG_TOKENS_PER_CHAT)
    latency = _below(float(stats.latency_p95_ms), TARGET_LATENCY_P95_MS)
    error_rate = _below(stats.error_rate, TARGET_ERROR_RATE)

    return UsageStatsResponse(
        since=stats.since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        until=stats.until.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        calls=stats.calls,
        chat_turns=stats.chat_turns,
        prompt_tokens=stats.prompt_tokens,
        completion_tokens=stats.completion_tokens,
        total_tokens=stats.prompt_tokens + stats.completion_tokens,
        by_task=stats.by_task,
        by_answer_source=stats.by_answer_source,
        free_ratio=free_ratio,
        avg_tokens_per_chat=avg_tokens,
        latency_p95_ms=latency,
        error_rate=error_rate,
        all_targets_met=all((free_ratio.ok, avg_tokens.ok, latency.ok, error_rate.ok)),
    )


@router.get("/stats", response_model=UsageStatsResponse)
async def usage_stats(
    request: Request,
    # `from` là từ khoá Python nên không đặt tên tham số như vậy được; `alias`
    # giữ đúng tên query param mà SPEC muc 11.8 công bố.
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
) -> UsageStatsResponse:
    """Mặc định 24 giờ gần nhất."""

    service = request.app.state.service

    until = _parse(to, datetime.now(UTC))
    since = _parse(from_, until - timedelta(hours=DEFAULT_WINDOW_HOURS))

    if since >= until:
        raise InvalidRequest("`from` phải nhỏ hơn `to`.")

    return build_response(await service.usage_repo.stats(since, until))
