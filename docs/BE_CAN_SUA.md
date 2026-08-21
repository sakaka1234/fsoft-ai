# fsoft-ai → Backend: sáu việc cần sửa

**Ngày kiểm:** 22/08/2026
**Cách kiểm:** đăng nhập bằng tài khoản thật qua `POST /fsoft/auth/login`, rồi gọi ba endpoint
`/api/ai/*` bằng JWT nhận được — đúng đường người dùng thật đi.
**Tài liệu này tự chứa.** Không cần đọc file nào khác.

---

## Tóm tắt

| # | Việc | Đang chặn gì | Mức |
|---|---|---|---|
| 1 | `allowed_deck_ids` BE gửi sang fsoft-ai không phải deck của user | `/api/ai/search` sai kết quả, `/api/ai/quiz` hỏng 100% | 🔴 **Chặn** |
| 2 | `/api/ai/chat` trả `429` mà **chưa hề gọi** fsoft-ai | Chat hỏng 100% | 🔴 **Chặn** |
| 3 | Lỗi từ fsoft-ai bị dịch hết thành `500` | Mất `retry_after_seconds`, client xử lý sai | 🟠 Cao |
| 4 | `"card_ids": []` bị chặn ở tầng validate | Ai copy ví dụ Swagger sang đều dính | 🟠 Cao |
| 5 | `/api/ai/chat` và `/api/ai/quiz` nhận `allowed_deck_ids` **từ client** | Đọc được thẻ riêng tư của người khác | 🔴 **Bảo mật** |
| 6 | `400 "Validation error"` không nói field nào sai; `/fsoft/v3/api-docs` trả `500` | Khó debug | 🟡 Thấp |

**Phía fsoft-ai không có lỗi nào.** Mọi lời gọi tương đương đánh thẳng vào fsoft-ai đều trả
`200` với dữ liệu đúng. Toàn bộ vấn đề nằm ở lớp proxy `/api/ai/*`.

---

## Bối cảnh: tài khoản dùng để kiểm

`sakaka1@gmail.com`, role `USER`, sở hữu 3 deck (lấy từ chính `GET /decks/my` của các bạn):

| deck_id | Tên | visibility | Số thẻ |
|---|---|---|---|
| 22 | Từ vựng Công việc văn phòng | **PRIVATE** | 6 |
| 34 | Từ vựng Du lịch | **PRIVATE** | 5 |
| 31 | meongao | PUBLIC | 0 |

Cả deck 22 và 34 **đã được fsoft-ai index đầy đủ** — kiểm bằng cách gọi thẳng thì tìm thấy
hết. Đây là điều quan trọng: dữ liệu có sẵn, chỉ là không tới được người dùng.

---

## 1. 🔴 `allowed_deck_ids` gửi sang fsoft-ai không phải deck của user

### Triệu chứng

Chín truy vấn khác nhau qua `GET /api/ai/search`, và BE **chỉ trả về đúng một thẻ duy nhất**:
`card_id 2`, thuộc `deck 4`.

| Truy vấn | Qua BE | Gọi thẳng fsoft-ai với deck của user |
|---|---|---|
| `hộ chiếu` | `[]` | `(82, deck 34)` |
| `sân bay` | `[]` | `(81, deck 34)` |
| `lịch trình` | `[]` | `(84, deck 34)` |
| `meeting` | `[]` | `(67, deck 22)` |
| `cuộc họp` | `(2, deck 4)` | `(67, deck 22)` |
| `đồng nghiệp` | `(2, deck 4)` | `(69, deck 22)`, `(71, deck 22)` |
| `lương` | `(2, deck 4)` | `(71, deck 22)` |
| `hành lý` | `(2, deck 4)` | `(83, 34)`, `(68, 22)`, `(71, 22)`, `(84, 34)` |
| `deadline` | `(2, deck 4)` | `(68, deck 22)` |

Deck 22 và 34 — **của chính người đang đăng nhập** — không lần nào xuất hiện. Deck 4
(`Smoke Deck da doi ten`) thì lần nào cũng có.

### Nguyên nhân

fsoft-ai **không biết user là ai**. Nó không có JWT, không có bảng quyền. Nó lọc đúng theo
`allowed_deck_ids` mà BE gửi sang, và tin tuyệt đối vào danh sách đó.

Kết quả trên cho thấy danh sách BE đang gửi không phải deck của user — nó cố định quanh
deck 4 bất kể ai đăng nhập.

### Cần sửa

Log ra **chính xác** `allowed_deck_ids` mà BE gửi sang fsoft-ai trong một request thật. Nó
phải là hợp của:

```
deck user sở hữu  ∪  deck được chia sẻ cho user  ∪  deck public
```

Đây cũng là **rủi ro hai chiều**: gửi thiếu thì user không thấy thẻ của mình (đang xảy ra);
gửi thừa thì user đọc được thẻ riêng tư của người khác. fsoft-ai không có cách nào tự phát
hiện cả hai.

### Cùng nguyên nhân này làm hỏng luôn quiz

`POST /api/ai/quiz` hỏng **100%**, thử 7 deck:

| `deck_id` | Qua BE | Gọi thẳng fsoft-ai, **cùng tham số** |
|---|---|---|
| 7, 9, 10, 11, 22, 34 | `500 "Lỗi sinh bài tập AI"` | `200`, 4 câu |
| 4 | `500 "Lỗi sinh bài tập AI"` | `400` — "Bộ thẻ chỉ có 2 thẻ trong phạm vi" (đúng, deck này thiếu thẻ) |

Nếu BE tự đè `allowed_deck_ids` bằng danh sách sai thì `deck_id` nằm ngoài danh sách đó,
fsoft-ai trả `400 INVALID_SCOPE`, và BE dịch thành `500`. Khớp với mọi quan sát.

---

## 2. 🔴 `/api/ai/chat` trả `429` mà chưa hề gọi fsoft-ai

### Triệu chứng

```
POST /fsoft/api/ai/chat
HTTP 500   body: {"status": 429, "message": "Lỗi hỏi đáp AI"}
```

Ba lần liên tiếp đều vậy.

### Bằng chứng nó không hề tới fsoft-ai

fsoft-ai có bộ đếm `GET /internal/v1/stats`. Mọi lượt chat đều được ghi, **kể cả lượt 0
token**. Đo:

```
fsoft-ai TRƯỚC : calls=0  chat_turns=0
   → 3 lần POST /api/ai/chat qua BE, cả 3 đều HTTP 500 / status 429
fsoft-ai SAU   : calls=0  chat_turns=0        (tăng 0)

Đối chứng — gọi THẲNG fsoft-ai một lần:
fsoft-ai TRƯỚC : calls=0  chat_turns=0
   → HTTP 200, answer_source = DIRECT_LOOKUP
fsoft-ai SAU   : calls=1  chat_turns=1        (tăng 1)
```

Gọi thẳng làm bộ đếm tăng; qua BE thì không. **Ba request kia dừng lại bên trong BE.**

### Nguyên nhân

`429` do chính BE tự sinh — một bộ giới hạn tần suất hoặc quota của BE đang bắn trên **mọi**
request, không phải chuyển tiếp từ fsoft-ai.

Thêm bằng chứng: lượt chat đó khi gọi thẳng cho `answer_source: DIRECT_LOOKUP` — **0 token,
không hề gọi LLM** — nên không thể là hết hạn mức phía fsoft-ai.

### Cần sửa

Tìm chỗ sinh `429` trong luồng `/api/ai/chat` và xem vì sao nó bắn ngay từ request đầu. Nếu
là quota theo user thì kiểm lại cách đếm; nếu là rate limit thì kiểm ngưỡng.

---

## 3. 🟠 Đừng dịch lỗi của fsoft-ai thành `500`

Response hiện tại **tự mâu thuẫn**: HTTP là `500` nhưng body ghi `"status": 429`. Client bắt
theo HTTP status sẽ xử lý sai hoàn toàn.

fsoft-ai trả lỗi theo dạng cố định, `message` **viết sẵn bằng tiếng Việt, hiện thẳng cho người
dùng cuối được**:

```json
{"error": {"code": "BUDGET_EXHAUSTED",
           "message": "Ngân sách token toàn cục đã cạn, thử lại sau 45 giây",
           "retry_after_seconds": 45}}
```

Bảng ánh xạ nên dùng:

| fsoft-ai trả | BE nên trả | Ghi chú |
|---|---|---|
| `400 INVALID_SCOPE` | `400` | Lỗi lập trình phía BE — đừng retry, sửa cách tính quyền |
| `400 INVALID_REQUEST` | `400` | Hiện `message` cho user |
| `401 UNAUTHORIZED` | `500` | Sai `X-Internal-Token` — lỗi cấu hình BE, **đừng** lộ ra user |
| `429 BUDGET_EXHAUSTED` | `429` | **Giữ nguyên `retry_after_seconds`** để client biết chờ bao lâu |
| `503 PROVIDER_UNAVAILABLE` | `503` | LLM lỗi. Retry backoff 2–3 lần rồi mới báo |
| `503 INDEX_NOT_READY` | `503` | Service đang khởi động, thử lại sau 5–10 giây |

**Quan trọng:** `503` từ `/chat` **không** được làm tắt cả tính năng AI. `/search` và
`/quiz` với `use_ai_context: false` chạy hoàn toàn không cần LLM — chúng vẫn phục vụ bình
thường khi LLM chết.

Và lưu ý một hình dạng lỗi **khác**: khi sai kiểu dữ liệu, fsoft-ai (FastAPI) trả `422` với
khoá `detail` chứ **không** có khoá `error`:

```json
{"detail":[{"type":"list_type","loc":["body","allowed_deck_ids"],
            "msg":"Input should be a valid list","input":"khong-phai-mang"}]}
```

Parser của BE phải chịu được cả hai hình dạng, nếu không sẽ ném NPE ở đúng chỗ khó debug nhất.

---

## 4. 🟠 `"card_ids": []` bị chặn ở tầng validate

```
{"deck_id":22,"allowed_deck_ids":[22],"card_ids":[]}         -> 400 "Validation error"
{"deck_id":22,"allowed_deck_ids":[22],"card_ids":[1,2,3,4]}  -> qua được tầng validate
{"deck_id":22,"allowed_deck_ids":[22]}                       -> qua được tầng validate
```

Mảng rỗng là giá trị **hợp lệ**, nghĩa là "lấy cả deck". Nó nằm ngay trong ví dụ mặc định của
Swagger fsoft-ai, nên ai copy ví dụ đó sang cũng dính.

**Sửa:** bỏ ràng buộc `@NotEmpty` trên `cardIds`.

> Nhân tiện, một luật của fsoft-ai cần biết khi làm tính năng "ôn lại thẻ đang sai":
> **`card_ids` phải có ít nhất 4 phần tử**, ít hơn thì trả `400`. Câu trắc nghiệm cần 1 đáp án
> đúng và 3 phương án nhiễu, mà nhiễu lấy từ chính các thẻ trong phạm vi. Người dùng sai 2 thẻ
> thì phải bù cho đủ 4, hoặc ẩn nút ôn tập.
>
> Và `question_count` bị chặn trên bởi số thẻ có thật: xin 5 câu với 4 thẻ thì nhận 4 câu,
> HTTP vẫn `200`. Đừng giả định `questions.length == question_count`.

---

## 5. 🔴 Client không được tự khai `allowed_deck_ids`

`GET /api/ai/search` chỉ nhận `query` và BE tự tính phạm vi — **đúng, giữ nguyên**.

Nhưng `POST /api/ai/chat` và `POST /api/ai/quiz` lại nhận `allowed_deck_ids` **từ client**:

```json
{"query": "...", "allowed_deck_ids": [22, 34]}
```

Nghĩa là bất kỳ ai có JWT hợp lệ đều có thể tự điền ID deck riêng tư của người khác vào và đọc
được nội dung. fsoft-ai không kiểm quyền — nó không biết user là ai, đó là việc của BE.

**Sửa:** bỏ `allowed_deck_ids` khỏi request của cả hai endpoint, cho BE tự tính giống
`/search`. Nếu muốn giữ để client thu hẹp phạm vi (ví dụ "chỉ hỏi trong deck đang mở") thì
phải **giao với** danh sách BE tính được:

```java
Set<Long> hieuLuc = new HashSet<>(deckCuaUser);
hieuLuc.retainAll(clientGuiLen);   // giao, KHÔNG dùng thẳng clientGuiLen
```

---

## 6. 🟡 Hai điểm nhỏ

**`400 "Validation error"` không nói field nào sai.** Phải bisect từng trường mới tìm ra thủ
phạm là `card_ids`. Trả kèm tên field và lý do.

**`GET /fsoft/v3/api-docs` trả `500`:**

```json
{"status":500,"message":"Something went wrong: No static resource v3/api-docs
 for request '/fsoft/v3/api-docs'."}
```

Swagger UI ở `/fsoft/swagger-ui/index.html` mở được nhưng không nạp được spec.
`/fsoft/api-docs` thì chạy — có vẻ lệch cấu hình `springdoc.api-docs.path`.

---

## Cách tái hiện

```bash
BE=https://fsoft-project-production.up.railway.app/fsoft

JWT=$(curl -s -X POST "$BE/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"<email>","password":"<password>"}' | jq -r .data.token.accessToken)

# Lỗi 1 — trả [] dù user sở hữu deck 34 có thẻ "passport / hộ chiếu"
curl -s "$BE/api/ai/search?query=h%E1%BB%99%20chi%E1%BA%BFu" \
  -H "Authorization: Bearer $JWT"

# Lỗi 1 — 500 với mọi deck
curl -s -X POST "$BE/api/ai/quiz" -H "Authorization: Bearer $JWT" \
  -H 'Content-Type: application/json' \
  -d '{"deck_id":22,"allowed_deck_ids":[22],"question_count":4}'

# Lỗi 2 — HTTP 500 nhưng body status 429
curl -s -X POST "$BE/api/ai/chat" -H "Authorization: Bearer $JWT" \
  -H 'Content-Type: application/json' \
  -d '{"query":"passport nghia la gi","allowed_deck_ids":[22,34]}'

# Lỗi 4 — chỉ khác đúng một trường card_ids
curl -s -X POST "$BE/api/ai/quiz" -H "Authorization: Bearer $JWT" \
  -H 'Content-Type: application/json' \
  -d '{"deck_id":22,"allowed_deck_ids":[22],"card_ids":[]}'
```

---

## Nghiệm thu — sửa xong thì phải đạt hết

- [ ] `GET /api/ai/search?query=hộ chiếu` trả về **card 82, deck 34** (deck riêng tư của user)
- [ ] `POST /api/ai/quiz` với `deck_id: 22` trả `200` kèm 4 câu hỏi
- [ ] `POST /api/ai/chat` với `passport nghĩa là gì` trả `200`, `answer_source: DIRECT_LOOKUP`
- [ ] Sau một lượt chat thành công, `GET /internal/v1/stats` của fsoft-ai có `chat_turns` **tăng**
- [ ] `"card_ids": []` không còn bị `400`
- [ ] Khi fsoft-ai trả `429`, BE trả `429` kèm `retry_after_seconds`, **không** phải `500`
- [ ] `allowed_deck_ids` không còn nhận từ client ở `/api/ai/chat` và `/api/ai/quiz`
- [ ] Ngắt fsoft-ai → `/api/ai/*` trả `503` message tiếng Việt, **không** phải `500`
- [ ] User A không thể đọc thẻ của user B dù tự điền `deck_id` của B

---

**Cần gì cứ hỏi đội AI.** Hướng dẫn tích hợp đầy đủ — mọi endpoint, mã lỗi, giới hạn vận hành —
nằm ở `docs/BACKEND_INTEGRATION.md` trong repo fsoft-ai.
