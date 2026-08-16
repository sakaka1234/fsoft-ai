"""
Schema dùng chung. Field theo snake_case — SPEC muc 8.

Mô tả từng field ở đây viết cho người đọc CHÊNH LỆCH giữa các số chứ không
phải người đọc từng số rời rạc: gần như mọi sự cố của service này không ném
lỗi ra đâu cả, nó chỉ hiện thành hai con số đáng lẽ bằng nhau mà lại lệch.
"""

from pydantic import BaseModel, ConfigDict, Field


class IndexStatus(BaseModel):
    """
    Phản hồi của GET /internal/v1/index/status. SPEC muc 8.5.

    Đọc theo cặp: `card_count` với `index_size`, `last_sync_ts` với
    `last_sync_at`, `last_sync_embedded` với `last_sync_skipped`. Một con số
    đứng một mình hầu như không nói lên điều gì.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
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
                }
            ]
        }
    )

    card_count: int = Field(
        description=(
            "Số thẻ đang nằm trong SQLite. Đây là nguồn sự thật: sau khi restart, "
            "index vector được dựng lại từ đúng bảng này chứ không gọi lại backend.\n\n"
            "- Bằng `0` nghĩa là chưa kéo được thẻ nào. Khi đó tìm kiếm luôn trả về "
            "rỗng, còn chat vẫn trả lời: câu chào hỏi/ngoài phạm vi vẫn vào `CANNED`, "
            "phần còn lại rơi vào `LLM_ONLY` — trả lời bằng kiến thức chung của model, "
            "không trích dẫn thẻ nào. Không có lỗi nào được ném ra để báo cho biết.\n"
            "- Với dữ liệu mẫu (`AI_SOURCE_MODE=fixture`) con số đúng là `24`."
        ),
        examples=[24],
    )
    index_size: int = Field(
        description=(
            "Số vector đang nằm trong RAM và thật sự được tìm kiếm.\n\n"
            "PHẢI bằng `card_count`. Lệch nhau là dấu hiệu hỏng:\n"
            "- Nhỏ hơn: một phần thẻ có trong DB nhưng không có trong index, nên "
            "chúng KHÔNG BAO GIỜ xuất hiện trong kết quả dù người dùng gõ đúng từ. "
            "Nếu `index_size = 0` mà `card_count > 0` thì thường chỉ là service "
            "đang khởi động — đợi `/readyz` trả 200 rồi xem lại.\n"
            "- Lớn hơn: vector của thẻ đã bị xoá còn kẹt trong RAM, kết quả sẽ trích "
            "dẫn thẻ không còn tồn tại. Restart sẽ dựng lại index từ SQLite và tự khớp."
        ),
        examples=[24],
    )
    model_version: str = Field(
        description=(
            "Nhãn model đang được đóng dấu lên mọi vector mới sinh, lấy từ "
            "`AI_MODEL_VERSION`. Nó trả lời câu hỏi 'vector này được sinh bằng cái gì'.\n\n"
            "Đổi giá trị này trong `.env` mà KHÔNG chạy `POST /internal/v1/index/sync?full=true` "
            "là lỗi tốn kém nhất ở đây: index sẽ chứa lẫn vector của hai model, mà khoảng "
            "cách cosine giữa hai không gian vector khác nhau là một con số vô nghĩa. "
            "Kết quả tìm kiếm sai một cách âm thầm — không exception, không log lỗi, "
            "chỉ là câu trả lời ngày càng lạc đề."
        ),
        examples=["multilingual-e5-small@t1"],
    )
    last_sync_ts: str | None = Field(
        description=(
            "Con trỏ đồng bộ: mốc `updated_at` LỚN NHẤT đã nhìn thấy trong dữ liệu "
            "nguồn. Lấy từ dữ liệu chứ không lấy từ đồng hồ máy, nhờ vậy lệch giờ "
            "giữa hai container không làm mất bản ghi.\n\n"
            "Chu kỳ sau chỉ hỏi backend những thẻ đổi sau mốc này (đã lùi 5 giây cho "
            "an toàn). Nếu mốc này đứng im trong khi backend vẫn đang sửa thẻ thì "
            "cập nhật sẽ không bao giờ về — chữa bằng `sync?full=true`.\n\n"
            "`null` nghĩa là chưa có chu kỳ nào kéo được thẻ."
        ),
        examples=["2026-08-20T06:00:00Z"],
    )
    last_sync_at: str | None = Field(
        description=(
            "Đồng hồ máy tại lúc chu kỳ đồng bộ gần nhất THÀNH CÔNG kết thúc. Khác hẳn "
            "`last_sync_ts` — cái kia là mốc nằm trong dữ liệu, cái này là 'lần cuối "
            "vòng lặp còn thở'.\n\n"
            "QUAN TRỌNG: bốn trường `last_sync_at`, `last_sync_duration_ms`, "
            "`last_sync_embedded`, `last_sync_skipped` chỉ được ghi khi chu kỳ chạy "
            "trót lọt. Chu kỳ hỏng thoát ra trước đó, nên khi `last_sync_error != null` "
            "thì cả bốn số này là DI SẢN của chu kỳ tốt gần nhất, không mô tả chu kỳ vừa hỏng.\n\n"
            "Cách thời điểm hiện tại quá 2-3 phút nghĩa là vòng lặp nền đã chết, chưa "
            "từng chạy, hoặc mọi chu kỳ đang hỏng: đọc `last_sync_error` trước, rồi "
            "kiểm tra `AI_SYNC_ENABLED` và tìm log `warmup_failed`. Giá trị mất sau khi "
            "restart nên `null` ngay sau khởi động là bình thường."
        ),
        examples=["2026-08-22T10:01:30Z"],
    )
    last_sync_duration_ms: int | None = Field(
        description=(
            "Thời gian chạy của chu kỳ THÀNH CÔNG gần nhất. Ở trạng thái nghỉ chỉ là "
            "vài mili giây vì chỉ có dăm thẻ trong cửa sổ chồng lấn phải đối chiếu hash.\n\n"
            "Vọt lên hàng giây thì đối chiếu `last_sync_embedded`: lớn theo nghĩa là "
            "đang tính lại vector (tốn CPU), còn bằng 0 mà vẫn chậm thì nút thắt nằm ở "
            "phía backend trả dữ liệu. Một chu kỳ TIMEOUT không bao giờ hiện ra ở đây — "
            "nó ném lỗi trước khi kịp ghi, xem `last_sync_error`."
        ),
        examples=[4],
    )
    last_sync_embedded: int = Field(
        description=(
            "Số thẻ PHẢI tính lại vector ở chu kỳ thành công gần nhất. Đây là phần tốn "
            "CPU duy nhất của vòng đồng bộ.\n\n"
            "Ở trạng thái nghỉ phải bằng `0`. Phép thử dứt khoát cho `content_hash`: "
            "gọi `sync?full=true` hai lần liền — lần thứ hai PHẢI cho `embedded = 0` và "
            "`skipped ≈ card_count`. Nếu `embedded` vẫn xấp xỉ `card_count` thì hash "
            "không ăn và service đang đốt CPU embed lại cùng một dữ liệu. Thường do "
            "nguồn đổi một trường nằm trong text embed (nghĩa, định nghĩa, ví dụ, ghi "
            "chú) mỗi lần gọi, hoặc `AI_MODEL_VERSION` bị đổi qua đổi lại giữa các lần chạy."
        ),
        examples=[0],
    )
    last_sync_skipped: int = Field(
        description=(
            "Số thẻ nguồn có trả về nhưng không phải làm gì vì `content_hash` không "
            "đổi. Đây là số tiền embedding đã tiết kiệm được.\n\n"
            "Ở chu kỳ gia tăng lúc rảnh, con số này KHÔNG bằng 0 mà là một số nhỏ: con "
            "trỏ được lùi 5 giây (`AI_SYNC_OVERLAP_SECONDS`) và nguồn lọc theo `>=`, "
            "nên những thẻ mang đúng mốc `last_sync_ts` luôn được kéo về lại rồi bị "
            "hash loại. Với dữ liệu mẫu con số đó là `3` (ba thẻ cùng mốc "
            "`2026-08-20T06:00:00Z`).\n\n"
            "Ngay sau một lần `sync?full=true` thì số này phải xấp xỉ `card_count` còn "
            "`last_sync_embedded` phải bằng 0 — ngược lại là `content_hash` hỏng. Còn "
            "`skipped = 0` trong khi `last_sync_ts != null` mới là chuyện lạ: nguồn "
            "không trả về cả những thẻ mang mốc con trỏ nữa."
        ),
        examples=[3],
    )
    last_full_sweep_at: str | None = Field(
        description=(
            "Lần quét toàn bộ ID gần nhất (tự chạy mỗi 60 phút).\n\n"
            "Chỉ có quét mới phát hiện được thẻ bị XOÁ ở backend: feed gia tăng chỉ "
            "báo thẻ mới và thẻ sửa, không có cách nào báo thẻ đã biến mất. Nếu mốc "
            "này cách hiện tại hơn một giờ thì thẻ người dùng đã xoá vẫn còn được "
            "trích dẫn trong câu trả lời — ép ngay bằng `sync?sweep=true`."
        ),
        examples=["2026-08-22T09:30:00Z"],
    )
    last_sync_error: str | None = Field(
        description=(
            "`null` là bình thường. Khác `null` nghĩa là chu kỳ gần nhất HỎNG, dạng "
            "`TênLỗi: chi tiết`.\n\n"
            "Trường này quan trọng vì lỗi đồng bộ được nuốt có chủ ý — nguyên tắc bất "
            "di bất dịch là đồng bộ hỏng không bao giờ được làm sập service, chat vẫn "
            "chạy trên dữ liệu cũ. Hệ quả: nếu không đọc log thì đây là nơi DUY NHẤT "
            "nhìn thấy sự cố, và triệu chứng phía người dùng chỉ là 'dữ liệu cũ dần'.\n\n"
            "Vài lỗi hay gặp: `BackendUnauthorized: Backend từ chối X-Internal-Token "
            "khi gọi /internal/cards/changed-since` là sai `AI_BACKEND_TOKEN`; "
            "`ConnectError: All connection attempts failed` là sai `AI_BACKEND_URL` "
            "hoặc backend đang tắt. Trường tự xoá ngay khi có một chu kỳ chạy trót lọt."
        ),
        examples=[None],
    )
    backend_reachable: bool | None = Field(
        description=(
            "Chu kỳ gần nhất có gọi được nguồn dữ liệu không.\n\n"
            "`null` nghĩa là chưa chạy chu kỳ nào kể từ lúc khởi động (chỉ số này nằm "
            "trong RAM, mất sau restart) — đừng đọc thành 'không gọi được'. `false` "
            "gần như luôn đi kèm `last_sync_error`, đọc trường đó để biết lý do."
        ),
        examples=[True],
    )
    source_mode: str = Field(
        description=(
            "`fixture` hay `http`, theo `AI_SOURCE_MODE`.\n\n"
            "- `fixture`: đọc 24 thẻ trong `tests/fixtures/cards.json` — đúng bộ ID mà "
            "mọi ví dụ trong trang này dùng (deck 1-4, thẻ 101-109, 201-208, 301-304, "
            "401-403).\n"
            "- `http`: kéo từ backend Java thật. Khi đó các ID trong ví dụ gần như chắc "
            "chắn không tồn tại, và request sẽ trả kết quả RỖNG chứ không báo lỗi — "
            "lấy ID thật trước khi thử."
        ),
        examples=["fixture"],
    )


class SyncTriggerResult(BaseModel):
    """
    Phản hồi của POST /internal/v1/index/sync.

    Cảnh báo lớn nhất: HTTP vẫn là 200 ngay cả khi chu kỳ hỏng. Phải đọc
    `error` chứ không được chỉ nhìn mã trạng thái.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "embedded": 0,
                    "skipped": 24,
                    "deleted": 0,
                    "duration_ms": 41,
                    "error": None,
                }
            ]
        }
    )

    embedded: int = Field(
        description=(
            "Số thẻ phải tính lại vector trong lần ép này. Gọi `?full=true` lại lần thứ "
            "hai ngay sau đó phải ra `0` — nếu vẫn lớn thì `content_hash` không ăn và "
            "service đang đốt CPU embed lại cùng một dữ liệu."
        ),
        examples=[0],
    )
    skipped: int = Field(
        description=(
            "Số thẻ nguồn trả về nhưng nội dung không đổi nên không phải embed lại. "
            "Chính con số này giải thích vì sao `full=true` vẫn rẻ.\n\n"
            "Nó đếm thẻ NGUỒN TRẢ VỀ, không phải toàn bộ bộ thẻ: với dữ liệu mẫu, "
            "`?full=true` cho `24` còn lời gọi không tham số chỉ cho `3` — con trỏ đã "
            "lọc phần còn lại từ trước khi tới bước so hash."
        ),
        examples=[24],
    )
    deleted: int = Field(
        description=(
            "Số thẻ bị gỡ khỏi index vì không còn tồn tại ở nguồn. LUÔN bằng `0` nếu "
            "không truyền `sweep=true` — chỉ có nhánh quét ID mới phát hiện thẻ bị xoá.\n\n"
            "Vẫn bằng `0` trong một trường hợp nữa: nguồn trả về danh sách ID RỖNG "
            "trong khi local đang có thẻ. Đó gần như luôn là backend hỏng chứ không "
            "phải mọi thẻ vừa bị xoá thật, nên quét bị bỏ qua có chủ ý (log "
            "`sweep_skipped_empty_source`) thay vì dọn sạch index."
        ),
        examples=[0],
    )
    duration_ms: int = Field(
        description=(
            "Thời gian chạy. Với dữ liệu mẫu và không có gì đổi thì chỉ vài mili giây; "
            "lần đầu phải embed đủ 24 thẻ mất khoảng hai giây. Với backend thật và "
            "`full=true` có thể lâu hơn vì phải kéo lại toàn bộ trang dữ liệu."
        ),
        examples=[41],
    )
    error: str | None = Field(
        description=(
            "`null` là thành công. Khác `null` nghĩa là chu kỳ hỏng — nhưng HTTP VẪN "
            "TRẢ 200, vì lỗi đồng bộ có chủ ý không được làm sập service. Người gọi "
            "phải tự kiểm tra trường này; chỉ nhìn mã 200 rồi kết luận 'đồng bộ xong' "
            "là hiểu sai. Nội dung y hệt `last_sync_error` ở `/index/status`."
        ),
        examples=[None],
    )


class MetricTarget(BaseModel):
    """
    Một chỉ số kèm ngưỡng mục tiêu ở SPEC muc 11.8.

    Trả kèm `target` và `ok` để người đọc bảng stats không phải tra lại SPEC
    mới biết con số đang tốt hay xấu.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"value": 0.5104, "target": 0.4, "ok": True, "comparison": ">="}]
        }
    )

    value: float = Field(
        description="Giá trị đo được trong cửa sổ thời gian đang xét, đã làm tròn 4 chữ số.",
        examples=[0.5104],
    )
    target: float = Field(
        description=(
            "Ngưỡng mục tiêu ở SPEC muc 11.8. Cố định trong code, KHÔNG lấy từ biến "
            "môi trường: đây là mục tiêu thiết kế của hệ thống chứ không phải thứ "
            "chỉnh theo môi trường."
        ),
        examples=[0.4],
    )
    ok: bool = Field(
        description=(
            "Đã tính sẵn `value` so với `target` theo đúng chiều ở `comparison`. Dùng "
            "trường này để cảnh báo, đừng tự so lại — so nhầm chiều là lỗi im lặng."
        ),
        examples=[True],
    )
    comparison: str = Field(
        description=(
            "Chiều so sánh: `>=` là càng cao càng tốt (chỉ có `free_ratio`), `<` là "
            "càng thấp càng tốt (token, độ trễ, tỷ lệ lỗi)."
        ),
        examples=[">="],
    )


class UsageStatsResponse(BaseModel):
    """
    Phản hồi của GET /internal/v1/stats. SPEC muc 11.8.

    Bẫy phải nhớ trước khi dựng cảnh báo: cửa sổ KHÔNG có lượt chat nào cho ra
    `free_ratio = 0` và `all_targets_met = false`. Đó là 'chưa có dữ liệu',
    không phải 'đang hỏng' — luôn kiểm tra `chat_turns > 0` trước.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "since": "2026-08-16T00:00:00Z",
                    "until": "2026-08-17T00:00:00Z",
                    "calls": 137,
                    "chat_turns": 96,
                    "prompt_tokens": 41200,
                    "completion_tokens": 12800,
                    "total_tokens": 54000,
                    "by_task": {"CHAT": 96, "REWRITE": 28, "QUIZ": 13},
                    "by_answer_source": {
                        "DIRECT_LOOKUP": 31,
                        "CACHE": 12,
                        "CANNED": 6,
                        "RAG": 47,
                    },
                    "free_ratio": {
                        "value": 0.5104,
                        "target": 0.4,
                        "ok": True,
                        "comparison": ">=",
                    },
                    "avg_tokens_per_chat": {
                        "value": 468.75,
                        "target": 1200.0,
                        "ok": True,
                        "comparison": "<",
                    },
                    "latency_p95_ms": {
                        "value": 2140.0,
                        "target": 3000.0,
                        "ok": True,
                        "comparison": "<",
                    },
                    "error_rate": {
                        "value": 0.0146,
                        "target": 0.02,
                        "ok": True,
                        "comparison": "<",
                    },
                    "all_targets_met": True,
                }
            ]
        }
    )

    since: str = Field(
        description=(
            "Đầu cửa sổ thực sự được tính, ISO-8601 UTC hậu tố `Z`. Cửa sổ là nửa mở "
            "`[since, until)` nên gọi hai lần liền kề không đếm trùng dòng nằm đúng "
            "ranh giới. Đối chiếu với tham số bạn gửi để chắc chắn không bị rơi về "
            "mặc định 24 giờ."
        ),
        examples=["2026-08-16T00:00:00Z"],
    )
    until: str = Field(
        description="Cuối cửa sổ (không bao gồm chính mốc này), ISO-8601 UTC hậu tố `Z`.",
        examples=["2026-08-17T00:00:00Z"],
    )
    calls: int = Field(
        description=(
            "Tổng số dòng nhật ký trong cửa sổ, gồm mọi tác vụ (`CHAT`, `REWRITE`, "
            "`QUIZ`) và gồm cả lượt trả lời 0 token. Vì vậy luôn `calls >= chat_turns`. "
            "Đây là mẫu số của `error_rate`."
        ),
        examples=[137],
    )
    chat_turns: int = Field(
        description=(
            "Số dòng nhật ký `task=CHAT`, tính cả lượt miễn phí. Là MẪU SỐ của "
            "`free_ratio` và `avg_tokens_per_chat`.\n\n"
            "Bằng `0` thì hai chỉ số kia bằng 0 theo quy ước chia-cho-0, KHÔNG phải vì "
            "hệ thống tốt hay xấu. Luôn kiểm tra trường này trước khi diễn giải bất kỳ "
            "chỉ số nào bên dưới.\n\n"
            "Lúc bình yên thì một dòng = một lượt người dùng hỏi. Lúc nhà cung cấp LLM "
            "trả 429/5xx thì KHÔNG: mỗi lần thử lại và mỗi lần hạ xuống model dự phòng "
            "đều ghi thêm một dòng, nên một lượt hỏng có thể thành ba dòng. Vì vậy bão "
            "429 vừa đẩy `error_rate` lên vừa thổi phồng mẫu số và kéo `free_ratio` xuống."
        ),
        examples=[96],
    )
    prompt_tokens: int = Field(
        description="Tổng token đầu vào đã gửi cho nhà cung cấp LLM trong cửa sổ.",
        examples=[41200],
    )
    completion_tokens: int = Field(
        description="Tổng token đầu ra nhận về. Thường đắt hơn token đầu vào theo bảng giá.",
        examples=[12800],
    )
    total_tokens: int = Field(
        description=(
            "`prompt_tokens + completion_tokens`, tính sẵn cho tiện. Đây là tổng của "
            "MỌI tác vụ, không chỉ chat — nên đừng chia nó cho `chat_turns` để suy ra "
            "token mỗi lượt, đã có `avg_tokens_per_chat` làm đúng việc đó."
        ),
        examples=[54000],
    )
    by_task: dict[str, int] = Field(
        description=(
            "Đếm lượt gọi theo tác vụ: `CHAT` (hỏi đáp), `REWRITE` (viết lại câu hỏi "
            "theo ngữ cảnh trước khi tìm kiếm), `QUIZ` (sinh câu hỏi trắc nghiệm bằng "
            "LLM).\n\n"
            "`REWRITE` cao gần bằng `CHAT` nghĩa là hầu hết lượt chat đều phải viết lại "
            "câu hỏi — mỗi lần như vậy tốn thêm một lượt gọi LLM."
        ),
        examples=[{"CHAT": 96, "REWRITE": 28, "QUIZ": 13}],
    )
    by_answer_source: dict[str, int] = Field(
        description=(
            "Phân bố nhánh trả lời, CHỈ đếm dòng `task=CHAT` (các tác vụ khác không có "
            "khái niệm nguồn câu trả lời), nên tổng các giá trị ở đây khớp `chat_turns`.\n\n"
            "Ba nhánh `DIRECT_LOOKUP`, `CACHE`, `CANNED` tốn 0 token; `RAG` có gọi LLM.\n\n"
            "Chỉ có ĐÚNG BỐN khoá đó. `LLM_ONLY` KHÔNG BAO GIỜ xuất hiện ở đây, dù nó là "
            "một giá trị `answer_source` hợp lệ trong phản hồi chat: lúc ghi nhật ký, "
            "service chưa biết câu trả lời sẽ có trích dẫn hay không nên luôn ghi `RAG`. "
            "Nói cách khác `RAG` ở bảng này = `RAG` + `LLM_ONLY` thật. Muốn đo riêng "
            "`LLM_ONLY` thì phải đếm phản hồi chat có `citations` rỗng, số liệu này không "
            "tách được."
        ),
        examples=[{"DIRECT_LOOKUP": 31, "CACHE": 12, "CANNED": 6, "RAG": 47}],
    )
    free_ratio: MetricTarget = Field(
        description=(
            "CHỈ SỐ QUAN TRỌNG NHẤT của cả bảng: tỷ lệ lượt chat trả lời được mà không "
            "tốn token nào, tức `(DIRECT_LOOKUP + CACHE + CANNED) / chat_turns`. Mục "
            "tiêu `>= 0.40`.\n\n"
            "Dưới ngưỡng nghĩa là đang TRẢ TIỀN cho việc mà dữ liệu cục bộ làm được "
            "miễn phí — thường do tra từ khớp chính xác hỏng, hoặc semantic cache bị "
            "xoá liên tục vì index thay đổi quá thường xuyên.\n\n"
            "Bẫy: `chat_turns = 0` cũng cho `value = 0` và `ok = false`. Đó là cửa sổ "
            "chưa có lưu lượng, không phải hệ thống hỏng."
        ),
    )
    avg_tokens_per_chat: MetricTarget = Field(
        description=(
            "Token trung bình mỗi lượt chat, tính trên MỌI lượt chat kể cả lượt 0 "
            "token. Mục tiêu `< 1200`.\n\n"
            "Vượt ngưỡng nghĩa là ngữ cảnh nhồi vào prompt quá dài hoặc lịch sử hội "
            "thoại gửi kèm quá nhiều — hậu quả trực tiếp là chạm trần "
            "`AI_GLOBAL_TOKENS_PER_MINUTE` sớm hơn và người dùng bắt đầu nhận 429."
        ),
    )
    latency_p95_ms: MetricTarget = Field(
        description=(
            "Phân vị 95 độ trễ của lượt chat, mili giây. Mục tiêu `< 3000`.\n\n"
            "Dùng p95 chứ không dùng trung bình vì trung bình bị các lượt 0 token kéo "
            "xuống rất thấp và che mất chính những lượt chậm mà người dùng nhớ. Với "
            "cửa sổ ít dữ liệu, con số này rơi về đúng lượt chậm nhất nên đừng vội "
            "kết luận khi `chat_turns` còn nhỏ."
        ),
    )
    error_rate: MetricTarget = Field(
        description=(
            "Tỷ lệ lượt gọi thất bại trên TỔNG `calls` (mọi tác vụ, không riêng chat). "
            "Mục tiêu `< 0.02`.\n\n"
            "Vượt ngưỡng thường là nhà cung cấp LLM trả 429/5xx. Lưu ý mẫu số là "
            "`calls`, nên khi lưu lượng thấp chỉ một lỗi cũng đủ đẩy tỷ lệ vọt lên."
        ),
    )
    all_targets_met: bool = Field(
        description=(
            "`true` khi cả bốn chỉ số trên đều `ok`. Tiện cho dashboard đèn xanh - đèn đỏ.\n\n"
            "TUYỆT ĐỐI đừng cảnh báo chỉ dựa vào trường này: cửa sổ không có lượt chat "
            "nào luôn cho `false` (vì `free_ratio = 0 < 0.40`, trong khi ba chỉ số còn "
            "lại bằng 0 nên đều 'đạt'). Điều kiện cảnh báo đúng là "
            "`chat_turns > 0 && !all_targets_met`."
        ),
        examples=[True],
    )
