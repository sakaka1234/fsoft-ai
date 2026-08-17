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
- **Khoảng 2,5 GB đĩa trống.** Ảnh cuối khoảng 1 GB, nhưng lúc build cần thêm chỗ cho layer
  trung gian.
- **Ít nhất 768 MB RAM cấp cho container** (đo được: 504 MB khi chạy, đỉnh 540 MB lúc nạp
  model). Docker Desktop trên Windows/macOS mặc định cấp 2 GB cho cả máy ảo — đủ.
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

### Khởi động dừng ngay với `Cấu hình đường dẫn không dùng được`

Đây là preflight đang làm việc. Nó liệt kê từng biến sai kèm giá trị đúng — làm đúng theo
thông báo là xong, không cần tra thêm. Hai thủ phạm thường gặp là `AI_DB_PATH` và
`FASTEMBED_CACHE_PATH` bị đặt bằng đường dẫn tương đối.

### Service báo "live" nhưng mọi request trả `503 INDEX_NOT_READY`

Tìm `warmup_failed` trong log. `warmup` chạy ở task nền nên nó hỏng mà không kéo tiến trình
xuống — nền tảng vẫn thấy cổng có người nghe và kết luận là thành công.

Nguyên nhân hay gặp nhất là `FASTEMBED_CACHE_PATH` sai, xem
[mục 11b](#11b-triển-khai-lên-render--và-ba-lỗi-chắc-chắn-gặp). Từ bản mới preflight bắt được
trường hợp này ngay lúc khởi động.

### Deploy bị coi là thất bại dù log không có lỗi

Nền tảng đang gõ health check vào cổng khác cổng service đang nghe. `CMD` đọc `$PORT` nên
thường tự khớp — trừ khi bạn tự đặt `PORT` sai, hoặc nền tảng dùng tên biến khác. Kiểm dòng
`Uvicorn running on http://0.0.0.0:...` trong log xem cổng thật là bao nhiêu.

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
- RSS thật trong container Linux. Con số 504 MB ở mục 13 đo trên Windows; container Linux
  với bản fp32 trước đó cho 881,5 MB so với 893,5 MB trên Windows, tức Linux thấp hơn khoảng
  12 MB — nên dự kiến khoảng 492 MB, nhưng chưa xác nhận.
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

## 11b. Triển khai lên Render — và ba lỗi chắc chắn gặp

Render chỉ đọc `Dockerfile`, **không đọc `docker-compose.yml`**. Nghĩa là khối `environment:`
đè đường dẫn trong compose không hề có tác dụng, và bạn phải tự đặt biến trong bảng điều
khiển Render.

### Lỗi 1 — `PermissionError: [Errno 13] Permission denied: '/app/data'`

Nguyên nhân: `AI_DB_PATH` được đặt bằng đường dẫn **tương đối** (thường vì dán nguyên nội
dung `.env` của máy dev lên). `./data/fsoft-ai.db` giải ra thành `/app/data`, mà trong image
`/app` thuộc `root` còn service chạy bằng user không đặc quyền.

Sửa: đặt **đường dẫn tuyệt đối**.

```
AI_DB_PATH=/data/fsoft-ai.db
```

`/data` đã được tạo sẵn và cấp quyền trong image. Render free **không có disk bền**, nên dữ
liệu mất mỗi lần container bị thay — không sao, service tự đồng bộ lại từ backend, chỉ tốn
thời gian embed lại.

### Lỗi 2 — `warmup_failed` … `Permission denied: '/app/.cache'`, service "live" nhưng `/readyz` mãi 503

Y hệt lỗi 1, chỉ khác biến: `FASTEMBED_CACHE_PATH=./.cache/fastembed` giải ra `/app/.cache`.
Encoder không tìm thấy model ở đó nên rơi xuống nhánh tự tải, rồi `fastembed` chết khi cố
tạo thư mục cache.

Triệu chứng đặc biệt dễ nhầm: Render báo **"Your service is live 🎉"** và URL trả về, nhưng
mọi request nghiệp vụ đều `503 INDEX_NOT_READY` — vì `warmup` chạy ở task nền, nó hỏng mà
không kéo tiến trình xuống.

Sửa: `FASTEMBED_CACHE_PATH=/opt/fastembed_cache` (chỗ model đã nạp sẵn lúc build image).

> **Từ bản này về sau, cả hai lỗi trên bị bắt cùng lúc lúc khởi động.** `app/core/preflight.py`
> kiểm mọi đường dẫn trước khi làm gì khác, liệt kê hết những cái sai trong một thông báo,
> và **cho deploy thất bại dứt khoát** thay vì để service sống mà không bao giờ sẵn sàng.
> Thông báo gọi tên từng biến, in ra đường dẫn đã giải, và nói luôn giá trị đúng.

### Lỗi 3 — cảnh báo `AI_MIN_SCORE=0.83 không phải ngưỡng đã hiệu chỉnh`

Cùng nguyên nhân: biến môi trường lấy từ bản `.env` cũ. Bỏ qua cảnh báo này thì **cổng lọc
liên quan sai âm thầm** — 2 trong 5 case NEGATIVE trả về thẻ bừa thay vì trả rỗng.

Sửa: `AI_MIN_SCORE=0.8344`.

### Bộ biến tối thiểu cho Render

Đừng dán cả `.env`. Chỉ đặt đúng những biến này:

```
AI_DB_PATH=/data/fsoft-ai.db
FASTEMBED_CACHE_PATH=/opt/fastembed_cache
AI_EMBEDDING_MODEL_FILE=onnx/model_qint8_avx512_vnni.onnx
AI_MIN_SCORE=0.8344
AI_MODEL_VERSION=multilingual-e5-small-q8@t1
AI_ONNX_CPU_ARENA=false

AI_INTERNAL_TOKEN=<chuỗi ngẫu nhiên 64 ký tự>
AI_LLM_API_KEY=<khoá Groq, để trống nếu chỉ cần ba nhánh 0 token>

AI_SOURCE_MODE=fixture
```

Ba biến đầu tiên trùng với `ENV` trong `Dockerfile` nên **không đặt cũng được** — nhưng đặt
tường minh thì đọc bảng biến là biết ngay service đang chạy cấu hình nào.

**Nguy hiểm nhất là đặt chúng SAI**, vì biến của nền tảng đè lên `ENV` của image. Hai giá trị
tuyệt đối không được dùng ở đây, dù chúng đúng trên máy dev:

```
AI_DB_PATH=./data/fsoft-ai.db            <- SAI, giải ra /app/data
FASTEMBED_CACHE_PATH=./.cache/fastembed  <- SAI, giải ra /app/.cache
```

**Đừng** đặt `PORT`: Render tự đặt, và `CMD` đã đọc nó.

### Cảnh báo thẳng: Render free có thể không đủ

| Hạng mục | Render free | Cần |
|---|---|---|
| RAM | 512 MB | **540 MB đỉnh** lúc nạp model |
| CPU | 0,1 vCPU | nạp model đo 1,45s trên 1 nhân đầy đủ |
| Disk bền | không có | không bắt buộc |
| Ngủ khi rảnh | sau 15 phút | mỗi lần thức phải nạp lại model |

Hai hàng đầu là vấn đề thật. RAM 512 MB nằm **dưới** đỉnh 540 MB, nên rất có thể OOM ngay
lúc khởi động; và với 0,1 vCPU thì bước nạp model chậm gấp nhiều lần, dễ vượt thời gian chờ
health check của Render.

Vá xong hai lỗi trên mà vẫn thấy container bị giết không kèm traceback thì gần như chắc là
OOM. Kiểm bằng cách xem log có dòng nào của `lifespan` chạy xong không. Khi đó đổi nền tảng
— xem mục 13 để biết chỗ nào đủ RAM.

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

---

## 13. RAM và chọn gói hosting

### Con số đo được

Đo trên Xeon E5-2680, cùng một service, chỉ đổi biến thể ONNX:

| Biến thể | File | RSS chạy | Đỉnh | Recall@5 | NEGATIVE | p50 |
|---|---|---|---|---|---|---|
| `model.onnx` fp32 | 448 MB | 893 MB | — | 0.971 | 1.00 | 11,7ms |
| `model_O4.onnx` | 224 MB | 707 MB | — | 0.971 | 1.00 | 12,4ms |
| **`..._qint8...onnx`** ← mặc định | **113 MB** | **504 MB** | **540 MB** | **0.971** | **1.00** | **10,3ms** |

Bản lượng tử **giảm 44% RAM, giữ nguyên mọi chỉ số, và còn nhanh hơn** — nạp 1,4 giây thay
vì 4,0 giây. Sau 40 lượt search và 3 lượt quiz, RSS không nhích lên: 503,3 → 503,9 MB.

Đỉnh 540 MB xảy ra **lúc nạp model**, không phải lúc phục vụ. Đây là con số quyết định khi
đặt trần bộ nhớ.

> **Cái bẫy:** đổi `AI_EMBEDDING_MODEL_FILE` mà quên đổi `AI_MIN_SCORE` thì cổng lọc liên
> quan sai âm thầm. Đo thật: bản lượng tử dùng ngưỡng 0.83 của fp32 làm **2 trong 5 case
> NEGATIVE hỏng** — câu lẽ ra trả rỗng bắt đầu trả về thẻ bừa, không có lỗi nào báo.
> `Settings` sẽ cảnh báo, và `tests/test_embedding.py` ghim cặp này lại.

Muốn đo lại trên máy bạn: `uv run python scripts/do_bien_the_onnx.py` (chạy cả ba biến thể,
mỗi cái một tiến trình riêng). Hiệu chỉnh lại ngưỡng: `uv run python scripts/hieu_chinh_nguong.py`.

### Gói nào dùng được

**Gói 512 MB không dùng được**, kể cả sau khi đã tối ưu: đỉnh 540 MB làm nó OOM ngay lúc
khởi động, tức là chết trước khi kịp phục vụ request đầu tiên. Loại Render free,
Koyeb free, Fly `shared-cpu-1x` 256 MB.

Cần **tối thiểu 768 MB**. Vài lựa chọn thực tế:

| Nơi chạy | RAM | Ghi chú |
|---|---|---|
| Oracle Cloud Always Free | 24 GB (ARM) | Miễn phí thật và rộng nhất. Cần build ảnh cho `arm64` |
| Hugging Face Spaces (Docker, CPU basic) | 16 GB | Miễn phí, dựng nhanh nhất để demo. Không có volume bền |
| Google Cloud Run | đặt tuỳ ý, 1 GB | Có bậc miễn phí, tự co về 0 khi rảnh. Nạp model 1,4 giây nên cold start chấp nhận được |
| Railway Hobby | 1 GB | Rẻ, sẵn volume, đúng thứ SPEC mục 13 mô tả |
| Fly.io `shared-cpu-1x` 1 GB | 1 GB | Có volume |

Với Cloud Run và các nền tảng tự co giãn, nhớ **giới hạn 1 instance**: semantic cache và
ngân sách token đều nằm trong RAM, và nhiều instance sẽ chạy nhiều vòng lặp đồng bộ chồng lên
nhau (SPEC mục 14.3).

### Còn giảm được nữa không

Khó. Sau khi bỏ model, phần sàn khoảng **354 MB** là `onnxruntime` + `numpy` + tokenizer —
đo được bằng cách trừ: fp32 tốn 799 MB cho file 448 MB, lượng tử tốn 466 MB cho file 113 MB,
chênh lệch đúng bằng chênh lệch kích thước file. Muốn xuống dưới 400 MB thì phải đổi hẳn
cách làm, ví dụ gọi API embedding bên ngoài — nhưng như vậy mất luôn tính chất "0 token,
không phụ thuộc mạng" vốn là nền tảng của thiết kế này.

Một mẹo đã áp dụng: tắt bộ cấp phát arena của ONNX Runtime (`AI_ONNX_CPU_ARENA=false`) tiết
kiệm ~47 MB mà độ trễ không đổi.
