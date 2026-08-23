# Chạy fsoft-ai bằng Docker

> **Không được build thử trên máy viết ra nó.** Máy đó không cài được Docker, nên tôi bù bằng
> cách đối chiếu từng dòng với code thật thay vì chạy thử. Mục [10](#10-những-gì-chưa-được-kiểm-chứng)
> nói rõ chỗ nào là chắc chắn và chỗ nào là suy luận.
>
> Đã có build thật ở nơi khác, và **cả năm lỗi ở mục 11b đều đã gặp ngoài thực tế** — mỗi lỗi
> kèm nguyên văn thông báo, xem mục
> [11b](#11b-triển-khai-lên-render--và-năm-lỗi-chắc-chắn-gặp).
> Vấp lỗi khác thì chụp lại thông báo; mục [9](#9-gặp-lỗi-thì-tra-ở-đây) liệt kê sẵn những lỗi
> dễ gặp nhất.

---

## 1. Cần có gì

- **Docker Engine 20.10+** kèm plugin Compose v2 (`docker compose`, có dấu cách).
  Kiểm bằng `docker compose version`. Nếu máy bạn chỉ có `docker-compose` (có gạch nối, bản v1)
  thì mọi lệnh dưới đây thay `docker compose` thành `docker-compose`.
- **Khoảng 2,5 GB đĩa trống.** Ảnh cuối khoảng 1 GB, nhưng lúc build cần thêm chỗ cho layer
  trung gian.
- **Ít nhất 512 MB RAM cấp cho container** (đo được: đỉnh 317 MB). Docker Desktop trên
  Windows/macOS mặc định cấp 2 GB cho cả máy ảo — thừa đủ.
- **Mạng lúc build** để tải model gốc 118 MB từ Hugging Face (bước tỉa từ vựng sau đó chạy
  hoàn toàn cục bộ). Lúc *chạy* thì không cần mạng, trừ
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
| `AI_LLM_API_KEY` | Rỗng. Ba nhánh 0 token vẫn chạy; nhánh `RAG` và `POST /vocab/extract` trả `503` | Khoá Groq dạng `gsk_...` |

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
`FILL_BLANK`, và toàn bộ `POST /vocab/extract`) — chúng cần `AI_LLM_API_KEY`.

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

### Build dừng ở bước lấy `uv`

Thông báo có `ghcr.io` hoặc `astral-sh/uv`: không lấy được binary `uv`. Chạy lại trước đã —
gần như luôn là mạng nấc một nhịp. Nếu ở chỗ bạn ghcr.io bị chặn hẳn thì đổi dòng
`COPY --from=ghcr.io/astral-sh/uv:0.12.1 ...` trong `Dockerfile` về đường PyPI:

```dockerfile
RUN pip install --retries 10 --timeout 60 uv==0.12.1
```

Đừng thêm `--no-cache-dir` như bản cũ: nó làm mỗi lần thử lại phải tải lại từ đầu. Và biết
trước rằng đường này kém tin cậy hơn — chính nó đã làm deploy trên Render chết với
`too many 502 error responses` từ `files.pythonhosted.org`, xem [mục 11b](#lỗi-4--could-not-install-packages-due-to-an-oserror--too-many-502-error-responses).

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
[mục 11b](#11b-triển-khai-lên-render--và-năm-lỗi-chắc-chắn-gặp). Từ bản mới preflight bắt được
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
- Tag `0.12.1` có thật trên `ghcr.io/astral-sh/uv`, và index của nó có **cả `linux/amd64` lẫn
  `linux/arm64`** — nên đường Oracle Cloud ARM ở mục 13 vẫn dùng được, Docker tự chọn đúng
  kiến trúc. Ảnh đó chứa `/uv` + `/uvx` ở gốc với mode 755, và `/uv` là ELF64 x86-64
  **tĩnh hoàn toàn**
  (không có `ld-linux-x86-64.so.2`, không có symbol `GLIBC_`) nên chạy được trong
  `python:3.12-slim`. Bản `uv` này đọc được `uv.lock` `revision = 3` của repo — nó chính là
  bản đã sinh ra file lock đó trên máy dev.
- `.dockerignore` không loại nhầm thứ nào mà Dockerfile `COPY` tới. Ba thứ nặng nhất
  (`.cache` 486 MB, `.venv` 250 MB, `.mypy_cache` 47 MB) đều đã bị loại; mọi thứ còn lại ở
  gốc repo đều ≤ 2 MB.

**Chưa kiểm chứng được — cần Docker thật:**

- Thời gian build và dung lượng ảnh cuối. Một lần build thật trên Docker Desktop (bản fp32,
  trước khi lượng tử hoá và tỉa) cho **881,5 MB** — thấp hơn con số ước lượng 1,3 GB đã ghi ở
  đây trước đó. Ảnh hiện tại nhẹ hơn nữa vì model trong cache đi từ 448 MB xuống 66 MB, nhưng
  chưa đo lại.
- Quyền ghi vào volume `/data` khi chạy bằng user `fsoft` (uid 10001). Theo tài liệu Docker,
  volume **có tên** kế thừa quyền của thư mục trong image nên phải chạy đúng; nhưng nếu bạn
  đổi sang **bind mount** thì quyền của host thắng và có thể gặp `Permission denied`.
- RSS thật trong container Linux. Mọi con số ở mục 13 (đỉnh **317 MB** cho bản mặc định) đều
  đo trên Windows bằng `PeakWorkingSetSize`. Linux thường thấp hơn một chút, nhưng đừng lập kế
  hoạch dung lượng dựa vào phần "thấp hơn" đó — hãy lấy 317 MB làm số.
- **Bước tỉa từ vựng trong `Dockerfile` chưa từng chạy trong một image thật.** Nó chạy đúng
  trên máy dev bằng cùng interpreter và cùng câu lệnh, nhưng lần build đầu là lần đầu nó chạy
  trong container. Vấp ở đó thì log chỉ thẳng vào dòng `scripts/tia_vocab.py`.
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

## 11b. Triển khai lên Render — và năm lỗi chắc chắn gặp

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

### Lỗi 4 — `Could not install packages due to an OSError` … `too many 502 error responses`

Toàn văn, xảy ra ở bước `[4/14]` tức rất sớm trong build:

```
ERROR: Could not install packages due to an OSError: HTTPSConnectionPool(
  host='files.pythonhosted.org', port=443): Max retries exceeded with url:
  /packages/.../uv-0.12.1-py3-none-manylinux_2_17_x86_64...whl.metadata
  (Caused by ResponseError('too many 502 error responses'))
error: failed to solve: process "/bin/sh -c pip install --no-cache-dir uv==0.12.1"
  did not complete successfully: exit code: 1
```

**Đây không phải lỗi của bạn và không phải lỗi cấu hình.** 502 là CDN của PyPI hỏng nhất
thời — `pip` đã tự thử lại và vẫn thua. Deploy lại lần nữa thường là xong.

Nhưng nó lộ ra một điểm yếu thật trong `Dockerfile`, nên **bản mới đã sửa để không còn dựa vào
PyPI**: `uv` giờ lấy từ ảnh chính thức của Astral trên ghcr.io.

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
```

Vì sao đổi thay vì chỉ thêm `--retries`: bước này đứng **trước** layer tải model 118 MB, nên
một nhịp nấc của PyPI làm mất trắng cả lần build. Cộng thêm `--no-cache-dir` khiến mọi lần thử
lại phải tải lại từ đầu. Đổi sang `COPY --from` bỏ luôn `pip` khỏi đường đi, và **không tốn
thêm byte nào** trong ảnh cuối vì wheel `uv` trên PyPI vốn chỉ là bao bì quanh đúng binary đó.

Nếu ghcr.io bị chặn ở nơi bạn build thì quay về đường PyPI, xem
[mục 9](#build-dừng-ở-bước-lấy-uv).

### Lỗi 5 — build xanh, "Your service is live", rồi 502 lặp lại mãi

Triệu chứng chính xác, quan sát bằng cách gọi liên tục:

```
/healthz  200        /readyz  503  {"ready":false,"encoder_ready":true,"index_ready":false}
/healthz  200        /readyz  503  ... khoảng 30 giây ...
/healthz  502        /readyz  502  ... rồi lặp lại từ đầu sau vài phút
```

Đây là **OOM**, không phải lỗi trong code. Ba dấu hiệu để chắc:

1. `encoder_ready=true` — model nạp xong, nên không phải lỗi đường dẫn hay tệp hỏng.
2. `index_ready=false` mãi — nó chết ở giữa `intent_classifier.warmup()` và
   `load_index_from_db()`, tức đúng lúc chạy lô embedding đầu tiên, tức đúng đỉnh RAM.
3. `502` chứ không phải `503` đứng mãi — **tiến trình biến mất**. Nếu là một exception thường
   thì `warmup` đã bắt, ghi `warmup_failed`, và service **vẫn sống** với `/readyz` 503 vĩnh
   viễn. Không có traceback nào là dấu hiệu của cgroup: kernel giết thẳng, không báo ai.

Nguyên nhân hay gặp nhất **không phải** RAM thật sự không đủ, mà là service đang chạy **biến
thể ONNX nặng hơn** biến thể bạn tưởng. Hai đường dẫn tới đó, và **phải phân biệt được** vì
chỗ phải sửa khác nhau hoàn toàn:

| | Ảnh CŨ đang chạy | Biến CŨ còn sót trong bảng điều khiển |
|---|---|---|
| Dấu hiệu | `model_version` = mặc định trong code của commit cũ | `model_version` = giá trị bạn từng gõ tay |
| Sửa ở đâu | Deploy lại (xem dưới) | Xoá biến, [bộ biến tối thiểu](#bộ-biến-tối-thiểu-cho-render) |

Hai trường hợp này cho **cùng một triệu chứng và thường cùng một giá trị `model_version`** —
vì giá trị bạn từng gõ tay chính là mặc định của commit lúc đó. Đã mất hai vòng chẩn đoán vào
đúng chỗ này: kết luận "biến cũ còn sót" là **sai**, thủ phạm là ảnh cũ.

Cách phân biệt dứt điểm là dòng `commit` trong log khởi động (xem dưới) — nó nói thẳng mã đang
chạy là commit nào. So với `git log -1 --format=%h` trên máy bạn.

**Vì sao ảnh cũ lại chạy tiếp dù bạn đã push:** Render chỉ deploy khi nhận được webhook từ
GitHub rồi tải mã về. Cả hai bước đó đều có thể im lặng thất bại:

- Build của commit mới **hỏng** → Render giữ nguyên ảnh cũ đang chạy. Đây là hành vi đúng,
  nhưng nó nghĩa là một build hỏng trông giống hệt "service vẫn như cũ".
- **GitHub đang sự cố.** Đã gặp thật: `Webhooks` ở trạng thái degraded và archive download lỗi
  ~50%, nên push lên GitHub thành công mà Render không hề biết. Kiểm ở
  <https://www.githubstatus.com> — quan tâm ba dòng `Webhooks`, `API Requests`, và ghi chú về
  *archive downloads*.
- **Auto-Deploy** bị đặt `No` trong Settings của service.

Trong cả ba trường hợp, cách chắc chắn nhất là **Manual Deploy → Deploy latest commit**, rồi
đối chiếu `commit` trong log với commit bạn vừa push.

**Từ bản này, log nói thẳng ra.** Dòng đầu tiên khi khởi động là cấu hình đang có hiệu lực,
kèm danh sách field nào bị biến môi trường đè:

```json
{"event":"cau_hinh_hieu_luc","commit":"610b1b7c44421211cca9202b9edc61e9ebacd1ce",
 "model_file":"onnx/model_qint8_avx512_vnni.onnx","model_version":"multilingual-e5-small-q8@t1",
 "dat_tu_moi_truong":["ai_embedding_model_file","ai_min_score",...]}
{"event":"ram_container","gioi_han_MB":512,"dinh_du_kien_MB":538}
{"event":"ram_co_the_khong_du","level":"warning","bi_de_boi_moi_truong":true,
 "goi_y":"bỏ hẳn AI_EMBEDDING_MODEL_FILE khỏi bảng biến của nền tảng ..."}
```

Đọc theo thứ tự này:

1. **`commit`** — mã đang chạy. Lấy từ `RENDER_GIT_COMMIT` (Railway và Heroku có biến riêng,
   đều được thử). Khác commit bạn vừa push nghĩa là **ảnh cũ**, và mọi suy luận về cấu hình
   phía dưới đều đang nói về mã cũ. `null` nghĩa là nền tảng không công bố — lúc đó dựa vào
   `model_version`.
2. **`dat_tu_moi_truong`** — field nào bị biến môi trường đè, lấy từ `model_fields_set` của
   pydantic. Trong container không có `.env` nên danh sách này đúng bằng bảng biến của nền tảng.
3. **`gioi_han_MB`** — đọc từ cgroup của chính container, không phải RAM của máy chủ. Đối chiếu
   với đỉnh đo được của biến thể đang cấu hình. Đây là **cảnh báo, không chặn deploy** — con số
   đỉnh đo trên máy khác nên không đáng để chặn, nhưng đáng để in ra.

Không thấy dòng nào cả nghĩa là tiến trình chết trước cả lifespan; lúc đó xem `Events` của
Render, nó ghi riêng sự kiện hết bộ nhớ.

Không đọc được log thì `/readyz` cũng nói — **không cần token, chạy được cả khi chưa sẵn sàng**:

```bash
curl -s https://<service>.onrender.com/readyz
# {"ready":false,"encoder_ready":true,"index_ready":false,
#  "model_version":"e5-small-q8-tia113k@t1"}     <- đúng thì ảnh MỚI đã lên
```

### Bộ biến tối thiểu cho Render

Đừng dán cả `.env`. Chỉ đặt đúng bốn biến này:

```
AI_DB_PATH=/data/fsoft-ai.db
FASTEMBED_CACHE_PATH=/opt/fastembed_cache

AI_INTERNAL_TOKEN=<chuỗi ngẫu nhiên 64 ký tự>
AI_LLM_API_KEY=<khoá Groq; để trống thì mất nhánh RAG và cả /vocab/extract>

AI_SOURCE_MODE=fixture
```

Hai biến đầu trùng với `ENV` trong `Dockerfile` nên **không đặt cũng được** — nhưng đặt tường
minh thì đọc bảng biến là biết ngay service đang chạy cấu hình nào.

> **NẾU BẠN ĐÃ TỪNG DEPLOY THEO BẢN CŨ CỦA MỤC NÀY, VÀO XOÁ BỐN BIẾN SAU.**
>
> Bản trước của tài liệu này bảo đặt tường minh cả cụm cấu hình model:
>
> ```
> AI_EMBEDDING_MODEL_FILE=onnx/model_qint8_avx512_vnni.onnx   <- xoá
> AI_MODEL_VERSION=multilingual-e5-small-q8@t1                <- xoá
> AI_MIN_SCORE=0.8344                                         <- xoá
> AI_ONNX_CPU_ARENA=false                                     <- xoá
> ```
>
> Cả bốn giá trị đó **vẫn đúng cú pháp và không gây lỗi nào**, nên không có gì báo cho bạn
> biết. Nhưng biến của nền tảng đè lên mặc định của code, và biến thứ nhất trỏ vào bản
> **chưa tỉa từ vựng**. Hậu quả: ảnh mới có sẵn model đã tỉa nhưng service vẫn nạp bản cũ,
> đỉnh RAM quay về **538 MB**, Render free OOM thành vòng lặp chết — y như trước khi tỉa.
>
> Bỏ trống cả bốn thì code tự lấy mặc định đã khớp nhau: `onnx/model_tia113k.onnx`,
> `e5-small-q8-tia113k@t1`, `0.8344`, `false`. Ba biến sau chỉ tồn tại trong bảng cũ vì
> **phải** đi kèm biến thứ nhất — mặc định của code nay đã là chúng.

**Nguy hiểm nhất là đặt biến SAI**, chứ không phải thiếu biến, vì biến của nền tảng đè lên
`ENV` của image. Hai giá trị tuyệt đối không được dùng ở đây, dù chúng đúng trên máy dev:

```
AI_DB_PATH=./data/fsoft-ai.db            <- SAI, giải ra /app/data
FASTEMBED_CACHE_PATH=./.cache/fastembed  <- SAI, giải ra /app/.cache
```

**Đừng** đặt `PORT`: Render tự đặt, và `CMD` đã đọc nó.

### Render free: RAM đã đủ, nhưng CPU thì chậm

| Hạng mục | Render free | Cần |
|---|---|---|
| RAM | 512 MB | **317 MB đỉnh** — vừa, biên 195 MB |
| CPU | 0,1 vCPU | nạp model 0,76s trên 1 nhân đầy đủ |
| Disk bền | không có | không bắt buộc |
| Ngủ khi rảnh | sau 15 phút | mỗi lần thức phải nạp lại model |

RAM **đã hết là vấn đề** kể từ bản tỉa từ vựng: đỉnh 317 MB, biên 195 MB. Trước đó đỉnh là
538 MB và Render free OOM thành vòng lặp chết — nếu bạn đang xem một bản cũ hơn thì đó là
nguyên nhân.

Còn lại là chuyện tốc độ: 0,1 vCPU khiến bước nạp model chậm gấp nhiều lần con số 0,76 giây,
và service ngủ sau 15 phút rảnh nên mỗi lần thức là nạp lại. `Dockerfile` đặt
`AI_ORT_INTRA_OP_THREADS=1` để ONNX Runtime không mở 16 luồng cho 0,1 vCPU rồi tự tranh nhau.

Nếu container vẫn bị giết mà **không kèm traceback** thì mới là OOM. Kiểm bằng cách xem log có
dòng nào của `lifespan` chạy xong không, rồi xem mục 13.

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

| Biến thể | File | Đỉnh RSS | Recall@5 | NEGATIVE | p50 | Nạp |
|---|---|---|---|---|---|---|
| `model.onnx` fp32 | 448 MB | 935,8 MB | 0.971 | 1.00 | 10,9ms | 2,48s |
| `model_O4.onnx` | 224 MB | 696,7 MB | 0.971 | 1.00 | 11,2ms | 2,31s |
| `..._qint8...onnx` | 118 MB | 537,8 MB | 0.971 | 1.00 | 9,2ms | 1,45s |
| **`model_tia113k.onnx`** ← mặc định | **66 MB** | **317,0 MB** | **0.971** | **1.00** | **9,0ms** | **0,76s** |

Bản tỉa từ vựng **giảm 66% RAM so với fp32, giữ nguyên mọi chỉ số, và nạp nhanh hơn ba lần**.

Vì sao lượng tử hoá một mình thì tắc ở 538 MB: kẻ tốn RAM nhất **không phải model mà là
tokenizer**. `Tokenizer.from_file` nạp bảng Unigram 250.002 token và ăn **250 MB** (~1 KB mỗi
token), còn `ort.InferenceSession` chỉ ăn 130 MB. Vì 250 MB đó là hằng số không phụ thuộc biến
thể ONNX, đổi biến thể ONNX kéo được 936 → 697 → 538 rồi hết đường. Tỉa từ vựng đánh vào cả
hai: tokenizer 250 → 79 MB, session 134 → 82 MB.

Tỉa **không mất chất lượng** vì `scale`/`zero_point` của bảng là vô hướng per-tensor, nên chọn
hàng trên mảng uint8 là phép toán chính xác — token nào được giữ thì vector giống **từng bit**.

> **Hai cái bẫy, cả hai đều hỏng âm thầm:**
>
> Đổi `AI_EMBEDDING_MODEL_FILE` mà quên đổi `AI_MIN_SCORE` thì cổng lọc liên quan sai. Đo
> thật: bản lượng tử dùng ngưỡng 0.83 của fp32 làm **2 trong 5 case NEGATIVE hỏng**.
> `Settings` sẽ cảnh báo, và `tests/test_embedding.py` ghim cặp này lại.
>
> `AI_EMBED_BATCH_SIZE` cũng đổi kết quả embedding qua nhiễu padding, và cổng NEGATIVE hiện
> cách mép đúng **4,7e-4**. Hạ batch 32 → 8 làm NEGATIVE tụt 1.000 → 0.800. Mọi lần đổi batch
> phải chạy lại `scripts/hieu_chinh_nguong.py`.

Muốn đo lại trên máy bạn: `uv run python scripts/do_bien_the_onnx.py` (chạy cả bốn biến thể,
mỗi cái một tiến trình riêng). Hiệu chỉnh ngưỡng: `uv run python scripts/hieu_chinh_nguong.py`.
Dựng lại bản tỉa: `uv run python scripts/tia_vocab.py --ngan-sach 120000 --ten tia113k`.

### Gói nào dùng được

Với đỉnh 317 MB, **gói 512 MB đã dùng được** — biên 195 MB. Trước bản tỉa thì không:
đỉnh 538 MB làm Render free OOM thành vòng lặp chết.

| Nơi chạy | RAM | Ghi chú |
|---|---|---|
| Render free | 512 MB | Vừa. Nhưng 0,1 vCPU nên khởi động chậm, và ngủ sau 15 phút rảnh |
| Hugging Face Spaces (Docker, CPU basic) | 16 GB | Miễn phí, dựng nhanh nhất để demo. Không có volume bền |
| Oracle Cloud Always Free | 24 GB (ARM) | Miễn phí thật và rộng nhất. Cần build ảnh cho `arm64` |
| Google Cloud Run | đặt tuỳ ý | Có bậc miễn phí, tự co về 0. Nạp model 0,76s nên cold start chấp nhận được |
| Railway Hobby | 1 GB | Rẻ, sẵn volume, đúng thứ SPEC mục 13 mô tả |
| Fly.io `shared-cpu-1x` | 256 MB–1 GB | 256 MB vẫn KHÔNG đủ; chọn bậc 512 MB trở lên |

Với Cloud Run và các nền tảng tự co giãn, nhớ **giới hạn 1 instance**: semantic cache và
ngân sách token đều nằm trong RAM, và nhiều instance sẽ chạy nhiều vòng lặp đồng bộ chồng lên
nhau (SPEC mục 14.3).

### Còn giảm được nữa không

Được thêm khoảng 137 MB nữa, nhưng **chưa làm** vì rủi ro không đáng với 195 MB biên đang có.

Hướng đó là **thay hẳn backend tokenizer**: bỏ `tokenizers` của HuggingFace, đọc trực tiếp
`sentencepiece.bpe.model` (5 MB) bằng thư viện `sentencepiece` — đo được 250 → 39 MB. Đã kiểm
chất lượng: Recall@5, MRR, intent, NEGATIVE giống hệt, vector khớp tới 6e-8, 0/40 case đổi phía
ngưỡng.

Cái giá là phải tự viết lại `Encoder` (tokenize + mean-pool + L2 normalize + tạo
`ort.InferenceSession` trực tiếp), và **nhân bản đúng ba quirk** đã đo được:

1. `<unk>` KHÔNG theo quy luật `id_hf = id_sp + 1` — sentencepiece id 0 ứng với HF id 3. Cộng 1
   máy móc thì mọi ký tự ngoài từ vựng thành `<pad>`, sai âm thầm.
2. fastembed pad bằng **id 0 (`<s>`)**, không phải `<pad>=1`, vì nó lấy `pad_token_id` từ
   `config.json`. Dùng pad "đúng về lý" làm mọi vector lệch tới 1,9e-2 — cùng cỡ với khoảng
   chồng lấn 1,24e-2 đang bảo vệ NEGATIVE.
3. HF giữ một token `▁` cho khoảng trắng cuối, sentencepiece bỏ. Phải `strip()` cả hai phía.

Phần sàn còn lại khoảng **91–111 MB** là `onnxruntime` + `numpy`, không giảm được nữa mà không
bỏ hẳn embedding cục bộ — tức mất luôn tính chất "0 token, không phụ thuộc mạng" vốn là nền
tảng của thiết kế này.

Hai mẹo đã áp dụng: tắt bộ cấp phát arena (`AI_ONNX_CPU_ARENA=false`, ~47 MB, độ trễ không
đổi), và đặt `AI_ORT_INTRA_OP_THREADS=1` trong Docker để ONNX Runtime không mở luồng theo số
nhân của máy chủ.
