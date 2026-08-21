# Kết quả kiểm thử phía Backend — gửi đội Backend

> Tài liệu này có **hai đợt kiểm**: mục 1–7 là hai endpoint nội bộ (16/08/2026), mục 8 là
> ba endpoint `/api/ai/*` mà BE mở ra cho client (22/08/2026). Mục 8 là phần **đang chặn
> tính năng**, đọc trước nếu ít thời gian.

Ngày kiểm thử: 16/08/2026.
Môi trường: `https://fsoft-project-production.up.railway.app/fsoft` (bản Railway).
Token dùng để gọi: `fsoft-ai.backend-token` các bạn cấp.
Chỉ gọi `GET`, không ghi, không đụng dữ liệu.

> Ghi chú: `localhost:8080` lúc mình test không chạy nên mình bắn vào bản Railway. Cùng
> token, cùng kết quả với ảnh Postman các bạn gửi.

**Tóm tắt: hợp đồng API các bạn làm đúng hết. Không cần sửa code.** Vấn đề còn lại nằm ở
**dữ liệu** chứ không ở API: 5 điểm, trong đó `audioUrl` null toàn bộ đang chặn hẳn một
dạng quiz.

---

## 1. Những gì đã kiểm và đạt

Mình chạy chính client thật của fsoft-ai (`HttpCardSource`) vào endpoint của các bạn,
không dùng mock:

| Hạng mục | Kết quả |
|---|---|
| Đi hết 13 trang (`size=3`) | **39 thẻ, 0 trùng, 0 sót**, `last=true` đúng ở trang cuối |
| `/cards/ids` khớp với danh sách đi theo trang | thiếu `[]`, thừa `[]` |
| Sắp xếp `ORDER BY updated_at ASC, id ASC` | đúng |
| `since` dùng `>=` chứ không phải `>` | **đúng** — gửi `since` = mốc lớn nhất thì trả về 1 thẻ, không phải 0 |
| Parse `Z`, có và không có phần thập phân giây | cả hai đều 200 |
| `since` trong tương lai (2099) | trả về 0 thẻ, đúng |
| 401 khi thiếu hoặc sai `X-Internal-Token` | đúng |
| 13 field camelCase | **khớp chính xác** bảng ánh xạ của fsoft-ai, không thiếu field nào |
| `/cards/ids` trả mảng số nguyên trần, có phân trang thật | đúng |

Bốn cái bẫy mình cảnh báo ở `BACKEND_INTEGRATION.md` — sắp xếp, `>=`, hậu tố `Z`,
phân trang — các bạn **tránh được cả bốn**. Cảm ơn.

Chạy nguyên service vào backend thật: đồng bộ **39/39 thẻ**, `last_sync_error = null`.
Đồng bộ lần hai `embedded = 0` (đúng, `content_hash` chặn được). Tìm kiếm 8–12ms.

---

## 2. Phân trang 1-based — đúng hợp đồng, chỉ ghi chú rủi ro

**Các bạn làm đúng, không cần sửa gì.** SPEC mục 3.3 đã chốt sẵn: *"Endpoint nội bộ
mới và module AI ở M6 chốt dùng kiểu deck: `page`/`size` phẳng, 1-based"*. Mình ghi lại
đây chỉ để cảnh báo một rủi ro về sau.

```
gửi page=0   ->  [1, 2, 3]     <- trùng y hệt page=1
gửi page=1   ->  [1, 2, 3]
gửi page=2   ->  [4, 5, 6]
gửi page=13  ->  [37,38,39]   last=true
gửi page=14  ->  []           last=true
```

Nghĩa là `offset = (page - 1) * size`, và `page=0` bị kẹp về trang 1.

Client fsoft-ai bắt đầu từ `page=1` nên khớp. Rủi ro nằm ở chỗ SPEC mục 3.3 cũng ghi
rằng codebase hiện có **hai kiểu phân trang lệch nhau**: Deck dùng `page`/`size` phẳng
1-based, còn Card dùng object `Pageable` 0-based. Endpoint nội bộ này nằm trong nhóm
Card nhưng lại theo quy ước của Deck.

Nghĩa là nếu sau này có ai đó thấy "endpoint card mà lại 1-based" rồi sửa cho đồng bộ
với phần Card còn lại, fsoft-ai sẽ **âm thầm bỏ mất trang đầu tiên**: không exception,
không log lỗi, chỉ là thiếu thẻ trong chỉ mục và người học không tra được từ.

**Đề nghị hai việc nhỏ:**

- Thêm comment ngay trên `InternalCardController` giải thích vì sao endpoint này cố ý
  1-based dù nằm trong nhóm Card, kèm một test khoá lại `page=1` trả về phần tử đầu.
- Cân nhắc bỏ việc kẹp `page=0` về trang 1. Hiện `page=0` và `page=1` trả về **y hệt
  nhau**; để `page=0` báo `400` sẽ khiến lỗi lộ ra ngay thay vì lặng lẽ trả trùng.

---

## 3. Các deck ID bị khuyết — đã có lời giải, không cần làm gì

Dữ liệu hiện có 8 deck: `4, 7, 9, 10, 11, 12, 14, 15`, khuyết `1, 2, 3, 5, 6, 8, 13`.

Đã xác nhận: **những deck đó đã bị xoá**, không phải bị lọc mất. Ghi lại đây để lần sau
ai nhìn vào danh sách ID không liên tục cũng không phải đi điều tra lại.

---

## 4. Năm vấn đề về dữ liệu, đang chặn tính năng thật

Đây là chất lượng dữ liệu trong DB chứ không phải lỗi code.

### 4.1 `audioUrl` null ở **39/39** thẻ

Quiz dạng **LISTENING không sinh được câu nào**. Hiện service tự lùi về câu trắc
nghiệm, nên client gọi quiz nghe sẽ nhận HTTP 200 kèm câu trắc nghiệm — học viên không
có gì để nghe. Cần biết: dự án có kế hoạch sinh file audio không, hay bỏ hẳn dạng
LISTENING khỏi phạm vi?

### 4.2 `definitionEn` null ở **38/39** thẻ

### 4.3 12/39 thẻ chỉ có `word` + `meaning`

Chủ yếu ở deck 7 và 15. Ví dụ thẻ 31 chỉ có `'test'` + `'thử'` — vỏn vẹn 15 ký tự để
embed.

Hậu quả **đo được**, không phải suy đoán: vector của các thẻ này dồn thành một cụm rất
sát nhau, cosine giữa chúng nằm trong khoảng **0.90–0.92**. Bộ sinh quiz loại bỏ ứng
viên nhiễu có cosine > 0.92 (để tránh chọn phải từ đồng nghĩa làm đáp án sai), nên
**6/10 thẻ deck 7 không tìm đủ 3 nhiễu**. Kết quả: xin 5 câu quiz chỉ nhận về **2 câu**.

Thẻ đủ 4–5 dòng thì hoàn toàn ổn. Ví dụ thẻ 1 (`negotiate`) có phonetic, part of speech,
definition, ví dụ song ngữ — nhiễu sinh ra rất tốt. Vấn đề nằm ở thẻ nghèo dữ liệu.

**Đề nghị:** với thẻ do người dùng tự tạo, tối thiểu nên có `meaning` + `exampleSentence`.
Nếu là dữ liệu seed để test thì nhờ các bạn làm đầy đủ hơn cho giống thật.

### 4.4 Deck 9 và deck 14 trùng hoàn toàn

Cùng tên `'Từ vựng Gia đình cơ bản'`, cùng 5 thẻ, cùng nghĩa từng chữ:

```
deck  9: mother=mẹ, father=bố, sibling=anh chị em ruột, grandparent=ông/bà, cousin=anh chị em họ
deck 14: mother=mẹ, father=bố, sibling=anh chị em ruột, grandparent=ông/bà, cousin=anh chị em họ
```

Hậu quả: tìm kiếm trả về hai kết quả y hệt nhau, người dùng thấy trùng lặp.

Cần biết đây là **dữ liệu test bị chạy hai lần**, hay là tính năng **nhân bản deck** có
thật. Nếu là tính năng thật thì mình cần xử lý khử trùng ở phía fsoft-ai.

### 4.5 Deck quá ít thẻ

`deck 15` có **1 thẻ**, `deck 4` có **2 thẻ**. Dưới mức tối thiểu 4 thẻ để tạo câu
trắc nghiệm, service trả về HTTP 400 kèm message tiếng Việt rõ ràng:

```
Bộ thẻ chỉ có 1 thẻ trong phạm vi, cần ít nhất 4 thẻ để tạo câu trắc nghiệm.
```

Không phải lỗi — chỉ cần frontend hiển thị message này thay vì báo "lỗi hệ thống", và
tốt hơn là ẩn nút tạo quiz khi deck có dưới 4 thẻ.

---

## 5. Một điểm nhỏ: `partOfSpeech` chưa thống nhất

Trên Railway thẻ 1 có `"partOfSpeech": "verb"`, còn trong ảnh Postman ở `localhost` thẻ 1
có `"partOfSpeech": "n"`. Hai bộ từ vựng khác nhau (`"verb"` vs `"n"`).

fsoft-ai chỉ in nguyên văn giá trị này vào ngữ cảnh nên **không hỏng gì**, nhưng câu trả
lời cho người học sẽ lúc thì "(verb)" lúc thì "(n)". Nên chốt một bộ giá trị duy nhất
(gợi ý: `noun`, `verb`, `adj`, `adv`, `prep`, `phrase`) và kiểm tra ở tầng nhập liệu.

---

## 6. Lỗi bên fsoft-ai, đã sửa xong — chỉ để các bạn nắm

Không cần các bạn làm gì, ghi lại cho minh bạch.

Khi test trên dữ liệu thật, câu `"từ nào nói về gia đình"` trả về `resign` ở **hạng 1**.
Nguyên nhân: tầng tìm kiếm từ khoá tách tiếng Việt theo âm tiết, và âm tiết `từ` trong
câu hỏi khớp với nghĩa `"từ chức"` của thẻ `resign`. Trong 39 thẻ, `resign` là thẻ duy
nhất chứa `từ` nên thuật toán tưởng đó là từ khoá quý hiếm, cho **2.85 điểm** trong khi
mọi thẻ khác đều **0 điểm** — toàn bộ thứ hạng bị một hư từ quyết định.

Đã sửa bằng cách lọc hư từ khỏi câu hỏi (giữ nguyên dữ liệu thẻ). Kiểm chứng lại trên
chính dữ liệu của các bạn:

```
trước:  từ nào nói về gia đình  ->  resign, grandparent, grandparent
sau:    từ nào nói về gia đình  ->  grandparent, grandparent, sibling
```

(Vẫn còn hai `grandparent` vì deck 9 và 14 trùng nhau — xem mục 4.4.)

---

## 7. Việc cần các bạn phản hồi

| # | Việc | Ai làm |
|---|---|---|
| 1 | Comment + test khoá lại quy ước 1-based cho endpoint nội bộ | BE |
| 2 | Cho biết deck 9 / 14 trùng là dữ liệu rác hay tính năng nhân bản | BE |
| 3 | Kế hoạch cho `audioUrl` — có sinh audio không, hay bỏ dạng quiz LISTENING | BE + PM |
| 4 | Bổ sung `definitionEn` / `exampleSentence` cho thẻ seed | BE |
| 5 | Thống nhất bộ giá trị `partOfSpeech` | BE |
| 6 | Frontend hiển thị message 400 khi deck dưới 4 thẻ | FE |

Chi tiết cách gọi ba endpoint chat / search / quiz nằm ở
[BACKEND_INTEGRATION.md](BACKEND_INTEGRATION.md).

---

## 8. ⛔ Ba endpoint `/api/ai/*` đang hỏng — kiểm ngày 22/08/2026

Đợt này kiểm **đúng đường người dùng thật đi**: đăng nhập bằng tài khoản `sakaka1@gmail.com`
qua `POST /auth/login`, rồi gọi ba endpoint AI bằng JWT nhận được.

**Kết luận: cả ba đều lỗi, và fsoft-ai không liên quan.** Mọi lời gọi tương đương đánh thẳng
vào fsoft-ai đều trả `200` với dữ liệu đúng. Lỗi nằm ở lớp proxy phía BE.

Tài khoản test sở hữu 3 deck: **22** "Từ vựng Công việc văn phòng" (PRIVATE, 6 thẻ),
**34** "Từ vựng Du lịch" (PRIVATE, 5 thẻ), **31** "meongao" (PUBLIC, 0 thẻ).

### 8.1 `GET /api/ai/search` — trả `200` nhưng **sai phạm vi**, deck của chính user vô hình

Chín truy vấn khác nhau, và BE **chỉ trả về đúng một thẻ duy nhất**: `card_id 2`, `deck 4`.

| Truy vấn | Qua BE | Thẳng fsoft-ai với `allowed_deck_ids=[22,34,31]` |
|---|---|---|
| `hộ chiếu` | `[]` | `[(82, deck 34)]` |
| `sân bay` | `[]` | `[(81, deck 34)]` |
| `lịch trình` | `[]` | `[(84, deck 34)]` |
| `meeting` | `[]` | `[(67, deck 22)]` |
| `cuộc họp` | `[(2, deck 4)]` | `[(67, deck 22)]` |
| `đồng nghiệp` | `[(2, deck 4)]` | `[(69, deck 22), (71, deck 22)]` |
| `hành lý` | `[(2, deck 4)]` | `[(83, 34), (68, 22), (71, 22), (84, 34)]` |

Deck 22 và 34 là deck **của chính người đang đăng nhập**, có thẻ, đã được index — nhưng không
lần nào xuất hiện. Còn deck 4 (`Smoke Deck da doi ten`) thì lần nào cũng có.

**Nghĩa là `allowed_deck_ids` mà BE gửi sang fsoft-ai không phải danh sách deck của user.**
fsoft-ai tin tuyệt đối vào danh sách đó (theo đúng thiết kế), nên nó lọc đúng những gì được
đưa — chỉ là được đưa sai.

Việc cần làm: log ra chính xác `allowed_deck_ids` mà BE gửi trong một request thật. Nó phải
là hợp của *deck user sở hữu* + *deck được chia sẻ* + *deck public*, chứ không phải một danh
sách cố định.

> Đây đồng thời là **rủi ro bảo mật hai chiều**: gửi thiếu thì user không thấy thẻ của mình
> (đang xảy ra), gửi thừa thì user đọc được thẻ riêng tư của người khác. fsoft-ai không có
> cách nào tự phát hiện, xem [BACKEND_INTEGRATION.md mục 2](BACKEND_INTEGRATION.md#2-ranh-giới-trách-nhiệm).

### 8.2 `POST /api/ai/quiz` — hỏng **100%**, mọi deck

Thử 7 deck, qua BE hỏng hết; cùng tham số đánh thẳng fsoft-ai thì chạy:

| `deck_id` | Qua BE | Thẳng fsoft-ai |
|---|---|---|
| 7, 9, 10, 11, 22, 34 | `500 "Lỗi sinh bài tập AI"` | `200`, 4 câu |
| 4 | `500 "Lỗi sinh bài tập AI"` | `400` — "Bộ thẻ chỉ có 2 thẻ trong phạm vi" (đúng, deck này thiếu thẻ) |

Nghi nguyên nhân **cùng gốc với 8.1**: nếu BE tự đè `allowed_deck_ids` bằng danh sách sai thì
`deck_id` nằm ngoài danh sách đó, fsoft-ai trả `400 INVALID_SCOPE`, và BE dịch thành `500`.

Kèm hai lỗi phụ:

**a. `"card_ids": []` bị chặn ở tầng validate của BE.**

```
{"deck_id":22,"allowed_deck_ids":[22],"card_ids":[]}        -> 400 "Validation error"
{"deck_id":22,"allowed_deck_ids":[22],"card_ids":[1,2,3,4]} -> qua duoc tang validate
{"deck_id":22,"allowed_deck_ids":[22]}                      -> qua duoc tang validate
```

Mảng rỗng là giá trị **hợp lệ**, nghĩa là "lấy cả deck" — và nó nằm ngay trong ví dụ mặc định
của Swagger fsoft-ai, nên ai copy ví dụ đó sang cũng dính. Bỏ ràng buộc `@NotEmpty` trên
`cardIds`.

**b. `400 "Validation error"` không nói field nào sai.** Mình phải bisect từng trường mới tìm
ra. Trả kèm tên field và lý do.

### 8.3 `POST /api/ai/chat` — hỏng **100%**, và mã lỗi tự mâu thuẫn

```
HTTP 500   body: {"status": 429, "message": "Lỗi hỏi đáp AI"}
```

Ba lần liên tiếp đều vậy. Cùng lúc đó, gọi thẳng fsoft-ai với **đúng** tham số đó trả `200`:

```json
{"answer": "**passport** ... [#82]", "answer_source": "DIRECT_LOOKUP",
 "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
```

Chú ý lượt này là `DIRECT_LOOKUP` — **0 token, không hề gọi LLM**, nên không thể là hết hạn
mức phía fsoft-ai.

Ba vấn đề trong một response:

1. **HTTP status không khớp body.** Ngoài là `500`, trong là `429`. Client bắt theo HTTP sẽ
   xử lý sai hoàn toàn.
2. **`429` bị nuốt.** fsoft-ai trả `BUDGET_EXHAUSTED` kèm `retry_after_seconds` để client biết
   chờ bao lâu. BE cần **truyền tiếp `429` và cả `retry_after_seconds`**, đừng đổi thành `500`.
3. **Không rõ `429` từ đâu.** Nếu là bộ giới hạn riêng của BE thì cần nói rõ trong message;
   nếu là chuyển tiếp từ fsoft-ai thì sai, vì cùng lúc đó fsoft-ai trả `200`.

### 8.4 Điểm thiết kế nên xem lại: client tự khai `allowed_deck_ids`

`GET /api/ai/search` chỉ nhận `query` và BE tự tính phạm vi — **đúng**.

Nhưng `POST /api/ai/chat` và `POST /api/ai/quiz` lại nhận `allowed_deck_ids` **từ client**.
Nghĩa là bất kỳ ai có JWT hợp lệ đều có thể tự điền ID deck riêng tư của người khác vào và
đọc được nội dung. fsoft-ai không kiểm quyền — nó không biết user là ai.

Nên bỏ `allowed_deck_ids` khỏi request của cả hai endpoint, cho BE tự tính giống `/search`.
Muốn giữ để client thu hẹp phạm vi thì phải **giao với** danh sách BE tính được, đừng dùng
thẳng.

### 8.5 Cách tái hiện

```bash
BE=https://fsoft-project-production.up.railway.app/fsoft
JWT=$(curl -s -X POST "$BE/auth/login" -H 'Content-Type: application/json'   -d '{"email":"...","password":"..."}' | jq -r .data.token.accessToken)

# 8.1 — tra ve [] du user so huu deck 34 chua the "passport"
curl -s "$BE/api/ai/search?query=h%E1%BB%99%20chi%E1%BA%BFu" -H "Authorization: Bearer $JWT"

# 8.2 — 500 voi moi deck
curl -s -X POST "$BE/api/ai/quiz" -H "Authorization: Bearer $JWT"   -H 'Content-Type: application/json'   -d '{"deck_id":22,"allowed_deck_ids":[22],"question_count":4}'

# 8.3 — HTTP 500 nhung body status 429
curl -s -X POST "$BE/api/ai/chat" -H "Authorization: Bearer $JWT"   -H 'Content-Type: application/json'   -d '{"query":"passport nghia la gi","allowed_deck_ids":[22,34]}'
```

### 8.6 Việc cần làm, xếp theo mức chặn

| # | Việc | Mức |
|---|---|---|
| 1 | Log và sửa `allowed_deck_ids` BE gửi sang fsoft-ai — hiện không phải deck của user | **Chặn 8.1 + 8.2** |
| 2 | Đừng dịch lỗi fsoft-ai thành `500`; giữ nguyên mã và `retry_after_seconds` | **Chặn 8.3** |
| 3 | Bỏ `@NotEmpty` trên `cardIds` để chấp nhận `[]` | Cao |
| 4 | Bỏ `allowed_deck_ids` khỏi request của `/api/ai/chat` và `/api/ai/quiz` | **Bảo mật** |
| 5 | `400 Validation error` phải nói rõ field nào sai | Trung bình |
| 6 | Sửa `/fsoft/v3/api-docs` đang trả `500` (Swagger UI mở được nhưng không nạp được spec) | Thấp |

