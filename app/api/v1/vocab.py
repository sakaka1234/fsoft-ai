"""POST /internal/v1/vocab/extract. SPEC muc 8.5b."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request

from app.api.deps import require_internal_token
from app.core.errors import IndexNotReady
from app.schemas.errors import BUDGET, NOT_READY, UNAUTHORIZED, ErrorResponse
from app.schemas.vocab import VI_DU_VOCAB_EXTRACT, VocabExtractRequest, VocabExtractResponse

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Từ vựng"],
    dependencies=[Depends(require_internal_token)],
)

# Dùng lại NOT_READY và BUDGET của bộ chung — ngược với quiz.
#
# Quiz tự định nghĩa bộ riêng vì nó KHÔNG BAO GIỜ để `PROVIDER_UNAVAILABLE` hay
# `BUDGET_EXHAUSTED` lọt ra ngoài: hỏng LLM thì nó lùi về câu deterministic.
# Endpoint này thì ngược lại, cả hai mã đó đều với tới được, nên bộ chung mô tả
# đúng. Đó cũng chính là cái giá của việc không có đường lùi.
#
# Nhưng 400 thì vẫn phải tự viết: `SCOPE_ERRORS` dùng chung lấy ví dụ
# `scope_deck_id`, một trường không tồn tại ở đây, và lỗi 400 hay gặp nhất của
# endpoint này — văn bản quá dài — không có mặt trong bộ chung. Message dưới đây
# chép nguyên văn từ lần chạy thật.
EXTRACT_BAD_REQUEST: dict = {
    400: {
        "model": ErrorResponse,
        "description": (
            "Lỗi của người gọi, **đừng retry**. Ba nguyên nhân, phân biệt bằng "
            "`message` chứ không bằng `code`."
        ),
        "content": {
            "application/json": {
                "examples": {
                    "van_ban_qua_dai": {
                        "summary": "Đoạn văn vượt 4.000 ký tự",
                        "description": (
                            "Thông báo nêu **cả độ dài thật lẫn giới hạn** để backend "
                            "biết phải cắt bớt bao nhiêu. Service không tự cắt và "
                            "không tự chia nhỏ — xem mô tả trường `text`."
                        ),
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": (
                                    "Đoạn văn dài 12480 ký tự, vượt giới hạn 4000 "
                                    "ký tự. Hãy cắt ngắn rồi gọi nhiều lần."
                                ),
                            }
                        },
                    },
                    "khong_du_tu_tieng_anh": {
                        "summary": "Đoạn văn không phải tiếng Anh",
                        "description": (
                            "Chặn TRƯỚC khi đặt chỗ ngân sách, nên một đoạn thuần "
                            "tiếng Việt hay một khối số không tốn token nào."
                        ),
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": (
                                    "Đoạn văn chỉ có 1 từ tiếng Anh phân biệt, "
                                    "cần ít nhất 3 để trích từ vựng."
                                ),
                            }
                        },
                    },
                    "pham_vi_rong": {
                        "summary": "allowed_deck_ids rỗng",
                        "description": (
                            "Rỗng nghĩa là KHÔNG ĐƯỢC PHÉP GÌ CẢ, không bao giờ "
                            "hiểu thành 'không lọc'."
                        ),
                        "value": {
                            "error": {
                                "code": "INVALID_SCOPE",
                                "message": "allowed_deck_ids không được rỗng.",
                            }
                        },
                    },
                }
            }
        },
    }
}

EXTRACT_DESCRIPTION = """
Dán một đoạn văn tiếng Anh, nhận về danh sách **thẻ từ vựng ứng viên** đã dựng
sẵn đủ trường — backend chỉ việc lưu những thẻ người dùng chọn.

**fsoft-ai không lưu gì cả.** Nó không có endpoint ghi dữ liệu nghiệp vụ nào.
Thẻ nào vào deck nào là quyết định của backend sau khi người dùng tick chọn.

---

### Endpoint đắt nhất của service

Đây là lời gọi tốn token nhất ở đây, và không có nhánh 0 token nào. Đo thật trên
`gpt-oss-120b`:

| Phần | Token |
|---|---|
| Đoạn văn 4.000 ký tự + hướng dẫn | ~2.000 |
| Đầu ra ở `max_candidates=6` | ~3.500 |
| **Đặt chỗ mỗi lượt** | **~5.500 trên ngân sách 6.400/phút** |

Phần lớn đầu ra là **token suy luận ẩn** — model tiêu 1.100–2.900 token để đọc
và chọn lọc, và số đó không xuất hiện ở đâu trong câu trả lời.

Hệ quả cho backend:

- Gọi **tuần tự**, đừng bắn song song. Service tự giới hạn một lượt tại một
  thời điểm, nên lượt thứ hai sẽ xếp hàng chứ không chạy nhanh hơn.
- Trong lúc một lượt trích xuất đang chạy, `/chat` của người khác chỉ còn khoảng
  một lượt ngân sách. Đừng gọi endpoint này ở đường đi nóng.
- **Không** gọi mỗi lần người dùng gõ hay dán. Chờ họ bấm nút.

### Không thử lại, và cố ý như vậy

Đúng **một** lời gọi LLM mỗi request (`stats.llm_calls` luôn bằng `1`). Quiz thử
lại tới ba lần được vì mỗi lần gửi lại chỉ vài chục token; ở đây gửi lại nghĩa
là gửi lại **toàn bộ đoạn văn**, và ba lần như vậy vượt ngân sách của cả một
phút. Hỏng thì báo lỗi thật thay vì đốt hết phần của người khác.

### Ba tầng lọc sau khi model trả lời

Kết quả ở đây sẽ được **lưu thành thẻ** và thẻ thì chia sẻ được, nên không thứ
gì model nói được tin ngay:

1. **Whitelist trường** — chỉ bảy khoá đã định đi tiếp. Model trả thêm
   `audio_url` hay `card_id` thì rơi hết.
2. **Chốt chặn ký tự** — trường chứa `< > { } [ ] \\`, đường dẫn `http`, hay
   xuống dòng bị loại. Đếm ở `stats.dropped_unsafe`.
3. **Bám văn bản** — `example_sentence` phải có thật trong đoạn văn bạn gửi, so
   khớp sau khi bỏ qua khác biệt hình thức (nháy cong, gạch dài, khoảng trắng,
   hoa thường). Không khớp thì **loại cả ứng viên**. Đếm ở
   `stats.dropped_not_grounded`.

Tầng 3 là tầng quan trọng nhất: `example_sentence` là trường duy nhất model có
thể dùng để đưa nội dung mới vào dữ liệu được lưu trữ.

### Phân biệt "không có gì đáng học" với "model bịa"

Hai tình huống trông giống nhau nếu chỉ nhìn `candidates` rỗng:

| `returned_by_llm` | `candidates` | HTTP | Nghĩa |
|---|---|---|---|
| `0` | `[]` | **200** | Đoạn văn thật sự không có từ nào đáng học |
| `> 0` | `[]` | **503** | Model bịa toàn bộ, đã bị lọc sạch — thử lại có cơ hội |

Đừng coi `200` kèm mảng rỗng là lỗi, và đừng gọi lại.

### Khử trùng: đánh dấu, không xoá

Từ người học đã có **vẫn nằm trong kết quả**, mang `already_in_deck: true` kèm
`existing_card_id`. Giao diện nên bỏ tick sẵn thay vì giấu đi.

Kiểm tra chỉ diễn ra TRONG `allowed_deck_ids` — cùng một từ, phạm vi hẹp lại thì
cờ về `false`. Và khớp theo `word` viết thường, **không có lemma hoá**: bộ thẻ có
`emission` mà đoạn văn cho ra `emissions` thì vẫn báo chưa có.

Luôn kiểm `stats.dedup_checked`. `false` nghĩa là chỉ mục đang rỗng nên mọi cờ
`already_in_deck` đều vô nghĩa — xảy ra trong cửa sổ sau khi container khởi động
lại mà đồng bộ đầu tiên chưa xong.
""".strip()


@router.post(
    "/vocab/extract",
    response_model=VocabExtractResponse,
    summary="Trích từ vựng đáng học từ một đoạn văn",
    description=EXTRACT_DESCRIPTION,
    responses={
        200: {
            "description": (
                "Danh sách ứng viên đã qua lọc. **Mảng rỗng kèm "
                "`stats.returned_by_llm = 0` cũng là 200** — đoạn văn không có gì "
                "đáng học. Số ứng viên có thể ít hơn `max_candidates`, luôn đọc "
                "độ dài mảng."
            )
        },
        **UNAUTHORIZED,
        **EXTRACT_BAD_REQUEST,
        **BUDGET,
        **NOT_READY,
    },
)
async def extract_vocab(
    request: Request,
    body: Annotated[VocabExtractRequest, Body(openapi_examples=VI_DU_VOCAB_EXTRACT)],
) -> VocabExtractResponse:
    service = request.app.state.service

    # Cần chỉ mục sẵn sàng vì bước khử trùng đọc nó. Trích xuất không cần
    # encoder, nhưng `is_ready` gộp cả hai cờ nên kiểm chung là đủ và nhất quán
    # với /search, /quiz.
    if not service.is_ready:
        raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

    candidates, stats = await service.vocab.extract(body)

    return VocabExtractResponse(candidates=candidates, stats=stats)
