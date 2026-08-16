# fsoft-ai

Service RAG cho nền tảng học từ vựng tiếng Anh. Trả lời câu hỏi **chỉ dựa trên bộ thẻ
của chính người học**, tìm kiếm ngữ nghĩa, và sinh câu hỏi ôn tập.

Chạy độc lập với backend Java. Đặc tả đầy đủ ở [docs/SPEC.md](docs/SPEC.md); cách gọi
từ phía Java ở [docs/BACKEND_INTEGRATION.md](docs/BACKEND_INTEGRATION.md).

---

## Nguyên tắc chi phối mọi thứ

**Token LLM là tài nguyên đắt nhất.** Groq free tier cho 12.000 token mỗi phút; service
chỉ dùng 9.600 (80%). Vì vậy mọi câu hỏi đều đi qua bộ lọc rẻ tiền trước, và chỉ những
câu thật sự cần suy luận mới chạm tới LLM.

| Nhánh | Token | Khi nào |
|---|---|---|
| `CANNED` | **0** | Câu ngoài chủ đề học tiếng Anh |
| `DIRECT_LOOKUP` | **0** | Tra nghĩa một từ có sẵn trong bộ thẻ |
| `CACHE` | **0** | Câu tương tự đã hỏi trong 24 giờ, cùng phạm vi deck |
| `RAG` | ~800–1.200 | Cần LLM diễn giải trên ngữ cảnh lấy từ bộ thẻ |
| `LLM_ONLY` | ~600 | Không thẻ nào khớp — trả lời kèm cảnh báo |

Mục tiêu vận hành: **≥ 40% lượt chat rơi vào ba nhánh 0 token**. Dưới ngưỡng này nghĩa
là đang trả tiền cho việc mà dữ liệu cục bộ làm được miễn phí — xem `GET /internal/v1/stats`.

Embedding chạy **ngay trong process**, trên CPU, bằng ONNX Runtime. Không gọi API
embedding nào, không tốn token, không phụ thuộc mạng.

---

## Luồng một câu hỏi

```
                 backend Java  ──► POST /internal/v1/chat
                                        │  query, allowed_deck_ids, history
                                        ▼
                          ┌─────────────────────────┐
                          │ 1. Kiểm phạm vi deck    │  rỗng -> 400, KHÔNG hiểu là "tất cả"
                          │ 2. Viết lại câu hỏi?    │  chỉ khi câu ngắn / có đại từ
                          │ 3. Embed 1 lần          │  384 chiều, dùng lại cho bước 4 và 6
                          │ 4. Phân loại ý định     │  0 token: luật regex + centroid
                          └───────────┬─────────────┘
                                      │
            ┌─────────────────────────┼──────────────────────────┐
            ▼                         ▼                          ▼
     OUT_OF_SCOPE              VOCAB_LOOKUP                 còn lại
     trả câu mẫu               tra thẳng bộ thẻ                  │
     CANNED · 0 token          DIRECT_LOOKUP · 0 token           │
                                                                 ▼
                                                    ┌────────────────────────┐
                                                    │ 6. Retrieval 3 tầng    │
                                                    │    khớp chính xác      │
                                                    │    BM25 lexical        │
                                                    │    semantic (cosine)   │
                                                    │    hợp nhất bằng RRF   │
                                                    └───────────┬────────────┘
                                                                ▼
                                                    ┌────────────────────────┐
                                                    │ 7. Semantic cache      │  trúng -> CACHE · 0 token
                                                    │ 8. Dựng ngữ cảnh       │
                                                    │ 9. Kiểm ngân sách      │  cạn -> 429, chưa gọi Groq
                                                    │ 10. Gọi Groq           │  Groq trả 429 -> hạ model
                                                    └───────────┬────────────┘  vẫn hỏng -> 503
                                                                ▼
                                                          RAG · ~1.000 token
```

Dữ liệu thẻ đi theo chiều ngược lại và hoàn toàn tách rời:

```
backend Java ──► GET /fsoft/internal/cards/changed-since  ──► embed ──► SQLite
                 GET /fsoft/internal/cards/ids                             │
                 (mỗi 120 giây, chỉ lấy thẻ đã đổi)                        ▼
                                                              index vector + BM25 trong RAM
```

Đường nóng **không bao giờ chạm SQLite** — đọc hoàn toàn từ RAM.

---

## Chạy trên máy local

Cần Python 3.12 và [uv](https://docs.astral.sh/uv/). Không cần Docker, không cần
backend Java, không cần khoá Groq để khởi động.

> Muốn chạy bằng **Docker** thay vì cài Python? Ba lệnh là xong, xem
> [docs/DOCKER.md](docs/DOCKER.md):
>
> ```bash
> cp .env.example .env
> docker compose up --build
> ```

```bash
uv sync
cp .env.example .env
```

Sửa `.env`: đặt `AI_INTERNAL_TOKEN` bằng `python -c "import secrets; print(secrets.token_hex(32))"`,
và `AI_LLM_API_KEY` bằng khoá Groq nếu muốn thử nhánh RAG.

```bash
uv run uvicorn app.main:app --port 8000
```

Lần chạy đầu tải model 486 MB về `.cache/fastembed` (khoảng 2–3 phút tuỳ mạng). Những
lần sau khởi động mất khoảng **2,6 giây**. Tải trước cho chắc:

```bash
uv run python scripts/download_model.py
```

Kiểm tra:

```bash
curl localhost:8000/healthz    # 200 ngay lập tức
curl localhost:8000/readyz     # 503 -> 200 khi model nạp xong và index sẵn sàng
```

### Hai chế độ nguồn dữ liệu

| `AI_SOURCE_MODE` | Lấy thẻ từ | Dùng khi |
|---|---|---|
| `fixture` | `tests/fixtures/cards.json` (24 thẻ) | Phát triển và test, **hoàn toàn offline** |
| `http` | Backend Java qua `AI_BACKEND_URL` | Ghép thật |

Fixture cố ý mô phỏng đúng shape phản hồi của backend (camelCase, bọc `ApiResponse`), nên
nó vừa là dữ liệu test vừa là hợp đồng đối chiếu với đội Java.

---

## Ép đồng bộ, không đợi hết 120 giây

```bash
# Neo `^` là bắt buộc: không có nó, grep còn khớp cả dòng AI_BACKEND_TOKEN
# (chú thích của nó có nhắc tên AI_INTERNAL_TOKEN) và $TOKEN sẽ chứa xuống dòng.
TOKEN=$(grep -E '^AI_INTERNAL_TOKEN=' .env | head -1 | cut -d= -f2 | tr -d ' ')

curl -X POST -H "X-Internal-Token: $TOKEN" localhost:8000/internal/v1/index/sync
curl -H "X-Internal-Token: $TOKEN" localhost:8000/internal/v1/index/status
```

PowerShell:

```powershell
$TOKEN = (Select-String -Path .env -Pattern '^AI_INTERNAL_TOKEN=(.+)$').Matches[0].Groups[1].Value.Trim()
```

Hai tham số hữu ích:

| Tham số | Tác dụng |
|---|---|
| `?sweep=true` | Quét thêm danh sách ID để phát hiện thẻ đã bị xoá ở backend |
| `?full=true` | Bỏ qua con trỏ đồng bộ, kéo lại **toàn bộ** thẻ |

`full=true` vẫn rẻ: `content_hash` khiến thẻ không đổi trở thành no-op, không embed lại.
Dùng khi nghi con trỏ lệch, hoặc **bắt buộc dùng sau khi đổi `AI_MODEL_VERSION`**.

Đọc `GET /internal/v1/index/status`:

| Field | Nghĩa khi bất thường |
|---|---|
| `last_sync_error` | Khác `null` → backend không gọi được, service vẫn chạy bằng dữ liệu cũ |
| `last_sync_embedded` | Lần nào cũng lớn → `content_hash` không ăn, đang embed lại vô ích |
| `card_count` ≠ `index_size` | SQLite và index lệch nhau — restart service |
| `backend_reachable` | `false` → kiểm `AI_BACKEND_URL` và `AI_BACKEND_TOKEN` |

---

## Đổi model

### Model embedding

**Đổi model embedding là thao tác phá huỷ**: mọi vector cũ trở nên vô nghĩa vì nằm
trong không gian khác. Bắt buộc ba bước, đúng thứ tự:

```bash
# 1. Đổi cả hai biến, KHÔNG được quên biến thứ hai
AI_EMBEDDING_MODEL=<model mới>
AI_EMBEDDING_DIM=<số chiều mới>
AI_MODEL_VERSION=<tên mới>@t1     # <-- đổi giá trị này là chìa khoá

# 2. Xoá index cũ
rm -f data/fsoft-ai.db*

# 3. Khởi động lại rồi ép đồng bộ toàn bộ
curl -X POST -H "X-Internal-Token: $TOKEN" "localhost:8000/internal/v1/index/sync?full=true"
```

`AI_MODEL_VERSION` là thứ syncer so sánh để quyết định có embed lại hay không. Quên đổi
nó thì mọi thẻ đều "không đổi" và index sẽ trộn lẫn vector của hai model khác nhau — kết
quả tìm kiếm sai một cách rất khó lần ra.

Model hiện tại là **E5 bất đối xứng**: câu hỏi phải nối tiền tố `"query: "`, còn thẻ nối
`"passage: "`. Nối sai thì retrieval kém đi âm thầm chứ không báo lỗi. Bằng chứng đo được
ở [docs/M0_FINDINGS.md](docs/M0_FINDINGS.md) mục 2.

### Model LLM

Đổi thẳng trong `.env`, không cần làm gì thêm:

```bash
AI_MODEL_CHAT=llama-3.3-70b-versatile     # chat chính
AI_MODEL_REWRITE=llama-3.1-8b-instant     # viết lại câu hỏi, rẻ
AI_MODEL_QUIZ=llama-3.3-70b-versatile     # sinh quiz, cần JSON mode
AI_MODEL_FALLBACK=llama-3.1-8b-instant    # dùng khi ngân sách token gần cạn
```

Model quiz **bắt buộc hỗ trợ JSON mode**. Kiểm trước khi đổi — script đọc `GROQ_API_KEY`
từ môi trường chứ không đọc `.env`, và chỉ kiểm những model có trong danh sách
`CANDIDATES` của chính nó:

```powershell
$env:GROQ_API_KEY = "gsk_..."
uv run python scripts/m0_groq.py
```

---

## Đọc bảng stats

```bash
curl -H "X-Internal-Token: $TOKEN" localhost:8000/internal/v1/stats
curl -H "X-Internal-Token: $TOKEN" \
  "localhost:8000/internal/v1/stats?from=2026-08-01T00:00:00Z&to=2026-08-16T00:00:00Z"
```

Mặc định 24 giờ gần nhất. Mỗi chỉ số trả kèm `target` và `ok` nên không cần tra lại SPEC:

```json
{
  "chat_turns": 120,
  "by_answer_source": {"DIRECT_LOOKUP": 40, "CANNED": 12, "CACHE": 8, "RAG": 60},
  "free_ratio":          {"value": 0.5,  "target": 0.4,  "ok": true,  "comparison": ">="},
  "avg_tokens_per_chat": {"value": 640,  "target": 1200, "ok": true,  "comparison": "<"},
  "latency_p95_ms":      {"value": 2100, "target": 3000, "ok": true,  "comparison": "<"},
  "error_rate":          {"value": 0.01, "target": 0.02, "ok": true,  "comparison": "<"},
  "all_targets_met": true
}
```

Đọc theo thứ tự quan trọng:

1. **`free_ratio` tụt dưới 0.4** — chỉ số đáng lo nhất. Thường do người dùng hỏi bằng
   cách diễn đạt mà `DIRECT_LOOKUP` không nhận ra, hoặc cache bị vô hiệu quá thường
   xuyên (mỗi lần index đổi là cache bị xoá sạch).
2. **`avg_tokens_per_chat` vượt 1.200** — ngữ cảnh đang quá dài. Hạ `AI_TOP_K` hoặc
   `AI_CONTEXT_MAX_CHARS_PER_FIELD`.
3. **`error_rate` vượt 0.02** — phần lớn là 429 từ Groq. Hạ `AI_GLOBAL_TOKENS_PER_MINUTE`.
4. **`latency_p95_ms` vượt 3.000** — nếu `free_ratio` vẫn tốt thì nguyên nhân ở phía Groq
   chứ không phải ở service.

`by_answer_source` chỉ đếm `task = "CHAT"`. Lượt miễn phí được ghi với `provider = "local"`,
lượt tốn token do `LlmClient` ghi — cả hai cùng `task = "CHAT"` nên mẫu số luôn đúng.

> **Khi dựng cảnh báo:** cửa sổ không có lượt nào thì `free_ratio` bằng 0 và
> `all_targets_met` bằng `false`. Đó là **chưa có dữ liệu**, không phải đang hỏng. Luật
> cảnh báo phải kiểm `chat_turns > 0` trước, nếu không sẽ kêu suốt đêm khi không ai dùng.

---

## Test và bộ đo

```bash
uv run pytest -q                        # 226 test, không cần mạng
uv run python scripts/run_eval.py       # bộ đo retrieval, 40 case
uv run ruff check . && uv run mypy app
```

Test và bộ đo bắt hai loại hồi quy **khác nhau**:

- **Test** bắt hồi quy *hành vi* — thứ gì đó hỏng hẳn.
- **Bộ đo** bắt hồi quy *chất lượng* — Recall@5 tụt trong khi mọi test vẫn xanh.

Ngưỡng chặn: Recall@5 ≥ 0.80, MRR ≥ 0.60, intent đúng ≥ 90%. Hiện đang ở **0.971 / 0.971 / 100%**.

Bộ đo thoát với mã 1 khi dưới ngưỡng nên cắm thẳng vào CI được. Thêm `--json <đường dẫn>`
để nối một dòng lịch sử (`.jsonl`) — [CI](.github/workflows/ci.yml) lưu file này thành
artifact 90 ngày để nhìn ra xu hướng.

Test có nhãn `live` gọi Groq thật và **bị loại mặc định**. Chạy riêng khi cần:

```bash
uv run pytest -m live
```

---

## Cấu trúc thư mục

```
app/
  api/v1/       chat · search · quiz · index · stats
  chat/         orchestrator 12 bước, semantic cache, câu trả lời mẫu
  core/         logging có cấu trúc, lỗi và mã lỗi
  embedding/    encoder ONNX, vector index trong RAM, dựng text để embed
  llm/          client Groq, ngân sách token, prompt (tách khỏi code)
  quiz/         3 dạng không cần LLM + 1 dạng cần, bộ chọn nhiễu, validator
  retrieval/    3 tầng + RRF, phân loại ý định, hư từ, dựng ngữ cảnh
  schemas/      pydantic model cho request/response
  store/        SQLite thuần SQL, không ORM
  sync/         syncer, nguồn HTTP và nguồn fixture
docs/           SPEC · M0_FINDINGS · BACKEND_INTEGRATION · BACKEND_FEEDBACK
migrations/     001_init.sql
scripts/        spike M0, tải model, chạy bộ đo
tests/          226 test + bộ đo 40 case
```

---

## Những chỗ dễ sai

Danh sách đầy đủ ở [SPEC mục 14](docs/SPEC.md). Bốn cái hay gặp nhất:

| Triệu chứng | Nguyên nhân thường gặp |
|---|---|
| Tìm kiếm trả về thẻ của deck khác | `allowed_deck_ids` bị hiểu là "tất cả" khi rỗng. Rỗng phải là **không thẻ nào** |
| Recall thấp bất thường | Quên tiền tố `query:` / `passage:` của E5 |
| Đổi model xong kết quả loạn | Quên đổi `AI_MODEL_VERSION` → index trộn vector hai model |
| Câu hỏi tiếng Việt **không dấu** trả về rỗng | Giới hạn đã biết, [SPEC mục 14.2b](docs/SPEC.md) |

---

## Triển khai

Railway, **đúng 1 replica**. Semantic cache và ngân sách token đều nằm trong RAM, và
nhiều replica sẽ chạy nhiều vòng lặp đồng bộ chồng lên nhau.

Gắn volume cho `AI_DB_PATH=/data/fsoft-ai.db`. Mất file này không phải thảm hoạ — service
tự đồng bộ lại từ backend — nhưng phải embed lại toàn bộ.

> **Chưa giải quyết:** RSS đo được **784 MB** trên Windows, vượt gói Railway 512 MB. Phần
> lớn là model ONNX (~710 MB), không phải vector (73 MB). Hướng xử lý: dùng bản lượng tử
> `model_qint8_avx512_vnni.onnx` (118 MB). Xem [SPEC mục 14.6](docs/SPEC.md).
