"""Endpoint quản trị index. SPEC muc 8.5."""

from fastapi import APIRouter, Depends, Request

from app.api.deps import require_internal_token
from app.schemas.common import IndexStatus, SyncTriggerResult
from app.store.sync_state_repo import (
    KEY_LAST_FULL_SWEEP_AT,
    KEY_LAST_SYNC_ERROR,
    KEY_LAST_SYNC_TS,
)

router = APIRouter(
    prefix="/internal/v1/index",
    tags=["Internal - Index"],
    dependencies=[Depends(require_internal_token)],
)


def _as_z(iso: str | None) -> str | None:
    """
    '+00:00' -> 'Z'. Giữ nguyên phần lẻ giây.

    SPEC muc 8.5 công bố định dạng có hậu tố Z, và `Instant.parse` phía Java
    khó tính với offset dạng '+00:00'. Chuẩn hoá ở ranh giới ra, không đụng
    dữ liệu lưu trong sync_state.
    """

    return iso.replace("+00:00", "Z") if iso else iso


@router.get("/status", response_model=IndexStatus)
async def index_status(request: Request) -> IndexStatus:
    service = request.app.state.service

    return IndexStatus(
        card_count=await service.card_repo.count(),
        index_size=service.index.size,
        model_version=service.settings.ai_model_version,
        last_sync_ts=_as_z(await service.sync_state_repo.get(KEY_LAST_SYNC_TS)),
        last_sync_at=_as_z(service.syncer.state.last_sync_at),
        last_sync_duration_ms=service.syncer.state.last_sync_duration_ms,
        last_sync_embedded=service.syncer.state.last_sync_embedded,
        last_sync_skipped=service.syncer.state.last_sync_skipped,
        last_full_sweep_at=_as_z(await service.sync_state_repo.get(KEY_LAST_FULL_SWEEP_AT)),
        last_sync_error=await service.sync_state_repo.get(KEY_LAST_SYNC_ERROR),
        backend_reachable=service.syncer.state.backend_reachable,
        source_mode=service.settings.ai_source_mode,
    )


@router.post("/sync", response_model=SyncTriggerResult)
async def trigger_sync(
    request: Request, sweep: bool = False, full: bool = False
) -> SyncTriggerResult:
    """
    Ép đồng bộ ngay, không đợi hết 120 giây.

    - `sweep=true` chạy thêm quét ID để phát hiện thẻ bị xoá.
    - `full=true` bỏ qua con trỏ đồng bộ và kéo lại toàn bộ. Dùng khi nghi con
      trỏ lệch hoặc sau khi đổi `AI_MODEL_VERSION`. Vẫn rẻ vì `content_hash`
      khiến thẻ không đổi thành no-op.
    """

    service = request.app.state.service

    stats = await service.syncer.run_cycle(with_sweep=sweep, force_full=full)

    return SyncTriggerResult(
        embedded=stats.embedded,
        skipped=stats.skipped,
        deleted=stats.deleted,
        duration_ms=stats.duration_ms,
        error=stats.error,
    )
