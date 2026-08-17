# M0 — Findings

> Kết quả spike M0 theo [SPEC.md §11.1](SPEC.md). Đây là **ràng buộc bắt buộc** cho M1 trở đi:
> code phải theo đúng kết luận trong tài liệu này, đặc biệt là phần prefix ở §2.3.
>
> Ngày chạy: 16/08/2026 · Máy: Windows 10, Python 3.12.4 · Sinh ra bởi
> `scripts/m0_embedding.py` và `scripts/m0_groq.py`.

> ## ⚠ Phần LLM của tài liệu này đã LỖI THỜI (17/08/2026)
>
> **Cả hai model M0 chọn đều đã bị Groq khai tử.** Gọi vào trả `404 model_not_found`, và triệu
> chứng ở API là `PROVIDER_UNAVAILABLE` "Trợ lý AI đang quá tải" — không hề nói ra nguyên nhân.
> Chat hỏng 100% cho tới khi đổi model.
>
> | M0 chọn | Trạng thái | Thay bằng |
> |---|---|---|
> | `llama-3.3-70b-versatile` (chat, quiz) | KHAI TỬ | `openai/gpt-oss-120b` |
> | `llama-3.1-8b-instant` (rewrite, fallback) | KHAI TỬ | `openai/gpt-oss-20b` |
>
> Kèm theo đó, **TPM thật là 8.000 chứ không phải 12.000**, nên `AI_GLOBAL_TOKENS_PER_MINUTE`
> đi từ 9.600 xuống 6.400, và `AI_MAX_OUTPUT_TOKENS` từ 400 lên 700 vì họ `gpt-oss` sinh
> `reasoning_tokens` ẩn trừ vào hạn mức đó. Số hiện hành ở [SPEC §4.1](SPEC.md#41-ràng-buộc-cứng--groq-free-tier-8000-tokenphút-cho-model-chat).
>
> **Phần Embedding (§2) vẫn còn đúng nguyên** — chỉ phần LLM (§3 trở đi) là lỗi thời. Giữ lại
> nguyên văn thay vì sửa lịch sử: nó là bản ghi của những gì đã đo được ngày 16/08/2026.

---

## Mục lục

1. [Tóm tắt — những gì M1 phải tuân theo](#1-tóm-tắt--những-gì-m1-phải-tuân-theo)
2. [Embedding](#2-embedding)
3. [Groq](#3-groq)
4. [Đối chiếu acceptance §11.1](#4-đối-chiếu-acceptance-111)
5. [Chênh lệch so với giả định ban đầu của SPEC](#5-chênh-lệch-so-với-giả-định-ban-đầu-của-spec)

---

## 1. Tóm tắt — những gì M1 phải tuân theo

| Hạng mục | Kết luận |
|---|---|
| Model embedding | `intfloat/multilingual-e5-small`, **không có** trong fastembed built-in registry |
| Cách nạp | `TextEmbedding.add_custom_model(...)` + `specific_model_path` trỏ snapshot local |
| **Prefix** | **TỰ NỐI TAY.** fastembed không thêm gì cả |
| Số chiều / norm | 384 · L2 norm = 1.000000 → cosine = dot product, không chia norm |
| Chất lượng ngữ nghĩa tiếng Việt | **ĐẠT** |
| RSS sau khi nạp model | **784 MB** (tăng 710 MB) — xem cảnh báo ở [§2.6](#26-ram-và-tốc-độ) |
| Model chat / quiz | `llama-3.3-70b-versatile` |
| Model rewrite / fallback | `llama-3.1-8b-instant` |
| TPM thật (chat) | **12.000** → `AI_GLOBAL_TOKENS_PER_MINUTE = 9600` |
| JSON mode | **Có hoạt động** trên `llama-3.3-70b-versatile` → M5 dùng được `response_format` |

---

## 2. Embedding

Nguồn: `uv run python scripts/m0_embedding.py`

### 2.1 Model không có trong built-in registry

```text
================================================================
1. FASTEMBED BUILT-IN SUPPORT
================================================================
intfloat/multilingual-e5-small: KHÔNG

>>> Model KHÔNG có trong FastEmbed built-in registry.
>>> Sử dụng CustomTextEmbedding.
```

Đây là chênh lệch đầu tiên so với giả định của SPEC §5.5. `TextEmbedding(model_name=...)`
gọi thẳng sẽ **ném lỗi**. Phải đăng ký trước:

```python
TextEmbedding.add_custom_model(
    model="intfloat/multilingual-e5-small",
    pooling=PoolingType.MEAN,
    normalization=True,
    sources=ModelSource(hf="intfloat/multilingual-e5-small"),
    dim=384,
    model_file="onnx/model.onnx",
    additional_files=[
        "onnx/tokenizer.json",
        "onnx/tokenizer_config.json",
        "onnx/special_tokens_map.json",
        "onnx/sentencepiece.bpe.model",
        "onnx/config.json",
    ],
)
```

> **Cạm bẫy:** gọi `add_custom_model` lần thứ hai với cùng tên sẽ raise.
> `app/embedding/encoder.py` phải guard cho idempotent.

### 2.2 File ONNX local

```text
================================================================
2. TÌM MODEL ONNX LOCAL
================================================================
Snapshot : .cache\fastembed\models--intfloat--multilingual-e5-small\snapshots\614241f622f53c4eeff9890bdc4f31cfecc418b3

Các file quan trọng:
  ✓ onnx/model.onnx                          448.5 MB
  ✓ onnx/config.json                         0.0 MB
  ✓ onnx/tokenizer.json                      16.3 MB
  ✓ onnx/tokenizer_config.json               0.0 MB
  ✓ onnx/special_tokens_map.json             0.0 MB
  ✓ onnx/sentencepiece.bpe.model             4.8 MB
```

Tổng khoảng **470 MB**, khớp con số SPEC §5.5 dự đoán.

Cache nằm ở `FASTEMBED_CACHE_PATH`. Dev local dùng `./.cache/fastembed` (đã nằm trong
`.gitignore`); trong Docker là `/opt/fastembed_cache` và được nạp sẵn lúc build image.

> Ban đầu cache nằm ở `%LOCALAPPDATA%\Temp\fastembed_cache`. Đã chuyển ra khỏi `Temp`
> vì Windows có quyền tự dọn thư mục đó — mất là phải tải lại 470 MB.

### 2.3 Prefix — kết luận quan trọng nhất của M0

```text
================================================================
5. PREFIX QUERY / PASSAGE
================================================================
FastEmbed CustomTextEmbedding KHÔNG tự thêm prefix.

Input query:
    "query: xin chào"
Input passage:
    "passage: xin chào"

>>> KẾT LUẬN:
>>> encoder.py PHẢI tự thêm:
    query   -> "query: " + text
    passage -> "passage: " + text

>>> Không dùng query_embed() để suy luận prefix.
```

### ✅ Kết luận: **TỰ NỐI TAY**

`app/embedding/encoder.py` chịu trách nhiệm nối prefix. Không chỗ nào khác được nối.

Ràng buộc đi kèm, theo SPEC §7.1:

| Thứ tự | Việc |
|---|---|
| 1 | `text = build_text(card)` — template 5 dòng |
| 2 | `content_hash = sha256(text)` — **tính TRƯỚC khi thêm prefix** |
| 3 | `"passage: " + text` — thêm **SAU** khi đã hash |
| 4 | Cắt còn 512 token rồi mới đưa vào model |

Nối prefix hai lần (`"query: query: ..."`) hỏng âm thầm y hệt như quên nối.

### 2.4 Dimension và normalization

```text
================================================================
6. DIMENSION & L2 NORMALIZATION
================================================================
số chiều : 384 (kỳ vọng 384)
L2 norm  : 1.000000 (kỳ vọng ~1.0)

Dimension test : ĐẠT
Normalize test : ĐẠT
```

Vector đã L2-normalize sẵn → **cosine = dot product**. `VectorIndex` không chia norm.

### 2.5 Ngữ nghĩa tiếng Việt

```text
================================================================
7. KIỂM TRA NGỮ NGHĨA TIẾNG VIỆT
================================================================
Query: query: từ nào chỉ cảm giác lo lắng

Ranking:
  1. apprehensive     0.8994
  2. anxious          0.8676
  3. diligent         0.8247
  4. deforestation    0.8208

>>> ĐẠT
>>> Hai từ liên quan đến lo lắng đứng top 2.
```

Đúng kỳ vọng của SPEC §10.1: cụm lo lắng (`apprehensive` 102, `anxious` 108) xếp trên
cụm chăm chỉ (`diligent` 107). Câu hỏi tiếng Việt truy được thẻ tiếng Anh — cross-lingual
hoạt động.

> **Lưu ý cho M2:** khoảng cách giữa hạng 2 (0.8676) và hạng 3 (0.8247) chỉ 0.043, và
> `deforestation` — từ hoàn toàn không liên quan — vẫn đạt 0.8208. Điểm cosine tuyệt đối
> của E5 **luôn cao và nén sát nhau**. Vì vậy `AI_MIN_SCORE=0.35` gần như không lọc được gì;
> thứ có ý nghĩa là **thứ hạng tương đối**, không phải ngưỡng tuyệt đối. Đây chính là lý do
> SPEC §11.3 hợp nhất bằng RRF (dựa trên rank) chứ không dựa trên score thô.

#### E5 là model BẤT ĐỐI XỨNG — đo thêm ở M1

Đo bốn kiểu ghép cặp trên cùng một bộ ba từ, để trả lời câu "so hai đoạn text bất kỳ thì
dùng prefix nào":

| Cách ghép | `cos(lo lắng, bồn chồn)` | `cos(lo lắng, cái bàn)` | Xếp đúng? |
|---|---:|---:|:---:|
| `query:` ↔ `query:` | 0.8349 | **0.8474** | ❌ |
| không prefix | 0.8819 | **0.8862** | ❌ |
| `passage:` ↔ `passage:` | **0.9271** | 0.9076 | ✅ |
| **`query:` ↔ `passage:`** | **0.8246** | 0.8108 | ✅ |

Và trên kịch bản RAG thật (câu hỏi tiếng Việt ↔ thẻ đã serialize):

| Thẻ | Điểm |
|---|---:|
| `apprehensive` | 0.8919 |
| `anxious` | 0.8514 |
| `meticulous` | 0.8402 |
| `deforestation` | 0.8276 |

**Kết luận:** E5 được huấn luyện cho ghép **bất đối xứng** `query: ` ↔ `passage: `. Đó đúng
là cách retrieval dùng nó, nên retrieval an toàn. Nhưng so **hai câu hỏi với nhau**
(`query:` ↔ `query:`) là dùng model ngoài phân phối huấn luyện — kết quả có thể **đảo ngược**,
đúng như bảng trên.

Hai chỗ trong hệ thống có so query với query, cần cẩn thận:

| Chỗ | Ảnh hưởng | Xử lý |
|---|---|---|
| **M4 semantic cache** (`query` mới ↔ `query` đã cache) | Thấp. Ngưỡng 0.97 rất cao, chỉ khớp câu gần như giống hệt | Giữ nguyên |
| **M2 intent centroid** (`query` ↔ centroid câu mẫu) | **Cao.** Cặp không liên quan vẫn đạt ~0.85, nên ngưỡng `AI_INTENT_THRESHOLD=0.50` sẽ **không bao giờ** phân loại được `OUT_OF_SCOPE` | Phải hiệu chỉnh lại ngưỡng bằng dữ liệu thật ở M2, đừng tin con số 0.50 trong SPEC |

> Vì vậy acceptance của SPEC §11.2 — `cos(embed("lo lắng"), embed("bồn chồn")) >
> cos(embed("lo lắng"), embed("cái bàn"))` — **chỉ đúng khi ghép bất đối xứng**. Test ở
> `tests/test_embedding.py` viết theo đúng chiều mà ứng dụng thật sử dụng, và có thêm một
> test ghim lại hành vi query↔query ở trên để M2 không bị bất ngờ.

### 2.6 RAM và tốc độ

```text
Thời gian load : 2.55s
RSS trước      : 73 MB
RSS sau        : 784 MB
RSS tăng       : 710 MB

8. TỐC ĐỘ — SINGLE QUERY
10 lần chạy
Trung bình : 10.20 ms / câu

9. TỐC ĐỘ — BATCH 64
Batch size       : 64
Batch time       : 392.88 ms
Per sentence     : 6.14 ms
Ước tính 10k     : 61.4 s
```

| Chỉ số | Giá trị | Đánh giá |
|---|---|---|
| Nạp model | 2.55s | Chấp nhận được cho cold start, miễn là model đã nằm sẵn trong image |
| **RSS sau khi nạp** | **784 MB** | ⚠️ Xem cảnh báo bên dưới |
| Embed 1 câu | 10.20 ms | Đủ nhanh cho đường nóng chat |
| Embed batch 64 | 6.14 ms/câu | Batch nhanh hơn 40% — syncer phải embed theo lô |
| Ước tính 10.000 thẻ | 61.4s | Khớp con số "khoảng 50 giây" ở SPEC §5.3 |

> ⚠️ **Cảnh báo RAM.** 784 MB đã chạm **trần trên** của ước tính SPEC §14.6 (600–900 MB),
> và đây mới chỉ là process embedding trần — chưa có FastAPI, chưa có vector index,
> chưa có BM25 index. Railway plan 512 MB sẽ **không đủ**.
>
> Phương án nếu chật, theo thứ tự ưu tiên:
> 1. Dùng bản ONNX quantized (`model_optimized.onnx` / int8) — giảm khoảng 4 lần
> 2. Nâng plan Railway
>
> Cần đo lại RSS thật trên container Linux ở cuối M1, vì con số trên đo trên Windows.

---

## 3. Groq

Nguồn: `GROQ_API_KEY=... uv run python scripts/m0_groq.py`

### 3.1 Model còn sống

```text
    allam-2-7b
    canopylabs/orpheus-arabic-saudi
    canopylabs/orpheus-v1-english
    groq/compound
    groq/compound-mini
    llama-3.1-8b-instant
    llama-3.3-70b-versatile
    meta-llama/llama-prompt-guard-2-22m
    meta-llama/llama-prompt-guard-2-86m
    openai/gpt-oss-120b
    openai/gpt-oss-20b
    openai/gpt-oss-safeguard-20b
    qwen/qwen3.6-27b
    whisper-large-v3
    whisper-large-v3-turbo

  llama-3.3-70b-versatile          CÒN SỐNG
  llama-3.1-8b-instant             CÒN SỐNG
  openai/gpt-oss-120b              CÒN SỐNG
  qwen/qwen3-32b                   KHÔNG THẤY - sửa lại CANDIDATES
```

`qwen/qwen3-32b` đã bị gỡ; bản thay thế hiện tại là `qwen/qwen3.6-27b`. Đây là minh chứng
sống cho việc Groq deprecate model thường xuyên — **mọi ID model phải nằm trong biến môi
trường, không hard-code**.

### 3.2 Rate limit thật

```text
MODEL: llama-3.3-70b-versatile
  độ trễ            : 0.47s
  TPM giới hạn      : 12000      <-- SỐ QUAN TRỌNG NHẤT
  RPM/RPD giới hạn  : 1000
  token dùng        : in=87 out=61

MODEL: llama-3.1-8b-instant
  độ trễ            : 0.42s
  TPM giới hạn      : 6000
  RPM/RPD giới hạn  : 14400
  token dùng        : in=87 out=129

MODEL: openai/gpt-oss-120b
  độ trễ            : 0.98s
  TPM giới hạn      : 8000
  RPM/RPD giới hạn  : 1000
  token dùng        : in=126 out=250
```

| Model | Độ trễ | **TPM** | RPM/RPD | Output tokens |
|---|---:|---:|---:|---:|
| `llama-3.3-70b-versatile` | 0.47s | **12.000** | 1.000 | 61 |
| `llama-3.1-8b-instant` | 0.42s | 6.000 | 14.400 | 129 |
| `openai/gpt-oss-120b` | 0.98s | 8.000 | 1.000 | 250 |

Header đọc được đầy đủ: `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens`,
`x-ratelimit-limit-requests`, `x-ratelimit-reset-tokens`. → M3 đọc được qua
`with_raw_response` đúng như SPEC §5.8 thiết kế.

### 3.3 Chất lượng tiếng Việt — chấm tay 1–5

| Model | Điểm | Nhận xét |
|---|:---:|---|
| `openai/gpt-oss-120b` | **4,5** | Tốt nhất. Có phiên âm, nghĩa chính xác ("có khả năng phục hồi nhanh"), ví dụ công sở tự nhiên kèm bản dịch khớp. Nhưng **bị cắt giữa chừng** vì chạm `max_tokens` — rất dài dòng |
| `llama-3.1-8b-instant` | **3** | Nghĩa đúng ("phục hồi, bền bỉ, sức chống chịu"). Nhưng câu ví dụ *"sự resilient trong việc..."* là lỗi ngữ pháp trộn Anh–Việt, và bản dịch **không khớp** câu ví dụ |
| `llama-3.3-70b-versatile` | **3** | Ngắn gọn, đúng từ loại, ví dụ tiếng Anh chuẩn. Nhưng dịch `resilient` thành *"kháng chịu", "đàn hồi"* — chọn từ kém tự nhiên |

### 3.4 Chốt model cho từng vai trò

| Vai trò | Model chốt | Lý do |
|---|---|---|
| Chat RAG | `llama-3.3-70b-versatile` | TPM cao nhất (12.000), nhanh nhất (0.47s), **súc tích nhất** (61 token out) |
| Viết lại câu hỏi | `llama-3.1-8b-instant` | Việc dễ, RPM 14.400 rộng rãi, không cần model mạnh |
| Sinh quiz JSON | `llama-3.3-70b-versatile` | Là model duy nhất đã xác nhận JSON mode chạy |
| Dự phòng khi 429 | `llama-3.1-8b-instant` | TPM riêng 6.000, tách biệt khỏi quota của 70b |

**Vì sao không chọn `gpt-oss-120b` dù tiếng Việt tốt hơn:**

1. TPM chỉ 8.000 so với 12.000 — mất 1/3 công suất phục vụ
2. Dài dòng gấp 4 lần (250 so với 61 token out) → **đốt ngân sách nhanh gấp 4**
3. Chậm gấp đôi (0.98s so với 0.47s), ảnh hưởng trực tiếp mục tiêu p95 < 3s ở SPEC §11.8

Điểm tiếng Việt thấp của 70b đến từ việc **nó phải tự nghĩ ra nghĩa tiếng Việt**. Trong ứng
dụng thật thì không như vậy: nghĩa tiếng Việt đã có sẵn trong phần NGỮ_CẢNH lấy từ chính thẻ
của người học, model chỉ diễn giải lại. Nhược điểm "chọn từ kém tự nhiên" vì thế bị vô hiệu hoá
phần lớn.

> Nếu tới M4 thấy chất lượng tiếng Việt vẫn là vấn đề thật, `openai/gpt-oss-120b` là ứng viên
> thay thế số một — chỉ cần đổi `AI_MODEL_CHAT`, không đổi dòng code nào.

### 3.5 Ngân sách token

Công thức SPEC §11.1 bước 4: đặt thấp hơn TPM thật khoảng 20%.

```
TPM thật của model chat  = 12.000
AI_GLOBAL_TOKENS_PER_MINUTE = 12.000 × 0,8 = 9.600
```

Số lượt chat phục vụ được, theo Phụ lục B (trung bình ~950 token/lượt sau tối ưu):

```
9.600 ÷ 950 ≈ 10,1 lượt chat/phút cho toàn app
```

Rộng hơn hẳn con số 6,3 mà SPEC §Phụ lục B ước tính, và chưa tính semantic cache.

> **Lưu ý:** model rewrite (`8b-instant`) có quota TPM **riêng, chỉ 6.000**. Token bucket
> trong `app/llm/budget.py` là bucket **toàn cục dùng chung**, canh theo 70b. An toàn vì
> rewrite chỉ tốn ~150 token và chỉ chạy khoảng 30% số lượt — còn rất xa 6.000. Nếu sau này
> thay `AI_MODEL_REWRITE` bằng model đắt hơn thì phải xem lại chỗ này.

### 3.6 JSON mode — M5 phụ thuộc

```text
======================================================================
3. JSON mode có hoạt động không? (M5 cần)
======================================================================
  llama-3.3-70b-versatile: OK
  {
  "word": "resilient",
   "meaning_vi": "có khả năng chịu đựng, chống chọi được với khó khăn, thử thách"
}
```

✅ **Có hoạt động.** M5 dùng được `response_format={"type": "json_object"}` cho
`app/quiz/llm_generator.py` đúng như SPEC §11.6 dự phòng.

Nhưng vẫn **bắt buộc giữ `app/quiz/validator.py`**: JSON mode chỉ đảm bảo *cú pháp* hợp lệ,
không đảm bảo *nội dung* đúng — số lựa chọn, `correct_index` hợp lệ, câu `FILL_BLANK` có
`______`, đáp án không lộ trong đề bài.

---

## 4. Đối chiếu acceptance §11.1

| # | Acceptance | Trạng thái |
|---|---|:---:|
| 1 | `docs/M0_FINDINGS.md` có đủ output của cả hai script | ✅ |
| 2 | Kết luận rõ ràng về prefix: `TỰ NỐI TAY` hay `fastembed tự xử lý` | ✅ **TỰ NỐI TAY** ([§2.3](#23-prefix--kết-luận-quan-trọng-nhất-của-m0)) |
| 3 | Vector đúng 384 chiều, norm ≈ 1.0 | ✅ 384 · 1.000000 |
| 4 | Test ngữ nghĩa tiếng Việt báo `ĐẠT` | ✅ |
| 5 | Chốt được ID model cho từng vai trò | ✅ ([§3.4](#34-chốt-model-cho-từng-vai-trò)) |
| 6 | Ghi rõ TPM thật và số lượt chat/phút suy ra | ✅ 12.000 → ~10,1 lượt/phút |
| 7 | Ghi RSS sau khi nạp model, kết luận có vừa Railway plan không | ⚠️ 784 MB — **không vừa plan 512 MB**, xem [§2.6](#26-ram-và-tốc-độ) |
| 8 | Có xác nhận của đội Java về hai endpoint và mốc thời gian | ❌ **CHƯA LÀM** |

> **Mục 8 là việc của con người, không phải của code.** Cần chốt với đội Java: đường dẫn
> `/fsoft/internal/cards/**`, tên header `X-Internal-Token`, ai sinh token, khi nào xong.
> Gửi kèm `tests/fixtures/cards.json` làm mẫu phản hồi. Không chặn M1–M5 (đã có
> `FixtureCardSource`), nhưng để tới M6 mới nói là muộn — SPEC §14.4 cảnh báo đúng điểm này.

---

## 5. Chênh lệch so với giả định ban đầu của SPEC

Bốn chỗ M0 chứng minh SPEC v3 đoán sai. SPEC đã được cập nhật theo.

| # | SPEC ban đầu | Thực tế đo được | Ảnh hưởng |
|---|---|---|---|
| 1 | §5.5 ngầm định fastembed dùng thẳng được model | Không có trong built-in registry | `encoder.py` phải `add_custom_model` idempotent |
| 2 | §4.1 giả định Groq free tier ~6.000 TPM | **12.000** cho model chat | Ngân sách 5.000 → **9.600**; ~10 lượt/phút thay vì ~2,5 |
| 3 | §11.6 "dùng JSON mode **nếu** M0 xác nhận" | Xác nhận **có** | M5 dùng được `response_format` |
| 4 | §14.6 ước tính RAM 600–900 MB | 784 MB, mới chỉ tính riêng embedding | Rủi ro RAM cao hơn dự kiến, cần đo lại trên Linux ở cuối M1 |
