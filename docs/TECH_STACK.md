# fsoft-ai — Tech Stack

> Nguồn: `pyproject.toml`, `docs/SPEC.md` §5.9, `uv.lock`. Danh sách dependency là **đóng** —
> không thêm gì ngoài danh sách này (SPEC §4.2).

---

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Runtime & quản lý dependency](#2-runtime--quản-lý-dependency)
3. [Web framework](#3-web-framework)
4. [AI / ML](#4-ai--ml)
5. [Lưu trữ & retrieval](#5-lưu-trữ--retrieval)
6. [Hạ tầng phụ trợ](#6-hạ-tầng-phụ-trợ)
7. [Dev tooling](#7-dev-tooling)
8. [Vì sao KHÔNG có X](#8-vì-sao-không-có-x)
9. [Bảng tổng hợp](#9-bảng-tổng-hợp)

---

## 1. Tổng quan

Stack được chọn theo một nguyên tắc duy nhất: **token LLM là tài nguyên đắt nhất**, và mọi
dependency phải phục vụ hai mục tiêu — chạy embedding in-process không tốn token, và giữ RAM ở
mức thấp nhất có thể (đỉnh 317 MB, gói 512 MB dùng được).

Dependency production: **11**. Không ORM, không vector DB, không framework orchestration.

```
fastapi ── uvicorn ── anyio ── (threadpool cho sqlite + ONNX)
   │
   ├── fastembed (ONNX Runtime) ── multilingual-e5-small ── numpy (vector index RAM)
   ├── openai SDK ──► Groq API (gpt-oss-120b / gpt-oss-20b)
   ├── rank-bm25 ──► BM25 lexical trong RAM
   ├── httpx ──► backend Java (kéo thẻ)
   ├── pydantic + pydantic-settings ── schemas + config
   └── structlog ── logging JSON
```

---

## 2. Runtime & quản lý dependency

| Hạng mục | Giá trị | Ghi chú |
|---|---|---|
| Ngôn ngữ | **Python ≥ 3.12** | `.python-version` ghim phiên bản |
| Package manager | **uv** | `uv sync` chỉ cài dependency, không build project (`[tool.uv] package = false` — đây là service, không phải library) |
| Cách chạy | `uv run uvicorn app.main:app --port 8000` | `app/` nằm ở root nhờ không build |

Vì `package = false`, `app/` nằm ở root và uvicorn import trực tiếp — không cần build-backend,
không wheel, không cài bản thân project.

---

## 3. Web framework

| Dependency | Vai trò | Chi tiết đáng nhớ |
|---|---|---|
| `fastapi` | 9 endpoint nội bộ + 2 health endpoint | SSE qua `StreamingResponse`; Swagger khai báo security scheme để có nút Authorize |
| `uvicorn[standard]` | ASGI server | **Chạy đúng 1 worker** — mỗi worker nhân đôi RAM và chạy vòng lặp sync riêng gây kéo trùng |
| `pydantic` | Schemas request/response | `snake_case` cho API nội bộ của mình, `camelCase` khi parse phản hồi từ backend Java |
| `pydantic-settings` | Config từ biến môi trường | Mọi giá trị lấy từ `.env`; field trùng tên biến `AI_*` |
| `anyio` | Threadpool | Được khai báo tường minh dù đi kèm FastAPI — code import thẳng nó. Né 3 bẫy: GIL (tokenize/numpy chặn event loop), oversubscription (`OMP_NUM_THREADS=1`, `ORT_NUM_THREADS=1` set trước khi import fastembed), worker |

**Concurrency model:** 1 process · 1 worker · 1 event loop. Đường nóng đọc RAM, không chạm SQLite.
Tác vụ chặn (embedding, SQL) đẩy qua `anyio.to_thread.run_sync`. Concurrency đến từ threadpool,
không phải từ nhiều worker.

---

## 4. AI / ML

### 4.1 Embedding — `fastembed` (ONNX Runtime)

| Thuộc tính | Giá trị |
|---|---|
| Model | `intfloat/multilingual-e5-small` |
| Số chiều | 384, L2-normalize → **cosine = dot product** |
| Ngôn ngữ | 94 (có tiếng Việt) — cross-lingual là năng lực cả sản phẩm phụ thuộc vào |
| Giới hạn token | 512 (dài hơn bị cắt) |
| License | MIT |
| Chạy | **In-process, CPU** — không gọi API embedding nào, không tốn token, không phụ thuộc mạng |

**Vì sao fastembed thay `sentence-transformers`:** khoảng **50 MB dependency thay vì hơn 2 GB**
(torch + transformers). Gần như không cần dependency ngoài, không đòi driver CUDA để chạy trên CPU.

Bẫy đã xử lý (từ `docs/M0_FINDINGS.md`):

- Model **không có** trong built-in registry → `TextEmbedding.add_custom_model(...)` idempotent
- **Prefix E5 tự nối tay** (`query: ` / `passage: `) — fastembed không tự thêm, quên thì retrieval
  tụt thảm âm thầm
- RAM 936 → **317 MB**: lượng tử hoá int8 (936→538) + `scripts/tia_vocab.py` tỉa bảng Unigram
  250k → 113k token (tokenizer mới là kẻ ăn RAM nhất: 250 MB)

### 4.2 LLM — SDK `openai` trỏ vào Groq

| Hạng mục | Giá trị |
|---|---|
| SDK | `openai` với `base_url = https://api.groq.com/openai/v1` — **KHÔNG dùng SDK groq riêng** |
| Model chat/quiz | `openai/gpt-oss-120b` |
| Model rewrite/fallback | `openai/gpt-oss-20b` |
| `max_retries` | **0 (bắt buộc)** — SDK tự retry bằng backoff mù, không đọc `retry-after` |
| Timeout | 30s |

**Vì sao SDK openai thay SDK groq:** Groq tương thích chuẩn OpenAI, và hầu hết nhà cung cấp khác
cũng vậy — đổi provider chỉ là đổi biến môi trường.

Bẫy của `gpt-oss`: sinh `reasoning_tokens` ẩn trừ vào `max_tokens` — dưới 300 thì JSON mode hỏng
0/3 lần với lỗi không nói ra nguyên nhân. Vì vậy `AI_MAX_OUTPUT_TOKENS = 700`, không phải 400.

---

## 5. Lưu trữ & retrieval

| Dependency | Vai trò | Chi tiết đáng nhớ |
|---|---|---|
| `sqlite3` (stdlib) | 1 file DB, WAL mode, `synchronous = NORMAL` | Không ORM, không connection pool, không migration framework. `check_same_thread=False`, gọi qua anyio threadpool |
| `numpy` | Vector index brute-force trong RAM | `(N, 384) @ (384,)` dưới 5ms ở 50k thẻ — HNSW chỉ thêm phức tạp ở quy mô này |
| `rank-bm25` | Tầng lexical trong RAM | BM25Okapi tính sẵn IDF lúc khởi tạo — không cập nhật từng phần được, phải dựng lại toàn bộ |
| `hashlib`, `secrets`, `sqlite3` (stdlib) | content_hash, compare_digest, DB | Không cần khai báo trong pyproject |

Ba bảng: `card` (thẻ + text + vector BLOB), `sync_state` (con trỏ đồng bộ), `usage_log` (nhật ký
LLM). Không có `profile_id` ở đâu cả — fsoft-ai không biết gì về user.

**Vì sao numpy thay vector DB:** 50.000 vector × 4 byte × 384 chiều = 73 MB; brute-force dưới
5ms. Vector DB ngoài (Qdrant, Chroma, Milvus, Pinecone, FAISS) là hạ tầng thừa ở quy mô này —
càng thêm càng khó deploy 1 replica 512 MB. `VectorIndexProtocol` được tách sẵn để sau này thay
Qdrant chỉ là thêm một class.

---

## 6. Hạ tầng phụ trợ

| Dependency | Vai trò |
|---|---|
| `httpx` | Kéo thẻ từ backend Java qua 2 endpoint nội bộ (`HttpCardSource`); chuyển đổi camelCase↔snake_case nằm đúng một chỗ |
| `structlog` | Logging JSON có cấu trúc (SPEC §11.4 mục 6) |
| stdlib: `sqlite3`, `hashlib`, `secrets` | DB, SHA-256 content_hash, compare_digest xác thực token |

**Hạ tầng ngoài:**
- **Groq API** — LLM provider (free tier 8.000 TPM, service tự trần 6.400)
- **Backend Java (Spring Boot 3)** — nguồn dữ liệu thẻ + caller, kết nối qua `X-Internal-Token`
- **Docker Compose** — chạy local 3 lệnh; `Dockerfile` + `scripts/download_model.py` nạp sẵn
  model ONNX vào cache lúc build
- **Render / Railway** — deploy 1 replica, không public domain, volume cho `/data`
- **GitHub Actions** (`.github/workflows/ci.yml`) — pytest + ruff + mypy + eval gates
  (Recall@5/MRR/intent), lưu history `.jsonl` thành artifact 90 ngày

---

## 7. Dev tooling

| Dependency | Vai trò |
|---|---|
| `pytest` + `pytest-asyncio` | 385 test, hoàn toàn offline (fixture mode); `asyncio_mode = "auto"` |
| `respx` | Mock httpx — test `HttpCardSource` không cần backend thật |
| `ruff` | Lint (`line-length = 100`, `target-version = py312`) |
| `mypy` | Type check (`uv run mypy app`) |

Test nhãn `live` gọi Groq thật và **bị loại mặc định** (`pytest -m live` để chạy riêng).
Bộ đo retrieval (`scripts/run_eval.py`) thoát mã 1 khi dưới ngưỡng — cắm thẳng CI được.

---

## 8. Vì sao KHÔNG có X

Danh sách cấm (SPEC §4.2 và §5.9) — mỗi cái có một lý do đo được:

| Bị cấm | Lý do |
|---|---|
| **LangChain / LlamaIndex / Haystack** | Framework orchestration sinh ra để chuẩn hoá luồng gọi LLM — ngược với bài toán ở đây là *né gọi LLM bất cứ khi nào có thể*. Prompt viết tay để kiểm soát từng token (ngân sách 8.000 TPM). Cái LangChain làm cho case này chỉ là: 1 endpoint call, 1 context string, 1 JSON parse — ~300 dòng tự viết |
| **torch / sentence-transformers / transformers** | Hơn 2 GB dependency. fastembed làm được cùng việc với ~50 MB |
| **Vector DB (Qdrant, Chroma, Milvus, Pinecone, FAISS)** | 50k vector × brute-force numpy < 5ms. Hạ tầng thừa cho 1 replica |
| **MySQL / Redis / docker-compose phụ thuộc** | SPEC v3 bỏ hết: SQLite 1 file đủ nhanh vì đường nóng không chạm DB; cache/token bucket nằm trong RAM (1 replica). Dependency giảm 12 → 11 |
| **SQLAlchemy / Alembic (ORM)** | Schema chỉ 3 bảng, SQL thuần ngắn hơn và minh bạch hơn. Không migration framework — `001_init.sql` chạy tự động lúc khởi động |
| **Chunking / text splitter** | 1 card = 1 chunk — card vốn là đơn vị ngữ nghĩa ngắn và hoàn chỉnh. Cap 4.000 ký tự của `/vocab/extract` chính là cách né chunking |
| **SDK groq riêng** | openai SDK + `base_url` đủ dùng, giữ khả năng đổi provider bằng 1 biến môi trường |

Nguyên tắc chung (từ `claude/CLAUDE.md`): *"No abstractions for single-use code. No 'flexibility'
or 'configurability' that wasn't requested."*

---

## 9. Bảng tổng hợp

| Lớp | Công nghệ | Thay thế bằng gì nếu cần scale |
|---|---|---|
| Runtime | Python 3.12 + uv | — |
| API | FastAPI + uvicorn (1 worker) | — |
| Validation | pydantic v2 + pydantic-settings | — |
| Embedding | fastembed (ONNX) + multilingual-e5-small | Đổi `AI_EMBEDDING_MODEL` + `AI_EMBEDDING_DIM` + `AI_MODEL_VERSION` (phá huỷ, phải reindex) |
| LLM | SDK openai → Groq gpt-oss | Đổi `AI_LLM_BASE_URL` — provider-agnostic |
| Vector store | numpy trong RAM | Qdrant — chỉ thêm 1 class hiện thực `VectorIndexProtocol` |
| Lexical | rank-bm25 trong RAM | — |
| DB | SQLite 1 file, thuần SQL, WAL | — |
| Cache | SemanticCache trong RAM (500 entry) | Redis — chỉ khi chạy > 1 replica |
| HTTP client | httpx | — |
| Logging | structlog (JSON) | — |
| Test | pytest + respx + pytest-asyncio | — |
| Lint/Type | ruff + mypy | — |
| Deploy | Docker, Render/Railway, 1 replica | Xem SPEC §14.3 khi muốn nhiều replica |

Số liệu vận hành đỉnh: **317 MB RAM**, khởi động ~2,6 giây sau lần đầu, index 50k thẻ build < 5ms/query.