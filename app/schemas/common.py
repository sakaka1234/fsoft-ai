"""Schema dùng chung. Field theo snake_case — SPEC muc 8."""

from pydantic import BaseModel


class IndexStatus(BaseModel):
    """Phản hồi của GET /internal/v1/index/status. SPEC muc 8.5."""

    card_count: int
    index_size: int
    model_version: str
    last_sync_ts: str | None
    last_sync_at: str | None
    last_sync_duration_ms: int | None
    last_sync_embedded: int
    last_sync_skipped: int
    last_full_sweep_at: str | None
    last_sync_error: str | None
    backend_reachable: bool | None
    source_mode: str


class SyncTriggerResult(BaseModel):
    """Phản hồi của POST /internal/v1/index/sync."""

    embedded: int
    skipped: int
    deleted: int
    duration_ms: int
    error: str | None


class MetricTarget(BaseModel):
    """
    Một chỉ số kèm ngưỡng mục tiêu ở SPEC muc 11.8.

    Trả kèm `target` và `ok` để người đọc bảng stats không phải tra lại SPEC
    mới biết con số đang tốt hay xấu.
    """

    value: float
    target: float
    ok: bool
    comparison: str  # ">=" hoặc "<"


class UsageStatsResponse(BaseModel):
    """Phản hồi của GET /internal/v1/stats. SPEC muc 11.8."""

    since: str
    until: str

    calls: int
    chat_turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    by_task: dict[str, int]
    by_answer_source: dict[str, int]

    free_ratio: MetricTarget
    avg_tokens_per_chat: MetricTarget
    latency_p95_ms: MetricTarget
    error_rate: MetricTarget

    all_targets_met: bool
