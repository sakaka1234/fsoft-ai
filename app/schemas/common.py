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
