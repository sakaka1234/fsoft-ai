# Chạy fsoft-ai bằng Docker

> **Chưa được build thử.** Máy viết ra bộ file này không cài được Docker, nên tôi đã bù bằng
> cách đối chiếu từng dòng với code thật thay vì chạy thử. Mục [10](#10-những-gì-chưa-được-kiểm-chứng)
> nói rõ chỗ nào là chắc chắn và chỗ nào là suy luận. Lần build đầu tiên, nếu vấp lỗi thì
> chụp lại thông báo — mục [9](#9-gặp-lỗi-thì-tra-ở-đây) đã liệt kê sẵn những lỗi dễ gặp nhất.

---

## 1. Cần có gì

- **Docker Engine 20.10+** kèm plugin Compose v2 (`docker compose`, có dấu cách).
  Kiểm bằng `docker compose version`. Nếu máy bạn chỉ có `docker-compose` (có gạch nối, bản v1)
  thì mọi lệnh dưới đây thay `docker compose` thành `docker-compose`.
- **Khoảng 3 GB đĩa trống.** Ảnh cuối khoảng 1,3 GB, nhưng lúc build cần thêm chỗ cho layer
  trung gian.
- **Ít nhất 1 GB RAM cấp cho container.** Model ONNX một mình đã chiếm ~710 MB.
  Docker Desktop trên Windows/macOS mặc định cấp 2 GB cho cả máy ảo — đủ, nhưng nếu bạn đã
  hạ xuống thì phải nâng lại.
- **Mạng lúc build** để tải model 470 MB từ Hugging Face. Lúc *chạy* thì không cần mạng, trừ
  khi bạn dùng nhánh LLM hoặc nối vào backend Java thật.

---

## 2. Chạy trong ba lệnh

```bash
git clone https://github.com/sakaka1234/fsoft-ai.git
cd fsoft-ai

cp .env.example .env          # BẮT BUỘC — thiếu file này compose sẽ báo lỗi ngay

docker compose up --build
```

Mở http://localhost:8000/docs

Lần build đầu mất **5–15 phút**, phần lớn là tải model. Những lần sau chỉ vài giây nếu bạn
chỉ sửa code trong `app/` — model nằm ở một layer riêng phía trên nên không phải tải lại.

Chạy nền thì thêm `-d`:

```bash
docker compose up --build -d
docker compose logs -f
```

---

## 3. Sửa `.env` trước khi chạy thật

`cp .env.example .env` cho ra một cấu hình **chạy được ngay** với dữ liệu mẫu 24 thẻ. Nhưng
trước khi dùng nghiêm túc thì sửa hai chỗ:

| Biến | Để nguyên thì sao | Nên đổi thành |
|---|---|---|
| `AI_INTERNAL_TOKEN` | Là `dev-token` — ai biết cũng gọi được API | `python -c "import secrets;print(secrets.token_hex(32))"` |
| `AI_LLM_API_KEY` | Rỗng. Ba nhánh 0 token vẫn chạy, nhánh `RAG` trả `503` | Khoá Groq dạng `gsk_...` |

> **Quy tắc khi sửa `.env`:** biến để trống thì chú thích phải nằm ở **dòng riêng** phía trên.
> Viết `AI_FOO=   # giải thích` sẽ khiến thư viện đọc `.env` lấy nguyên chuỗi `# giải thích`
> làm **giá trị**. Với `AI_INTERNAL_TOKEN` thì hậu quả là **mọi request trả 500**, kể cả
> request mang đúng token.

`.env` **không bao giờ** đi vào image — nó nằm trong `.dockerignore`, và compose nạp nó lúc
chạy. Ai kéo được image của bạn cũng không đọc được khoá Groq.

### Ba biến compose tự đè lên, đừng sửa trong `.env`

`.env` viết cho máy dev nên chứa đường dẫn của **host**. Trong container chúng đều sai, nên
`docker-compose.yml` đè lên:

```yaml
AI_DB_PATH: /data/fsoft-ai.db          # .env ghi ./data/fsoft-ai.db
FASTEMBED_CACHE_PATH: /opt/fastembed_cache   # .env ghi ./.cache/fastembed
```

Cái thứ hai quan trọng hơn vẻ ngoài của nó: nếu không đè, service sẽ không thấy model ở
`/app/.cache` và **đi tải lại 470 MB lúc khởi động**, dù image đã có sẵn model.

---

## 4. Kiểm tra chạy đúng chưa

```bash
# 1. Còn sống chưa (trả 200 ngay cả khi model đang nạp)
curl http://localhost:8000/healthz

# 2. Sẵn sàng chưa — 503 khoảng 3 giây đầu, sau đó 200
curl http://localhost:8000/readyz

# 3. Index đã có thẻ chưa
TOKEN=$(grep -E '^AI_INTERNAL_TOKEN=' .env | head -1 | cut -d= -f2 | tr -d ' ')
curl -H "X-Internal-Token: $TOKEN" http://localhost:8000/internal/v1/index/status
```

Đạt khi thấy `"card_count": 24` và `"last_sync_error": null`.

PowerShell lấy token:

```powershell
$TOKEN = (Select-String -Path .env -Pattern '^AI_INTERNAL_TOKEN=(.+)$').Matches[0].Groups[1].Value.Trim()
```

Docker cũng tự theo dõi giúp — cột `STATUS` phải chuyển sang `(healthy)`:

```bash
docker compose ps
```

Trong khoảng 120 giây đầu, healthcheck thất bại **không** bị tính là hỏng (`start_period`),
vì đó là lúc model đang nạp.

---

## 5. Dùng thử trên Swagger

http://localhost:8000/docs

1. Bấm **Authorize** góc trên bên phải.
2. Dán **chỉ phần sau dấu `=`** của `AI_INTERNAL_TOKEN`. Dán cả dòng là lỗi hay gặp nhất,
   và nó trả `401` giống hệt như khi sai token thật.
3. Mở `POST /internal/v1/search`, bấm **Try it out**, chọn kịch bản ở menu **Examples**,
   bấm **Execute**.

Mọi ví dụ đều chạy được ngay với dữ liệu mẫu, trừ những ví dụ đi vào nhánh LLM (`RAG`,
`FILL_BLANK`) — chúng cần `AI_LLM_API_KEY`.

---

## 6. Việc thường làm

```bash
docker compose logs -f                 # xem log
docker compose logs -f --tail=100      # 100 dòng gần nhất

docker compose restart                 # khởi động lại, giữ dữ liệu
docker compose down                    # dừng và xoá container, GIỮ volume
docker compose down -v                 # xoá luôn volume -> mất index, phải embed lại

docker compose up --build -d           # build lại sau khi sửa code
docker compose exec fsoft-ai sh        # vào trong container

docker stats fsoft-ai                  # xem RAM đang dùng
```

Ép đồng bộ ngay, không đợi hết 120 giây:

```bash
curl -X POST -H "X-Internal-Token: $TOKEN" \
  http://localhost:8000/internal/v1/index/sync
```

---

## 7. Nối vào backend Java thật

Mặc định là `AI_SOURCE_MODE=fixture` — 24 thẻ mẫu, không cần backend.

Nối thật thì sửa `.env`:

```bash
AI_SOURCE_MODE=http
AI_BACKEND_URL=https://.../fsoft
AI_BACKEND_TOKEN=<token backend Java cấp>
```

Rồi `docker compose up -d`.

> **Nếu backend chạy trên chính máy host** (ví dụ `localhost:8080`) thì trong container
> `localhost` trỏ về **chính container**, không phải máy bạn. Dùng:
>
> ```bash
> AI_BACKEND_URL=http://host.docker.internal:8080/fsoft
> ```
>
> Trên Docker Desktop (Windows/macOS) tên này có sẵn. Trên Linux thì thêm vào
> `docker-compose.yml`:
>
> ```yaml
> extra_hosts:
>   - "host.docker.internal:host-gateway"
> ```

Chạy chung một compose với backend thì cho hai service vào cùng một network và gọi nhau bằng
tên service, ví dụ `http://backend:8080/fsoft`.

---

## 8. Dữ liệu nằm ở đâu

| Thứ | Ở đâu | Mất đi thì sao |
|---|---|---|
| Model ONNX 470 MB | Trong image, `/opt/fastembed_cache` | Phải build lại image |
| SQLite + vector đã embed | Volume `fsoft-ai-data` gắn vào `/data` | Service tự đồng bộ lại từ backend, nhưng phải embed lại toàn bộ |
| Cấu hình | File `.env` trên host | — |

Mất volume **không phải thảm hoạ**, chỉ tốn thời gian embed lại. Sao lưu:

```bash
docker run --rm -v fsoft-ai_fsoft-ai-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/fsoft-ai-data.tar.gz -C /data .
```

Tên volume thật có tiền tố là tên thư mục dự án. Xem bằng `docker volume ls`.

---

## 9. Gặp lỗi thì tra ở đây

### `env file .env not found`

Bạn quên bước `cp .env.example .env`.

### Build dừng ở `uv sync --locked`

Hai nguyên nhân, phân biệt bằng thông báo:

- **`The lockfile is not up-to-date`** — `uv.lock` lệch với `pyproject.toml`. Đây chính là
  việc `--locked` sinh ra để bắt. Trên máy dev chạy `uv lock` rồi commit lại file lock.
  Kiểm trước bằng `uv lock --check` (exit 0 là khớp).
- **Lỗi về định dạng lock** — bản uv ghim trong `Dockerfile` quá cũ. Kiểm `head -3 uv.lock`
  xem `revision` là mấy, rồi nâng số ghim `uv==` cho khớp.

### Build dừng ở bước tải model

Lỗi mạng, hoặc Hugging Face chặn vì gọi quá nhiều. Chạy lại `docker compose build` — Docker
giữ nguyên các layer đã xong nên chỉ làm lại bước hỏng.

Nếu thấy `Files have been corrupted during downloading process` thì **không phải mạng hỏng**:
đó là lỗi trong thư viện `fastembed`, đã được vòng qua trong `scripts/download_model.py`.
Gặp lại nghĩa là file đó bị sửa mất — đọc phần chú thích đầu file.

### Container khởi động rồi tắt ngay

```bash
docker compose logs --tail=50
```

Hay gặp nhất là hết RAM (bị Linux giết lúc nạp model). Kiểm bằng:

```bash
docker inspect fsoft-ai --format '{{.State.OOMKilled}}'
```

Trả `true` thì nâng `mem_limit` trong `docker-compose.yml`, và trên Docker Desktop nâng luôn
RAM cấp cho máy ảo.

### `STATUS` mãi không thành `(healthy)`

Đợi đủ 120 giây trước đã. Vẫn không lên thì:

```bash
docker compose exec fsoft-ai python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/readyz').read())"
```

Nó sẽ chỉ ra `encoder_ready` hay `index_ready` đang là `false`.

### Mọi request trả `500` dù token đúng

`AI_INTERNAL_TOKEN` trong `.env` đang mang giá trị là một chuỗi chú thích — xem cảnh báo ở
[mục 3](#3-sửa-env-trước-khi-chạy-thật). Sửa `.env` rồi `docker compose up -d` lại.

### `401` mà chắc chắn token đúng

Bạn dán cả `AI_INTERNAL_TOKEN=` vào ô Authorize. Chỉ dán phần sau dấu `=`.

### Sửa code mà container không đổi gì

Image chưa build lại. Dùng `docker compose up --build`, hoặc gắn thêm bind mount khi phát
triển (xem mục dưới).

---

## 10. Những gì CHƯA được kiểm chứng

Nói thẳng để bạn biết chỗ nào cần để mắt ở lần chạy đầu.

**Đã kiểm chứng được trên máy này (không cần Docker):**

- Mọi câu lệnh trong Dockerfile đối chiếu khớp với code thật: `download_model.py` không import
  `app/` nên chạy được trước khi `COPY app`; `huggingface_hub` nằm trong nhóm phụ thuộc chính
  nên `--no-dev` không cắt mất.
- `uvicorn` tự chèn thư mục làm việc vào `sys.path` (`uvicorn/main.py`), nên gọi thẳng
  `/app/.venv/bin/uvicorn app.main:app` import được `app.main` mà không cần `uv run`.
- `PROJECT_ROOT` trong container giải ra `/app`, nên `AI_FIXTURE_PATH` mặc định trỏ đúng vào
  `/app/tests/fixtures/cards.json` — khớp với dòng `COPY tests/fixtures`.
- `app/store/db.py` tự tạo thư mục cha của file SQLite, nên `/data/fsoft-ai.db` không cần
  chuẩn bị trước.
- Câu lệnh Python một dòng trong `HEALTHCHECK` chạy đúng cú pháp.
- `uv==0.12.1` có thật trên PyPI và đọc được `uv.lock` `revision = 3` của repo này.
- `.dockerignore` không loại nhầm thứ nào mà Dockerfile `COPY` tới. Ba thứ nặng nhất
  (`.cache` 486 MB, `.venv` 250 MB, `.mypy_cache` 47 MB) đều đã bị loại; mọi thứ còn lại ở
  gốc repo đều ≤ 2 MB.

**Chưa kiểm chứng được — cần Docker thật:**

- Thời gian build và dung lượng ảnh cuối (ước lượng 1,3 GB).
- Quyền ghi vào volume `/data` khi chạy bằng user `fsoft` (uid 10001). Theo tài liệu Docker,
  volume **có tên** kế thừa quyền của thư mục trong image nên phải chạy đúng; nhưng nếu bạn
  đổi sang **bind mount** thì quyền của host thắng và có thể gặp `Permission denied`.
- RSS thật trong container Linux. Con số 784 MB là đo trên Windows.
- `docker compose` có nhận `mem_limit`/`cpus` ở cấp service hay cảnh báo bỏ qua — tuỳ phiên
  bản Compose.

Chạy xong lần đầu, nếu mọi thứ trơn thì xoá mục này đi.

---

## 11. Phát triển bằng Docker

Muốn sửa code mà không build lại mỗi lần, tạo `docker-compose.override.yml` (Compose tự đọc,
và file này nên được gitignore):

```yaml
services:
  fsoft-ai:
    volumes:
      - fsoft-ai-data:/data
      - ./app:/app/app:ro
      - ./migrations:/app/migrations:ro
    command:
      - /app/.venv/bin/uvicorn
      - app.main:app
      - --host
      - 0.0.0.0
      - --port
      - "8000"
      - --reload
```

`--reload` chỉ dùng khi phát triển: nó khởi động lại tiến trình mỗi lần file đổi, và **nạp
lại model 470 MB mỗi lần**, mất vài giây.

---

## 12. Triển khai lên Railway

Railway tự nhận `Dockerfile`. Cần làm thêm:

1. Gắn **volume** vào `/data`.
2. Đặt biến môi trường trong bảng điều khiển Railway — **không** commit `.env`.
   Nhớ đặt `AI_DB_PATH=/data/fsoft-ai.db` và `FASTEMBED_CACHE_PATH=/opt/fastembed_cache`.
3. **Không gán public domain.** Backend Java gọi qua mạng nội bộ
   `http://fsoft-ai.railway.internal:8000` (SPEC mục 13).
4. Giữ đúng **1 replica**. Semantic cache và ngân sách token đều nằm trong RAM, và nhiều
   replica sẽ chạy nhiều vòng lặp đồng bộ chồng lên nhau.

> **Cảnh báo về gói:** RSS đo được ~784 MB, vượt gói Railway 512 MB. Hướng xử lý là dùng bản
> model lượng tử `model_qint8_avx512_vnni.onnx` (118 MB thay vì 470 MB) — chưa làm, xem
> SPEC mục 14.6.
