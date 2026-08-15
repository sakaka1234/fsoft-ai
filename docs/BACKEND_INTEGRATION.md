# fsoft-ai — Hướng dẫn tích hợp cho backend Java

> **Đối tượng:** đội backend Java (Spring Boot).
> **Bạn không cần đọc `SPEC.md`.** Tài liệu này tự chứa mọi thứ để tích hợp.
>
> **Trạng thái ngày 16/08/2026:** M1 đã xong. Mục [5](#5-fsoft-ai-cung-cấp-gì) nói rõ
> endpoint nào **gọi được ngay**, endpoint nào **chưa tồn tại** — đừng viết client cho
> những cái chưa có.

---

## Mục lục

1. [Ba việc backend cần làm](#1-ba-việc-backend-cần-làm)
2. [Ranh giới trách nhiệm](#2-ranh-giới-trách-nhiệm)
3. [Kết nối và hai token](#3-kết-nối-và-hai-token)
4. [Backend PHẢI cung cấp: hai endpoint nội bộ](#4-backend-phải-cung-cấp-hai-endpoint-nội-bộ)
5. [fsoft-ai cung cấp gì](#5-fsoft-ai-cung-cấp-gì)
6. [Định dạng lỗi](#6-định-dạng-lỗi)
7. [Chạy thử ở máy local](#7-chạy-thử-ở-máy-local)
8. [Checklist bàn giao](#8-checklist-bàn-giao)

---

## 1. Ba việc backend cần làm

| # | Việc | Ước lượng | Mục |
|---|---|---|---|
| 1 | Làm **hai endpoint nội bộ** để fsoft-ai kéo dữ liệu thẻ về | ~60 dòng Java | [mục 4](#4-backend-phải-cung-cấp-hai-endpoint-nội-bộ) |
| 2 | Sinh và trao đổi **hai token** | 10 phút | [mục 3](#3-kết-nối-và-hai-token) |
| 3 | Gọi fsoft-ai qua mạng nội bộ Railway | tuỳ tính năng | [mục 5](#5-fsoft-ai-cung-cấp-gì) |

**Việc số 1 là gấp nhất.** Không có nó thì fsoft-ai không có dữ liệu thật để index, và
toàn bộ tính năng AI chỉ chạy trên dữ liệu mẫu.

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
| Vòng đời job quiz | **Backend Java** |
| Embedding, vector index | fsoft-ai |
| Retrieval, phân loại intent | fsoft-ai |
| Gọi LLM, quản ngân sách token | fsoft-ai |
| Sinh câu hỏi quiz | fsoft-ai |

**Vì sao chia thế này:** logic phân quyền đã tồn tại trong Java. Viết lại bằng Python
nghĩa là hai bản logic có thể lệch nhau — và khi lệch thì hậu quả là lộ bộ thẻ riêng tư
của người khác. Một nguồn sự thật duy nhất.

> ⚠️ **Hệ quả bắt buộc:** mọi request gọi vào fsoft-ai **phải** kèm `allowed_deck_ids`
> đã được backend tính đúng theo quyền của user đang đăng nhập. fsoft-ai tin tuyệt đối
> vào danh sách này. Truyền sai là lộ dữ liệu.
>
> Danh sách **rỗng** nghĩa là "không được phép gì" chứ không phải "không lọc" — fsoft-ai
> sẽ trả `400 INVALID_SCOPE`, không bao giờ hiểu thành "tất cả".

---

## 3. Kết nối và hai token

### 3.1 Địa chỉ

| | Giá trị |
|---|---|
| Base URL | `http://fsoft-ai.railway.internal:8000` |
| Public domain | **Không có, và cố ý không có** |
| Kiểu chữ trong JSON | `snake_case` (khác backend Java dùng `camelCase`) |
| OpenAPI tương tác | `http://fsoft-ai.railway.internal:8000/docs` |

Frontend **không bao giờ** gọi thẳng fsoft-ai. Mọi thứ đi qua backend Java.

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

Sinh token:

```bash
openssl rand -hex 32
# hoặc
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3.3 ⚠️ Vì sao TOKEN_B nguy hiểm hơn hẳn

Hai endpoint ở [mục 4](#4-backend-phải-cung-cấp-hai-endpoint-nội-bộ) **bỏ qua toàn bộ
logic phân quyền deck** — chúng trả về mọi thẻ của mọi người dùng, kể cả deck `PRIVATE`.
Đó là chủ ý: fsoft-ai cần index tất cả, việc lọc theo quyền diễn ra lúc truy vấn.

Nghĩa là **TOKEN_B là chìa khoá vạn năng đọc toàn bộ bộ thẻ riêng tư của mọi người dùng.**

Vì vậy:

- Tối thiểu 32 byte ngẫu nhiên
- **Không dùng chung một token cho cả hai chiều.** Lộ một đầu không được kéo theo đầu kia,
  và phải đổi được token nguy hiểm hơn mà không đụng phía còn lại
- Đặt thẳng vào biến môi trường Railway. **Không commit, không gửi qua chat**
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

---

## 5. fsoft-ai cung cấp gì

### 5.1 Dùng được ngay hôm nay

| Method | Đường dẫn | Cần token | Công dụng |
|---|---|:---:|---|
| `GET` | `/healthz` | không | Service còn sống không. Luôn `200` nếu tiến trình còn chạy |
| `GET` | `/readyz` | không | Model nạp xong và index sẵn sàng chưa. `503` khi chưa |
| `GET` | `/internal/v1/index/status` | có | Trạng thái đồng bộ và index |
| `POST` | `/internal/v1/index/sync` | có | Ép đồng bộ ngay, không đợi hết 120 giây |

**Dùng cái nào cho health check của Railway:** `/healthz`. Đừng dùng `/readyz` —
nó trả `503` trong vài giây đầu lúc nạp model, Railway sẽ tưởng deploy hỏng và restart vòng lặp.

**`GET /internal/v1/index/status`**

```json
{
  "card_count": 24,
  "index_size": 24,
  "model_version": "multilingual-e5-small@t1",
  "last_sync_ts": "2026-08-20T06:00:00Z",
  "last_sync_at": "2026-08-15T18:51:04.978619Z",
  "last_sync_duration_ms": 2,
  "last_sync_embedded": 0,
  "last_sync_skipped": 3,
  "last_full_sweep_at": "2026-08-15T18:51:04.980614Z",
  "last_sync_error": null,
  "backend_reachable": true,
  "source_mode": "fixture"
}
```

Ba field đáng theo dõi khi ghép hệ thống:

| Field | Ý nghĩa |
|---|---|
| `backend_reachable` | `false` nghĩa là fsoft-ai không gọi được endpoint của bạn |
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

Sau khi deploy lần đầu, gọi `POST /internal/v1/index/sync?sweep=true&full=true` rồi theo dõi
`card_count` cho tới khi khớp số thẻ thật.

### 5.2 ⛔ Chưa tồn tại — đừng viết client vội

Các endpoint dưới đây đã có đặc tả trong `SPEC.md` nhưng **chưa được implement**. Gọi vào
sẽ nhận `404`.

| Đường dẫn | Milestone | Dự kiến |
|---|---|---|
| `POST /internal/v1/search` | M2 | Tìm kiếm ngữ nghĩa, **không tốn token LLM** |
| `POST /internal/v1/chat` | M4 | Hỏi đáp RAG, blocking |
| `POST /internal/v1/chat/stream` | M4 | Hỏi đáp RAG, SSE |
| `POST /internal/v1/quiz/generate` | M5 | Sinh câu hỏi kiểm tra |
| `GET /internal/v1/stats` | M7 | Thống kê token và độ trễ |

Đội AI sẽ cập nhật tài liệu này khi từng cái xong. Trong lúc chờ, việc số 1 và số 2 ở
[mục 1](#1-ba-việc-backend-cần-làm) đã đủ để làm song song.

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

| Code | HTTP | Ý nghĩa | Backend nên làm gì |
|---|---|---|---|
| `INVALID_SCOPE` | 400 | `allowed_deck_ids` rỗng, hoặc `scope_deck_id` ngoài phạm vi | Bug phía backend, sửa cách tính quyền |
| `INVALID_REQUEST` | 400 | Sai schema, tham số ngoài khoảng | Bug phía backend |
| `UNAUTHORIZED` | 401 | Sai hoặc thiếu `X-Internal-Token` | Kiểm lại TOKEN_A |
| `BUDGET_EXHAUSTED` | 429 | Hết ngân sách token dùng chung | Trả `429` cho user kèm `retry_after_seconds` |
| `PROVIDER_UNAVAILABLE` | 503 | Nhà cung cấp LLM hỏng sau khi đã retry | Trả `503` **message tiếng Việt**, không phải `500` |
| `INDEX_NOT_READY` | 503 | Model chưa nạp xong hoặc index rỗng | Thử lại sau vài giây |

`retry_after_seconds` chỉ xuất hiện khi có ý nghĩa.

> Không bao giờ có traceback trong response. Nếu thấy traceback, đó là bug — báo đội AI.

---

## 7. Chạy thử ở máy local

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

Khởi động mất khoảng 3 giây (nạp model embedding). Rồi:

```bash
curl localhost:8000/healthz
curl localhost:8000/readyz

curl -H "X-Internal-Token: dev-token" localhost:8000/internal/v1/index/status

curl -X POST -H "X-Internal-Token: dev-token" \
  "localhost:8000/internal/v1/index/sync?full=true"
```

Mở `http://localhost:8000/docs` để xem OpenAPI tương tác.

Muốn thử với backend thật thì đổi trong `.env`:

```bash
AI_SOURCE_MODE=http
AI_BACKEND_URL=http://localhost:8080/fsoft
AI_BACKEND_TOKEN=<TOKEN_B>
```

---

## 8. Checklist bàn giao

**Phía Java**

- [ ] `InternalCardController` với hai endpoint ở [mục 4](#4-backend-phải-cung-cấp-hai-endpoint-nội-bộ)
- [ ] Filter kiểm `X-Internal-Token`, `SecurityFilterChain` riêng loại `/internal/**` khỏi JWT
- [ ] Pass đủ 9 mục acceptance ở [4.5](#45-acceptance-cho-phía-java)
- [ ] Sinh TOKEN_B, đặt vào env Railway của **cả hai** service
- [ ] Nhận TOKEN_A từ đội AI, đặt vào env Railway của backend
- [ ] Frontend **không** gọi thẳng fsoft-ai
- [ ] Tắt fsoft-ai → backend trả `503` message tiếng Việt, **không phải** `500`

**Phía hạ tầng**

- [ ] fsoft-ai và backend nằm **cùng project** Railway
- [ ] fsoft-ai **không** gán public domain
- [ ] Health check của fsoft-ai trỏ `/healthz`, không phải `/readyz`
- [ ] TOKEN_A và TOKEN_B là **hai giá trị khác nhau**
- [ ] Sau deploy: `POST /internal/v1/index/sync?full=true&sweep=true`, theo dõi `card_count`
      cho tới khi khớp số thẻ thật

---

**Vướng gì hỏi đội AI.** Đặc tả đầy đủ, kể cả phần bên trong fsoft-ai, nằm ở
[`docs/SPEC.md`](SPEC.md) — nhưng để tích hợp thì tài liệu này là đủ.
