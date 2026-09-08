# fsoft-ai — Kiến trúc & Workflow

> Tài liệu tổng hợp từ `docs/SPEC.md`, `README.md`, `docs/M0_FINDINGS.md` và `docs/BACKEND_INTEGRATION.md`.
> Mục tiêu: bức tranh lớn trước, chi tiết từng phần sau.

---

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Kiến trúc hệ thống](#2-kiến-trúc-hệ-thống)
3. [Kiến trúc nội bộ fsoft-ai](#3-kiến-trúc-nội-bộ-fsoft-ai)
4. [Workflow chính](#4-workflow-chính)
5. [Chi tiết từng phần](#5-chi-tiết-từng-phần)
   - 5.1 [Embedding & Vector Index](#51-embedding--vector-index)
   - 5.2 [Retrieval 3 tầng + RRF](#52-retrieval-3-tầng--rrf)
   - 5.3 [Intent Classifier](#53-intent-classifier)
   - 5.4 [Chat Orchestrator](#54-chat-orchestrator)
   - 5.5 [Semantic Cache](#55-semantic-cache)
   - 5.6 [Token Budget & LLM Client](#56-token-budget--llm-client)
   - 5.7 [Store — SQLite & Repos](#57-store--sqlite--repos)
   - 5.8 [Sync — vòng lặp đồng bộ](#58-sync--vòng-lặp-đồng-bộ)
   - 5.9 [Vocab — extract / generate / lookup](#59-vocab--extract--generate--lookup)
   - 5.10 [Quiz](#510-quiz)
   - 5.11 [API layer & bảo mật](#511-api-layer--bảo-mật)
   - 5.12 [Stats & quan trắc](#512-stats--quan-trắc)
6. [Ràng buộc cấm & nguyên tắc](#6-ràng-buộc-cấm--nguyên-tắc)
7. [Triển khai & vận hành](#7-triển-khai--vận-hành)

---

## 1. Tổng quan

`fsoft-ai` là **một cỗ máy RAG thuần tuý cho từ vựng tiếng Anh**, chạy độc lập với backend Java.
Nó trả lời câu hỏi **chỉ dựa trên bộ thẻ của chính người học**, tìm kiếm ngữ nghĩa, sinh quiz,
và ba endpoint từ vựng (extract / generate / lookup).

**Nguyên tắc chi phối mọi thiết kế:** *token LLM là tài nguyên đắt nhất.*

- Groq free tier cho **8.000 TPM** trên `openai/gpt-oss-*`; service tự đặt trần **6.400** (80%).
- Mọi câu hỏi đi qua bộ lọc 0-token trước, chỉ câu thật sự cần suy luận mới chạm LLM.
- Mục tiêu vận hành: **≥ 40% lượt chat rơi vào ba nhánh 0 token** (`GET /internal/v1/stats`).

| Nhánh trả lời | Token | Khi nào |
|---|---|---|
| `CANNED` | 0 | Câu ngoài chủ đề học tiếng Anh |
| `DIRECT_LOOKUP` | 0 | Tra nghĩa một từ có sẵn trong bộ thẻ |
| `CACHE` | 0 | Câu tương tự đã hỏi trong 24 giờ, cùng phạm vi deck |
| `RAG` | ~800–1.200 | Cần LLM diễn giải trên ngữ cảnh lấy từ bộ thẻ |
| `LLM_ONLY` | ~600 | Không thẻ nào khớp — trả lời kèm cảnh báo |

---

## 2. Kiến trúc hệ thống

Ba lớp, hai chiều gọi nhau nhưng **không vòng lặp**:

```
Frontend
   │  JWT, HTTPS, public
   ▼
Backend Java  (/fsoft/**)                    ← Spring Boot 3, MySQL 8, JWT
   │
   ├── /fsoft/ai/**          auth · quota · phân quyền · hội thoại · job quiz
   │       │  X-Internal-Token, mạng nội bộ Railway
   │       ▼
   │   fsoft-ai  (fsoft-ai.railway.internal:8000)   ← KHÔNG có public domain
   │       │
   │       ├──> SQLite (1 file)   thẻ + vector + nhật ký
   │       ├──> RAM               vector index · semantic cache · token bucket
   │       └──> Groq              https://api.groq.com/openai/v1
   │
   └── /fsoft/internal/cards/**   ← fsoft-ai KÉO dữ liệu thẻ về qua đây
           (X-Internal-Token, bỏ qua JWT và phân quyền)
```

- **Chiều 1 (gọi):** Java gọi fsoft-ai để chat / search / quiz.
- **Chiều 2 (kéo):** fsoft-ai chủ động kéo thẻ từ Java mỗi 120 giây. Java không cần biết fsoft-ai tồn tại.

**Ranh giới trách nhiệm — nguyên tắc số 1:**

| Backend Java làm | fsoft-ai làm |
|---|---|
| Xác thực JWT, lấy `profile_id` | Embedding, vector index |
| Quyết định user đọc được deck nào | Retrieval, phân loại intent |
| Quota theo user, FREE/PRO | Gọi LLM, quản ngân sách token toàn cục |
| Lưu hội thoại, lịch sử (U020) | Sinh câu hỏi quiz |
| Vòng đời job quiz | **Không biết gì về user** — không JWT, không role |

Lý do: logic phân quyền đã tồn tại trong Java. Viết lại bằng Python nghĩa là hai bản logic có thể
lệch nhau — hậu quả là lộ bộ thẻ riêng tư. Một nguồn sự thật duy nhất. Lợi ích phụ: fsoft-ai test
được hoàn toàn độc lập (`allowed_deck_ids=[1,2]` là chạy).

---

## 3. Kiến trúc nội bộ fsoft-ai

```
app/
  api/v1/       chat · search · quiz · vocab · index · stats   (endpoints + X-Internal-Token guard)
  chat/         orchestrator 12 bước, semantic cache, canned answer, direct answer
  core/         logging có cấu trúc (structlog), lỗi và mã lỗi, preflight
  embedding/    encoder ONNX (fastembed), vector index trong RAM, text builder
  llm/          client Groq (SDK openai), token budget, prompt registry, prompts/*.txt
  quiz/         generator (3 deterministic + 1 LLM), distractors, validator
  retrieval/    3 tầng + RRF, intent classifier, stopwords, context builder
  schemas/      pydantic model cho request/response
  store/        SQLite thuần SQL: card_repo, usage_repo, sync_state_repo, db
  sync/         syncer, CardSource protocol, http_source + fixture_source
  vocab/        extractor (M8), generator (M9), lookup (M10), guard, grounding, dedup
```

**Ba đường dữ liệu độc lập:**

1. **Đường nóng (read path)** — retrieval / chat / quiz / search đọc hoàn toàn từ RAM
   (`SearchIndex`), **không bao giờ chạm SQLite** khi xử lý request.
2. **Đường đồng bộ (write path)** — syncer nền ghi SQLite, cập nhật index, một writer duy nhất.
3. **Đường LLM** — mọi lời gọi đi qua `LlmClient` (ngân sách, retry, usage_log).

**Ba cái bẫy Python đã xử lý sẵn:**

- **GIL:** endpoint `async def` không gọi `model.embed()` trực tiếp — mọi tác vụ chặn
  (tokenize, numpy, ONNX) chạy qua `anyio.to_thread.run_sync`.
- **Oversubscription:** `OMP_NUM_THREADS=1`, `ORT_NUM_THREADS=1` set **trước khi import fastembed**.
- **Worker:** chạy đúng **1 worker uvicorn** — mỗi worker nhân đôi RAM và chạy vòng lặp sync riêng.

---

## 4. Workflow chính

### 4.1 Luồng chat (12 bước — `ChatOrchestrator`)

```
backend Java ──► POST /internal/v1/chat   (query, allowed_deck_ids, history)
                        │
        ┌───────────────┴────────────────┐
        │ 1. Kiểm phạm vi deck           │ rỗng -> 400, KHÔNG hiểu là "tất cả" (fail closed)
        │ 2. Viết lại câu hỏi?           │ chỉ khi câu ngắn / có đại từ (~130 token)
        │ 3. Embed 1 lần                 │ 384 chiều, dùng lại cho bước 4 và 6
        │ 4. Phân loại ý định            │ 0 token: luật regex + centroid
        └───────────────┬────────────────┘
                        │
   ┌────────────────────┼──────────────────────┐
   ▼                    ▼                      ▼
OUT_OF_SCOPE      VOCAB_LOOKUP             còn lại
câu mẫu           tra thẳng bộ thẻ             │
CANNED · 0 token  DIRECT_LOOKUP · 0 token      ▼
                                    ┌────────────────────────┐
                                    │ 6. Retrieval 3 tầng     │
                                    │    khớp chính xác       │
                                    │    BM25 lexical         │
                                    │    semantic (cosine)    │
                                    │    hợp nhất bằng RRF    │
                                    └───────────┬────────────┘
                                                ▼
                                    ┌────────────────────────┐
                                    │ 7. Semantic cache       │  trúng -> CACHE · 0 token
                                    │ 8. Dựng ngữ cảnh        │  serialize thẻ gọn
                                    │ 9. Kiểm ngân sách        │  cạn -> 429, chưa gọi Groq
                                    │ 10. Gọi Groq             │  429 -> hạ model; vẫn hỏng -> 503
                                    └───────────┬────────────┘
                                                ▼
                                          RAG · ~1.000 token
```

Thứ tự các bước **KHÔNG được đổi**. Mỗi lượt 0-token cũng ghi dấu vết vào `usage_log`
(`provider = "local"`), để mẫu số của `free_ratio` luôn đúng.

### 4.2 Luồng đồng bộ thẻ (đường dữ liệu ngược, tách rời hoàn toàn)

```
backend Java ──► GET /fsoft/internal/cards/changed-since ──► embed ──► SQLite
                 GET /fsoft/internal/cards/ids                 │
                 (mỗi 120 giây, chỉ lấy thẻ đã đổi)            ▼
                                                     index vector + BM25 trong RAM
```

Xem chi tiết ở [§5.8](#58-sync--vòng-lặp-đồng-bộ).

### 4.3 Các luồng phụ

- **Quiz** (`POST /internal/v1/quiz/generate`): 3/4 dạng sinh deterministic từ dữ liệu thẻ
  (0 token); riêng FILL_BLANK gọi LLM theo lô 5 thẻ, JSON mode, validator chặn đáp án lộ.
  `use_ai_context=false` → 100% deterministic, không chạm Groq (chế độ dự phòng ngày bảo vệ).
- **Search** (`POST /internal/v1/search`): retrieval thuần, **luôn 0 token**, là công cụ debug
  retrieval tốt nhất.
- **Vocab** (3 endpoint): xem [§5.9](#59-vocab--extract--generate--lookup).
- **Index sync** (`POST /internal/v1/index/sync`): kích hoạt thủ công, `?full=true` bỏ qua con trỏ
  (vẫn rẻ nhờ `content_hash`), bắt buộc dùng sau khi đổi `AI_MODEL_VERSION`.

---

## 5. Chi tiết từng phần

### 5.1 Embedding & Vector Index

**Mô hình:** `intfloat/multilingual-e5-small` qua `fastembed` — 384 chiều, hỗ trợ 94 ngôn ngữ
(có tiếng Việt), chạy ONNX in-process trên CPU. **Không gọi API embedding nào, không tốn token.**

- Model **KHÔNG có** trong built-in registry của fastembed → phải
  `TextEmbedding.add_custom_model(...)` trước, guard idempotent (`app/embedding/encoder.py`).
- **Prefix E5 bắt buộc, tự nối tay:** câu hỏi nối `"query: "`, thẻ nối `"passage: "`. fastembed
  KHÔNG tự thêm — quên prefix thì hệ thống vẫn chạy bình thường nhưng retrieval tụt thảm âm thầm.
  Nối hai lần hỏng y hệt. Có test ghim lại hành vi này.
- **1 card = 1 chunk.** Card vốn là đơn vị ngữ nghĩa ngắn và hoàn chỉnh — không chunking, không
  text splitter. `content_hash = sha256(text)` tính **trước** khi nối prefix; template text có
  version (`t1`) nằm trong `model_version`.
- **Tối ưu RAM 936 → 317 MB:** lượng tử hoá int8 (936→538) rồi `scripts/tia_vocab.py` tỉa bảng
  Unigram 250k xuống 113k token vì **tokenizer mới là kẻ tốn RAM nhất** (250 MB), không phải model.
  Đổi `AI_EMBEDDING_MODEL_FILE` là phải đổi kèm `AI_MIN_SCORE` và `AI_MODEL_VERSION` — lượng tử
  hoá làm dịch phân bố cosine, sai cặp thì cổng lọc sai âm thầm.

**`VectorIndex`** (`app/embedding/vector_index.py`) — numpy trong RAM:

- `_matrix (N,384) float32` đã L2-normalize → **cosine = dot product**, không chia norm.
- 50.000 vector × 1 query: dưới 5ms. HNSW ở quy mô này chỉ thêm phức tạp.
- Lọc phạm vi bằng `_rows_of_deck` (nhóm sẵn theo deck), **không** `np.isin` toàn mảng mỗi query.
- Tách `VectorIndexProtocol` để sau này thay Qdrant chỉ là thêm một class.
- **Đổi model embedding là thao tác phá huỷ:** phải đổi cả hai biến env + `AI_MODEL_VERSION`,
  xoá index cũ, sync `full=true`. Quên đổi `AI_MODEL_VERSION` → index trộn vector hai model khác
  nhau — kết quả sai một cách rất khó lần ra.

### 5.2 Retrieval 3 tầng + RRF

`HybridRetriever` (`app/retrieval/hybrid.py`) hợp nhất bằng **Reciprocal Rank Fusion**:
`score(d) = Σ 1/(k + rank(d))`, rank bắt đầu từ 1. Thẻ xuất hiện ở cả hai danh sách được ưu tiên.

| Tầng | Cơ chế | Chi phí |
|---|---|---|
| 1. Khớp chính xác | `_word_index` (word viết thường → card_id) trong phạm vi deck | 0 token |
| 2. BM25 lexical | `rank_bm25` trên toàn corpus, lọc deck **sau** khi chấm điểm; corpus = word + meaning + example_sentence | 0 token |
| 3. Semantic | cosine trên vector đã embed, trong phạm vi deck | 0 token |

Chi tiết đáng nhớ:

- Hư từ tiếng Việt bị loại khỏi **câu hỏi** trước tầng 1 và 2 (`app/retrieval/stopwords.py`);
  câu toàn hư từ → rỗng. BM25Okapi tính sẵn IDF lúc khởi tạo nên không cập nhật từng phần được —
  phải dựng lại toàn bộ.
- Ngưỡng lọc semantic `AI_MIN_SCORE` **đi cặp với biến thể ONNX** — có test ghim lại từng cặp.
- `match_type` ∈ `EXACT | LEXICAL | SEMANTIC | HYBRID`.
- Bộ đo quality (khác test hành vi): ≥ 40 case, ngưỡng chặn Recall@5 ≥ 0.80, MRR ≥ 0.60,
  intent đúng ≥ 90%. Hiện tại 0.971 / 0.971 / 100%. Thoát mã 1 khi tụt → cắm thẳng CI, lưu
  `.jsonl` history thành artifact 90 ngày.

### 5.3 Intent Classifier

`app/retrieval/intent.py` — **0 lời gọi LLM**. Hai bước:

1. **Rule regex** — bắt các ý định rõ (`classify_by_rule`). Luật đặc biệt ưu tiên
   EXAMPLE_REQUEST hơn VOCAB_LOOKUP khi câu chứa dấu hiệu cả hai; hư từ tiếng Việt ("thì")
   chỉ được coi là ngữ pháp khi đúng vị trí.
2. **Centroid embedding** — mỗi intent có sẵn câu mẫu, embed một lần lúc warmup và giữ centroid;
  ưa `OUT_OF_SCOPE` khi không rule nào bắt được.

`intent` ∈ `VOCAB_LOOKUP | EXAMPLE_REQUEST | TRANSLATE | GRAMMAR_QA | QUIZ_REQUEST | SMALLTALK | OUT_OF_SCOPE`.
Chưa warmup thì suy biến an toàn, không raise. OUT_OF_SCOPE là một lớp thật có câu mẫu riêng.

### 5.4 Chat Orchestrator

`app/chat/orchestrator.py` — trạng thái giữa các bước gom trong `PreparedTurn` (kết quả bước 1–9:
mọi thứ cần để gọi LLM, hoặc câu trả lời đã có sẵn). Các phương thức chính:

| Phương thức | Vai trò | Lý do tồn tại |
|---|---|---|
| `._validate_scope()` | Bước 1 | `allowed_deck_ids` rỗng phải là **không thẻ nào** — toàn bộ ranh giới bảo mật |
| `.prepare()` | Bước 2–9 | Chuỗi embed → intent → retrieval → cache → context → budget |
| `._rewrite()` | Bước 2 | Viết lại tốn ~150 token nên chỉ làm khi câu ngắn/có đại từ; hỏng thì dùng câu gốc, không sập lượt chat |
| `._try_direct_answer()` | Bước 6 | Tầng 1 trúng + `is_simple_lookup()` → template trả lời, 0 token, vẫn ghi mã `[#id]` như khi LLM trả |
| `._log_free_turn()` | Bước 11 | Lượt 0 token cũng phải vào `usage_log` — `LlmClient` chỉ ghi lượt tốn token |
| `mark_used_citations()` | Bước 11 | Thẻ nào có mã `[#id]` trong câu trả lời là đã dùng |

**Streaming** (`POST /chat/stream`, SSE): `meta` → `citations` → `token`* → `done`. Sự kiện
`citations` **phải** phát trước token đầu tiên — giao diện hiện nguồn ngay, tạo cảm giác phản
xiêu nhanh. Nhánh CANNED cũng phát đúng chuỗi sự kiện, không rẽ định dạng riêng.

### 5.5 Semantic Cache

`app/chat/semantic_cache.py` — trong RAM, không Redis (với 1 replica, Redis là hạ tầng thừa; cả
cache và token bucket đều là dữ liệu tạm, mất khi restart cũng không sao).

- Danh sách có giới hạn (500 entry), quét tuyến tính — 500 × 384 dot product dưới 1ms, nhanh hơn
  một vòng đi Redis. Đuổi theo LRU khi đầy.
- Key tra cứu: **vector câu hỏi + `scope_hash`** (phạm vi deck) + TTL 24h. Cùng câu hỏi, hai
  phạm vi khác nhau → hai mục cache khác nhau (chống lộ thẻ riêng tư giữa hai người).
- `.invalidate_all()` gọi sau mỗi lần index đổi — câu trả lời đã cache dựng từ nội dung thẻ; thẻ
  đổi thì cache thành sai.

### 5.6 Token Budget & LLM Client

**`TokenBudget`** (`app/llm/budget.py`) — cửa sổ cố định theo phút:

- `.reserve()` **trước** khi gọi LLM (ước lượng `estimate_tokens`), cạn → `BudgetExhausted` → 429
  kèm `retry_after_seconds`. Chặn **trước** khi tốn một request nào.
- `.settle()` thay ước lượng bằng số thật từ usage. Hỏng toàn phần → trả lại chỗ đã giữ.
- `.sync_from_provider()` đồng bộ với header `x-ratelimit-remaining-tokens` — nhà cung cấp là
  nguồn sự thật, họ báo còn ít hơn thì tin họ.

**`LlmClient`** (`app/llm/client.py`) — SDK `openai` trỏ vào Groq (`base_url`), KHÔNG dùng SDK
groq riêng — đổi provider chỉ là đổi biến môi trường.

- `max_retries=0` **bắt buộc**: retry mặc định của SDK dùng backoff mù, không đọc header
  `retry-after`, sẽ thử lại quá sớm và ăn thêm 429. Tự xử: đọc `retry-after`, đợi đúng số giây,
  thử lại đúng số lần cấu hình.
- 429 mãi → **hạ cấp sang model dự phòng** (gpt-oss-120b → gpt-oss-20b). Mọi model mọi lần thử
  hỏng → 503 `PROVIDER_UNAVAILABLE`, message tiếng Việt, không lộ traceback.
- Ghi `usage_log` cho **MỌI** lời gọi, kể cả lời gọi lỗi — bỏ qua lỗi là cách chắc chắn nhất để
  không bao giờ biết tỷ lệ 429 thật.
- Phân vai model: chat/quiz = `gpt-oss-120b`, rewrite/fallback = `gpt-oss-20b`.
  **Bẫy `gpt-oss`:** sinh `reasoning_tokens` ẩn trừ vào `max_tokens` — dưới 300 thì JSON mode hỏng
  0/3 lần với lỗi không nói ra nguyên nhân. Vì vậy `AI_MAX_OUTPUT_TOKENS = 700`, không phải 400.
- **Prompt registry:** prompt là file `.txt` trong `app/llm/prompts/`, render `{{biến}}`; thiếu
  biến thì ném lỗi. Có test khẳng định không prompt nào được nhúng trong `.py` (docstring được quét
  AST để loại khỏi phép quét).

### 5.7 Store — SQLite & Repos

Một file SQLite (`AI_DB_PATH`, mặc định `./data/fsoft-ai.db`). Không server DB, không connection
pool, không ORM, không migration framework — SQL thuần trong `migrations/001_init.sql`, chạy tự
động lúc khởi động. WAL mode, `synchronous = NORMAL`.

| Bảng | Vai trò |
|---|---|
| `card` | Thẻ + **cả text lẫn vector** (BLOB float32 little-endian, L2-normalize). Không còn MySQL để join nên phải tự giữ đủ text để dựng context và sinh quiz |
| `sync_state` | Con trỏ đồng bộ: `last_sync_ts`, `last_full_sweep_at`, `last_sync_error` |
| `usage_log` | Nhật ký mọi lượt gọi LLM: task, provider, model, answer_source, intent, tokens, latency, success, error_code |

- Không có `profile_id` ở đâu cả — đúng nguyên tắc 5.1.
- `Database` kiểm quyền ghi **TRƯỚC** khi mở SQLite để lỗi nói được nó là lỗi gì;
  `check_same_thread=False`, gọi qua `anyio.to_thread.run_sync` (không cần `aiosqlite`).
- `CardRepo`: vector lưu/trả bằng `vector_to_blob` / `blob_to_vector`;
  `.load_index_rows()` nạp index lúc khởi động — **restart không tốn chi phí embedding**.
  Embedding là dữ liệu dẫn xuất — mất file DB không phải thảm hoạ, sync lại toàn bộ là xong
  (tính tự chữa lành), nhưng phải embed lại.

### 5.8 Sync — vòng lặp đồng bộ

`app/sync/syncer.py` — fsoft-ai **chủ động kéo**, backend không cần biết fsoft-ai tồn tại.
(Webhook đã bị loại: cần retry, cần xử lý sai thứ tự, mất một event là index sai vĩnh viễn mà
không ai biết.)

**Vòng gia tăng — mỗi 120 giây:**

1. `since = last_sync_ts − 5 giây` (lần đầu: kéo toàn bộ).
2. Phân trang `GET /internal/cards/changed-since?since=...&page=...&size=200`, mỗi thẻ ba nhánh:
   - `content_hash` khác (hoặc `model_version` lệch) → **embed lại**, ghi SQLite + index;
   - chỉ `deck_id` / `deck_title` / `audio_url` khác → ghi lại + cập nhật index, **dùng lại
     vector cũ** (0 embedding). Ba field này không nằm trong text embed — chỉ dựa hash thì thẻ
     chuyển deck sẽ giữ `deck_id` cũ trong index: vừa sai kết quả, vừa là **lỗ hổng phạm vi**;
   - giống hết → bỏ qua hẳn (kéo trùng thành no-op).
3. `last_sync_ts` = `max(updatedAt)` **nhìn thấy trong phản hồi**, KHÔNG dùng `datetime.now()`
   — lệch đồng hồ giữa hai container không gây mất dữ liệu. Cộng lùi 5 giây làm kéo trùng ở
   ranh giới trang; `content_hash` khiến kéo trùng thành no-op → toàn bộ vòng lặp idempotent.

**Quét toàn bộ — mỗi 60 phút:** `GET /internal/cards/ids` trả mảng ID trần;
`local_ids − remote_ids` = thẻ đã bị xoá → xoá khỏi SQLite và index. Feed gia tăng không thể báo
thẻ bị xoá, nên cần bước này.

**Không bao giờ raise ra ngoài:** lỗi đồng bộ ghi vào `sync_state.last_sync_error`, service vẫn
chạy bằng dữ liệu cũ. Sắp xếp `(updated_at ASC, id ASC)` phía backend là bắt buộc — thiếu
tie-break bằng `id` thì phân trang không ổn định và bỏ sót bản ghi.

**Nguồn dữ liệu:** `CardSource` protocol (`fetch_changed_since`, `fetch_all_ids`, `health`) với
hai hiện thực — `HttpCardSource` (camelCase↔snake_case chuyển đổi nằm đúng một chỗ; 401 không
retry, 5xx backoff mũ tối đa 3 lần) và `FixtureCardSource` (24 thẻ mẫu, mô phỏng đúng shape
`ApiResponse` của backend, đọc lại file mỗi lần gọi — cố ý không cache).

### 5.9 Vocab — extract / generate / lookup

Ba endpoint, ba hồ sơ chi phí khác nhau. Service **không lưu gì** — backend quyết định thẻ nào
vào deck nào sau khi người dùng chọn (ranh giới 5.1 giữ nguyên).

| Endpoint | Đặt chỗ ngân sách | Đường 0 token |
|---|---|---|
| `/vocab/extract` (M8) | **87%** ngân sách một phút | không có |
| `/vocab/generate` (M9) | **68%** | không có |
| `/vocab/lookup` (M10) | **38%** | **có hai** — từ đã có trong bộ thẻ, và cache dùng chung |

- **extract**: dán một đoạn văn (≤ 4.000 ký tự — cap này chính là cách né chunking), trả ứng viên
  thẻ. Ba tầng lọc: whitelist trường → chốt chặn ký tự (XSS/URL) → **bám văn bản**
  (`example_sentence` phải có thật trong đoạn văn — trường duy nhất model có thể dùng để đưa nội
  dung mới vào dữ liệu lưu trữ). Một lời gọi LLM, không thử lại. Model trả rác → 503 (khác quiz,
  ở đây không có đường lùi deterministic).
- **generate**: chỉ có một chủ đề. Không có văn bản gốc → **xuất xứ biến mất và không dựng lại
  được** — chỉ kiểm được hình dạng, không kiểm được xuất xứ. Vì vậy **bước người dùng xác nhận là
  ranh giới đúng-sai của tính năng**, không phải chi tiết giao diện. Bốn nhóm chốt chặn tất định
  (`app/vocab/guard.py`): trần độ dài, IPA hợp lệ, nghĩa có dấu tiếng Việt, câu ví dụ kết đúng
  dấu câu. `exclude_words` là *lời khuyên*, `already_in_deck` mới là *luật*.
- **lookup**: tra đúng một từ, dựng thẻ; `allowed_deck_ids` tuỳ chọn (`null` = không khai báo,
  `[]` = khai báo trống — hai trạng thái phải phân biệt); `WordCache` khoá theo từ + ngữ cảnh,
  dùng chung toàn cục; không có thẻ thì không có gì để cache. Đây là "cái nút bấm" nên không nằm
  chung hàng đợi semaphore nâng với extract/generate.

Đánh dấu trùng (`danh_dau_trung`) dùng chung cho cả ba: **đánh dấu, KHÔNG xoá** — từ người học đã
có vẫn trả về kèm `already_in_deck: true` + `existing_card_id` (luôn nằm trong `allowed_deck_ids`
— không tiết lộ sự tồn tại của thẻ ngoài phạm vi, kể cả qua trường này).

### 5.10 Quiz

`app/quiz/generator.py` điều phối. **3/4 dạng không cần LLM** (~75% token quiz):

| Dạng | Cách sinh |
|---|---|
| MULTIPLE_CHOICE | từ → nghĩa; nhiễu là `meaning` của láng giềng gần nhất theo embedding (deck cùng deck, ngưỡng cosine để tránh hai đáp án "đúng") |
| LISTENING | nghe → chọn từ; cần `audio_url`, thẻ thiếu thì **bỏ qua** |
| MATCHING | nối tối đa 5 thẻ với nghĩa, xáo trộn cột nghĩa |
| FILL_BLANK | **duy nhất cần LLM** — JSON mode, gọi theo lô 5 thẻ, `temperature` KHÔNG ghim 0.0 (cố ý, khác M8) |

`app/quiz/validator.py` **bắt buộc** với câu LLM: đúng 4 lựa chọn, `correct_index` hợp lệ,
`explanation` có thật, đáp án không lộ nguyên dạng trong đề bài (lỗi hay gặp nhất của LLM).
LLM hỏng/json hỏng → thay bằng câu deterministic fallback, không bao giờ trả lỗi cho người dùng.
`generated_by` ∈ `DETERMINISTIC | LLM` để khoanh vùng truy vết.

### 5.11 API layer & bảo mật

- **Xác thực:** `X-Internal-Token` header, so sánh bằng `secrets.compare_digest` (không phải `==`;
  non-ASCII token → 401 chứ không phải 500). Đúng toàn bộ chuỗi, tiền tố đúng không đủ.
- `GET /healthz` không cần token (sống chưa). `GET /readyz` 503→200 khi model nạp xong và index
  sẵn sàng; luôn nói ra `model_version` kể cả khi chưa sẵn sàng (câu hỏi đầu sau deploy).
- **Fail closed:** `allowed_deck_ids` rỗng → 400 trên chat/quiz/search/vocab — không được hiểu là
  "tất cả". `scope_deck_id` phải là tập con của `allowed_deck_ids`.
- Lỗi chuẩn hoá `ErrorResponse {error: {code, message, retry_after_seconds?}}` — không bao giờ lộ
  traceback; mã lỗi ánh xạ thẳng bảng ở SPEC §8.6 (số mục `8.6` bị khoá vì được trích dẫn từ 5
  chỗ trong mã nguồn).
- Swagger khai báo security scheme để có nút Authorize.

### 5.12 Stats & quan trắc

`GET /internal/v1/stats?from=&to=` (mặc định 24 giờ). Mỗi chỉ số trả kèm `target` và `ok`:

```
free_ratio          {"value": 0.5,  "target": 0.4,  "ok": true,  "comparison": ">="}
avg_tokens_per_chat {"value": 640,  "target": 1200, "ok": true,  "comparison": "<"}
latency_p95_ms      {"value": 2100, "target": 3000, "ok": true,  "comparison": "<"}
error_rate          {"value": 0.01, "target": 0.02, "ok": true,  "comparison": "<"}
```

Đọc theo thứ tự quan trọng: (1) `free_ratio` tụt — đang trả tiền cho việc dữ liệu cục bộ làm được
miễn phí; (2) `avg_tokens_per_chat` vượt — hạ `AI_TOP_K` / giới hạn field; (3) `error_rate` vượt —
thường là 429 từ Groq; (4) `latency` vượt nhưng `free_ratio` tốt → nguyên nhân ở Groq.
`by_answer_source` chỉ đếm `task = "CHAT"`; lượt miễn phí ghi `provider = "local"`. Cửa sổ rỗng
trả 0 (không nổ ZeroDivisionError) — khi dựng cảnh báo phải kiểm `chat_turns > 0` trước.

---

## 6. Ràng buộc cấm & nguyên tắc

**Không được thêm:** LangChain / LlamaIndex / Haystack, vector DB ngoài (Qdrant, Chroma, FAISS...),
`torch` / `sentence-transformers` / `transformers` (>2 GB), MySQL, Redis, docker-compose phụ thuộc,
ORM (SQLAlchemy/Alembic), fine-tune, chunking/text splitter, lưu hội thoại trong fsoft-ai, JWT/role,
gọi endpoint nghiệp vụ nào của backend ngoài hai endpoint nội bộ, mở service ra internet.

**Dependency (đóng):** anyio, fastapi, fastembed, httpx, numpy, openai, pydantic,
pydantic-settings, rank-bm25, structlog, uvicorn. `sqlite3`/`hashlib`/`secrets` là stdlib.

**Giả định quy mô:** < 50.000 thẻ (50k × 384 × 4 byte = 73 MB, brute-force numpy < 5ms).
**Trả lời bằng tiếng Việt**, giữ nguyên tiếng Anh cho từ vựng và câu ví dụ.

---

## 7. Triển khai & vận hành

- **Railway, đúng 1 replica** — semantic cache và token bucket nằm trong RAM; nhiều replica sẽ chạy
  nhiều vòng lặp sync chồng lên nhau. Không gán public domain — chỉ nội bộ Railway.
- Gắn volume cho `AI_DB_PATH=/data/fsoft-ai.db`; mất file tự chữa lành nhưng phải embed lại.
- RAM đỉnh **317 MB** (gói 512 MB dùng được). Docker: `cp .env.example .env && docker compose up --build`
  (xem `docs/DOCKER.md`).
- Đổi model LLM: sửa thẳng `.env` (chat/rewrite/quiz/fallback), không cần reindex.
- Test: `uv run pytest -q` (385 test, không cần mạng; test nhãn `live` gọi Groq thật, bị loại mặc
  định). Bộ đo: `uv run python scripts/run_eval.py`. Lint + type: `uv run ruff check . && uv run mypy app`.

---

*Biểu đồ tương tác đầy đủ: mở `graphify-out/graph.html` trong trình duyệt
(1.619 nodes · 4.315 edges · 105 communities).*