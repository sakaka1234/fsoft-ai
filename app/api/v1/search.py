"""
POST /internal/v1/search — tìm kiếm ngữ nghĩa. SPEC muc 8.3.

Hoàn toàn KHÔNG tốn token LLM. Phục vụ U019 và đồng thời là công cụ debug
retrieval tốt nhất.

Phần dài dòng phía dưới chỉ là metadata cho /docs: `summary`, `description`,
`responses` và các ví dụ. Không dòng nào trong đó chạm vào luồng xử lý — logic
của endpoint vẫn đúng ba bước như trước: kiểm phạm vi, kiểm trạng thái sẵn sàng,
gọi retriever.
"""

import time
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request

from app.api.deps import require_internal_token
from app.core.errors import IndexNotReady, InvalidScope
from app.schemas.errors import NOT_READY, SCOPE_ERRORS, UNAUTHORIZED
from app.schemas.search import (
    VI_DU_TIM_KIEM,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

router = APIRouter(
    prefix="/internal/v1",
    tags=["Internal - Search"],
    dependencies=[Depends(require_internal_token)],
)


# Số liệu trong các ví dụ này lấy từ lần chạy thật trên dữ liệu mẫu 24 thẻ,
# không phải bịa cho đẹp — bấm "Try it out" với đúng request tương ứng sẽ ra
# gần như y hệt (chỉ `latency_ms` là đổi theo máy).
_RESPONSE_EXAMPLES: dict = {
    "khop_chinh_xac": {
        "summary": "Gõ đúng một từ có trong bộ thẻ -> EXACT, score 1.0",
        "description": (
            "Request `tra_dung_tu_tieng_anh`. Chỉ một kết quả: tầng khớp chính xác đã "
            "trả lời dứt điểm nên không cần nghe hai tầng còn lại."
        ),
        "value": {
            "results": [
                {
                    "card_id": 101,
                    "word": "resilient",
                    "meaning": "kiên cường, có khả năng phục hồi nhanh",
                    "deck_id": 1,
                    "deck_title": "TOEIC - Cảm xúc & Tính cách",
                    "score": 1.0,
                    "match_type": "EXACT",
                }
            ],
            "latency_ms": 11,
            "candidate_count": 1,
        },
    },
    "cau_hoi_tieng_viet": {
        "summary": "Câu tiếng Việt mô tả nghĩa -> HYBRID + nhiễu LEXICAL",
        "description": (
            "Request `cau_hoi_tieng_viet`. Hai thẻ đầu là thứ người dùng cần. Hai thẻ "
            "sau là `LEXICAL`: `empathetic` lọt vào vì nghĩa của nó cũng chứa chữ "
            '"cảm", `impulsive` vì chứa chữ "trước". Đây chính là lý do nên hiển thị '
            "theo `match_type` chứ đừng hiển thị hết."
        ),
        "value": {
            "results": [
                {
                    "card_id": 102,
                    "word": "apprehensive",
                    "meaning": "lo lắng, e ngại về điều sắp xảy ra",
                    "deck_id": 1,
                    "deck_title": "TOEIC - Cảm xúc & Tính cách",
                    "score": 0.0325,
                    "match_type": "HYBRID",
                },
                {
                    "card_id": 108,
                    "word": "anxious",
                    "meaning": "lo âu, bồn chồn, bất an",
                    "deck_id": 1,
                    "deck_title": "TOEIC - Cảm xúc & Tính cách",
                    "score": 0.032,
                    "match_type": "HYBRID",
                },
                {
                    "card_id": 105,
                    "word": "empathetic",
                    "meaning": "thấu cảm, hiểu và chia sẻ cảm xúc người khác",
                    "deck_id": 1,
                    "deck_title": "TOEIC - Cảm xúc & Tính cách",
                    "score": 0.0161,
                    "match_type": "LEXICAL",
                },
                {
                    "card_id": 106,
                    "word": "impulsive",
                    "meaning": "bốc đồng, hành động không suy nghĩ trước",
                    "deck_id": 1,
                    "deck_title": "TOEIC - Cảm xúc & Tính cách",
                    "score": 0.0159,
                    "match_type": "LEXICAL",
                },
            ],
            "latency_ms": 13,
            "candidate_count": 4,
        },
    },
    "khong_co_ket_qua": {
        "summary": "Rỗng — bộ thẻ không chứa câu trả lời (vẫn là HTTP 200)",
        "description": (
            "Request `ngoai_pham_vi_cho_phep` hoặc `cau_ngoai_chu_de`. Thẻ 303 "
            "`deforestation` có tồn tại nhưng nằm ở deck 3, mà request chỉ cho phép deck "
            '1 và 2 — service không được phép nhìn thấy nó. Hiện "không tìm thấy trong '
            'bộ thẻ này", đừng lấp bằng thẻ ngẫu nhiên.'
        ),
        "value": {"results": [], "latency_ms": 10, "candidate_count": 0},
    },
}


def validate_scope(allowed_deck_ids: list[int], scope_deck_id: int | None) -> list[int]:
    """
    Toàn bộ ranh giới bảo mật của service nằm ở đây.

    Danh sách rỗng phải FAIL ĐÓNG — hiểu thành "không được phép gì", tuyệt đối
    không hiểu thành "không lọc". SPEC muc 11.3 gọi đây là lỗi kinh điển.
    """

    if not allowed_deck_ids:
        raise InvalidScope("allowed_deck_ids không được rỗng.")

    if scope_deck_id is None:
        return allowed_deck_ids

    if scope_deck_id not in allowed_deck_ids:
        raise InvalidScope(f"scope_deck_id={scope_deck_id} không nằm trong allowed_deck_ids.")

    return [scope_deck_id]


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Tìm kiếm ngữ nghĩa trong bộ thẻ, 0 token LLM",
    responses={
        200: {
            "description": (
                "Danh sách thẻ khớp, đã sắp xếp sẵn. **Danh sách rỗng cũng là 200** — "
                "xem ví dụ `khong_co_ket_qua`."
            ),
            "content": {"application/json": {"examples": _RESPONSE_EXAMPLES}},
        },
        **UNAUTHORIZED,
        **SCOPE_ERRORS,
        **NOT_READY,
    },
    description="""
Tìm thẻ liên quan tới câu hỏi, **chỉ trong những bộ thẻ mà bạn khai báo là người
dùng được phép đọc**.

### Endpoint này KHÔNG BAO GIỜ tốn token LLM

Không phải "thường thì rẻ" mà là **bằng 0 tuyệt đối**: trong code không có đường
nào dẫn tới Groq. Mọi thứ chạy trên RAM của chính service, nên nó cũng không bao
giờ trả `429 BUDGET_EXHAUSTED`, và ngân sách token cạn cũng không ảnh hưởng gì.

Hệ quả thực dụng:

- Gắn thẳng vào giao diện dưới dạng "tìm kiếm thông minh", gõ tới đâu tìm tới đó
  cũng được — khoảng 10ms một lượt, không tốn tiền.
- Đây cũng là chỗ soi lỗi `POST /internal/v1/chat` mà không đốt thêm token — xem
  mục ngay dưới.

### Dùng để dựng lại bước tìm thẻ của `/chat`

Khi chat trả lời lạ, gọi endpoint này để tách bạch lỗi nằm ở khâu tìm thẻ hay ở
khâu LLM diễn giải. Nhưng **gửi lại y nguyên `query` là chưa đủ** — chat không
phải lúc nào cũng tìm bằng đúng câu người dùng gõ. Chép đủ ba thứ:

| Gửi cho `/search` | Lấy từ đâu |
|---|---|
| `query` | `rewritten_query` trong phản hồi chat nếu nó khác `null`, còn không thì mới là `query` gốc. Có `history` là chat có thể đã nhờ model 8b viết lại câu hỏi rồi mới đi tìm |
| `allowed_deck_ids`, `scope_deck_id` | y hệt request chat |
| `top_k` | `options.top_k` của request chat — **bỏ trống thì là 3** (`AI_TOP_K`), không phải 5 |

Có ca không dựng lại được vì chat không hề tìm thẻ: `intent` trả về là
`GRAMMAR_QA`, `TRANSLATE`, `SMALLTALK` hay `OUT_OF_SCOPE` thì luồng chat bỏ qua
hẳn bước retrieval. `citations` rỗng ở những intent đó không nói lên điều gì về
việc bộ thẻ có chứa câu trả lời hay không.

### `allowed_deck_ids` là toàn bộ ranh giới bảo mật

Service không biết người dùng là ai. Không `profileId`, không phiên đăng nhập,
không bảng phân quyền — nó tin tuyệt đối vào danh sách deck mà bạn gửi lên.

- **Rỗng `[]` → `400 INVALID_SCOPE`**, KHÔNG phải "không lọc". Fail đóng là cố ý:
  một `List<Long>` quên gán mà bị hiểu thành "cho xem tất" thì lộ sạch bộ thẻ
  riêng tư của mọi người dùng. SPEC muc 11.3 xếp đây vào nhóm lỗi kinh điển.
- Thẻ ngoài danh sách **không bao giờ** xuất hiện, kể cả để làm nhiễu.
- Gửi thừa một deck của người khác thì thẻ của người đó hiện ra bình thường và
  service không có cách nào biết. Trách nhiệm tính danh sách này thuộc về backend.

`scope_deck_id` là chuyện khác hẳn: nó **thu hẹp thêm** bên trong phạm vi đã cho
phép, và không bao giờ mở rộng được.

| Bạn gửi | Service tìm trong |
|---|---|
| `allowed_deck_ids=[1,2,3,4]` | deck 1, 2, 3, 4 |
| `allowed_deck_ids=[1,2,3,4]`, `scope_deck_id=2` | **chỉ deck 2** |
| `allowed_deck_ids=[1,2]`, `scope_deck_id=3` | không tìm gì cả → **400** |

Dùng khi người học đang mở một bộ thẻ cụ thể: vẫn gửi đủ danh sách theo quyền,
cộng thêm `scope_deck_id` là deck đang mở.

### Service tìm bằng ba tầng, rồi trộn kết quả lại

| Tầng | Bắt được cái gì | `match_type` sinh ra |
|---|---|---|
| Khớp chính xác | Người dùng gõ đúng một từ có trong bộ thẻ | `EXACT` |
| BM25 (trùng chữ) | Trùng chữ **đúng từng ký tự** với `word`, phần nghĩa hoặc câu ví dụ | `LEXICAL` |
| Vector (gần nghĩa) | Câu hỏi diễn đạt vòng vo, không trùng chữ nào | `SEMANTIC` |

Lưu ý về sai chính tả: BM25 so token nguyên vẹn nên nó **không** đỡ được gõ sai.
Gõ `resiliant` vẫn ra `resilient` là nhờ tầng vector (`match_type: "SEMANTIC"`),
còn `deadlne` thì trả về rỗng — sai một ký tự ở từ ngắn là mất cả ba tầng.

Thẻ được **cả** BM25 lẫn vector tìm thấy thì mang nhãn `HYBRID` — bằng chứng mạnh
nhất trong nhóm không khớp chính xác. Đây là thứ nên đọc để quyết định hiển thị.
**Đừng lọc theo `score`**: nó là điểm RRF, không phải xác suất, và không so sánh
được giữa hai câu hỏi khác nhau (mô tả đầy đủ nằm ngay ở trường `score` bên dưới).

### Kết quả rỗng là kết quả ĐÚNG

`results: []` kèm HTTP 200 nghĩa là *bộ thẻ được phép xem không chứa câu trả lời*.
Đó không phải lỗi, không phải service hỏng, và **tăng `top_k` cũng không làm nó
hết rỗng**.

Sở dĩ có chuyện đó là vì tầng vector bị chặn bởi ngưỡng liên quan `AI_MIN_SCORE`
(mặc định `0.83`, hiệu chỉnh từ bộ đo `tests/eval/retrieval_golden.json`). Không
có ngưỡng ấy thì mọi câu hỏi đều trả về đủ `top_k` thẻ, kể cả câu hỏi chẳng liên
quan gì — hỏi "deforestation" trong bộ TOEIC sẽ nhận về 5 từ ngẫu nhiên trông rất
thuyết phục. Hiện "không tìm thấy trong bộ thẻ này" và dừng lại ở đó.

---

### Năm ví dụ trong ô "Request body"

Bấm **Try it out**, rồi chọn kịch bản ở menu thả xuống **Examples** ngay phía trên
ô Request body. Cả năm đều chạy được ngay với dữ liệu mẫu
(`AI_SOURCE_MODE=fixture`, 24 thẻ), không cần sửa gì.

**1. `tra_dung_tu_tieng_anh`** — người dùng gõ nguyên một từ vựng.

```json
{ "query": "resilient", "allowed_deck_ids": [1, 2, 3, 4], "top_k": 5 }
```

→ Đúng 1 kết quả: thẻ `101 resilient`, `match_type: "EXACT"`, `score: 1.0`. Tầng
khớp chính xác đã chắc chắn nên hai tầng kia không có gì để thêm.

**2. `cau_hoi_tieng_viet`** — người dùng quên mất từ, chỉ tả được nghĩa.

```json
{ "query": "từ nào diễn tả cảm giác lo lắng trước kỳ thi", "allowed_deck_ids": [1], "top_k": 5 }
```

→ `102 apprehensive` và `108 anxious` với `HYBRID`, kèm hai thẻ `LEXICAL` là nhiễu
(`105 empathetic`, `106 impulsive`). Câu hỏi không chứa từ tiếng Anh nào nên tầng
khớp chính xác im lặng hoàn toàn; BM25 vớt được nhờ chữ "lo lắng" nằm trong phần
nghĩa tiếng Việt của thẻ, còn tầng vector mới là thứ xếp đúng thứ tự.

**3. `ngoai_pham_vi_cho_phep`** — từ **có thật** nhưng nằm ngoài quyền.

```json
{ "query": "deforestation", "allowed_deck_ids": [1, 2], "top_k": 5 }
```

→ `results: []`. Thẻ `303 deforestation` tồn tại trong index nhưng thuộc deck 3,
mà request chỉ cho phép deck 1 và 2. Đổi thành `"allowed_deck_ids": [1, 2, 3]` là
thấy nó ngay — đây là cách kiểm tra ranh giới bảo mật trong 10 giây.

**4. `thu_hep_bang_scope_deck_id`** — người học đang mở deck 2.

```json
{ "query": "deadline", "allowed_deck_ids": [1, 2, 3, 4], "top_k": 5, "scope_deck_id": 2 }
```

→ Chỉ tìm trong deck 2, trả về thẻ `201 deadline` với `EXACT`. Đổi `scope_deck_id`
thành `9` (không nằm trong danh sách cho phép) sẽ nhận `400 INVALID_SCOPE`.

**5. `cau_ngoai_chu_de`** — câu hỏi chẳng dính gì tới bộ thẻ.

```json
{ "query": "cách nấu phở bò", "allowed_deck_ids": [1, 2, 3, 4], "top_k": 5 }
```

→ `results: []` dù đã cho phép cả 24 thẻ. Không thẻ nào vượt ngưỡng liên quan, và
service chọn im lặng thay vì bịa.

---

### Lỗi có thể gặp

| HTTP | `code` | Nguyên nhân |
|---|---|---|
| 400 | `INVALID_SCOPE` | `allowed_deck_ids` rỗng, hoặc `scope_deck_id` nằm ngoài danh sách |
| 401 | `UNAUTHORIZED` | Thiếu/sai `X-Internal-Token`. Bấm **Authorize** ở góc trên |
| 422 | *(không có)* | `top_k` ngoài khoảng 1–50, thiếu trường bắt buộc. Dạng `{"detail": [...]}`, khác các lỗi trên |
| 503 | `INDEX_NOT_READY` | Model chưa nạp xong. Đợi `/readyz` trả 200, thường vài giây sau khi khởi động |

Không có `429` ở đây: endpoint không tiêu token nên không đụng tới ngân sách.

Phạm vi deck được kiểm **trước** trạng thái sẵn sàng, nên request sai phạm vi luôn
nhận `400` kể cả lúc service vừa khởi động.
""",
)
async def search(
    request: Request,
    body: Annotated[SearchRequest, Body(openapi_examples=VI_DU_TIM_KIEM)],
) -> SearchResponse:
    service = request.app.state.service

    deck_ids = validate_scope(body.allowed_deck_ids, body.scope_deck_id)

    if not service.is_ready:
        raise IndexNotReady("Model chưa nạp xong hoặc index chưa sẵn sàng.")

    started = time.perf_counter()

    query_vector = await service.encoder.embed_query(body.query)

    hits = service.retriever.retrieve(
        query=body.query,
        query_vector=query_vector,
        allowed_deck_ids=deck_ids,
        top_k=body.top_k,
    )

    return SearchResponse(
        results=[
            SearchResult(
                card_id=hit.card.card_id,
                word=hit.card.word,
                meaning=hit.card.meaning,
                deck_id=hit.card.deck_id,
                deck_title=hit.card.deck_title,
                score=round(hit.score, 4),
                match_type=hit.match_type,
            )
            for hit in hits
        ],
        latency_ms=int((time.perf_counter() - started) * 1000),
        candidate_count=len(hits),
    )
