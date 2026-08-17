# syntax=docker/dockerfile:1
#
# Ảnh chạy fsoft-ai. Xem docs/DOCKER.md để biết cách build và chạy.
#
# Ba quyết định chi phối toàn bộ file này:
#
# 1. Model 470 MB được nạp SẴN vào image lúc build, không tải lúc chạy. Cold
#    start mà phải tải 470 MB thì container bị coi là chết trước khi kịp sống.
# 2. Đúng 1 worker. Nhiều worker nghĩa là nhân bản model trong RAM (mỗi bản
#    ~710 MB) và chạy nhiều vòng lặp đồng bộ song song cùng ghi vào một file
#    SQLite. SPEC muc 5.10 bẫy 3.
# 3. Thứ tự COPY xếp theo tần suất thay đổi: thứ ít đổi nhất lên trước, để sửa
#    một dòng trong app/ không làm mất layer 470 MB và bắt tải lại từ đầu.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Nơi model ONNX nằm. Cả bước build lẫn lúc chạy đều đọc biến này, nên đổi
    # nó là phải đổi cả hai chỗ.
    FASTEMBED_CACHE_PATH=/opt/fastembed_cache \
    # SQLite nằm trên volume, KHÔNG nằm trong image: xoá container không được
    # làm mất index đã embed.
    AI_DB_PATH=/data/fsoft-ai.db \
    # ONNX Runtime mặc định mở thread bằng số nhân CPU. Trong container bị giới
    # hạn CPU, nó vẫn đọc số nhân của MÁY CHỦ nên tạo thừa thread rồi tranh nhau
    # — chậm hơn hẳn chạy 1 thread. SPEC muc 5.10 bẫy 2.
    OMP_NUM_THREADS=1 \
    ORT_NUM_THREADS=1 \
    # uv mặc định hardlink từ cache sang .venv; qua ranh giới layer thì không
    # hardlink được và nó cảnh báo suốt. Copy thẳng cho yên.
    UV_LINK_MODE=copy \
    # Biên dịch sẵn .pyc lúc build để lần khởi động đầu không phải làm.
    UV_COMPILE_BYTECODE=1 \
    # Không giữ cache của uv. Cache nằm ở /root/.cache/uv và chứa bản GIẢI NÉN
    # của đúng những wheel vừa cài, nên không tắt là image mang hai bản của cùng
    # một bộ thư viện — phình vô ích vài trăm MB. Cache cũng chẳng dùng lại được
    # gì vì layer `uv sync` đã được Docker cache sẵn.
    UV_NO_CACHE=1

WORKDIR /app

# Tạo user và /data NGAY TỪ ĐÂY, trước mọi bước nặng.
#
# Trước đây dòng này nằm ở cuối file kèm `chown -R fsoft:fsoft /data /app`, và
# nó hỏng theo hai đường: `/app/.venv` (~250 MB) bị đổi metadata nên Docker nhân
# đôi nó sang một layer mới, và vì dòng đó đứng SAU `COPY app` nên mỗi lần sửa
# một dòng code là phải chạy lại `chown -R` trên hàng chục nghìn tệp rồi xuất
# lại cả layer đó.
#
# Không chown `/app`: lúc chạy không có gì ghi vào đó. Nơi duy nhất service ghi
# là `/data` (app/store/db.py), và PYTHONDONTWRITEBYTECODE=1 chặn sinh .pyc.
# Để /app thuộc root còn an toàn hơn.
RUN useradd --create-home --uid 10001 fsoft \
    && mkdir -p /data \
    && chown fsoft:fsoft /data

# Ghim phiên bản uv vì hai lý do, lý do thứ hai mới là lý do bắt buộc:
#
# 1. Không ghim thì hai lần build cách nhau vài tháng có thể ra hai môi trường
#    khác nhau — đúng thứ mà uv.lock sinh ra để tránh.
# 2. `uv.lock` của repo này ghi `revision = 3`. Bản uv quá cũ KHÔNG đọc được
#    định dạng đó và `uv sync` sẽ hỏng giữa lúc build. Con số dưới đây khớp bản
#    đang dùng trên máy dev, tức bản đã sinh ra chính file lock này.
#
# Nâng uv thì kiểm lại `head -3 uv.lock` xem revision có đổi không.
# Kiểm điều kiện của `--locked` ở dòng dưới bằng `uv lock --check`.
RUN pip install --no-cache-dir uv==0.12.1

# ---------------------------------------------------------------------
# Layer 1 — thư viện. Chỉ đổi khi pyproject.toml hoặc uv.lock đổi.
# ---------------------------------------------------------------------
COPY pyproject.toml uv.lock ./

# --locked: KHẲNG ĐỊNH uv.lock đang khớp với pyproject.toml. Lệch thì build hỏng
#           ngay tại đây, thay vì âm thầm cài bộ thư viện khác máy dev.
#           (`--frozen` KHÔNG làm việc này — nó chỉ dùng lock hiện có mà không
#           kiểm tra lock có cũ hay không. Dễ nhầm hai cờ này.)
# --no-dev: bỏ pytest/ruff/mypy — không có lý do gì để chúng nằm trong ảnh chạy.
RUN uv sync --locked --no-dev

# ---------------------------------------------------------------------
# Layer 2 — model 470 MB. Layer đắt nhất, nên đứng càng cao càng tốt.
# ---------------------------------------------------------------------
#
# `download_model.py` cố ý KHÔNG import app, nhờ vậy nó chạy được ở đây khi app/
# còn chưa được copy vào. Đổi một dòng trong app/ sẽ không đụng tới layer này.
#
# Script tự gọi huggingface_hub thay vì để fastembed tự tải: fastembed có lỗi
# đối chiếu tệp làm mọi lần tải sạch đều thất bại. Chi tiết trong chính file đó.
COPY scripts/download_model.py ./scripts/

# Gọi THẲNG interpreter trong .venv, không qua `uv run`.
#
# `uv run` tự đồng bộ lại môi trường trước khi chạy, và mặc định nó cài CẢ nhóm
# `dev` — tức là kéo ngược pytest, ruff, mypy vào đúng cái .venv mà dòng trên
# vừa cố ý dựng bằng `--no-dev`. Hỏng hoàn toàn im lặng: build vẫn xanh, chỉ là
# ảnh chạy mang thêm vài chục MB công cụ phát triển.
RUN /app/.venv/bin/python scripts/download_model.py

# ---------------------------------------------------------------------
# Layer 3 — mã nguồn. Đổi liên tục, nên để cuối cùng.
# ---------------------------------------------------------------------
COPY app ./app
COPY migrations ./migrations
# Dữ liệu mẫu 24 thẻ. Cần cho AI_SOURCE_MODE=fixture — chạy thử được ngay mà
# không cần backend Java.
COPY tests/fixtures ./tests/fixtures

# Từ đây trở đi không chạy bằng root nữa. User và /data đã dựng ở đầu file.
USER fsoft

VOLUME ["/data"]
EXPOSE 8000

# Dùng /readyz chứ không phải /healthz: "khoẻ" ở đây phải có nghĩa là PHỤC VỤ
# ĐƯỢC, mà /healthz trả 200 ngay cả khi model hỏng.
#
# start-period 120 giây là để chờ nạp model (~3 giây trên máy khoẻ, nhưng lần
# chạy đầu trên máy yếu hoặc CPU bị bóp có thể lâu hơn nhiều). Trong khoảng đó
# thất bại không bị tính là hỏng.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["sh", "-c", "python -c \"import os,sys,urllib.request as u; p=os.environ.get('PORT','8000'); sys.exit(0 if u.urlopen(f'http://127.0.0.1:{p}/readyz', timeout=4).status==200 else 1)\""]

# Gọi thẳng uvicorn trong .venv, KHÔNG qua `uv run`: `uv run` kiểm lại môi
# trường mỗi lần khởi động, chậm hơn và cần đọc được uv.lock. uvicorn tự chèn
# thư mục làm việc vào sys.path (uvicorn/main.py) nên `app.main:app` import được.
#
# Vì sao phải qua `sh -c` chứ không dùng dạng exec thuần: các nền tảng PaaS
# (Render, Cloud Run, Heroku, Fly) TỰ ĐẶT biến `PORT` và bắt service lắng nghe
# đúng cổng đó. Ghim cứng 8000 thì health check của họ gõ vào cổng khác, không
# thấy ai trả lời, và deploy bị coi là thất bại — kể cả khi service chạy hoàn
# toàn bình thường bên trong.
#
# `exec` ở đầu là bắt buộc: nó thay thế `sh` bằng uvicorn nên uvicorn thành PID 1
# và nhận được SIGTERM. Không có `exec` thì `sh` giữ PID 1, không chuyển tiếp
# tín hiệu, và mỗi lần dừng container đều phải đợi hết 10 giây rồi bị SIGKILL —
# lifespan không kịp chạy, SQLite không được đóng tử tế.
CMD ["sh", "-c", "exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-${AI_SERVICE_PORT:-8000}} --workers 1"]
