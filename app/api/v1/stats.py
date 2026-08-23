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
from app.schemas.errors import UNAUTHORIZED, ErrorResponse
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

# 400 của riêng endpoint này. Dùng lại `ErrorResponse` ở app/schemas/errors.py
# chứ không định nghĩa hình dạng lỗi mới — SCOPE_ERRORS không hợp ở đây vì ví dụ
# trong đó nói về `allowed_deck_ids`, mà endpoint này không hề có tham số deck.
INVALID_TIME_RANGE: dict = {
    400: {
        "model": ErrorResponse,
        "description": "Mốc thời gian sai định dạng, hoặc `from` không nhỏ hơn `to`.",
        "content": {
            "application/json": {
                "examples": {
                    "sai_dinh_dang": {
                        "summary": "Không phải ISO-8601",
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": (
                                    "Không đọc được mốc thời gian '16/08/2026'. "
                                    "Cần ISO-8601, ví dụ 2026-08-16T00:00:00Z."
                                ),
                            }
                        },
                    },
                    "khoang_nguoc": {
                        "summary": "Đảo ngược hai đầu cửa sổ",
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": "`from` phải nhỏ hơn `to`.",
                            }
                        },
                    },
                }
            }
        },
    }
}

# Ba kịch bản người vận hành thật sự gặp. Kịch bản "cua_so_rong" là cái bẫy quan
# trọng nhất của endpoint này nên để ngay cạnh kịch bản khoẻ mạnh để so sánh.
STATS_EXAMPLES: dict = {
    "co_luu_luong": {
        "summary": "Bình thường — hơn một nửa số lượt chat không tốn token",
        "value": {
            "since": "2026-08-16T00:00:00Z",
            "until": "2026-08-17T00:00:00Z",
            "calls": 137,
            "chat_turns": 96,
            "prompt_tokens": 41200,
            "completion_tokens": 12800,
            "total_tokens": 54000,
            "by_task": {"CHAT": 96, "REWRITE": 28, "QUIZ": 13, "VOCAB_EXTRACT": 4},
            "by_answer_source": {
                "DIRECT_LOOKUP": 31,
                "CACHE": 12,
                "CANNED": 6,
                "RAG": 47,
            },
            "free_ratio": {"value": 0.5104, "target": 0.4, "ok": True, "comparison": ">="},
            "avg_tokens_per_chat": {
                "value": 468.75,
                "target": 1200.0,
                "ok": True,
                "comparison": "<",
            },
            "latency_p95_ms": {"value": 2140.0, "target": 3000.0, "ok": True, "comparison": "<"},
            "error_rate": {"value": 0.0146, "target": 0.02, "ok": True, "comparison": "<"},
            "all_targets_met": True,
        },
    },
    "cua_so_rong": {
        "summary": "BẪY: chưa có lượt nào — false ở đây KHÔNG phải hệ thống hỏng",
        "value": {
            "since": "2026-08-16T00:00:00Z",
            "until": "2026-08-17T00:00:00Z",
            "calls": 0,
            "chat_turns": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "by_task": {},
            "by_answer_source": {},
            "free_ratio": {"value": 0.0, "target": 0.4, "ok": False, "comparison": ">="},
            "avg_tokens_per_chat": {"value": 0.0, "target": 1200.0, "ok": True, "comparison": "<"},
            "latency_p95_ms": {"value": 0.0, "target": 3000.0, "ok": True, "comparison": "<"},
            "error_rate": {"value": 0.0, "target": 0.02, "ok": True, "comparison": "<"},
            "all_targets_met": False,
        },
    },
    "dang_dot_token": {
        "summary": "Sự cố thật — free_ratio tụt, token mỗi lượt vượt trần",
        "value": {
            "since": "2026-08-16T00:00:00Z",
            "until": "2026-08-17T00:00:00Z",
            "calls": 168,
            "chat_turns": 120,
            "prompt_tokens": 128000,
            "completion_tokens": 41200,
            "total_tokens": 169200,
            "by_task": {"CHAT": 120, "REWRITE": 40, "QUIZ": 8, "VOCAB_EXTRACT": 9},
            "by_answer_source": {
                "DIRECT_LOOKUP": 9,
                "CACHE": 4,
                "CANNED": 6,
                "RAG": 101,
            },
            "free_ratio": {"value": 0.1583, "target": 0.4, "ok": False, "comparison": ">="},
            "avg_tokens_per_chat": {
                "value": 1310.5,
                "target": 1200.0,
                "ok": False,
                "comparison": "<",
            },
            "latency_p95_ms": {"value": 2600.0, "target": 3000.0, "ok": True, "comparison": "<"},
            "error_rate": {"value": 0.0, "target": 0.02, "ok": True, "comparison": "<"},
            "all_targets_met": False,
        },
    },
}


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


@router.get(
    "/stats",
    response_model=UsageStatsResponse,
    summary="Số liệu vận hành trong một cửa sổ thời gian",
    responses={
        **UNAUTHORIZED,
        **INVALID_TIME_RANGE,
        200: {"content": {"application/json": {"examples": STATS_EXAMPLES}}},
    },
    description="""
Tổng hợp nhật ký sử dụng để trả lời đúng một câu hỏi: **có đang trả tiền cho việc
mà dữ liệu cục bộ làm được miễn phí hay không.**

Bấm **Try it out** → **Execute** là chạy được ngay, không cần tham số — mặc định là
**24 giờ gần nhất**. Nếu service vừa khởi động và bạn chưa gọi chat lần nào thì mọi
số sẽ bằng 0; gọi vài lượt `POST /internal/v1/chat` rồi quay lại đây để thấy số liệu.

### BẪY quan trọng nhất — đọc trước khi dựng cảnh báo

Cửa sổ **không có lượt chat nào** sẽ trả về `free_ratio = 0` và
`all_targets_met = false`.

Lý do: `free_ratio` là phép chia cho `chat_turns`, mà chia cho 0 thì quy ước trả 0 —
và 0 thì nhỏ hơn ngưỡng 0.40 nên bị tính là trượt. Ba chỉ số còn lại cũng bằng 0
nhưng chúng so theo chiều "càng thấp càng tốt" nên lại được tính là đạt. Kết quả là
một cửa sổ hoàn toàn im lặng trông y hệt một cửa sổ đang hỏng.

Đó là **"chưa có dữ liệu"**, không phải **"đang hỏng"**. Luật cảnh báo bắt buộc phải
kiểm tra mẫu số trước:

```text
canh_bao = chat_turns > 0 && !all_targets_met
```

Cảnh báo thẳng theo `all_targets_met` sẽ kêu suốt đêm và mỗi cuối tuần, rồi người
trực sẽ tắt nó đi — và lần thật sự hỏng thì không ai biết.

### Các chỉ số và ngưỡng (SPEC muc 11.8)

| Chỉ số | Ngưỡng | Trượt ngưỡng nghĩa là gì |
|---|---|---|
| `free_ratio` | `>= 0.40` | **Quan trọng nhất.** Đang trả tiền LLM cho câu hỏi mà tra thẳng trong bộ thẻ là xong. Thường do tra từ khớp chính xác hỏng, hoặc semantic cache bị xoá liên tục vì index thay đổi quá thường xuyên |
| `avg_tokens_per_chat` | `< 1200` | Prompt phình: ngữ cảnh nhồi quá nhiều thẻ hoặc gửi kèm quá nhiều lịch sử hội thoại. Hậu quả trực tiếp là chạm trần `AI_GLOBAL_TOKENS_PER_MINUTE` sớm hơn, người dùng bắt đầu nhận `429` |
| `latency_p95_ms` | `< 3000` | Lượt chậm nhất đã chậm tới mức người dùng bỏ cuộc. Dùng p95 chứ không dùng trung bình vì các lượt 0 token kéo trung bình xuống rất thấp và che mất đúng những lượt đáng lo |
| `error_rate` | `< 0.02` | Nhà cung cấp LLM đang trả 429/5xx. Mẫu số là **tổng** `calls`, nên khi lưu lượng thấp chỉ một lỗi cũng đủ đẩy tỷ lệ vọt lên |

Mỗi chỉ số trả kèm `target`, `ok` và `comparison` để không phải tra lại SPEC — hãy
dùng `ok` thay vì tự so, so nhầm chiều là lỗi im lặng.

### Vài điều dễ hiểu nhầm khác

- `by_answer_source` **chỉ đếm dòng `task=CHAT`**. `REWRITE`, `QUIZ` và
  `VOCAB_EXTRACT` không có khái niệm nguồn câu trả lời nên không xuất hiện ở đây —
  tổng các giá trị khớp `chat_turns`, không khớp `calls`.
- `VOCAB_EXTRACT` làm lệch các chỉ số một cách BẤT ĐỐI XỨNG, và biết trước thì
  đỡ hoảng: nó cộng vào `calls` và `total_tokens`, nên **pha loãng `error_rate`**
  (mẫu số là `calls`); nhưng nó KHÔNG cộng vào `chat_turns`, nên `free_ratio` và
  `avg_tokens_per_chat` không hề đổi. Một ngày nhiều lượt trích xuất sẽ thấy
  `total_tokens` vọt lên trong khi `avg_tokens_per_chat` đứng yên — đó là đúng,
  không phải hỏng.
- `by_answer_source` chỉ có **đúng bốn khoá**: `DIRECT_LOOKUP`, `CACHE`, `CANNED`,
  `RAG`. **`LLM_ONLY` không bao giờ xuất hiện ở đây**, dù nó là một `answer_source`
  hợp lệ trong phản hồi chat: lúc ghi nhật ký, service chưa biết câu trả lời sẽ có
  trích dẫn hay không nên luôn ghi `RAG`. Vậy `RAG` ở bảng này = `RAG` + `LLM_ONLY`
  thật; muốn tách thì phải đếm phản hồi chat có `citations` rỗng.
- `calls` đếm cả lượt **0 token** (chúng vẫn được ghi nhật ký với `provider='local'`),
  nên `calls` luôn lớn hơn hoặc bằng `chat_turns`.
- Một lượt hỏi **không phải lúc nào cũng là một dòng**. Khi nhà cung cấp LLM trả
  429/5xx, mỗi lần thử lại và mỗi lần hạ xuống model dự phòng đều ghi thêm một dòng
  `task=CHAT`. Bão 429 vì thế vừa đẩy `error_rate` lên, vừa thổi phồng `chat_turns`
  và kéo `free_ratio` xuống — đừng đọc hai chỉ số đó tách rời nhau.
- `total_tokens` là tổng của mọi tác vụ. Đừng chia nó cho `chat_turns` để suy ra token
  mỗi lượt — `avg_tokens_per_chat` đã tính đúng phần token của riêng chat.
- Cửa sổ là nửa mở `[from, to)`, nên hai lần gọi liền kề không đếm trùng dòng nằm
  đúng ranh giới.
""",
)
async def usage_stats(
    request: Request,
    # `from` là từ khoá Python nên không đặt tên tham số như vậy được; `alias`
    # giữ đúng tên query param mà SPEC muc 11.8 công bố.
    from_: str | None = Query(
        None,
        alias="from",
        description=(
            "Đầu cửa sổ, ISO-8601. Tên query param là **`from`**, không phải `from_` — "
            "`from_` chỉ là tên biến trong Python vì `from` là từ khoá.\n\n"
            "Bỏ trống thì lấy `to` trừ đi 24 giờ. Thiếu offset (`2026-08-16T00:00:00`) "
            "được hiểu là UTC. Sai định dạng trả `400 INVALID_REQUEST`, và `from >= to` "
            "cũng vậy."
        ),
        examples=["2026-08-16T00:00:00Z"],
    ),
    to: str | None = Query(
        None,
        description=(
            "Cuối cửa sổ, ISO-8601, KHÔNG bao gồm chính mốc này. Bỏ trống thì lấy thời "
            "điểm hiện tại.\n\n"
            "Nhật ký ghi theo UTC: đưa mốc giờ Việt Nam mà quên `+07:00` sẽ lệch 7 tiếng "
            "và cửa sổ trông như rỗng."
        ),
        examples=["2026-08-17T00:00:00Z"],
    ),
) -> UsageStatsResponse:
    service = request.app.state.service

    until = _parse(to, datetime.now(UTC))
    since = _parse(from_, until - timedelta(hours=DEFAULT_WINDOW_HOURS))

    if since >= until:
        raise InvalidRequest("`from` phải nhỏ hơn `to`.")

    return build_response(await service.usage_repo.stats(since, until))
