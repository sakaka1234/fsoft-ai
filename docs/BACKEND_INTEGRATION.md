# fsoft-ai — Hướng dẫn tích hợp cho backend Java

> **Đối tượng:** đội backend Java (Spring Boot).
> **Bạn không cần đọc `SPEC.md`.** Tài liệu này tự chứa mọi thứ để tích hợp.
>
> **Trạng thái ngày 23/08/2026: M1 đến M10 đã xong và service đang chạy thật.** Mọi ví dụ
> request/response trong tài liệu này là **gọi thật vào service đang chạy**, không có ví dụ
> nào viết tay. Chỗ nào là số đo thì có ghi rõ.
>
> Swagger tương tác: <https://fsoft-ai.onrender.com/docs> — bấm **Authorize**, dán token, mỗi
> endpoint có sẵn nhiều kịch bản trong menu **Examples**, bấm **Execute** là chạy thật.

---

## Mục lục

1. [Ba việc backend cần làm](#1-ba-việc-backend-cần-làm)
2. [Ranh giới trách nhiệm](#2-ranh-giới-trách-nhiệm)
3. [Kết nối và hai token](#3-kết-nối-và-hai-token)
4. [Backend PHẢI cung cấp: hai endpoint nội bộ](#4-backend-phải-cung-cấp-hai-endpoint-nội-bộ)
5. [fsoft-ai cung cấp gì](#5-fsoft-ai-cung-cấp-gì)
6. [Định dạng lỗi](#6-định-dạng-lỗi)
7. [Giới hạn vận hành phải thiết kế theo](#7-giới-hạn-vận-hành-phải-thiết-kế-theo)
8. [Chạy thử ở máy local](#8-chạy-thử-ở-máy-local)
9. [Checklist bàn giao](#9-checklist-bàn-giao)
10. [Hạn chế đã biết](#10-hạn-chế-đã-biết)

---

## 1. Ba việc backend cần làm

| # | Việc | Ước lượng | Mục |
|---|---|---|---|
| 1 | Làm **hai endpoint nội bộ** để fsoft-ai kéo dữ liệu thẻ về | ~60 dòng Java | [mục 4](#4-backend-phải-cung-cấp-hai-endpoint-nội-bộ) |
| 2 | Sinh và trao đổi **hai token** | 10 phút | [mục 3](#3-kết-nối-và-hai-token) |
| 3 | Gọi fsoft-ai từ backend | tuỳ tính năng | [mục 5](#5-fsoft-ai-cung-cấp-gì) |

**Việc số 1 là gấp nhất.** Service hiện đang chạy ở chế độ `source_mode: fixture`, tức trả lời
bằng **24 thẻ mẫu** chứ không phải dữ liệu thật. Kiểm bất cứ lúc nào:

```bash
curl -H "X-Internal-Token: $TOKEN_A" \
  https://fsoft-ai.onrender.com/internal/v1/index/status
```

```json
{"card_count": 24, "index_size": 24, "model_version": "e5-small-q8-tia113k@t1",
 "last_sync_error": null, "backend_reachable": true, "source_mode": "fixture"}
```

Còn thấy `"source_mode": "fixture"` nghĩa là mọi câu trả lời đang dựa trên 24 thẻ mẫu đó.
(`backend_reachable: true` trong đoạn trên **không** có nghĩa là đã nối được với các bạn — ở chế
độ `fixture` thì "nguồn" chỉ là một tệp cục bộ, xem [5.1](#51-vận-hành-và-đồng-bộ).)

### Tích hợp có HAI chiều, và chiều dữ liệu mới là chiều quan trọng

```
   chiều 1   Backend Java  ──gọi──►  fsoft-ai        (chat, search, quiz)
   chiều 2   fsoft-ai  ──kéo thẻ──►  Backend Java    (đồng bộ mỗi 120 giây)
```

Làm xong chiều 1 thì API gọi được ngay, nhưng fsoft-ai **vẫn trả lời bằng 24 thẻ mẫu** cho tới
khi chiều 2 được bật. Đây là chỗ dễ tưởng đã xong nhất.

**Triệu chứng hôm nay nếu backend gửi `deck_id` / `card_ids` thật.** Index hiện chỉ chứa đúng
những ID của bộ mẫu:

```
deckId 1: 101-109      deckId 3: 301-304
deckId 2: 201-208      deckId 4: 401-403
```

Gọi bằng ID thật, đo trên service đang chạy:

| Gọi gì | Nhận về |
|---|---|
| `quiz/generate` với `deck_id: 77` | `400 INVALID_REQUEST` — "Bộ thẻ chỉ có **0 thẻ** trong phạm vi, cần ít nhất 4 thẻ..." |
| `search` với `allowed_deck_ids: [77]` | `200` với `{"results": [], "candidate_count": 0}` |

Cái thứ hai nguy hiểm hơn: nó **không báo lỗi**, chỉ trả rỗng, nên trông y hệt "không có thẻ
nào liên quan" chứ không phải "chưa có dữ liệu". Thấy `results` luôn rỗng với mọi truy vấn thì
việc đầu tiên phải kiểm là `source_mode`, đừng đi chỉnh câu truy vấn.

**Bật chiều 2:** đặt ba biến này ở phía fsoft-ai rồi ép đồng bộ một lần.

```
AI_SOURCE_MODE=http
AI_BACKEND_URL=https://<backend>/fsoft
AI_BACKEND_TOKEN=<TOKEN_B>
```

```bash
curl -X POST -H "X-Internal-Token: $TOKEN_A"   "https://fsoft-ai.onrender.com/internal/v1/index/sync?full=true&sweep=true"
```

Đạt khi `source_mode` thành `"http"`, `card_count` khớp số thẻ thật, và `last_sync_error: null`.

---

## 2. Ranh giới trách nhiệm

**fsoft-ai là một cỗ máy RAG thuần tuý cho từ vựng. Nó không biết gì về người dùng.**

Không JWT. Không role. Không quota theo user. Không logic phân quyền. Không bảng hội thoại.

Nó nhận `allowed_deck_ids` như một tham số phạm vi và làm việc trong đó. **Backend Java
quyết định danh sách ấy.**

| Trách nhiệm | Ai làm |
|---|---|
| Xác thực JWT, lấy `profile_id` | **Backend Java** |
| Quyết định user đọc được deck nào | **Backend Java** |
| Quota theo user, phân biệt gói FREE/PRO | **Backend Java** |
| Lưu hội thoại, tin nhắn, lịch sử | **Backend Java** |
| Vòng đời job quiz, lịch SRS | **Backend Java** |
| Embedding, vector index | fsoft-ai |
| Retrieval, phân loại intent | fsoft-ai |
| Gọi LLM, quản ngân sách token | fsoft-ai |
| Sinh câu hỏi quiz | fsoft-ai |
| Trích từ vựng từ đoạn văn | fsoft-ai |
| **Lưu thẻ vào deck sau khi người dùng chọn** | **Backend Java** |

**Vì sao chia thế này:** logic phân quyền đã tồn tại trong Java. Viết lại bằng Python
nghĩa là hai bản logic có thể lệch nhau — và khi lệch thì hậu quả là lộ bộ thẻ riêng tư
của người khác. Một nguồn sự thật duy nhất.

> ⚠️ **Hệ quả bắt buộc:** mọi request gọi vào fsoft-ai **phải** kèm `allowed_deck_ids`
> đã được backend tính đúng theo quyền của user đang đăng nhập. fsoft-ai tin tuyệt đối
> vào danh sách này. Truyền thừa một deck là để người dùng đọc được thẻ của người khác, và
> service **không có cách nào phát hiện**.
>
> Danh sách **rỗng** nghĩa là "không được phép gì" chứ không phải "không lọc" — fsoft-ai
> trả `400 INVALID_SCOPE`, không bao giờ hiểu thành "tất cả".

Thiết kế fail-closed, đã có test riêng cho từng nhánh. Response thật:

```json
// allowed_deck_ids: []
{"error":{"code":"INVALID_SCOPE","message":"allowed_deck_ids không được rỗng."}}

// scope_deck_id: 99 kèm allowed_deck_ids: [1,2,3]
{"error":{"code":"INVALID_SCOPE","message":"scope_deck_id=99 không nằm trong allowed_deck_ids."}}
```

`scope_deck_id` là để lọc **thêm** (người dùng đang mở một bộ thẻ cụ thể), không phải để thay
`allowed_deck_ids`. Luôn gửi cả hai.

---

## 3. Kết nối và hai token

### 3.1 Địa chỉ

| | Giá trị |
|---|---|
| Base URL hiện tại | `https://fsoft-ai.onrender.com` |
| Kiểu chữ trong JSON | `snake_case` (khác backend Java dùng `camelCase`) |
| Swagger | `https://fsoft-ai.onrender.com/docs` |
| Content-Type | `application/json; charset=utf-8` |

> **URL này đang là public** vì Render free không có mạng nội bộ. Đó là một đánh đổi tạm
> thời: bảo vệ duy nhất hiện nay là `X-Internal-Token`. Khi chuyển sang Railway (hoặc cùng
> VPC), địa chỉ đúng phải là nội bộ `http://fsoft-ai.railway.internal:8000` và service
> **không** gán public domain.
>
> Frontend **không bao giờ** gọi thẳng fsoft-ai, kể cả khi URL là public. Mọi thứ đi qua
> backend Java — nếu không thì `TOKEN_A` phải nằm trong code frontend.

### 3.2 Hai chiều gọi nhau, hai token khác nhau

Đây là chỗ dễ nhầm nhất: cả hai chiều dùng **cùng tên header** `X-Internal-Token`, nhưng
**giá trị khác nhau**.

```
   Backend Java  ──── X-Internal-Token: <TOKEN_A> ────►  fsoft-ai
   (hỏi đáp chat, search, quiz)                          fsoft-ai KIỂM token A

   fsoft-ai  ──── X-Internal-Token: <TOKEN_B> ────►  Backend Java
   (kéo dữ liệu thẻ về để index)                     Java KIỂM token B
```

| Token | Biến môi trường phía fsoft-ai | Ai **kiểm** | Ai **gửi** | Ai nên **sinh** |
|---|---|---|---|---|
| A | `AI_INTERNAL_TOKEN` | fsoft-ai | Backend Java | Đội fsoft-ai, rồi đưa cho Java |
| B | `AI_BACKEND_TOKEN` | Backend Java | fsoft-ai | Đội Java, rồi đưa cho fsoft-ai |

Quy tắc dễ nhớ: **bên nào kiểm token thì bên đó sinh token**, rồi đưa cho bên kia.

```bash
openssl rand -hex 32
# hoặc
python -c "import secrets; print(secrets.token_hex(32))"
```

Token phải là **ASCII**. Ký tự ngoài ASCII trả `401` (header HTTP giải mã bằng latin-1), không
phải `500` — đã có test ghim hành vi này.

### 3.3 ⚠️ Vì sao TOKEN_B nguy hiểm hơn hẳn

Hai endpoint ở [mục 4](#4-backend-phải-cung-cấp-hai-endpoint-nội-bộ) **bỏ qua toàn bộ
logic phân quyền deck** — chúng trả về mọi thẻ của mọi người dùng, kể cả deck `PRIVATE`.
Đó là chủ ý: fsoft-ai cần index tất cả, việc lọc theo quyền diễn ra lúc truy vấn.

Nghĩa là **TOKEN_B là chìa khoá vạn năng đọc toàn bộ bộ thẻ riêng tư của mọi người dùng.**

Vì vậy:

- Tối thiểu 32 byte ngẫu nhiên
- **Không dùng chung một token cho cả hai chiều.** Lộ một đầu không được kéo theo đầu kia,
  và phải đổi được token nguy hiểm hơn mà không đụng phía còn lại
- Đặt thẳng vào biến môi trường của nền tảng. **Không commit, không gửi qua chat**
- `/internal/**` phải nằm ngoài filter JWT, dùng filter kiểm token riêng

---

## 4. Backend PHẢI cung cấp: hai endpoint nội bộ

Đặt dưới `/fsoft/internal/**`. Xác thực bằng header `X-Internal-Token` (= TOKEN_B),
**không dùng JWT**.

### 4.1 `GET /fsoft/internal/cards/changed-since`

Feed gia tăng. fsoft-ai gọi mỗi 120 giây.

**Query params**

| Tham số | Kiểu | Bắt buộc | Ghi chú |
|---|---|---|---|
| `since` | ISO-8601 UTC có hậu tố `Z` | không | Bỏ trống nghĩa là "tất cả". VD `2026-08-20T03:00:00Z` |
| `page` | int | không | Mặc định `1`, **1-based** |
| `size` | int | không | Mặc định `200`, tối đa `500`. fsoft-ai gửi `200` |

**Truy vấn**

```sql
SELECT c.*, d.title AS deck_title
FROM card c
JOIN deck d ON d.id = c.deck_id
WHERE :since IS NULL OR c.updated_at >= :since
ORDER BY c.updated_at ASC, c.id ASC
```

**Response** — theo đúng quy ước `ApiResponse<PageResponse<T>>` sẵn có:

```json
{
  "status": 200,
  "message": "Success",
  "data": {
    "content": [
      {
        "cardId": 101,
        "deckId": 1,
        "deckTitle": "TOEIC - Cảm xúc & Tính cách",
        "word": "resilient",
        "phonetic": "/rɪˈzɪliənt/",
        "partOfSpeech": "adj",
        "meaning": "kiên cường, có khả năng phục hồi nhanh",
        "definitionEn": "able to recover quickly from difficult conditions",
        "exampleSentence": "She remained resilient despite repeated setbacks.",
        "exampleMeaning": "Cô ấy vẫn kiên cường dù liên tục gặp thất bại.",
        "audioUrl": "https://cdn.example.com/audio/resilient.mp3",
        "note": null,
        "updatedAt": "2026-08-20T03:00:00Z"
      }
    ],
    "pageNo": 1,
    "pageSize": 200,
    "totalElements": 24,
    "totalPages": 1,
    "last": true
  }
}
```

**Không** trả `imageUrl`, `position`, `createdAt` — fsoft-ai không dùng.
**Có** trả `audioUrl` vì quiz dạng nghe cần.

### 4.2 `GET /fsoft/internal/cards/ids`

Dùng để phát hiện thẻ bị xoá. fsoft-ai gọi mỗi 60 phút, và một lần lúc khởi động.

**Query params:** `page` (mặc định `1`), `size` (mặc định `5000`, tối đa `10000`).
fsoft-ai gửi `size=5000`.

```sql
SELECT c.id FROM card c ORDER BY c.id ASC
```

```json
{
  "status": 200,
  "message": "Success",
  "data": {
    "content": [101, 102, 103, 104],
    "pageNo": 1, "pageSize": 5000,
    "totalElements": 24, "totalPages": 1, "last": true
  }
}
```

`content` là **mảng số nguyên trần**, không phải mảng object.

### 4.3 Bốn cái bẫy, sai là hỏng âm thầm

**1. `ORDER BY updated_at ASC, id ASC` — thiếu tie-break bằng `id` là mất bản ghi.**
Nhiều thẻ có cùng `updated_at` mà không có thứ tự phụ thì phân trang không ổn định:
cùng một thẻ có thể xuất hiện ở hai trang, hoặc không xuất hiện ở trang nào.
Cách kiểm: tạo 300 thẻ cùng `updated_at`, phân trang `size=100`, phải ra đủ 300, không
trùng không sót.

**2. Dùng `>=` chứ không phải `>`.** fsoft-ai cố ý lùi mốc thời gian 5 giây và tự khử
trùng bằng hash nội dung, nên kéo trùng là vô hại còn bỏ sót thì mất vĩnh viễn.

**3. `updatedAt` phải là UTC ISO-8601 có hậu tố `Z`.** Trả về giờ địa phương không kèm
offset sẽ làm hỏng con trỏ đồng bộ — fsoft-ai lấy `max(updatedAt)` trong phản hồi làm mốc
cho lần kéo sau, nên lệch múi giờ nghĩa là bỏ sót hoặc kéo lại toàn bộ mỗi 2 phút.

**4. Phải trả cả thẻ thuộc deck `PRIVATE` của người khác.** Nếu bạn "cẩn thận" thêm bộ lọc
quyền vào đây thì AI Tutor sẽ mù với chính bộ thẻ người dùng đang học. Việc lọc quyền diễn
ra ở chỗ khác, lúc truy vấn.

### 4.4 fsoft-ai hành xử thế nào khi endpoint lỗi

| Tình huống | HTTP | fsoft-ai làm gì |
|---|---|---|
| Thiếu hoặc sai `X-Internal-Token` | `401` | **Không retry**, log lỗi, thử lại ở chu kỳ sau |
| `since` sai định dạng | `400` | Log ERROR — đây là bug của fsoft-ai, báo lại cho đội AI |
| Backend đang khởi động lại | `5xx` | Backoff mũ 1s → 2s, tối đa 3 lần rồi bỏ qua chu kỳ |

Timeout mỗi request: **20 giây**. fsoft-ai **không bao giờ** để lỗi đồng bộ làm sập chính
nó — đồng bộ hỏng chỉ có nghĩa là index cũ đi.

### 4.5 Acceptance cho phía Java

- [ ] Gọi không có `X-Internal-Token` → `401`
- [ ] Gọi có token đúng, **không** có JWT → `200`
- [ ] Trả về cả thẻ thuộc deck `PRIVATE` của người dùng khác
- [ ] `since` bỏ trống → trả toàn bộ thẻ, phân trang đúng
- [ ] `since` sát nút → chỉ trả thẻ mới cập nhật
- [ ] 300 thẻ cùng `updated_at`, phân trang `size=100` → đủ 300, không trùng không sót
- [ ] `updatedAt` là UTC ISO-8601 có hậu tố `Z`
- [ ] `/internal/cards/ids` trả mảng số nguyên trần
- [ ] Cấu trúc phản hồi khớp `tests/fixtures/cards.json` của fsoft-ai

> **File đối chiếu:** `tests/fixtures/cards.json` trong repo fsoft-ai chứa đúng 24 bản ghi
> mà `content` phải trả về, đúng shape. Dùng nó làm bài test so sánh.
>
> Những gì đội AI đã đối chiếu trên bản endpoint hiện tại của các bạn, và 5 vấn đề dữ liệu
> tìm thấy, nằm ở [`BACKEND_FEEDBACK.md`](BACKEND_FEEDBACK.md).

---

## 5. fsoft-ai cung cấp gì

### 5.1 Vận hành và đồng bộ

| Method | Đường dẫn | Cần token | Công dụng |
|---|---|:---:|---|
| `GET` | `/healthz` | không | Tiến trình còn sống không. Luôn `200` nếu còn chạy |
| `GET` | `/readyz` | không | Model nạp xong và index sẵn sàng chưa. `503` khi chưa |
| `GET` | `/internal/v1/index/status` | có | Trạng thái đồng bộ và index |
| `POST` | `/internal/v1/index/sync` | có | Ép đồng bộ ngay, không đợi hết 120 giây |
| `GET` | `/internal/v1/stats` | có | Số liệu token, độ trễ, phân bố `answer_source` |

**Dùng cái nào cho health check của nền tảng:** `/healthz`. Đừng dùng `/readyz` — nó trả `503`
trong những giây đầu lúc nạp model, nền tảng sẽ tưởng deploy hỏng và restart thành vòng lặp.

**`GET /readyz`** — không cần token, dùng để biết khi nào bắt đầu gọi được `/internal/*`:

```json
{"ready": true, "encoder_ready": true, "index_ready": true,
 "model_version": "e5-small-q8-tia113k@t1"}
```

`model_version` ở đây là cách nhanh nhất để biết **bản mới đã deploy chưa** mà không cần token.

**`GET /internal/v1/index/status`** — response thật:

```json
{
  "card_count": 24,
  "index_size": 24,
  "model_version": "e5-small-q8-tia113k@t1",
  "last_sync_ts": "2026-08-20T06:00:00Z",
  "last_sync_at": "2026-08-17T16:01:23.233232Z",
  "last_sync_duration_ms": 1,
  "last_sync_embedded": 0,
  "last_sync_skipped": 3,
  "last_full_sweep_at": "2026-08-17T15:53:23.224146Z",
  "last_sync_error": null,
  "backend_reachable": true,
  "source_mode": "fixture"
}
```

Bốn field đáng theo dõi khi ghép hệ thống:

| Field | Ý nghĩa |
|---|---|
| `source_mode` | `fixture` = đang dùng 24 thẻ mẫu. Phải thành `http` khi nối thật |
| `backend_reachable` | **Chỉ có nghĩa khi `source_mode` là `http`.** Lúc đó `false` = fsoft-ai không gọi được endpoint của bạn. Ở chế độ `fixture` nó vẫn báo `true` vì "nguồn" chỉ là một tệp cục bộ — đừng đọc nó như bằng chứng đã nối thật |
| `last_sync_error` | Nội dung lỗi của chu kỳ gần nhất, `null` là đang lành |
| `card_count` | Phải khớp số thẻ thật trong MySQL sau khi đồng bộ xong |

**`POST /internal/v1/index/sync`**

| Query param | Mặc định | Công dụng |
|---|---|---|
| `sweep` | `false` | Quét thêm danh sách ID để phát hiện thẻ đã bị xoá |
| `full` | `false` | Bỏ qua con trỏ, kéo lại toàn bộ. Vẫn rẻ vì thẻ không đổi thì không embed lại |

```json
{ "embedded": 24, "skipped": 0, "deleted": 0, "duration_ms": 1049, "error": null }
```

Sau khi nối thật lần đầu, gọi `POST /internal/v1/index/sync?sweep=true&full=true` rồi theo dõi
`card_count` cho tới khi khớp số thẻ thật.

**`GET /internal/v1/stats?from=&to=`** — bỏ trống thì mặc định 24 giờ gần nhất, cửa sổ nửa mở
`[from, to)`. Response thật:

```json
{
  "since": "2026-08-01T00:00:00Z", "until": "2026-09-01T00:00:00Z",
  "calls": 14, "chat_turns": 8,
  "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
  "by_task": {"CHAT": 8, "QUIZ": 6},
  "by_answer_source": {"CANNED": 1, "DIRECT_LOOKUP": 1, "RAG": 6},
  "free_ratio":          {"value": 0.25,   "target": 0.4,    "ok": false, "comparison": ">="},
  "avg_tokens_per_chat": {"value": 0.0,    "target": 1200.0, "ok": true,  "comparison": "<"},
  "latency_p95_ms":      {"value": 4104.0, "target": 3000.0, "ok": false, "comparison": "<"},
  "error_rate":          {"value": 0.8571, "target": 0.02,   "ok": false, "comparison": "<"},
  "all_targets_met": false
}
```

Endpoint này để dựng dashboard hoặc lấy số cho báo cáo. **Đừng gọi trong luồng người dùng.**
Nó cũng là chỗ duy nhất thấy được token của bước viết lại câu hỏi — xem [5.3](#53-post-internalv1chat--hỏi-đáp).

### 5.2 `POST /internal/v1/search` — tìm kiếm, 0 token

Không gọi LLM, nên **không có trần rate limit** và độ trễ phía server chỉ vài mili-giây. Nên
gắn thẳng vào giao diện dưới dạng "tìm kiếm thông minh".

```json
{ "query": "lo lắng bồn chồn", "allowed_deck_ids": [1,2,3,4], "top_k": 3, "scope_deck_id": null }
```

`top_k` mặc định 5, khoảng 1–50. Response thật:

```json
{
  "results": [
    { "card_id": 108, "word": "anxious", "meaning": "lo âu, bồn chồn, bất an",
      "deck_id": 1, "deck_title": "TOEIC - Cảm xúc & Tính cách",
      "score": 0.0328, "match_type": "HYBRID" },
    { "card_id": 102, "word": "apprehensive", "meaning": "lo lắng, e ngại về điều sắp xảy ra",
      "deck_id": 1, "deck_title": "TOEIC - Cảm xúc & Tính cách",
      "score": 0.0161, "match_type": "LEXICAL" }
  ],
  "latency_ms": 7, "candidate_count": 2
}
```

> **`score` không phải phần trăm và không so sánh được giữa hai lần gọi.** Nó là điểm RRF
> (reciprocal rank fusion), chỉ có ý nghĩa **thứ tự** trong cùng một response. Đừng hiển thị
> "khớp 3%", đừng dựng ngưỡng lọc trên nó. Ngoại lệ duy nhất: `match_type: EXACT` luôn có
> `score: 1.0`.

`match_type` cho biết vì sao thẻ được chọn: `EXACT` (trùng đúng từ), `LEXICAL` (trùng chữ),
`SEMANTIC` (gần nghĩa), `HYBRID` (cả hai tầng đều tìm ra).

**Kết quả rỗng là câu trả lời đúng, không phải lỗi.** Đo thật:

```
query "cách nấu phở bò"  ->  {"results": [], "candidate_count": 0}
query "photosynthesis"   ->  {"results": [], "candidate_count": 0}
```

Đừng retry, đừng hiển thị thẻ ngẫu nhiên thay thế. Hãy hiện "không tìm thấy từ nào trong bộ
thẻ của bạn".

### 5.3 `POST /internal/v1/chat` — hỏi đáp

```json
{ "query": "cho tôi ví dụ với từ resilient",
  "allowed_deck_ids": [1, 2, 4],
  "scope_deck_id": 1,
  "history": [{ "role": "user", "content": "resilient nghĩa là gì?" }],
  "options": { "top_k": 3, "max_output_tokens": 500 } }
```

`history` và `options` không bắt buộc. Backend cắt sẵn `history`, tối đa 6 phần tử — service
cũng tự cắt, nhưng gửi nhiều là tốn token vô ích.

`answer_source` là trường quan trọng nhất: nó cho biết lượt này có tốn token hay không, và câu
trả lời dựa trên đâu. **Ba trong năm giá trị là miễn phí.**

| `answer_source` | Token | Khi nào |
|---|---|---|
| `DIRECT_LOOKUP` | **0** | Tra từ khớp chính xác — dựng thẳng từ thẻ |
| `CANNED` | **0** | Chào hỏi hoặc câu ngoài chủ đề học tiếng Anh |
| `CACHE` | **0** | Câu hỏi tương tự đã hỏi trước đó, cùng phạm vi |
| `RAG` | có | LLM trả lời **dựa trên thẻ** trong `citations` |
| `LLM_ONLY` | có | LLM trả lời bằng kiến thức chung, `citations` **rỗng** |

Ba response thật, ba nhánh khác nhau:

```json
// DIRECT_LOOKUP — 0 token
{"answer": "**sustainable** /səˈsteɪnəbl/ (adj)\nNghĩa: bền vững...[#301]",
 "intent": "VOCAB_LOOKUP", "answer_source": "DIRECT_LOOKUP", "rewritten_query": null,
 "citations": [{"card_id": 301, "word": "sustainable", "deck_id": 3, "score": 1.0,
                "rank": 1, "used_in_answer": true}],
 "usage": {"provider": "groq", "model": null,
           "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}}
```

```json
// RAG — có gọi LLM, trích dẫn thẻ
{"answer": "**Giải thích:** \"Resilient\" (adj) mô tả người có khả năng phục hồi nhanh...\n**Ví dụ trong thẻ:** She remained resilient despite repeated setbacks. [#101]\n**Ví dụ khác:** ... (kiến thức chung, không lấy từ thẻ).",
 "intent": "EXAMPLE_REQUEST", "answer_source": "RAG",
 "citations": [{"card_id": 101, "word": "resilient", "deck_id": 1, "score": 1.0,
                "rank": 1, "used_in_answer": true}],
 "usage": {"provider": "groq", "model": "openai/gpt-oss-120b",
           "prompt_tokens": 537, "completion_tokens": 328, "latency_ms": 2301}}
```

```json
// LLM_ONLY — ngoài phạm vi, từ chối, citations rỗng
{"answer": "Xin lỗi, tôi chỉ có thể hỗ trợ các câu hỏi liên quan đến việc học tiếng Anh...",
 "intent": "VOCAB_LOOKUP", "answer_source": "LLM_ONLY", "citations": [],
 "usage": {"provider": "groq", "model": "openai/gpt-oss-120b",
           "prompt_tokens": 503, "completion_tokens": 117, "latency_ms": 772}}
```

Hai điều về `answer`:

- Nó là **Markdown**, và chứa mã trích dẫn `[#101]` trùng `card_id` trong `citations`. Giao
  diện nên đổi `[#101]` thành link tới thẻ. Không muốn hiện thì phải tự bỏ — service không có
  tuỳ chọn tắt.
- **`usage.prompt_tokens` không đếm bước viết lại câu hỏi.** Nếu `rewritten_query` khác `null`
  thì đã tốn thêm khoảng 150 token của `openai/gpt-oss-20b` mà không hiện ở đâu trong
  response. Nghĩa là một lượt `DIRECT_LOOKUP` với `prompt_tokens: 0` vẫn có thể đã tiêu 150
  token. Số đầy đủ nằm ở `GET /internal/v1/stats`.

### 5.4 `POST /internal/v1/chat/stream` — SSE

Cùng request body như `POST /chat`, trả `text/event-stream`. Transcript **thật**, cắt phần
giữa:

```
event: meta
data: {"intent": "EXAMPLE_REQUEST", "answer_source": "RAG", "rewritten_query": null}

event: citations
data: [{"card_id": 107, "word": "diligent", "deck_id": 1, "deck_title": "TOEIC - Cảm xúc & Tính cách", "score": 1.0, "rank": 1, "used_in_answer": false}]

event: token
data: {"t": "D"}

event: token
data: {"t": "ư"}

event: token
data: {"t": "ới"}

event: token
data: {"t": " đây"}

...

event: done
data: {"usage": {"prompt_tokens": 812, "completion_tokens": 96, "latency_ms": 1840}}
```

Thứ tự cố định: `meta` → `citations` → `token`* → (`done` **hoặc** `error`). `citations`
**luôn** đến trước token đầu tiên. Không có `id:`, không có heartbeat. Khi proxy ra frontend
phải **giữ nguyên tên sự kiện và thứ tự**.

Bốn điều bắt buộc phải làm đúng:

1. **Nối `t` nguyên văn, đừng `trim()` từng mẩu.** Xem transcript trên: token cắt ở giữa từ
   (`"D"`, `"ư"`, `"ới"`), và khoảng trắng đầu mẩu (`" đây"`) là một phần của câu. Trim xong sẽ
   ra `Dưới đâylà` thay vì `Dưới đây là`.
2. **Ba nhánh 0 token phát đúng MỘT sự kiện `token`** chứa trọn câu trả lời. Đừng giả định
   luôn có nhiều mẩu.
3. **`error` có thể là sự kiện DUY NHẤT của cả lượt**, không có `meta` trước nó. Ca này bắt
   được thật khi index chưa sẵn sàng:
   ```
   event: error
   data: {"code": "INDEX_NOT_READY", "message": "Model chưa nạp xong hoặc index chưa sẵn sàng."}
   ```
   Đừng viết code chờ `meta` rồi mới lắng nghe `error`.
4. **Có `error` thì không bao giờ có `done`**, và ngược lại. `error` cũng có thể chen vào giữa
   chuỗi `token` khi kết nối tới LLM đứt giữa đường — lúc đó câu trả lời đã hiện một phần và BE
   phải quyết định giữ hay xoá.

Hai khác biệt so với `POST /chat`, dễ gây nhầm khi làm dashboard:

- `answer_source` trong `meta` là **kết luận sớm** và **không bao giờ là `LLM_ONLY`**. Thấy
  `RAG` mà `citations` ngay sau đó là `[]` thì lượt đó thực chất là `LLM_ONLY`.
- `usage` trong `done` **không có** `provider` và `model`. Và `latency_ms` ở đây đo trọn lượt
  với ba nhánh 0 token, nhưng chỉ đo lời gọi LLM với nhánh `RAG` — đừng gộp hai loại vào cùng
  một biểu đồ.

`used_in_answer` là `false` ở mọi phần tử với nhánh `RAG`, vì lúc phát `citations` thì chưa
sinh chữ nào nên chưa thể biết. Muốn tô sáng thẻ được dùng thật thì tự dò `[#id]` trong chuỗi
đang gom.

### 5.5 `POST /internal/v1/quiz/generate`

```json
{ "deck_id": 1, "allowed_deck_ids": [1, 2, 4], "question_count": 8,
  "types": ["MULTIPLE_CHOICE", "FILL_BLANK", "LISTENING", "MATCHING"],
  "card_ids": [101, 102, 103, 104], "use_ai_context": true }
```

`question_count` mặc định 10, khoảng 1–50. `card_ids` do backend chọn theo thẻ đến hạn ôn SRS
— **fsoft-ai không biết gì về SRS**. Bỏ trống thì tự lấy trong deck.

| Dạng | Cần LLM? | Trường riêng |
|---|:---:|---|
| `MULTIPLE_CHOICE` | không | `options`, `correct_index` |
| `LISTENING` | không | thêm `audio_url` |
| `MATCHING` | không | thêm `matching`, không có `correct_index` |
| `FILL_BLANK` | **có** | đề bài chứa `______` |

`use_ai_context` quyết định endpoint này có tốn token hay không:

| | `false` (mặc định) | `true` |
|---|---|---|
| Dạng sinh được | `MULTIPLE_CHOICE`, `LISTENING`, `MATCHING` | thêm `FILL_BLANK` |
| Token | **0** | có |
| `generated_by` | `DETERMINISTIC` | `LLM` cho `FILL_BLANK` |

> ⚠️ **`use_ai_context: false` là chế độ dự phòng cho ngày bảo vệ.** Sinh 100% không chạm
> Groq, dưới 2 giây, không thể bị rate limit. Nếu Groq nghẽn thì bật cờ này.

Response thật với `use_ai_context: false`:

```json
{"questions": [
   {"index": 1, "type": "MULTIPLE_CHOICE", "card_id": 101,
    "prompt": "\"resilient\" nghĩa là gì?",
    "options": ["kiên cường, có khả năng phục hồi nhanh",
                "lo lắng, e ngại về điều sắp xảy ra",
                "tỉ mỉ, cẩn thận đến từng chi tiết",
                "siêng năng, chăm chỉ và bền bỉ"],
    "correct_index": 0,
    "explanation": "resilient: kiên cường... Ví dụ: She remained resilient despite repeated setbacks.",
    "generated_by": "DETERMINISTIC", "audio_url": null, "matching": null}],
 "stats": {"deterministic_count": 3, "llm_count": 0,
           "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0}}
```

Và với `use_ai_context: true`, `types: ["FILL_BLANK"]`:

```json
{"index": 1, "type": "FILL_BLANK", "card_id": 101,
 "prompt": "After the hurricane, the coastal town rebuilt its houses ______.",
 "options": ["resilient", "diligent", "apprehensive", "meticulous"],
 "correct_index": 0, "generated_by": "LLM"}
```

`correct_index` là chỉ số **0-based** trong `options`.

**Ba cái bẫy của endpoint này:**

**1. `card_ids` phải có ít nhất 4 thẻ, không thì `400`.** Câu trắc nghiệm cần 1 đáp án đúng và
3 phương án nhiễu, mà nhiễu lấy từ chính các thẻ trong phạm vi. Đo thật từng mức:

```
card_ids = 1 thẻ  ->  400  "Bộ thẻ chỉ có 1 thẻ trong phạm vi, cần ít nhất 4 thẻ..."
card_ids = 3 thẻ  ->  400  (cùng thông báo, đổi số)
card_ids = 4 thẻ  ->  200, 4 câu
```

Đây là cái bẫy của đúng tính năng "ôn lại thẻ đang sai": người dùng sai 2 thẻ thì backend gửi 2
`card_ids` và **nhận 400**. Xử lý bằng cách bù thêm thẻ cho đủ 4, hoặc chỉ hiện nút ôn tập khi
có từ 4 thẻ trở lên.

**2. `question_count` bị chặn trên bởi số thẻ có thật.** Xin 5 câu với 4 `card_ids` thì nhận
đúng 4 câu, HTTP vẫn `200`. Đừng giả định `questions.length == question_count`.

**3. `types` là mong muốn, không phải cam kết.** Xin `FILL_BLANK` mà LLM không dùng được
(thiếu khoá, hết ngân sách, nhà cung cấp lỗi) thì service **tự lùi về `MULTIPLE_CHOICE`** với
`generated_by: DETERMINISTIC` và vẫn trả `200`. Xin `LISTENING` cho thẻ không có `audioUrl`
cũng lùi về `MULTIPLE_CHOICE`. **Luôn đọc `type` của từng câu**, đừng giả định theo yêu cầu đã
gửi. Đây là hành vi có chủ ý: thà ra đề dễ hơn còn hơn không ra được đề nào.

### 5.6 `POST /internal/v1/vocab/extract`

Người dùng dán một đoạn văn tiếng Anh, nhận về **thẻ từ vựng ứng viên** đã dựng sẵn đủ
trường — đúng shape thẻ nên backend chỉ việc lưu những thẻ người dùng tick chọn.

**fsoft-ai không lưu gì cả.** Nó không có endpoint ghi dữ liệu nghiệp vụ nào. Việc thẻ nào
vào deck nào là quyết định của backend.

```json
{
  "text": "The team stayed resilient after missing the first deadline.",
  "allowed_deck_ids": [1, 2, 3, 4],
  "max_candidates": 5
}
```

Response thật (rút gọn còn một ứng viên, lần chạy trả về năm):

```json
{
  "candidates": [
    {
      "word": "resilient",
      "phonetic": "/rɪˈzɪliənt/",
      "part_of_speech": "adj",
      "meaning": "kiên cường, có khả năng phục hồi nhanh",
      "definition_en": "able to recover quickly from difficulties",
      "example_sentence": "The team stayed resilient after missing the first deadline.",
      "example_meaning": "Cả nhóm vẫn kiên cường sau khi lỡ hạn chót đầu tiên.",
      "already_in_deck": true,
      "existing_card_id": 101
    }
  ],
  "stats": {
    "text_chars": 177, "distinct_english_tokens": 21, "returned_by_llm": 5,
    "dropped_not_grounded": 0, "dropped_unsafe": 0, "already_in_deck_count": 2,
    "dedup_checked": true, "llm_calls": 1,
    "prompt_tokens": 821, "completion_tokens": 1433, "latency_ms": 5005
  }
}
```

**Bốn cái bẫy của endpoint này:**

**1. Đây là endpoint ĐẮT NHẤT, và không có đường lùi 0 token.** Một lượt đặt chỗ tới **87%**
ngân sách token mỗi phút của TOÀN hệ thống. Đo thật: mỗi từ xin thêm tốn ~350 token đầu ra,
và phần lớn là **token suy luận ẩn** không hề xuất hiện trong câu trả lời.

- Gọi **tuần tự**, đừng bắn song song. Service tự giới hạn một lượt tại một thời điểm.
- Trong lúc một lượt đang chạy, `/chat` của người khác gần như chắc chắn nhận `429`.
- **Đừng** gọi khi người dùng vừa dán xong. Chờ họ bấm nút.
- Khác quiz, ở đây **không có** cờ kiểu `use_ai_context: false` để chạy miễn phí. Groq chết
  là tính năng này chết theo.

**2. `text` vượt 4.000 ký tự trả `400`, và là `{"error": {...}}` chứ không phải `{"detail": [...]}`.**
Thông báo nêu cả độ dài thật lẫn giới hạn. Service **không tự cắt và không tự chia nhỏ** —
chia nhỏ là việc của backend, có chủ ý. Cắt im lặng sẽ khiến người dùng mất phần cuối bài đọc
mà không biết.

Ngược lại, `max_candidates` ngoài khoảng 1–6 trả `422` với `{"detail": [...]}`. Hai hình dạng
lỗi này khác nhau là cố ý — parser phải chịu được cả hai.

**3. `already_in_deck` là đánh dấu, KHÔNG phải lọc.** Từ người học đã có **vẫn nằm trong kết
quả**, chỉ mang thêm cờ và `existing_card_id`. Giao diện nên bỏ tick sẵn thay vì giấu đi.

Cờ chỉ đúng TRONG `allowed_deck_ids`: cùng một từ, phạm vi hẹp lại thì cờ về `false` — đó là
đúng, không phải lỗi. Và khớp theo `word` viết thường, **không có lemma hoá**: bộ thẻ có
`emission` mà đoạn văn cho ra `emissions` thì vẫn báo chưa có.

**Luôn kiểm `stats.dedup_checked`.** `false` nghĩa là chỉ mục đang rỗng nên mọi cờ đều vô
nghĩa — xảy ra trong cửa sổ sau khi container khởi động lại mà đồng bộ đầu tiên chưa xong.

**4. Mảng rỗng có hai nghĩa khác nhau, phân biệt bằng `stats.returned_by_llm`:**

| `returned_by_llm` | `candidates` | HTTP | Nghĩa |
|---|---|---|---|
| `0` | `[]` | **200** | Đoạn văn thật sự không có từ nào đáng học. Đừng gọi lại |
| `> 0` | `[]` | **503** | Model bịa toàn bộ, đã bị lọc sạch. Thử lại có cơ hội |

> **Nội dung trả về là do người dùng gây ảnh hưởng được.** `word`, `meaning`,
> `example_sentence` đi ra từ một LLM đọc văn bản người dùng tự dán. Service đã chặn ký tự
> `< > { } [ ] \`, đường dẫn `http` và xuống dòng ở mọi trường, nhưng **frontend vẫn phải
> escape khi render** — dùng Markdown renderer có `html: false` hoặc DOMPurify, và tuyệt đối
> không `dangerouslySetInnerHTML` / `v-html` / `th:utext`. Thẻ lưu xong là chia sẻ được, nên
> một payload lọt qua sẽ tấn công người khác chứ không phải người dán.

---

### 5.7 `POST /internal/v1/vocab/generate`

Người dùng gõ một **chủ đề**, nhận về **thẻ từ vựng mới** đã dựng sẵn đủ trường — đúng
shape thẻ nên backend chỉ việc lưu những thẻ người dùng tick chọn.

Anh em với mục 5.6 nhưng ngược chiều: `extract` đòi sẵn một đoạn văn, ở đây đầu vào chỉ là
một chủ đề. **fsoft-ai không lưu gì cả.**

```json
{
  "topic": "tôi muốn học từ về công việc",
  "allowed_deck_ids": [1, 2, 3, 4],
  "level": "B2",
  "count": 5
}
```

Response thật (rút gọn còn hai thẻ, lần chạy trả về năm):

```json
{
  "topic_understood": "Công việc và nơi làm việc",
  "cards": [
    {
      "word": "escalate",
      "phonetic": "/ˈeskəleɪt/",
      "part_of_speech": "verb",
      "meaning": "chuyển vấn đề lên cấp cao hơn",
      "definition_en": "to refer an issue to a higher level of authority",
      "example_sentence": "We had to escalate the issue to the regional director.",
      "example_meaning": "Chúng tôi phải chuyển vấn đề lên giám đốc vùng.",
      "already_in_deck": false,
      "existing_card_id": null
    }
  ],
  "stats": {
    "topic_chars": 27, "level": "B2", "requested": 5,
    "avoid_list_size": 18, "avoid_list_dropped": 0, "returned_by_llm": 5,
    "dropped_unsafe": 0, "dropped_incoherent": 0, "dropped_duplicate_in_batch": 0,
    "already_in_deck_count": 0, "dedup_checked": true, "llm_calls": 1,
    "prompt_tokens": 1042, "completion_tokens": 1876, "latency_ms": 6210
  }
}
```

---

#### ⚠️ ĐỌC MỤC NÀY TRƯỚC KHI DỰNG GIAO DIỆN

Ở mục 5.6, câu ví dụ **bắt buộc có thật trong văn bản người dùng dán vào**. Đó không phải
một bộ lọc, đó là phép kiểm **xuất xứ**: không một chữ tiếng Anh mới nào lọt vào dữ liệu
lưu trữ. Người dùng đã đọc đoạn văn đó rồi mới dán.

Ở đây không có văn bản gốc. **100% mỗi byte được lưu đều do một model bịa ra.**

| | `/vocab/extract` | `/vocab/generate` |
|---|---|---|
| Nội dung tiếng Anh mới | model **không thể** đưa vào | model **có thể** |
| Service kiểm được | xuất xứ + hình dạng | **chỉ hình dạng** |
| Cờ phân biệt máy đọc được | có `stats.dropped_not_grounded` | **không có trường đó** |

Service **không kiểm được và không thể kiểm được**: nghĩa tiếng Việt có đúng không, phiên
âm có phải IPA thật của từ đó không, từ đó có tồn tại trong tiếng Anh không, có đúng
`level` đã xin không, từ có thật sự thuộc chủ đề không.

**Vì vậy bước người dùng tick chọn là lần soát nội dung DUY NHẤT mấy thẻ này sẽ có**, trước
khi chúng thành thẻ nằm trong deck chia sẻ được rồi đồng bộ ngược vào chính corpus RAG trả
lời `/chat` của người khác. Đó không phải một chi tiết giao diện. Bốn điều dưới đây là
**yêu cầu**, không phải gợi ý:

**1. Giao diện phải hiện MỌI trường sắp lưu.** Không ai đồng ý được với văn bản họ không
được xem. Hiện `word` + `meaning` rồi lặng lẽ lưu thêm `example_sentence`, `definition_en`,
`example_meaning` và `phonetic` là đã xoá bỏ ranh giới này — tính năng khi đó không còn
một lần soát nội dung nào.

**2. Không có đường hàng loạt.** Không nút "chọn tất cả", không tự lưu, không job nền,
không "sinh 50 thẻ cho deck của tôi". Mục 5.6 sống sót được với một nút chọn tất cả; mục
này thì không.

**3. Backend PHẢI kiểm lại khi ghi.** Luồng là: sinh → **trả về client** → client gửi ngược
lên phần đã tick → backend lưu. Chặng giữa đi qua client, nên client gửi lên được những
thẻ service này chưa từng sinh ra, và **mọi chốt chặn dưới đây bị vòng qua sạch**. Hãy dựng
lại các luật sau ở phía Java, trên đường ghi:

| Trường | Luật |
|---|---|
| `word` | ≤ 32 ký tự, khớp `^[a-z]+(?:['-][a-z]+)*(?: [a-z]+(?:['-][a-z]+)*){0,2}$` |
| `meaning` | ≤ 100 ký tự, **phải** có dấu tiếng Việt |
| `definition_en` | ≤ 120 ký tự, **không** được có dấu tiếng Việt |
| `example_sentence` | ≤ 140 ký tự, **không** được có dấu tiếng Việt |
| `example_meaning` | ≤ 140 ký tự |
| `phonetic` | ≤ 40 ký tự, bọc hai dấu `/` |
| mọi trường văn xuôi | chỉ chữ cái, chữ số, khoảng trắng và `. , ' " ? ! ; : ( ) -` |

Chú ý luật cuối là **allowlist**, không phải danh sách cấm. `=`, `&`, `@`, `<`, `>` bị loại
vì **không được kể tên**. Chuỗi `" autofocus onfocus=alert(1) x="` không chứa một dấu ngoặc
nhọn nào và vẫn thoát ra khỏi mọi thuộc tính HTML — danh sách cấm không bắt được nó.

**4. Đánh dấu xuất xứ khi lưu.** Gắn một cờ kiểu `generated_by: AI_TOPIC` lên thẻ (có tiền
lệ: `generated_by: DETERMINISTIC` của quiz). Không có cờ này thì sáu tháng nữa, một thẻ sai
xuất hiện trong deck chia sẻ và không ai phân biệt được nó do người học gõ hay model bịa —
không rà soát hàng loạt được, không thu hồi được.

> **Kiểm duyệt nội dung là việc của backend.** Mục 5.6 chỉ có thể làm nổi lên những từ vốn
> đã nằm trong clipboard của chính người dùng. Mục này sẽ vui vẻ sinh trọn một bộ từ vựng
> cho **bất kỳ** chủ đề nào người dùng gõ. Không có chốt tất định nào cho việc đó, và một
> danh sách từ cấm song ngữ thì vòng qua quá dễ.

---

#### Năm cái bẫy khác

**1. `topic` vượt 120 ký tự trả `400` với `{"error": {...}}`**, còn `count` ngoài 1–5 hay
`level` ngoài `A1`–`C2` trả `422` với `{"detail": [...]}`. Hai hình dạng khác nhau là cố ý:
400 cho luật nghiệp vụ, 422 cho sai kiểu. Parser phải chịu được cả hai.

**Chủ đề tiếng Việt hoàn toàn hợp lệ** — đó là ca dùng chính. Ngược với mục 5.6, nơi một
đoạn thuần tiếng Việt bị từ chối vì không có từ tiếng Anh nào để trích.

**2. Mảng rỗng KHÔNG phải câu trả lời hợp lệ ở đây** — ngược mục 5.6:

| | `/vocab/extract` | `/vocab/generate` |
|---|---|---|
| `returned_by_llm = 0` | **200**, đừng gọi lại | **503**, thử lại được |

"Chủ đề của bạn không có từ vựng nào" gần như không bao giờ đúng. Số 0 nghĩa là model từ
chối, chạm bộ lọc an toàn, hoặc trả rác. Gợi ý người dùng gõ chủ đề cụ thể hơn.

**3. `count` là lời hứa, `level` thì không.** Service cắt kết quả về đúng `count` (model
thực tế hay trả dư). Nhưng `level` chỉ là gợi ý đưa vào prompt — **không có bộ kiểm CEFR
nào**, nên đừng dựng giao diện hứa "từ vựng trình độ A1" như một sự thật đã kiểm chứng.

**4. Nút "thêm từ nữa" dùng `exclude_words`.** Gửi lại chính những từ giao diện vừa hiện ở
lượt trước; lượt này sẽ ra từ khác. Client giữ trạng thái, service không lưu gì — đúng cách
`/chat` làm với `history`. Không có `exclude_words` thì gọi lại cùng chủ đề sẽ ra phần lớn
là từ cũ, cho tới khi người dùng lưu thẻ và chu kỳ đồng bộ 120 giây tiếp theo chạy xong.

**5. Ngân sách: rẻ hơn mục 5.6 nhưng vẫn đắt, và dùng CHUNG hàng đợi với nó.** Một lượt đặt
chỗ ~69% ngân sách token mỗi phút của toàn hệ thống (mục 5.6 là ~87%). Hai endpoint
`/vocab/*` chỉ chạy **một lượt tại một thời điểm**; chờ quá 10 giây thì trả `429` luôn chứ
không để request treo. Gọi tuần tự, và đừng gọi khi người dùng vừa gõ xong — chờ họ bấm nút.

> **Nội dung trả về do model sinh ra hoàn toàn.** Service đã chặn ký tự nguy hiểm, đường
> dẫn và ký tự vô hình ở mọi trường, nhưng **frontend vẫn phải escape khi render** — dùng
> Markdown renderer có `html: false` hoặc DOMPurify, tuyệt đối không
> `dangerouslySetInnerHTML` / `v-html` / `th:utext`. Ở mục 5.6 lời nhắc này là phòng thủ
> thêm một lớp; ở đây nó là lớp cuối cùng.

---

### 5.8 `POST /internal/v1/vocab/lookup`

Tra **đúng một từ**, nhận về thẻ dựng sẵn đủ trường. Hai ca dùng chung một endpoint:

- người dùng bấm nút tra trong lúc soạn thẻ mới;
- người dùng **bôi đen một từ** trong lúc đọc — ca này gửi kèm `context`.

```json
{
  "word": "bank",
  "context": "They sat on the river bank and watched the boats go by.",
  "allowed_deck_ids": [1, 2, 3, 4]
}
```

```json
{
  "source": "AI",
  "found": true,
  "suggestion": null,
  "card": {
    "word": "bank", "phonetic": "/bæŋk/", "part_of_speech": "noun",
    "meaning": "bờ sông", "definition_en": "the land alongside a river",
    "example_sentence": "We picnicked on the grassy bank all afternoon.",
    "example_meaning": "Chúng tôi dã ngoại trên bờ cỏ suốt buổi chiều.",
    "already_in_deck": false, "existing_card_id": null
  },
  "stats": {
    "source": "AI", "word_chars": 4, "context_chars": 55, "llm_calls": 1,
    "cache_size": 137, "prompt_tokens": 978, "completion_tokens": 611,
    "latency_ms": 1780
  }
}
```

---

#### ⚠️ Điều quan trọng nhất: gọi tới đây SAU khi từ điển của bạn đã trượt

Ngân sách LLM là **6.400 token mỗi phút cho toàn bộ ứng dụng**, dùng chung với
`/chat`, `/quiz` và hai endpoint `/vocab/*` kia. Một lượt `AI` ở đây đặt chỗ
khoảng **2.400**, nên nếu mỗi lần bấm nút đều tới thẳng đây thì cả hệ thống chỉ
chịu được chừng **ba lượt bấm một phút** — và `/chat` của mọi người khác chết theo.

Thứ tự đúng:

```
người dùng bấm "Look up"
   |
   v
1. từ điển của backend            0 token, nhanh, phủ phần lớn từ thông dụng
   |  có mục từ?  -> trả về luôn, HẾT
   |  không có?
   v
2. POST /internal/v1/vocab/lookup
```

Ảnh giao diện hiện tại đã hiện đúng câu *"No dictionary entry for that word"* —
đó chính là chỗ nên gọi tới endpoint này thay vì bắt người dùng tự điền tay.

#### Đọc `stats.source` trước mọi thứ khác

| `source` | Token | Nghĩa | Giao diện nên làm gì |
|---|---|---|---|
| `YOUR_DECK` | **0** | Từ **đã có** trong bộ thẻ người dùng | **Báo trùng**, mời họ mở thẻ cũ |
| `CACHE` | **0** | Đã có người tra từ này gần đây | Điền vào form như bình thường |
| `AI` | ~2.400 | Gọi LLM thật | Điền vào form như bình thường |

`YOUR_DECK` là câu trả lời đáng giá nhất, và không phải vì nó miễn phí: người
dùng đang ở màn hình **soạn thẻ mới**, nên biết mình sắp tạo thẻ trùng còn hữu
ích hơn một thẻ mới. `card.existing_card_id` là id thẻ cũ — hãy cho họ một nút
mở thẳng nó, đừng lặng lẽ điền vào form rồi để họ tạo bản sao thứ hai.

Theo dõi tỉ lệ ba giá trị này trong `GET /internal/v1/stats` (`by_task` có dòng
`VOCAB_LOOKUP`). Tỉ lệ `AI` cao bất thường nghĩa là từ điển phía backend đang bị
bỏ qua ở đâu đó.

#### Năm cái bẫy

**1. Gõ sai chính tả trả `200`, không phải lỗi.**

```json
{ "source": "AI", "found": false, "suggestion": "receive", "card": null, ... }
```

`found: false` nghĩa là **đây không phải một từ tiếng Anh có thật**, và `card` là
`null`. Đừng coi là lỗi — câu hỏi đã được trả lời, câu trả lời là "không có từ
này". Giao diện nên hiện *"Không tìm thấy từ này. Ý bạn là **receive**?"* kèm nút
tra lại, rồi để người dùng tự điền nếu họ vẫn muốn.

Service **không kiểm chứng được** cờ đó — nó là lời của model. Nhưng hỏi thẳng
vẫn tốt hơn hẳn để model bịa một nghĩa nghe rất thật cho một từ không tồn tại,
rồi người học lưu vào bộ thẻ và học thuộc nó.

**2. `card.word` có thể khác chuỗi bạn gửi lên.** Gửi `"Donuts"`, nhận `"donut"`
— thẻ từ vựng cần dạng từ điển, và đó là chủ ý. Đừng so sánh hai chuỗi rồi báo
lỗi. Service đã kiểm rằng hai dạng vẫn là cùng một từ; model trả về một từ khác
hẳn thì lượt đó bị loại và bạn nhận `503`.

**3. `word` sai định dạng trả `400`, nhưng SAI CHÍNH TẢ THÌ KHÔNG.** Chuỗi có
chữ số, dấu chấm, hay quá ba từ bị chặn ngay ở `400` với `{"error": {...}}` —
không lời gọi LLM nào cứu được nó. Nhưng `recieve` là chuỗi hợp lệ về hình dạng,
nó đi tiếp và nhận `200` với `found: false`. Đừng gộp hai ca này làm một.

`context` thì ngược lại: dài quá 300 ký tự bị **cắt**, không báo lỗi. Bôi đen một
câu dài không bao giờ nên là lỗi.

**4. Đường 0 token vẫn chạy khi hết hạn mức.** Nếu `/chat` đang trả `429` vì cạn
ngân sách, endpoint này **vẫn** trả lời được cho từ đã có trong bộ thẻ. Đừng tắt
nút tra chỉ vì thấy 429 ở chỗ khác.

**5. Cache dùng chung giữa mọi người dùng — và điều đó an toàn.** Nghĩa của từ
`donut` không phụ thuộc bộ thẻ của ai, nên một người tra rồi thì mọi người sau
đều miễn phí. Hai cờ `already_in_deck` / `existing_card_id` **không** nằm trong
cache; chúng được tính lại theo `allowed_deck_ids` của từng request, nên không
bao giờ lộ thẻ của người khác.

> **Với `source: AI` và `CACHE`, mọi trường đều do model sinh ra.** Service kiểm
> được hình dạng — độ dài, bộ ký tự, ngôn ngữ, câu ví dụ có dùng đúng từ không —
> nhưng **không** kiểm được nghĩa tiếng Việt có đúng, phiên âm có thật, hay từ có
> tồn tại. Người dùng vẫn phải xem trước khi lưu, và **frontend vẫn phải escape
> khi render**: Markdown renderer có `html: false` hoặc DOMPurify, tuyệt đối
> không `dangerouslySetInnerHTML` / `v-html` / `th:utext`.
>
> Riêng `source: YOUR_DECK` thì khác: đó là nội dung thẻ thật của chính người
> dùng, không có chữ nào do model sinh.

---

## 6. Định dạng lỗi

fsoft-ai **không** dùng bọc `ApiResponse` như backend Java. Mọi lỗi có dạng:

```json
{
  "error": {
    "code": "BUDGET_EXHAUSTED",
    "message": "Ngân sách token toàn cục đã cạn, thử lại sau 45 giây",
    "retry_after_seconds": 45
  }
}
```

`message` viết bằng tiếng Việt và **đã đủ tử tế để hiện thẳng cho người dùng cuối**. `code` là
thứ để code rẽ nhánh.

| Code | HTTP | Ý nghĩa | Backend nên làm gì |
|---|---|---|---|
| `INVALID_SCOPE` | 400 | `allowed_deck_ids` rỗng, hoặc `scope_deck_id` ngoài phạm vi | Bug phía backend. **Đừng retry**, sửa cách tính quyền |
| `INVALID_REQUEST` | 400 | Tham số vô lý (deck không đủ 4 thẻ, giá trị ngoài khoảng, `text` vượt 4.000 ký tự) | **Đừng retry**, hiện `message` |
| `UNAUTHORIZED` | 401 | Sai hoặc thiếu `X-Internal-Token` | **Đừng retry**, kiểm lại TOKEN_A |
| `BUDGET_EXHAUSTED` | 429 | Hết ngân sách token dùng chung | **Đọc `retry_after_seconds`**, trả `429` cho user kèm số đó |
| `PROVIDER_UNAVAILABLE` | 503 | Nhà cung cấp LLM hỏng sau khi đã retry | Retry có backoff 2–3 lần. Trả `503` **message tiếng Việt**, không phải `500` |
| `INDEX_NOT_READY` | 503 | Model chưa nạp xong hoặc index rỗng | Thử lại sau 5–10 giây |

Ba điều dễ sai:

**1. `422` KHÔNG theo hình dạng trên.** Đây là lỗi validate của FastAPI, body có khoá `detail`
chứ không có `error`. Parser của backend phải chịu được cả hai, nếu không sẽ ném NPE ở đúng chỗ
khó debug nhất. Response thật khi gửi `allowed_deck_ids` là chuỗi thay vì mảng:

```json
{"detail":[{"type":"list_type","loc":["body","allowed_deck_ids"],
            "msg":"Input should be a valid list","input":"khong-phai-mang"}]}
```

**2. `retry_after_seconds` chỉ có giá trị ở `429`.** Các mã khác để `null`.

**3. Ba nhánh 0 token vẫn chạy khi LLM chết.** `PROVIDER_UNAVAILABLE` chỉ ảnh hưởng `RAG` và
`LLM_ONLY`. `POST /search` và `POST /quiz/generate` với `use_ai_context: false` vẫn hoạt động
bình thường — **đừng cho cả tính năng AI "sập"** khi thấy một lỗi 503 từ `/chat`.

Ngoại lệ duy nhất: `POST /vocab/extract` **chết theo LLM**, vì không cách nào tự chế nghĩa
tiếng Việt của một từ mà không có model. Ẩn riêng nút đó đi, đừng ẩn cả tính năng.

> Không bao giờ có traceback trong response. Nếu thấy traceback, đó là bug — báo đội AI.

---

## 7. Giới hạn vận hành phải thiết kế theo

### Ngân sách token: khoảng 4 lượt chat RAG mỗi phút cho TOÀN BỘ ứng dụng

Groq free tier cho **8.000 token/phút**; service tự giới hạn ở 6.400 (80%). Một lượt `RAG` đo
được tốn 500–900 token prompt cộng 100–350 token trả lời.

Con số này là **cho cả hệ thống**, không phải mỗi người dùng. Ba người chat cùng lúc là đã chạm
trần. Hệ quả cho thiết kế backend:

- **Đừng gọi `/chat` cho việc mà `/search` làm được.** `/search` không tốn token, không có trần.
- Ưu tiên `use_ai_context: false` cho quiz, trừ khi người dùng chủ động xin đề khó.
- Chuẩn bị sẵn giao diện cho `429`: "trợ lý đang bận, thử lại sau N giây", N lấy từ
  `retry_after_seconds`.

**`POST /vocab/extract` là ngoại lệ đắt nhất.** Một lượt đặt chỗ tới **87%** ngân sách một
phút — tức gần bằng bốn lượt chat cộng lại. Đo thật, không phải ước lượng:

| | Token |
|---|---|
| Một lượt chat RAG | ~700–1.200 |
| Một lượt `/vocab/extract` với `max_candidates=6` | **~5.500 đặt chỗ** |

Phần lớn là token suy luận ẩn của model. Hệ quả: trong lúc một lượt trích xuất đang chạy,
`/chat` của người khác gần như chắc chắn nhận `429`. Gọi tuần tự, và đừng để nó nằm trên
đường đi nóng của giao diện.

### Render free: service **ngủ** sau 15 phút không có request

Lần gọi đầu sau khi ngủ mất **25–60 giây** (khởi động container và nạp lại model). Đo được:
request đầu timeout, rồi `/readyz` trả 503 khoảng 10 giây, rồi 200.

- Đặt **connect timeout riêng, rộng** (60 giây) cho lần gọi đầu, hoặc
- Ping `/healthz` mỗi 10 phút để giữ service thức, hoặc
- Chấp nhận và hiện trạng thái "đang khởi động" ở giao diện.

Đừng để timeout mặc định 5 giây của HTTP client biến chuyện này thành lỗi.

### Độ trễ thật, đo từ ngoài Internet

| Endpoint | Server tự đo | Từ máy client (gồm mạng) |
|---|---|---|
| `/search` | 5–14 ms | 285–342 ms |
| `/chat` nhánh 0 token | 0 ms | ~600 ms |
| `/chat` nhánh RAG | 770–2.300 ms | +300 ms |

Phần lớn độ trễ của `/search` là mạng chứ không phải xử lý. Backend và service chạy cùng vùng
sẽ nhanh hơn nhiều.

---

## 8. Chạy thử ở máy local

fsoft-ai chạy được **hoàn toàn ngoại tuyến** bằng 24 thẻ mẫu — không cần backend Java,
không cần MySQL. Dev backend có thể dựng lên để thử tích hợp trước khi endpoint sẵn sàng.

```bash
git clone <repo fsoft-ai>
cd fsoft-ai
uv sync

cp .env.example .env
# sửa AI_INTERNAL_TOKEN thành một chuỗi bất kỳ, ví dụ: dev-token

uv run uvicorn app.main:app --port 8000
```

Khởi động mất khoảng **1 giây** (nạp model embedding đã tỉa từ vựng). Rồi:

```bash
curl localhost:8000/healthz
curl localhost:8000/readyz

curl -H "X-Internal-Token: dev-token" localhost:8000/internal/v1/index/status

curl -X POST -H "X-Internal-Token: dev-token" \
  "localhost:8000/internal/v1/index/sync?full=true"
```

Mở `http://localhost:8000/docs` để xem OpenAPI tương tác.

Nhánh `RAG` cần khoá Groq trong `AI_LLM_API_KEY`. Không có khoá thì ba nhánh 0 token
(`DIRECT_LOOKUP`, `CANNED`, `CACHE`), `/search` và quiz `use_ai_context: false` vẫn chạy đủ.

Muốn thử với backend thật thì đổi trong `.env`:

```bash
AI_SOURCE_MODE=http
AI_BACKEND_URL=http://localhost:8080/fsoft
AI_BACKEND_TOKEN=<TOKEN_B>
```

Chạy bằng Docker: xem [`DOCKER.md`](DOCKER.md).

---

## 9. Checklist bàn giao

**Phía Java**

- [ ] `InternalCardController` với hai endpoint ở [mục 4](#4-backend-phải-cung-cấp-hai-endpoint-nội-bộ)
- [ ] Filter kiểm `X-Internal-Token`, `SecurityFilterChain` riêng loại `/internal/**` khỏi JWT
- [ ] Pass đủ 9 mục acceptance ở [4.5](#45-acceptance-cho-phía-java)
- [ ] Sinh TOKEN_B, đặt vào env của **cả hai** service
- [ ] Nhận TOKEN_A từ đội AI, đặt vào env của backend
- [ ] Frontend **không** gọi thẳng fsoft-ai
- [ ] Parser lỗi chịu được **cả hai** hình dạng: `{"error":{...}}` và `{"detail":[...]}`
- [ ] Timeout cho lần gọi đầu ≥ 60 giây (Render free ngủ sau 15 phút)
- [ ] Tắt fsoft-ai → backend trả `503` message tiếng Việt, **không phải** `500`
- [ ] Lỗi 503 từ `/chat` **không** làm tắt `/search` và quiz ở giao diện

**Phía Java — riêng cho `/vocab/generate` ([mục 5.7](#57-post-internalv1vocabgenerate))**

- [ ] Giao diện xác nhận hiện **mọi trường** sắp lưu, không giấu trường nào
- [ ] **Không** có nút "chọn tất cả", không tự lưu, không job nền sinh thẻ
- [ ] Đường ghi **kiểm lại** độ dài và bộ ký tự từng trường — client gửi ngược lên
      được thứ service này chưa từng sinh ra
- [ ] Thẻ lưu từ endpoint này mang cờ xuất xứ (`generated_by: AI_TOPIC` hoặc tương đương)
- [ ] Mọi trường được **escape khi render**, không `dangerouslySetInnerHTML` / `v-html` / `th:utext`
- [ ] Có đường để người dùng báo cáo thẻ không phù hợp

**Phía Java — riêng cho `/vocab/lookup` ([mục 5.8](#58-post-internalv1vocablookup))**

- [ ] **Từ điển của backend chạy TRƯỚC**, endpoint này chỉ là đường lùi khi trượt
- [ ] `found: false` được xử lý như một câu trả lời hợp lệ, không phải lỗi
- [ ] `source: YOUR_DECK` hiện cảnh báo trùng kèm nút mở thẻ cũ, không lặng lẽ
      điền vào form
- [ ] Không so `card.word` với chuỗi gửi lên rồi báo lỗi — dạng từ điển khác là đúng
- [ ] Nút tra **không** bị tắt khi `/chat` đang 429: đường 0 token vẫn chạy

**Phía hạ tầng**

- [ ] TOKEN_A và TOKEN_B là **hai giá trị khác nhau**
- [ ] Health check của fsoft-ai trỏ `/healthz`, không phải `/readyz`
- [ ] `AI_SOURCE_MODE=http` cùng `AI_BACKEND_URL`, `AI_BACKEND_TOKEN`
- [ ] Sau khi nối: `POST /internal/v1/index/sync?full=true&sweep=true`, theo dõi `card_count`
      cho tới khi khớp số thẻ thật, và `source_mode` phải là `"http"`
- [ ] Khi chuyển sang Railway: cùng project, fsoft-ai **không** gán public domain, backend gọi
      qua `http://fsoft-ai.railway.internal:8000`

---

## 10. Hạn chế đã biết

### Câu tiếng Việt viết không dấu trả về rỗng

Đo thật trên service đang chạy:

```
query "lo lang"  ->  0 kết quả
query "lo lắng"  ->  2 kết quả  [apprehensive, anxious]
```

| Câu hỏi | Có dấu | Không dấu |
|---|---|---|
| `resilient nghĩa là gì` | ✅ tìm thấy 101 | ✅ tìm thấy 101 |
| `từ nào chỉ cảm giác lo lắng` | ✅ 102, 108 | ❌ **rỗng** |
| `từ nào nói về hạn chót công việc` | ✅ 201 | ❌ **rỗng** |

Câu chứa từ tiếng Anh vẫn chạy nhờ tầng khớp chính xác. Nhưng câu thuần tiếng Việt không dấu
thì model embedding coi như một chuỗi khác hẳn, và không thẻ nào vượt được cổng lọc liên quan.

Người Việt gõ không dấu rất phổ biến, nhất là trên điện thoại. **Gợi ý cho frontend:** nhắc
người dùng gõ có dấu, hoặc bật bộ thêm dấu tự động ở ô nhập. Phía fsoft-ai chưa xử lý
(SPEC mục 14.2b).

### Model LLM có thể bị nhà cung cấp khai tử mà không báo

Đã xảy ra ngày 17/08/2026: cả bốn model Groq mà service đang cấu hình bị gỡ khỏi API. Triệu
chứng phía backend là **mọi lượt `RAG` trả `503 PROVIDER_UNAVAILABLE`** với message "Trợ lý AI
đang quá tải" — không hề nói ra nguyên nhân thật là `404 model_not_found`.

Hiện đã đổi sang `openai/gpt-oss-120b` (chat, quiz) và `openai/gpt-oss-20b` (rewrite,
fallback). Nếu thấy `PROVIDER_UNAVAILABLE` kéo dài nhiều giờ thì báo đội AI kiểm lại danh sách
model, đừng cho là nghẽn tạm thời.

Đây cũng là lý do phải làm đúng điểm 3 ở [mục 6](#6-định-dạng-lỗi): LLM chết **không** được
làm tắt `/search` và quiz.

---

**Vướng gì hỏi đội AI.** Đặc tả đầy đủ, kể cả phần bên trong fsoft-ai, nằm ở
[`SPEC.md`](SPEC.md) — nhưng để tích hợp thì tài liệu này là đủ.
