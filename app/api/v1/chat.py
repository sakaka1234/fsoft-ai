"""
POST /internal/v1/chat và /chat/stream. SPEC muc 8.1 và 8.2.

Backend Java gọi hai endpoint này. fsoft-ai không biết gì về người dùng —
`allowed_deck_ids` là toàn bộ ranh giới bảo mật, và backend chịu trách nhiệm
tính đúng danh sách đó.
"""

import json
import time
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import require_internal_token
from app.chat.orchestrator import mark_used_citations
from app.core.errors import AppError
from app.core.logging import get_logger
from app.schemas.chat import (
    VI_DU_CHAT,
    AnswerSource,
    ChatRequest,
    ChatResponse,
    CitationOut,
    UsageOut,
)
from app.schemas.errors import BUDGET, NOT_READY, SCOPE_ERRORS, UNAUTHORIZED

log = get_logger(__name__)

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Chat"],
    dependencies=[Depends(require_internal_token)],
)


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# Hai đoạn dưới đây chỉ để hiện trong Swagger. Không ghép bằng chuỗi ba nháy vì
# một dòng `data:` phải nằm trọn trên một dòng mới đúng chuẩn SSE, mà dòng đó
# lại dài hơn 100 ký tự — nối chuỗi cho phép giữ mã nguồn ngắn mà văn bản sinh
# ra vẫn đúng.
SSE_VI_DU_RAG = (
    "event: meta\n"
    'data: {"intent": "EXAMPLE_REQUEST", "answer_source": "RAG", '
    '"rewritten_query": "cho tôi ví dụ với từ resilient"}\n'
    "\n"
    "event: citations\n"
    'data: [{"card_id": 101, "word": "resilient", "deck_id": 1, '
    '"deck_title": "TOEIC - Cảm xúc & Tính cách", "score": 1.0, "rank": 1, '
    '"used_in_answer": false}]\n'
    "\n"
    "event: token\n"
    'data: {"t": "Từ "}\n'
    "\n"
    "event: token\n"
    'data: {"t": "**resilient**"}\n'
    "\n"
    "event: token\n"
    'data: {"t": " nghĩa là kiên cường, phục hồi nhanh sau khó khăn"}\n'
    "\n"
    "event: token\n"
    'data: {"t": ". [#101]"}\n'
    "\n"
    "event: done\n"
    'data: {"usage": {"prompt_tokens": 812, "completion_tokens": 96, '
    '"latency_ms": 1840}}\n'
    "\n"
)

SSE_VI_DU_LOI = (
    "event: meta\n"
    'data: {"intent": "VOCAB_LOOKUP", "answer_source": "RAG", "rewritten_query": null}\n'
    "\n"
    "event: citations\n"
    'data: [{"card_id": 101, "word": "resilient", "deck_id": 1, '
    '"deck_title": "TOEIC - Cảm xúc & Tính cách", "score": 1.0, "rank": 1, '
    '"used_in_answer": false}]\n'
    "\n"
    "event: token\n"
    'data: {"t": "Từ **resilient** nghĩa là"}\n'
    "\n"
    "event: error\n"
    'data: {"code": "PROVIDER_UNAVAILABLE", '
    '"message": "Trợ lý AI đang quá tải, vui lòng thử lại sau ít phút."}\n'
    "\n"
)


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Hỏi đáp trên bộ thẻ, chờ câu trả lời đầy đủ",
    responses={**UNAUTHORIZED, **SCOPE_ERRORS, **NOT_READY, **BUDGET},
    description="""
Hỏi một câu, chờ tới khi có câu trả lời trọn vẹn rồi mới nhận về. Dùng endpoint
này cho mọi thứ không phải giao diện chat thời gian thực: job nền, API cho
mobile, và test tay ngay trong trang này.

Muốn chữ chạy dần ra màn hình như ChatGPT thì dùng `POST /internal/v1/chat/stream`
— nhưng Swagger không test SSE tử tế được, đọc mô tả bên đó trước.

---

### Thử trong 30 giây

1. Bấm **Authorize** ở góc trên bên phải, dán giá trị `AI_INTERNAL_TOKEN` trong
   `.env` (mặc định `dev-token`).
2. Bấm **Try it out**, giữ nguyên ví dụ có sẵn, bấm **Execute**.
3. Nhìn `answer_source` và `usage.prompt_tokens` TRƯỚC khi đọc `answer` — hai
   field đó cho biết câu trả lời từ đâu ra và lượt vừa rồi có tốn tiền không.

Ví dụ mặc định chạy được **không cần khoá Groq**: ba nhánh miễn phí chạy hoàn
toàn cục bộ. Các ví dụ đi vào `RAG`/`LLM_ONLY` cần `AI_LLM_API_KEY` hợp lệ,
thiếu khoá sẽ nhận **503 `PROVIDER_UNAVAILABLE`** chứ không phải câu trả lời rỗng.

---

### Một lượt chat đi qua những gì

12 bước, mỗi bước đứng trước là một cơ hội thoát ra mà không tốn một token nào:

```text
query ─► 1. kiểm allowed_deck_ids ──────────► rỗng thì 400
      ─► 2. viết lại câu hỏi (chỉ khi có history VÀ câu hỏi phụ thuộc lượt trước)
      ─► 3. embed câu hỏi (chạy cục bộ, 0 token)
      ─► 4. đoán intent (regex + so vector, 0 token)
      ─► 5. SMALLTALK / OUT_OF_SCOPE ───────► CANNED         0 token
      ─► 6. tìm thẻ ─► tra từ đơn giản ─────► DIRECT_LOOKUP  0 token
      ─► 7. semantic cache ────────────────► CACHE          0 token
      ─► 8. kiểm ngân sách token ──────────► cạn thì 429
      ─► 9-12. gọi LLM ───────────────────► RAG / LLM_ONLY
```

Thứ tự này là lý do mục tiêu "≥ 40% lượt không tốn token" khả thi. Nếu bạn thấy
tỷ lệ `usage.prompt_tokens = 0` tụt xuống thấp, gần như chắc chắn có thứ gì đó
làm hỏng một trong ba nhánh rẻ chứ không phải người dùng đột nhiên hỏi khó hơn.

---

### `answer_source`: điều kiện chính xác của từng nhánh

**`CANNED` — 0 token.** `intent` là `SMALLTALK` hoặc `OUT_OF_SCOPE`. Trả nguyên
văn một trong hai đoạn cố định (chào hỏi / từ chối lịch sự), `citations` luôn
rỗng, không một request nào được gửi sang Groq.

**`DIRECT_LOOKUP` — 0 token.** Cần **cả bốn** điều kiện đúng cùng lúc:

1. `intent = VOCAB_LOOKUP`;
2. thẻ hạng 1 khớp chính xác — trong câu hỏi có một từ tiếng Anh trùng khít
   `word` của thẻ;
3. câu hỏi dài tối đa 8 từ và có dạng "… nghĩa là gì / là gì / định nghĩa …";
4. câu hỏi KHÔNG chứa: *khác gì, khác nhau, so sánh, phân biệt, tại sao, vì sao,
   ngữ cảnh, chi tiết, thêm*.

Câu trả lời khi đó ghép thẳng từ các trường của thẻ (phiên âm, nghĩa, định
nghĩa tiếng Anh, câu ví dụ), đúng 1 phần tử `citations` với `used_in_answer=true`.
Hỏng một điều là tụt xuống `RAG`: `resilient nghĩa là gì` tốn 0 token, còn
`resilient khác gì diligent` tốn khoảng 1.000 token dù chỉ khác vài chữ.

**`CACHE` — 0 token.** Vector câu hỏi đạt cosine ≥ 0,97 với một câu đã hỏi trước
đó **và** trùng khít phạm vi: cùng tập `allowed_deck_ids` **và** cùng tập thẻ vừa
tìm được. Cache nằm trong RAM của tiến trình — restart là mất sạch, TTL 24 giờ,
tối đa 500 câu, và bị xoá toàn bộ mỗi lần đồng bộ làm index đổi (câu trả lời cũ
dựng từ nội dung thẻ cũ, thẻ đổi thì nó thành sai).

Được ghi vào cache là MỌI lượt thật sự gọi LLM — `RAG` lẫn `LLM_ONLY`. Nên một
câu "từ này chưa có trong bộ thẻ" cũng nằm trong cache, và lần hỏi lại sẽ trả về
`answer_source = CACHE` chứ không phải `LLM_ONLY` nữa. Ba nhánh 0 token không ghi
gì thêm vào cache, vì chúng vốn đã miễn phí.

Phạm vi phải nằm trong khoá cache, nếu không thì hai người dùng có quyền khác
nhau hỏi cùng một câu sẽ nhận cùng một câu trả lời dựng từ bộ thẻ của người kia
— đó là rò rỉ dữ liệu, không phải tối ưu.

**`RAG` — đo được 570–800 token, trung vị ~730.** Mọi trường hợp còn lại mà tìm
được ít nhất một thẻ. Nội dung các thẻ đó được nhét vào prompt làm ngữ cảnh, và
model bị buộc ghi mã `[#cardId]` ở cuối những câu lấy dữ liệu từ thẻ. Con số này
đo trên dữ liệu mẫu 24 thẻ với `top_k` mặc định là 3; nó tăng theo `top_k` và
theo độ dài nội dung thẻ.

**`LLM_ONLY` — khoảng 500–600 token.** Rẻ hơn `RAG` vì khối ngữ cảnh rỗng. Có gọi
LLM nhưng `citations` rỗng. Hai đường
dẫn tới đây, hoàn toàn khác nhau:

- `intent` là `GRAMMAR_QA` hoặc `TRANSLATE` → **cố tình bỏ qua bước tìm thẻ**.
  Ngữ pháp và dịch câu là kiến thức chung, không nằm trong bộ thẻ, tìm chỉ tốn
  thời gian.
- Có tìm nhưng không thẻ nào đủ liên quan (không khớp chính xác, và mọi điểm
  cosine đều dưới `AI_MIN_SCORE` = 0,83). Ví dụ: hỏi `deforestation` trong khi
  `allowed_deck_ids = [1, 2]`, còn từ đó nằm ở deck 3.

Lúc đó ngữ cảnh gửi cho model chỉ là dòng "(Bộ thẻ của người dùng không có thẻ
nào khớp câu hỏi này.)", và prompt hệ thống buộc model nói rõ "Từ này chưa có
trong bộ thẻ của bạn". Giao diện nên gắn nhãn "kiến thức chung" cho nhóm này —
hiện y như câu trả lời có nguồn là làm người học hiểu nhầm rằng từ đó có trong
bộ thẻ của họ.

---

### `intent` quyết định lượt chat đi đường nào

Đoán bằng regex rồi so vector với các câu mẫu — 0 token, không hề gọi LLM. Nếu
lượt này có viết lại câu hỏi thì phân loại chạy trên câu ĐÃ viết lại, không phải
trên `query` gốc.

| `intent` | Có tìm thẻ? | Ảnh hưởng tới luồng |
|---|---|---|
| `VOCAB_LOOKUP` | Có | Nhánh DUY NHẤT mở được `DIRECT_LOOKUP` 0 token |
| `EXAMPLE_REQUEST` | Có | Luôn phải gọi LLM, vì đặt câu mới thì template chịu |
| `QUIZ_REQUEST` | Có | Chỉ trả lời bằng chữ, KHÔNG sinh quiz |
| `GRAMMAR_QA` | **Không** | Bỏ hẳn bước tìm thẻ → `citations` luôn rỗng |
| `TRANSLATE` | **Không** | Bỏ hẳn bước tìm thẻ → `citations` luôn rỗng |
| `SMALLTALK` | Không | `CANNED`, câu chào cố định |
| `OUT_OF_SCOPE` | Không | `CANNED`, câu từ chối cố định |

Hai dòng in đậm là chỗ hay làm người mới hoang mang: hỏi ngữ pháp hay nhờ dịch
thì `citations` rỗng vì **không ai đi tìm cả**, chứ không phải vì bộ thẻ thiếu.

`QUIZ_REQUEST` cũng dễ hiểu nhầm: "tạo cho tôi bài kiểm tra" chỉ nhận lại một
đoạn văn xuôi. Muốn câu hỏi trắc nghiệm thật thì gọi
`POST /internal/v1/quiz/generate`.

Đoán sai intent chỉ làm câu trả lời kém hay đi, KHÔNG bao giờ làm lộ thẻ ngoài
phạm vi: `allowed_deck_ids` chặn ở tầng tìm thẻ, mà hai nhánh bỏ qua tìm thẻ thì
lại chẳng có thẻ nào để lộ.

---

### Chi phí ẩn: bước viết lại câu hỏi

`usage` chỉ đếm lời gọi sinh câu trả lời. Nếu lượt này có viết lại câu hỏi
(`rewritten_query` khác `null`) thì đã tốn thêm khoảng 150 token của model
`openai/gpt-oss-20b`, và số đó **không** xuất hiện ở đâu trong phản hồi.

Nghĩa là một lượt `DIRECT_LOOKUP` với `prompt_tokens = 0` vẫn có thể đã tiêu
150 token cho bước viết lại. Muốn con số đầy đủ thì xem `GET /internal/v1/stats`,
chỗ đó ghi cả lời gọi `REWRITE`.

---

### `history` và việc viết lại câu hỏi

Chỉ **6 tin nhắn cuối** được dùng (`AI_HISTORY_MAX_MESSAGES`, xếp cũ trước mới
sau). Gửi 50 tin không gây lỗi, nhưng 44 tin đầu bị cắt bỏ im lặng.

Bước viết lại tốn token nên chỉ chạy khi thật sự cần. Điều kiện: có `history`
**và** (câu hỏi tối đa 8 từ **hoặc** câu hỏi chứa đại từ chỉ định — *này, đó,
kia, ấy, nó, vậy, thế, vừa rồi, it, this, that, them…*).

- `cho tôi ví dụ với từ này` + history → viết lại thành
  `cho tôi ví dụ với từ resilient`, rồi CHÍNH câu này mới được đem đi tìm thẻ.
- `từ procurement trong lĩnh vực mua sắm doanh nghiệp có nghĩa chính xác ra sao`
  + history → **không** viết lại: câu dài và tự đủ nghĩa, viết lại chỉ tốn token.

Viết lại hỏng (Groq lỗi, hết giờ) không làm hỏng lượt chat: hệ thống lặng lẽ
dùng lại `query` gốc và `rewritten_query` trả về `null`.

---

### `citations` khác `used_in_answer` thế nào

- `citations` = những thẻ đã ĐƯA CHO model đọc, xếp theo `rank`. Đây là bằng
  chứng về việc tìm kiếm đã lấy gì.
- `used_in_answer = true` = những thẻ model THẬT SỰ trích dẫn, tính bằng cách dò
  mã `[#card_id]` trong `answer`.

Ở giao diện: tô đậm nhóm `true` và đặt ngay dưới câu trả lời; thu gọn nhóm
`false` vào mục "nguồn đã tham khảo". Đừng ẩn hẳn nhóm `false` — khi câu trả lời
lạc đề thì chính chúng cho biết tìm kiếm đã lấy nhầm thẻ nào, đó là công cụ gỡ
lỗi nhanh nhất bạn có.

---

### Các ví dụ có sẵn

Bấm **Try it out**, rồi chọn kịch bản ở menu thả xuống **Examples** ngay phía trên
ô Request body. Hai ví dụ đầu chạy được **không cần khoá Groq**.

**1. `tra_tu_0_token` → `DIRECT_LOOKUP`, chạy được khi chưa có khoá Groq**

```json
{ "query": "resilient nghĩa là gì", "allowed_deck_ids": [1, 2, 3, 4] }
```

**2. `ngoai_chu_de` → `CANNED`, cũng không cần khoá Groq**

```json
{ "query": "hôm nay thời tiết thế nào", "allowed_deck_ids": [1, 2, 3, 4] }
```

**3. `can_dien_giai` → `RAG` (cần `AI_LLM_API_KEY`)**

```json
{
  "query": "resilient dùng trong ngữ cảnh công sở thế nào",
  "allowed_deck_ids": [1],
  "options": { "top_k": 5 }
}
```

Chữ "ngữ cảnh" chặn nhánh template lại, nên lượt này bắt buộc phải gọi LLM. Gửi
đúng body này **lần thứ hai** sẽ nhận `answer_source = CACHE` với
`prompt_tokens = 0` — đó là cách nhanh nhất để tự thấy semantic cache hoạt động.

**4. `co_lich_su_viet_lai` → `RAG` kèm `rewritten_query` khác `null`**

```json
{
  "query": "cho tôi ví dụ với từ này",
  "allowed_deck_ids": [1],
  "history": [
    { "role": "user", "content": "resilient nghĩa là gì" },
    { "role": "assistant", "content": "resilient nghĩa là kiên cường. [#101]" }
  ]
}
```

Bỏ `history` đi rồi gửi lại: `rewritten_query` thành `null`, câu hỏi "với từ này"
không còn ai hiểu là từ nào, và `citations` sẽ lấy nhầm thẻ.

**5. `khong_co_trong_bo_the` → `LLM_ONLY`, `citations` rỗng**

```json
{ "query": "deforestation nghĩa là gì", "allowed_deck_ids": [1, 2] }
```

Thẻ 303 `deforestation` nằm ở deck 3. Không cho phép deck 3 thì nó không tồn tại
với lượt hỏi này. Thêm `3` vào `allowed_deck_ids` rồi gửi lại sẽ thấy
`answer_source` nhảy về `DIRECT_LOOKUP`.

**6. `hoi_ngu_phap` → `LLM_ONLY` do cố tình bỏ qua tìm thẻ**

```json
{ "query": "thì hiện tại hoàn thành dùng khi nào", "allowed_deck_ids": [1, 2, 3, 4] }
```

`citations` rỗng ở đây KHÔNG phải vì tìm không ra — mà vì `intent = GRAMMAR_QA`
nên bước tìm thẻ không hề chạy.

---

### Dữ liệu mẫu để tự đặt câu hỏi khác

`AI_SOURCE_MODE=fixture`, 24 thẻ. Chỉ những ID dưới đây tồn tại — hỏi ID khác
thì `citations` rỗng chứ không phải lỗi:

- **Deck 1 — TOEIC, Cảm xúc & Tính cách:** 101 resilient, 102 apprehensive,
  103 meticulous, 104 indifferent, 105 empathetic, 106 impulsive,
  107 diligent, 108 anxious, 109 benign
- **Deck 2 — TOEIC, Công việc & Văn phòng:** 201 deadline, 202 delegate,
  203 redundant, 204 procurement, 205 stakeholder, 206 streamline,
  207 onboarding, 208 appraisal
- **Deck 3 — IELTS, Môi trường:** 301 sustainable, 302 emission,
  303 deforestation, 304 renewable
- **Deck 4 — IELTS, Giáo dục:** 401 curriculum, 402 tuition, 403 literacy

Deck 4 chỉ có 3 thẻ: chat bình thường, nhưng KHÔNG đủ để
`POST /internal/v1/quiz/generate` sinh câu trắc nghiệm — nó trả
**400 `INVALID_REQUEST`** với thông báo "cần ít nhất 4 thẻ", vì đáp án nhiễu
được lấy trong cùng deck.

Nối vào backend thật (`AI_SOURCE_MODE=http`) thì ID hoàn toàn khác, và fsoft-ai
KHÔNG có endpoint nào liệt kê ID: `GET /internal/v1/index/status` chỉ trả về số
đếm (`card_count`, `index_size`, mốc đồng bộ), không trả về deck hay thẻ nào.
Danh sách thật phải lấy từ chính backend Java.

---

### Lỗi

Rẽ nhánh theo `error.code`, đừng khớp theo `message` (tiếng Việt, đổi lúc nào
cũng được). Chi tiết từng mã ở mục **Responses** ngay bên dưới.
""",
)
async def chat(
    request: Request,
    body: Annotated[ChatRequest, Body(openapi_examples=VI_DU_CHAT)],
) -> ChatResponse:
    service = request.app.state.service

    outcome = await service.orchestrator.chat(
        query=body.query,
        allowed_deck_ids=body.allowed_deck_ids,
        scope_deck_id=body.scope_deck_id,
        history=[item.model_dump() for item in body.history],
        top_k=body.options.top_k,
        ready=service.is_ready,
    )

    return ChatResponse(
        answer=outcome.answer,
        intent=outcome.intent,
        answer_source=outcome.answer_source,
        rewritten_query=outcome.rewritten_query,
        citations=[CitationOut(**c.to_dict()) for c in outcome.citations],
        usage=UsageOut(
            model=outcome.model,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            latency_ms=outcome.latency_ms,
        ),
    )


@router.post(
    "/chat/stream",
    summary="Hỏi đáp streaming SSE, chữ hiện dần từng mẩu",
    responses={
        200: {
            "description": (
                "Luồng SSE mở ra. **Status 200 KHÔNG có nghĩa là lượt chat thành "
                "công** — lỗi nghiệp vụ đến dưới dạng sự kiện `error` bên trong "
                "thân phản hồi. Chỉ khi nhận được sự kiện `done` mới coi là xong."
            ),
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "examples": {
                        "rag_binh_thuong": {
                            "summary": "Nhánh RAG: meta → citations → nhiều token → done",
                            "value": SSE_VI_DU_RAG,
                        },
                        "loi_giua_chung": {
                            "summary": "Lỗi đến bằng sự kiện error, HTTP vẫn là 200",
                            "value": SSE_VI_DU_LOI,
                        },
                    },
                }
            },
        },
        **UNAUTHORIZED,
        **SCOPE_ERRORS,
        **NOT_READY,
        **BUDGET,
    },
    description=r"""
Cùng một body, cùng một luồng xử lý 12 bước như `POST /internal/v1/chat`, chỉ
khác cách trả về: chữ được đẩy ra từng mẩu ngay khi model sinh ra, theo chuẩn
**SSE** (`text/event-stream`). Dùng cho giao diện chat để người học thấy câu trả
lời chạy dần thay vì nhìn vòng xoay 3 giây.

Mọi giải thích về `answer_source`, `intent`, `history`, `citations` nằm ở mô tả
của `POST /internal/v1/chat` — đọc bên đó trước, ở đây chỉ nói phần khác biệt.

Khác biệt duy nhất về hành vi đáng nhớ: `options.max_output_tokens` **có tác
dụng ở endpoint này** (còn `POST /chat` hiện bỏ qua nó).

---

### Swagger KHÔNG test được endpoint này tử tế

Bấm **Try it out** vẫn chạy và vẫn ra kết quả đúng, nhưng Swagger UI đợi stream
đóng hẳn rồi mới đổ cả cục vào ô Response. Bạn sẽ thấy đúng một khối text dài,
mất sạch ý nghĩa của streaming, và trong lúc chờ thì trông y như trang bị treo.

Muốn thấy hành vi thật thì dùng `curl -N` hoặc đoạn JavaScript ở cuối mô tả này.

---

### Chuỗi sự kiện, đúng theo thứ tự code phát ra

**1. `meta`** — sự kiện đầu tiên, đúng một lần.

```text
event: meta
data: {"intent": "...", "answer_source": "...", "rewritten_query": null}
```

Ngoại lệ duy nhất: nếu hỏng ngay ở bước chuẩn bị (`allowed_deck_ids` rỗng, index
chưa nạp xong) thì `error` là sự kiện DUY NHẤT của cả lượt — không có `meta`,
không có `citations`. Đừng viết code chờ `meta` rồi mới lắng nghe `error`.

`answer_source` ở đây là kết luận sớm, nhận một trong bốn giá trị `CANNED`,
`DIRECT_LOOKUP`, `CACHE`, `RAG`. **Không bao giờ là `LLM_ONLY`**: endpoint này
chỉ chép lại kết luận sớm của bước chuẩn bị, không có sẵn thì ghi thẳng `RAG` —
nó không hề xét `citations` rỗng hay không như `POST /chat` làm. Thấy `RAG` mà
sự kiện `citations` ngay sau đó là `[]` thì lượt này thực chất là `LLM_ONLY` —
câu trả lời bằng kiến thức chung, không dựa trên bộ thẻ.

**2. `citations`** — luôn đứng thứ hai, đúng một lần, và **luôn trước token đầu
tiên**. `data` là một **mảng** JSON (không phải object), có thể rỗng `[]`.

Vì sao phải trước: retrieval xong chỉ mất vài chục mili-giây, còn model thì mất
vài trăm mili-giây mới ra chữ đầu tiên. Phát nguồn ra ngay giúp giao diện dựng
xong danh sách thẻ trong lúc chờ, cảm giác nhanh hơn hẳn. Cái giá phải trả:
`used_in_answer` chưa thể biết nên nó là `false` ở mọi phần tử — trừ nhánh
`DIRECT_LOOKUP` và `CACHE`, nơi câu trả lời đã có sẵn từ trước. Muốn tô sáng
đúng thẻ nào được dùng, hãy tự dò mã `[#id]` trong chuỗi bạn đang gom.

**3. `token`** — không có, một, hoặc rất nhiều sự kiện.

```text
event: token
data: {"t": "mẩu chữ"}
```

Nối các `t` lại theo đúng thứ tự nhận được là ra `answer` hoàn chỉnh. **Đừng
trim từng mẩu**: khoảng trắng ở đầu/cuối mẩu là một phần của câu, trim xong sẽ
ra "Từngày" thay vì "Từ ngày".

Ba nhánh 0 token (`CANNED`, `DIRECT_LOOKUP`, `CACHE`) phát **đúng một** sự kiện
`token` chứa TRỌN câu trả lời, vì chẳng có gì để chờ. Giao diện phải xử lý được
cả hai trường hợp, đừng giả định luôn có nhiều mẩu.

**4. `done`** — sự kiện cuối cùng của một lượt thành công, đúng một lần.

```text
event: done
data: {"usage": {"prompt_tokens": 812, "completion_tokens": 96, "latency_ms": 1840}}
```

Hình dạng `usage` ở đây **khác** `POST /chat`: không có `provider`, không có
`model`.

`latency_ms` đo hai thứ khác nhau tuỳ nhánh, đừng gộp chung vào một biểu đồ:

- ba nhánh 0 token → đo TRỌN lượt, tính từ lúc thân endpoint bắt đầu chạy;
- nhánh gọi LLM → chỉ đo lời gọi streaming, KHÔNG gồm embed, tìm thẻ hay bước
  viết lại câu hỏi.

(Ở `POST /chat` thì `latency_ms` luôn chỉ đo lời gọi LLM, nên bằng 0 ở nhánh
miễn phí — ngược hẳn với ở đây.)

`prompt_tokens = 0` gần như luôn nghĩa là lượt miễn phí, nhưng sự kiện `done`
không kèm `answer_source` để đối chiếu như `POST /chat`: nếu Groq không gửi khối
`usage` (hay gặp khi stream bị cắt) thì một lượt đã tốn tiền cũng báo `0`. Muốn
chắc chắn thì nhìn `answer_source` ở sự kiện `meta` — chỉ `CANNED`,
`DIRECT_LOOKUP`, `CACHE` mới là lượt miễn phí thật.

**5. `error`** — thay cho tất cả những gì còn lại. Xuất hiện ở đúng ba chỗ:

1. **Ngay đầu lượt, chưa có `meta`.** Hỏng ở bước chuẩn bị: `allowed_deck_ids`
   rỗng, `scope_deck_id` ngoài phạm vi, index chưa nạp xong.
2. **Ngay sau `citations`, chưa có `token` nào.** `BUDGET_EXHAUSTED` và
   `PROVIDER_UNAVAILABLE` LUÔN rơi vào đây: cả hai được kiểm ở nhịp đầu tiên của
   lời gọi LLM, mà nhịp đó chỉ chạy sau khi `citations` đã phát đi.
3. **Chen vào giữa chuỗi `token`.** Đứt kết nối khi model đang sinh chữ.

```text
event: error
data: {"code": "INVALID_SCOPE", "message": "allowed_deck_ids không được rỗng."}
```

Trường hợp 2 là cái bẫy hay gặp nhất — thiếu `AI_LLM_API_KEY` là rơi thẳng vào
đó. Giao diện đã dựng xong danh sách nguồn rồi mới biết lượt này hỏng, nên phải
dọn danh sách đó đi, đừng để lại một khung "nguồn tham khảo" treo dưới một câu
trả lời không bao giờ tới.

Phát xong là stream đóng ngay. **Có `error` thì không bao giờ có `done`**, và
ngược lại.

Không có `id:`, không có heartbeat/ping. Mỗi sự kiện đúng chuẩn SSE:
`event: <tên>\ndata: <JSON một dòng>\n\n` (kết thúc bằng một dòng trống).
Phản hồi kèm `Cache-Control: no-cache` và `X-Accel-Buffering: no` để nginx ở
giữa không gom buffer làm mất tính streaming.

---

### Lỗi giữa chừng KHÔNG đến bằng HTTP status

Chỉ hai loại lỗi còn kịp trả bằng status thật, vì chúng xảy ra trước khi thân
endpoint chạy:

- **401 `UNAUTHORIZED`** — sai `X-Internal-Token`.
- **422** — body sai hình dạng (thiếu `query`, `role` không phải `user`/`assistant`…).

Tất cả những lỗi còn lại xảy ra **sau khi header `200 OK` đã gửi đi**. HTTP không
cho phép sửa status khi body đã bắt đầu chảy, nên chúng buộc phải đến dưới dạng
sự kiện `error`:

| `code` | Ở `POST /chat` là | Ở đây đến bằng |
|---|---|---|
| `INVALID_SCOPE` | 400 | sự kiện `error`, HTTP vẫn 200 |
| `INDEX_NOT_READY` | 503 | sự kiện `error`, HTTP vẫn 200 |
| `BUDGET_EXHAUSTED` | 429 | sự kiện `error`, HTTP vẫn 200 |
| `PROVIDER_UNAVAILABLE` | 503 | sự kiện `error`, HTTP vẫn 200 |

Mục **Responses** bên dưới liệt kê các mã đó vì nội dung `error` giống hệt, nhưng
đừng chờ chúng ở tầng status — trừ 401.

Hậu quả nếu code chỉ kiểm `status == 200`: một request `"allowed_deck_ids": []`
sẽ được coi là thành công, người dùng nhìn khung chat trống trơn và không có
thông báo nào. Quy tắc rút ra: **lượt chat chỉ thành công khi nhận được `done`.**

Lỗi cũng có thể đến sau vài chục sự kiện `token`, khi người dùng đã đọc được nửa
câu trả lời — ví dụ Groq đứt kết nối giữa chừng. Lúc đó hãy GIỮ phần chữ đã hiện
và nối thêm một dòng báo lỗi, đừng xoá trắng: người học vẫn dùng được nửa đầu.
Lưu ý luồng streaming **không thử lại và không hạ cấp sang model dự phòng** như
`POST /chat`, vì không thể rút lại những chữ đã phát ra.

Client tự ngắt kết nối giữa chừng thì câu trả lời dở dang **không** được ghi vào
cache, nhưng số token đã tiêu vẫn bị trừ vào ngân sách — đúng như vậy, vì Groq
đã tính tiền rồi.

---

### Ví dụ chạy thật bằng curl

```bash
curl -N -X POST http://localhost:8000/internal/v1/chat/stream \
  -H "X-Internal-Token: dev-token" \
  -H "Content-Type: application/json" \
  -d '{"query": "resilient dùng trong ngữ cảnh công sở thế nào", "allowed_deck_ids": [1]}'
```

`-N` là bắt buộc: thiếu nó curl gom hết vào buffer rồi mới in một lượt, nhìn y
như endpoint không hề streaming.

Đổi `query` thành `"resilient nghĩa là gì"` để xem nhánh 0 token — chạy được cả
khi chưa cấu hình `AI_LLM_API_KEY`.

---

### Ví dụ đọc stream bằng JavaScript

**Không dùng được `EventSource`**: nó chỉ biết gửi `GET` và không gắn được header
`X-Internal-Token`. Phải dùng `fetch` rồi tự tách sự kiện.

```js
const res = await fetch("http://localhost:8000/internal/v1/chat/stream", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Internal-Token": "dev-token",
  },
  body: JSON.stringify({
    query: "resilient dùng trong ngữ cảnh công sở thế nào",
    allowed_deck_ids: [1],
  }),
});

const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
let buffer = "";
let answer = "";

while (true) {
  const { value, done } = await reader.read();
  if (done) break;

  buffer += value;

  // Một sự kiện SSE kết thúc bằng dòng trống. Mẩu cuối có thể chưa đủ,
  // giữ lại chờ lần đọc sau — cắt bừa ở đây là JSON.parse nổ ngay.
  const parts = buffer.split("\n\n");
  buffer = parts.pop();

  for (const part of parts) {
    const name = part.match(/^event: (.*)$/m)[1];
    const data = JSON.parse(part.match(/^data: (.*)$/m)[1]);

    if (name === "meta") console.log("nhánh:", data.answer_source);
    if (name === "citations") console.log("nguồn:", data.length, "thẻ");
    if (name === "token") answer += data.t;
    if (name === "done") console.log("xong,", data.usage.prompt_tokens, "token");
    if (name === "error") console.error("hỏng:", data.code, data.message);
  }
}
```

Bên Java: dùng `WebClient` của Spring WebFlux với
`.retrieve().bodyToFlux(ServerSentEvent.class)`. `RestTemplate` KHÔNG dùng được
— nó đọc xong toàn bộ body mới trả về, tức là mất hết tính streaming.
""",
)
async def chat_stream(
    request: Request,
    body: Annotated[ChatRequest, Body(openapi_examples=VI_DU_CHAT)],
) -> StreamingResponse:
    service = request.app.state.service

    async def events():
        started = time.perf_counter()

        try:
            prepared = await service.orchestrator.prepare(
                query=body.query,
                allowed_deck_ids=body.allowed_deck_ids,
                scope_deck_id=body.scope_deck_id,
                history=[item.model_dump() for item in body.history],
                top_k=body.options.top_k,
                ready=service.is_ready,
            )

        except AppError as exc:
            yield sse("error", {"code": exc.code, "message": exc.message})
            return

        source = prepared.early_source or AnswerSource.RAG

        yield sse(
            "meta",
            {
                "intent": prepared.intent.value,
                "answer_source": source.value,
                "rewritten_query": prepared.rewritten_query,
            },
        )

        # SPEC muc 8.2: citations PHẢI phát trước token đầu tiên. Giao diện
        # hiện nguồn ngay, tạo cảm giác phản hồi nhanh hơn hẳn.
        yield sse("citations", [c.to_dict() for c in prepared.citations])

        if prepared.early_answer is not None:
            yield sse("token", {"t": prepared.early_answer})
            yield sse(
                "done",
                {
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                    }
                },
            )
            return

        pieces: list[str] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}

        try:
            async for chunk in service.llm.stream(
                task="CHAT",
                system=prepared.system_prompt,
                user=prepared.user_prompt,
                max_tokens=body.options.max_output_tokens,
                intent=prepared.intent.value,
                answer_source=AnswerSource.RAG.value,
            ):
                if chunk.done:
                    if chunk.usage:
                        usage = {
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                            "latency_ms": chunk.usage.latency_ms,
                        }

                    continue

                pieces.append(chunk.text)

                yield sse("token", {"t": chunk.text})

        except AppError as exc:
            yield sse("error", {"code": exc.code, "message": exc.message})
            return

        answer = "".join(pieces)

        mark_used_citations(answer, prepared.citations)

        service.cache.put(
            prepared.query_vector,
            prepared.scope,
            answer,
            [c.to_dict() for c in prepared.citations],
        )

        yield sse("done", {"usage": usage})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
