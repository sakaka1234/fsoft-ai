"""Endpoint quản trị index. SPEC muc 8.5."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import require_internal_token
from app.schemas.common import IndexStatus, SyncTriggerResult
from app.schemas.errors import UNAUTHORIZED
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


# Ba kịch bản dưới đây là ba thứ người vận hành thật sự gặp. Để chúng cạnh nhau
# trong Swagger để so sánh: cùng một hình dạng JSON, chỉ khác vài con số, và
# chính vài con số đó là toàn bộ chẩn đoán.
STATUS_EXAMPLES: dict = {
    "khoe_manh": {
        "summary": "Bình thường — index khớp DB, không lỗi (dữ liệu mẫu lúc rảnh)",
        "value": {
            "card_count": 24,
            "index_size": 24,
            "model_version": "multilingual-e5-small@t1",
            "last_sync_ts": "2026-08-20T06:00:00Z",
            "last_sync_at": "2026-08-22T10:01:30Z",
            "last_sync_duration_ms": 4,
            "last_sync_embedded": 0,
            "last_sync_skipped": 3,
            "last_full_sweep_at": "2026-08-22T09:30:00Z",
            "last_sync_error": None,
            "backend_reachable": True,
            "source_mode": "fixture",
        },
    },
    "backend_hong": {
        "summary": (
            "Backend không gọi được — dữ liệu cũ dần, chat VẪN chạy. Các số "
            "`last_sync_*` là của chu kỳ TỐT gần nhất, không phải chu kỳ vừa hỏng"
        ),
        "value": {
            "card_count": 24,
            "index_size": 24,
            "model_version": "multilingual-e5-small@t1",
            "last_sync_ts": "2026-08-20T06:00:00Z",
            "last_sync_at": "2026-08-22T09:31:30Z",
            "last_sync_duration_ms": 180,
            "last_sync_embedded": 0,
            "last_sync_skipped": 3,
            "last_full_sweep_at": "2026-08-22T09:30:00Z",
            "last_sync_error": "ConnectError: All connection attempts failed",
            "backend_reachable": False,
            "source_mode": "http",
        },
    },
    "content_hash_khong_an": {
        "summary": "Vừa gọi `sync?full=true` lần thứ hai mà vẫn embed lại đủ 24 thẻ",
        "value": {
            "card_count": 24,
            "index_size": 24,
            "model_version": "multilingual-e5-small@t1",
            "last_sync_ts": "2026-08-22T10:01:00Z",
            "last_sync_at": "2026-08-22T10:01:30Z",
            "last_sync_duration_ms": 8400,
            "last_sync_embedded": 24,
            "last_sync_skipped": 0,
            "last_full_sweep_at": "2026-08-22T09:30:00Z",
            "last_sync_error": None,
            "backend_reachable": True,
            "source_mode": "http",
        },
    },
}

SYNC_EXAMPLES: dict = {
    "gia_tang_luc_ranh": {
        "summary": "Gọi không tham số, không có gì đổi — dữ liệu mẫu cho đúng 3 thẻ skipped",
        "value": {"embedded": 0, "skipped": 3, "deleted": 0, "duration_ms": 4, "error": None},
    },
    "full_khong_co_gi_doi": {
        "summary": "`?full=true` lần thứ hai — kéo lại đủ 24 thẻ, không embed lại thẻ nào",
        "value": {"embedded": 0, "skipped": 24, "deleted": 0, "duration_ms": 41, "error": None},
    },
    "co_thay_doi": {
        "summary": "`?sweep=true&full=true` — 3 thẻ vừa sửa nội dung, 1 thẻ bị xoá ở nguồn",
        "value": {"embedded": 3, "skipped": 20, "deleted": 1, "duration_ms": 620, "error": None},
    },
    "chu_ky_hong": {
        "summary": "Chu kỳ hỏng — HTTP vẫn 200, phải đọc `error`",
        "value": {
            "embedded": 0,
            "skipped": 0,
            "deleted": 0,
            "duration_ms": 0,
            "error": (
                "BackendUnauthorized: Backend từ chối X-Internal-Token "
                "khi gọi /internal/cards/changed-since"
            ),
        },
    },
}


def _as_z(iso: str | None) -> str | None:
    """
    '+00:00' -> 'Z'. Giữ nguyên phần lẻ giây.

    SPEC muc 8.5 công bố định dạng có hậu tố Z, và `Instant.parse` phía Java
    khó tính với offset dạng '+00:00'. Chuẩn hoá ở ranh giới ra, không đụng
    dữ liệu lưu trong sync_state.
    """

    return iso.replace("+00:00", "Z") if iso else iso


@router.get(
    "/status",
    response_model=IndexStatus,
    summary="Xem tình trạng index và đồng bộ",
    responses={
        **UNAUTHORIZED,
        200: {"content": {"application/json": {"examples": STATUS_EXAMPLES}}},
    },
    description="""
Bảng đồng hồ của service: index đang có bao nhiêu thẻ, đồng bộ chạy lần cuối lúc
nào, có lỗi gì không.

**Đây là endpoint gỡ lỗi, không phải endpoint nghiệp vụ.** Service tự đồng bộ mỗi
120 giây; backend Java không cần gọi cái này trong luồng bình thường. Nơi dùng
đúng của nó là lúc ai đó báo "chat trả lời sai/thiếu thẻ" và bạn cần biết index
đang ở trạng thái nào.

Bấm **Try it out** → **Execute** là chạy được ngay, không cần tham số. Với dữ liệu
mẫu (`AI_SOURCE_MODE=fixture`) kết quả đúng là `card_count = 24` và
`index_size = 24`.

### Cách đọc bất thường

Đọc theo CẶP số, đừng đọc từng số rời rạc.

| Dấu hiệu | Nghĩa là gì | Làm gì |
|---|---|---|
| `last_sync_error != null` | Chu kỳ gần nhất hỏng. Service **không** sập, chat vẫn trả lời trên dữ liệu cũ — nên đây thường là dấu hiệu duy nhất nhìn thấy được | Đọc nội dung chuỗi: `BackendUnauthorized` là sai `AI_BACKEND_TOKEN`, `ConnectError` là sai URL hoặc backend đang tắt |
| `card_count != index_size` | Vector trong RAM lệch với dữ liệu trong SQLite. Nhỏ hơn: có thẻ không bao giờ được tìm thấy dù gõ đúng từ. Lớn hơn: trích dẫn cả thẻ đã xoá | Nếu `index_size = 0` thì chỉ là đang khởi động, đợi `/readyz` trả 200. Còn lại: restart, hoặc `sync?full=true` |
| Gọi `sync?full=true` hai lần liền mà `last_sync_embedded` vẫn xấp xỉ `card_count` | `content_hash` không ăn — service embed lại **toàn bộ** thẻ mỗi lần chạm tới, đốt CPU mà kết quả không đổi gì. Lần thứ hai đúng ra phải cho `embedded = 0`, `skipped ≈ card_count` | Kiểm tra nguồn có đổi một trường nằm trong text embed (nghĩa, định nghĩa, ví dụ, ghi chú) mỗi lần gọi không, và `AI_MODEL_VERSION` có bị đổi qua lại giữa các lần chạy không |
| `last_sync_at` cách hiện tại > 3 phút | Vòng lặp nền đã chết, chưa từng chạy, **hoặc** mọi chu kỳ đang hỏng — bốn trường `last_sync_*` chỉ được ghi khi chu kỳ chạy trót lọt | Đọc `last_sync_error` trước. Nếu `null` thì kiểm tra `AI_SYNC_ENABLED` và tìm log `warmup_failed` |
| `last_full_sweep_at` cách hiện tại > 1 giờ | Chưa quét ID nên thẻ đã bị xoá ở backend vẫn còn trong index và vẫn được trích dẫn | Gọi `POST /internal/v1/index/sync?sweep=true` |
| `backend_reachable = false` | Chu kỳ gần nhất không gọi được nguồn | Xem `last_sync_error`. `null` thì khác: chưa chạy chu kỳ nào từ lúc khởi động |
| `source_mode = "http"` | Đang nối backend thật, **không** phải dữ liệu mẫu | Các ID 101/201/301… trong ví dụ của trang này sẽ không tồn tại, request trả kết quả rỗng chứ không báo lỗi |

Lúc rảnh, `last_sync_embedded = 0` nhưng `last_sync_skipped` là một số nhỏ **khác 0**:
con trỏ được lùi 5 giây và nguồn lọc theo `>=`, nên những thẻ mang đúng mốc
`last_sync_ts` luôn được kéo về lại rồi bị `content_hash` loại. Với dữ liệu mẫu con
số đó là `3`. Ngược lại, `skipped = 0` trong khi `last_sync_ts != null` mới là chuyện
lạ — nguồn không trả về cả những thẻ mang mốc con trỏ nữa.
""",
)
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


@router.post(
    "/sync",
    response_model=SyncTriggerResult,
    summary="Ép chạy một chu kỳ đồng bộ ngay",
    responses={
        **UNAUTHORIZED,
        200: {"content": {"application/json": {"examples": SYNC_EXAMPLES}}},
    },
    description="""
Chạy ngay một chu kỳ đồng bộ thay vì đợi hết 120 giây.

**Endpoint gỡ lỗi, không dùng trong luồng nghiệp vụ.** Service đã tự đồng bộ mỗi
120 giây và tự quét ID mỗi 60 phút; gọi thêm ở đây chỉ để không phải ngồi chờ khi
đang sửa dữ liệu và muốn thấy kết quả lập tức.

Gọi không tham số là an toàn nhất: chỉ kéo phần thay đổi kể từ con trỏ đồng bộ.
Với dữ liệu mẫu, khi mọi thẻ đã nằm sẵn trong index thì kết quả là
`embedded = 0, skipped = 3, deleted = 0` — con trỏ lùi 5 giây khiến ba thẻ mang mốc
mới nhất luôn được kéo về lại rồi bị `content_hash` loại. Đó là kết quả **đúng**,
không phải endpoint không chạy. Muốn thấy `skipped = 24` thì phải thêm `full=true`.

### Hai tham số khác nhau ở đâu

| | `sweep=true` | `full=true` |
|---|---|---|
| Làm gì | Quét **toàn bộ ID** ở nguồn rồi xoá thẻ nào không còn ở đó | Bỏ qua con trỏ đồng bộ, kéo **lại từ đầu** mọi thẻ |
| Chữa được | Thẻ đã xoá ở backend nhưng vẫn còn trong index và vẫn bị trích dẫn | Con trỏ lệch nên bản cập nhật không bao giờ về; vector sinh bằng model cũ |
| Không chữa được | Thẻ sửa nội dung mà không đổi `updated_at` | Thẻ bị xoá — xoá chỉ phát hiện được bằng `sweep` |
| Giá | Một lượt gọi lấy danh sách ID | Kéo lại toàn bộ trang dữ liệu, nhưng **không** embed lại nếu nội dung không đổi |

Hai tham số độc lập, bật cả hai cùng lúc được: `?sweep=true&full=true` là phương án
chữa cháy toàn tập khi không rõ index sai ở đâu.

### BẮT BUỘC `full=true` sau khi đổi `AI_MODEL_VERSION`

Đổi `AI_MODEL_VERSION` (hoặc đổi `AI_EMBEDDING_MODEL`) mà không chạy `full=true`
thì index sẽ **trộn vector của hai model khác nhau**. Khoảng cách cosine giữa hai
không gian vector khác nhau là một con số vô nghĩa, nên kết quả tìm kiếm sai một
cách âm thầm: không exception, không log lỗi, chỉ là câu trả lời ngày càng lạc đề
và không ai biết vì sao. Chu kỳ gia tăng bình thường **không** cứu được vì con trỏ
khiến nó không nhìn tới thẻ cũ.

### Vì sao `full=true` vẫn rẻ

Mỗi thẻ được băm thành `content_hash`. Kéo lại một thẻ mà nội dung không đổi thì
hash trùng, service bỏ qua hoàn toàn — không gọi embedding, không ghi DB. Chi phí
thật của `full=true` chỉ là lượt tải dữ liệu về, còn phần đắt (tính vector) chỉ
xảy ra đúng với thẻ đã đổi thật, hoặc với mọi thẻ nếu `AI_MODEL_VERSION` vừa đổi.

### Đọc kết quả

`error != null` nghĩa là chu kỳ hỏng — **HTTP vẫn là 200**, vì lỗi đồng bộ có chủ ý
không bao giờ được làm sập service. Chỉ nhìn mã 200 rồi kết luận "đồng bộ xong" là
hiểu sai.

Endpoint chạy đồng bộ, tức là chờ xong mới trả. Nó dùng chung khoá với vòng lặp
nền nên nếu một chu kỳ tự động đang chạy thì lời gọi này xếp hàng đợi chứ không
chạy chồng lên nhau.
""",
)
async def trigger_sync(
    request: Request,
    sweep: Annotated[
        bool,
        Query(
            description=(
                "Quét toàn bộ ID ở nguồn để phát hiện thẻ đã bị XOÁ. Feed gia tăng chỉ "
                "báo thẻ mới và thẻ sửa, không có cách nào báo thẻ biến mất — nên không "
                "có `sweep` thì `deleted` luôn bằng 0 và thẻ người dùng đã xoá vẫn tiếp "
                "tục xuất hiện trong kết quả.\n\n"
                "Một chốt an toàn: nếu nguồn trả về danh sách ID rỗng trong khi local "
                "đang có thẻ thì quét bị bỏ qua và `deleted` vẫn là 0 (log "
                "`sweep_skipped_empty_source`) — một lần backend hắt hơi không được "
                "phép xoá sạch index."
            ),
        ),
    ] = False,
    full: Annotated[
        bool,
        Query(
            description=(
                "Bỏ qua con trỏ đồng bộ và kéo lại toàn bộ. BẮT BUỘC dùng sau khi đổi "
                "`AI_MODEL_VERSION`, nếu không index sẽ trộn vector của hai model và trả "
                "kết quả sai âm thầm. Vẫn rẻ vì thẻ không đổi nội dung thành no-op nhờ "
                "`content_hash`."
            ),
        ),
    ] = False,
) -> SyncTriggerResult:
    service = request.app.state.service

    stats = await service.syncer.run_cycle(with_sweep=sweep, force_full=full)

    return SyncTriggerResult(
        embedded=stats.embedded,
        skipped=stats.skipped,
        deleted=stats.deleted,
        duration_ms=stats.duration_ms,
        error=stats.error,
    )
