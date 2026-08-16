"""POST /internal/v1/quiz/generate. SPEC muc 8.4."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request

from app.api.deps import require_internal_token
from app.core.errors import IndexNotReady
from app.schemas.errors import UNAUTHORIZED, ErrorResponse
from app.schemas.quiz import VI_DU_QUIZ_GENERATE, QuizRequest, QuizResponse

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Quiz"],
    dependencies=[Depends(require_internal_token)],
)

# KHÔNG dùng SCOPE_ERRORS dùng chung: ví dụ trong đó viết `scope_deck_id=...`,
# là tên trường của /chat và /search. Quiz nhận `deck_id` nên message thật khác
# hẳn. Ngoài ra 400 hay gặp nhất của endpoint này là INVALID_REQUEST (deck dưới
# 4 thẻ), mã đó không có trong bộ dùng chung. Ba ví dụ dưới đây là message chép
# nguyên văn từ lần chạy thật trên dữ liệu mẫu.
QUIZ_BAD_REQUEST: dict = {
    400: {
        "model": ErrorResponse,
        "description": (
            "Phạm vi deck không hợp lệ (`INVALID_SCOPE`), hoặc phạm vi không đủ "
            "thẻ để dựng câu hỏi (`INVALID_REQUEST`). Cả hai đều là lỗi của người "
            "gọi — đừng retry, hãy sửa request hoặc bảo người học thêm thẻ."
        ),
        "content": {
            "application/json": {
                "examples": {
                    "danh_sach_deck_rong": {
                        "summary": "allowed_deck_ids rỗng = không cho phép gì cả",
                        "value": {
                            "error": {
                                "code": "INVALID_SCOPE",
                                "message": "allowed_deck_ids không được rỗng.",
                            }
                        },
                    },
                    "deck_ngoai_pham_vi": {
                        "summary": "deck_id không nằm trong allowed_deck_ids",
                        "value": {
                            "error": {
                                "code": "INVALID_SCOPE",
                                "message": "deck_id=9 không nằm trong allowed_deck_ids.",
                            }
                        },
                    },
                    "khong_du_the": {
                        "summary": "Deck 4 chỉ có 3 thẻ, cần tối thiểu 4",
                        "value": {
                            "error": {
                                "code": "INVALID_REQUEST",
                                "message": (
                                    "Bộ thẻ chỉ có 3 thẻ trong phạm vi, cần ít nhất "
                                    "4 thẻ để tạo câu trắc nghiệm."
                                ),
                            }
                        },
                    },
                }
            }
        },
    }
}

# KHÔNG dùng NOT_READY dùng chung: bộ đó kèm ví dụ PROVIDER_UNAVAILABLE, mà mã
# đó KHÔNG BAO GIỜ thoát ra khỏi endpoint này. `FillBlankGenerator._call_llm`
# bọc toàn bộ lời gọi Groq trong `except Exception`, nên mọi lỗi nhà cung cấp
# đều bị nuốt và thay bằng câu deterministic. 503 duy nhất ở đây là
# INDEX_NOT_READY, do chính handler bên dưới ném ra.
QUIZ_NOT_READY: dict = {
    503: {
        "model": ErrorResponse,
        "description": (
            "`INDEX_NOT_READY` — model chưa nạp xong hoặc index chưa sẵn sàng. "
            "Đợi `/readyz` trả 200 rồi gọi lại. Đây là 503 DUY NHẤT của endpoint "
            "này: lỗi từ Groq không bao giờ thành 503 ở đây."
        ),
        "content": {
            "application/json": {
                "examples": {
                    "chua_san_sang": {
                        "value": {
                            "error": {
                                "code": "INDEX_NOT_READY",
                                "message": "Model chưa nạp xong hoặc index chưa sẵn sàng.",
                            }
                        }
                    },
                }
            }
        },
    }
}


GENERATE_DESCRIPTION = """
Sinh một bộ câu hỏi ôn tập từ **một** bộ thẻ và trả về ngay trong một lượt gọi.

Backend Java gọi endpoint này rồi tự lưu bài làm, tự chấm, tự cập nhật SRS.
fsoft-ai **không lưu** câu hỏi đã sinh, không biết người học là ai và không chấm
điểm. Gọi hai lần sẽ ra hai bộ đề khác nhau (thứ tự lựa chọn được xáo ngẫu
nhiên), nên muốn hiện lại đúng đề cũ thì backend phải tự lưu đề.

---

### Bốn dạng câu hỏi

| `types` | Cần Groq? | `options` là gì | Chấm điểm bằng | Trường riêng |
|---|---|---|---|---|
| `MULTIPLE_CHOICE` | Không | 4 nghĩa tiếng Việt | `correct_index` | — |
| `LISTENING` | Không | 4 từ tiếng Anh | `correct_index` | `audio_url` |
| `MATCHING` | Không | tối đa 5 nghĩa (cột phải) | `matching[].correct_option_index` | `matching` |
| `FILL_BLANK` | **Có** | 4 từ tiếng Anh | `correct_index` | — |

Ba dạng đầu ráp thẳng từ dữ liệu thẻ đang nằm trong RAM. Chỉ `FILL_BLANK` cần
LLM, vì nó đòi một câu tiếng Anh **mới** có ngữ cảnh, khác câu ví dụ đã in sẵn
trên thẻ.

Mỗi thẻ lấy ra sinh nhiều nhất một câu, chia dạng theo **vòng tròn**: thẻ thứ
`i` nhận `types[i % len(types)]`. Muốn hai phần ba là trắc nghiệm thì lặp lại
trong danh sách: `["MULTIPLE_CHOICE", "MULTIPLE_CHOICE", "LISTENING"]`.

Thứ tự trong `questions` không giống thứ tự chia vòng tròn: các câu
`FILL_BLANK` được sinh theo lô sau cùng nên luôn dồn về cuối mảng.

**Số cặp của một câu `MATCHING` bị `question_count` chặn**, không phải lúc nào
cũng 5: số cặp = `min(5, số thẻ được lấy ra)`, mà số thẻ lấy ra chính là
`question_count`. Xin 3 câu thì mỗi câu nối chỉ có 3 cặp và 3 nghĩa, xin từ 5
trở lên mới đủ 5. Trường hợp biên: `question_count = 1` kèm `types:
["MATCHING"]` không nối được gì (cần ít nhất 2 cặp) nên câu đó **âm thầm lùi về
`MULTIPLE_CHOICE`**. Muốn câu nối đầy đủ 5 cặp thì xin `question_count >= 5`.

---

### `use_ai_context=false` — chế độ dự phòng, **0 token**

Đây là **mặc định**, và là chế độ nên dùng cho ngày demo (SPEC muc 14.1):

- **Không chạm Groq một lần nào.** Không phải "gọi ít đi" — không có một lời gọi
  HTTP nào rời khỏi tiến trình.
- `stats.prompt_tokens = 0`, `stats.completion_tokens = 0`, `stats.llm_count = 0`.
  Đó là bằng chứng: thấy khác 0 nghĩa là bạn không ở chế độ này.
- Rút mạng vẫn chạy.
- 8 câu trong **dưới 2 giây** (acceptance SPEC muc 11.6). Đo thật trên dữ liệu
  mẫu: `stats.latency_ms` là **1–2 ms** cho 6 câu deterministic.

Truyền `types: ["FILL_BLANK"]` kèm `use_ai_context=false` thì dạng cần LLM bị
loại khỏi kế hoạch và cả bộ lùi về `MULTIPLE_CHOICE`. Không lỗi, không cảnh báo
trong response — nhìn `questions[].type` để biết.

### `use_ai_context=true` — chỉ có nghĩa khi `types` chứa `FILL_BLANK`

Bật cờ này mà `types` không có `FILL_BLANK` thì vẫn 0 token, vì ba dạng kia
không dùng LLM.

Các thẻ điền từ được gom **5 thẻ một lời gọi** (`AI_QUIZ_LLM_BATCH_SIZE`), không
phải mỗi câu một lời gọi: system prompt chỉ gửi một lần nên tiết kiệm khoảng bốn
lần token. 8 câu điền từ = 2 lời gọi.

Groq hỏng, hết hạn mức, trả JSON sai, hay soạn câu lộ đáp án đều **không** làm
hỏng lượt gọi: câu đó bị thay bằng một câu trắc nghiệm deterministic và bạn vẫn
nhận **200**.

Hệ quả quan trọng, đã kiểm bằng code: endpoint này **không bao giờ trả 429
`BUDGET_EXHAUSTED`, cũng không bao giờ trả 503 `PROVIDER_UNAVAILABLE`** — kể cả
khi `use_ai_context=true`. `FillBlankGenerator._call_llm` bọc toàn bộ lời gọi
Groq trong `except Exception`, mà hạn mức token cũng được giữ chỗ *bên trong*
lời gọi đó, nên mọi lỗi nhà cung cấp lẫn lỗi cạn ngân sách đều bị nuốt tại chỗ.
Đừng viết nhánh retry-on-429 cho endpoint này, nó sẽ không bao giờ chạy.

Dấu hiệu thật của sự cố là `stats.llm_count` nhỏ hơn số câu `FILL_BLANK` bạn đặt
hàng, kèm `type` đã đổi thành `MULTIPLE_CHOICE`. 503 duy nhất ở đây là
`INDEX_NOT_READY`, ném ra trước khi chạm tới bộ sinh câu hỏi.

---

### Số câu trả về **có thể ít hơn** `question_count`

`question_count` là số thẻ tối đa lấy ra, không phải lời hứa về số câu. **Luôn
đọc `questions.length`, đừng giả định.**

Một câu trắc nghiệm cần 1 đáp án đúng + 3 nhiễu. Nhiễu chọn theo láng giềng
embedding **trong cùng deck** (không bao giờ lấy từ deck khác), rồi lọc bỏ:

1. Ứng viên có cosine với thẻ đang hỏi **trên `AI_QUIZ_DISTRACTOR_MAX_COSINE`
   (mặc định 0.92)** — quá giống thì thành đồng nghĩa, câu hỏi sẽ có hai đáp án
   đúng và người học khiếu nại đúng. Với dữ liệu mẫu, `apprehensive` (102) và
   `anxious` (108) là đúng cặp mà luật này sinh ra để loại: cosine đo được là
   **0.9231**, nhỉnh hơn ngưỡng 0.92 một chút, nên hai thẻ đó không bao giờ làm
   nhiễu cho nhau.
2. Ứng viên trùng nội dung, hoặc chứa / bị chứa trong đáp án đúng sau khi bỏ dấu
   câu và hạ chữ thường.
3. Ứng viên trùng với nhiễu đã chọn trước đó.

Deck nhỏ hoặc nhiều thẻ gần nghĩa thì có thể không gom đủ 3 nhiễu — thẻ đó bị
**bỏ**, các thẻ còn lại vẫn ra câu hỏi.

Nói rõ để khỏi hiểu lầm: **với dữ liệu mẫu, chuyện đó không xảy ra.** Đã chạy
thử từng deck và đều nhận đủ số câu — deck 1 xin 9 được 9, deck 2 xin 8 được 8,
deck 3 xin 4 được 4. Kể cả khi cặp 102/108 bị luật 1 lọc bỏ, thẻ 102 vẫn còn 7
láng giềng khác để lấy nhiễu nên không mất câu nào. Luật "đọc
`questions.length`" là để phòng dữ liệu thật của người dùng, không phải mô tả
hành vi bạn sẽ thấy khi bấm thử trên Swagger.

`index` được đánh lại 1..N **sau khi** lọc nên luôn liên tục — đừng tìm khoảng
trống của `index` để phát hiện câu bị bỏ, sẽ không bao giờ thấy.

Trường hợp cực đoan không sinh nổi câu nào: **400** `INVALID_REQUEST`, không
phải 200 với mảng rỗng.

---

### `LISTENING` âm thầm lùi về trắc nghiệm — cảnh báo

Dạng `LISTENING` chỉ dựng được từ thẻ **có `audio_url`**. Thẻ không có (backend
chưa sinh file đọc) thì không báo lỗi và cũng không bị bỏ: nó lùi về một câu
`MULTIPLE_CHOICE`.

**Người gọi không nhận được tín hiệu gì**: không cờ, không mã lỗi, không trường
"fallback", không header. Cách duy nhất để biết là **tự đếm** — so số câu có
`type == "LISTENING"` với số câu bạn kỳ vọng theo công thức chia vòng tròn ở
trên. Giao diện nào cứ thấy `types` có `LISTENING` là dựng sẵn trình phát âm
thanh sẽ hiện một ô phát rỗng với `audio_url = null`.

Cùng cơ chế đó áp cho `MATCHING` (thẻ thiếu `meaning`) và cho `FILL_BLANK` (LLM
hỏng). Nói ngắn gọn: **luôn đọc `questions[].type`, đừng tin `types` bạn gửi đi.**

Với dữ liệu mẫu bạn **không** quan sát được tình huống này: mọi thẻ deck 1–3 đều
có `audio_url`, còn thẻ duy nhất thiếu audio là 402 (`tuition`) lại nằm ở deck 4
— mà deck 4 chỉ có 3 thẻ nên bị chặn từ trước bởi luật ngay dưới đây.

---

### Deck dưới 4 thẻ → **400**, không phải 500

Trắc nghiệm cần 1 đáp án đúng + 3 nhiễu = 4 thẻ. Phạm vi có ít hơn 4 thẻ thì
request bị từ chối ngay, kèm câu tiếng Việt hiển thị được cho người dùng cuối:

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Bộ thẻ chỉ có 3 thẻ trong phạm vi, cần ít nhất 4 thẻ để tạo câu trắc nghiệm."
  }
}
```

Thử ngay bằng `{"deck_id": 4, "allowed_deck_ids": [4]}` — deck 4 (IELTS – Giáo
dục) trong dữ liệu mẫu chỉ có 3 thẻ (401, 402, 403) nên luôn ra đúng lỗi này.

Đây là **lỗi của người gọi**, không phải sự cố hệ thống: đừng retry, hãy hiện
thông báo bảo người học thêm thẻ vào bộ. Cùng luật đó áp **sau khi** lọc
`card_ids`: gửi 3 `card_ids` hợp lệ cũng ra 400.

---

### `card_ids` — ôn đúng những thẻ đến hạn

- **Để rỗng (mặc định) = lấy cả deck.** Rỗng ở đây KHÔNG có nghĩa "không thẻ
  nào" — khác hẳn `allowed_deck_ids`, nơi rỗng nghĩa là cấm hết.
- Truyền vào khi backend Java đã chọn sẵn thẻ **đến hạn ôn theo SRS**. fsoft-ai
  không biết SRS là gì: không có lịch ôn, không tính khoảng lặp, không lưu kết
  quả làm bài. Nó chỉ **lọc lại** danh sách theo `deck_id` cho an toàn.
- ID không thuộc `deck_id` bị bỏ qua **im lặng**: `[101, 999]` với deck 1 thì
  chỉ 101 được dùng, không có cảnh báo nào.
- Chỉ giới hạn thẻ **được đem ra hỏi**. Đáp án nhiễu vẫn lấy từ **cả deck** —
  nhờ vậy 4 thẻ đến hạn vẫn ra được câu hỏi tử tế.

---

### Bốn ví dụ chạy được ngay

Bấm **Try it out**, rồi chọn kịch bản ở menu thả xuống **Examples** ngay phía trên
ô Request body. Trước khi bấm **Execute**: bấm **Authorize** dán
`AI_INTERNAL_TOKEN`, và kiểm `GET /internal/v1/index/status` thấy `card_count = 24`.

**1. Chế độ dự phòng, 0 token** — 5 câu trắc nghiệm deck 1, không chạm Groq:

```json
{
  "deck_id": 1,
  "allowed_deck_ids": [1, 2, 3],
  "question_count": 5,
  "types": ["MULTIPLE_CHOICE"],
  "card_ids": [],
  "use_ai_context": false
}
```

**2. Trộn nhiều dạng** — vẫn 0 token. Sáu thẻ deck 1 chia vòng tròn thành
trắc nghiệm / nghe / nối, mỗi dạng 2 câu:

```json
{
  "deck_id": 1,
  "allowed_deck_ids": [1, 2, 3],
  "question_count": 6,
  "types": ["MULTIPLE_CHOICE", "LISTENING", "MATCHING"],
  "card_ids": [],
  "use_ai_context": false
}
```

**3. Ôn theo `card_ids` chỉ định** — chỉ hỏi 4 thẻ deck 2 mà backend Java cho là
đến hạn; nhiễu vẫn lấy từ cả deck 2:

```json
{
  "deck_id": 2,
  "allowed_deck_ids": [1, 2, 3],
  "question_count": 4,
  "types": ["MULTIPLE_CHOICE", "LISTENING"],
  "card_ids": [201, 203, 205, 207],
  "use_ai_context": false
}
```

**4. Dạng duy nhất cần Groq** — cần `AI_LLM_API_KEY` thật. Không có key vẫn trả
200 nhưng `stats.llm_count = 0` và mọi câu đã lùi về trắc nghiệm (lúc đó lượt
gọi còn chậm hơn vì phải đợi Groq trả lỗi):

```json
{
  "deck_id": 2,
  "allowed_deck_ids": [1, 2, 3],
  "question_count": 5,
  "types": ["FILL_BLANK"],
  "card_ids": [],
  "use_ai_context": true
}
```
"""


@router.post(
    "/quiz/generate",
    response_model=QuizResponse,
    summary="Sinh bộ câu hỏi ôn tập từ một bộ thẻ",
    description=GENERATE_DESCRIPTION,
    responses={
        200: {
            "description": (
                "Bộ câu hỏi đã sinh. Vẫn là 200 kể cả khi Groq hỏng hoặc cạn ngân "
                "sách — câu hỏng được thay bằng câu deterministic. Số câu có thể "
                "ít hơn `question_count`, luôn đọc độ dài `questions`."
            )
        },
        **UNAUTHORIZED,
        **QUIZ_BAD_REQUEST,
        **QUIZ_NOT_READY,
    },
)
async def generate_quiz(
    request: Request,
    body: Annotated[QuizRequest, Body(openapi_examples=VI_DU_QUIZ_GENERATE)],
) -> QuizResponse:
    service = request.app.state.service

    if not service.is_ready:
        raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

    questions, stats = await service.quiz.generate(body)

    return QuizResponse(questions=questions, stats=stats)
