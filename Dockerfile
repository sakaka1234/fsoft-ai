FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed_cache \
    OMP_NUM_THREADS=1 \
    ORT_NUM_THREADS=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Nạp sẵn model vào image. KHÔNG tải lúc runtime —
# cold start Railway mà phải tải 470 MB là hỏng.
#
# Bước này đứng TRƯỚC khi copy app/ để đổi code không làm mất layer 470 MB.
COPY scripts/download_model.py ./scripts/
RUN uv run python scripts/download_model.py

COPY app ./app
COPY migrations ./migrations
COPY tests/fixtures ./tests/fixtures

EXPOSE 8000

# 1 worker. Nhiều worker nghĩa là nhân bản model trong RAM
# và chạy nhiều vòng lặp đồng bộ song song (SPEC muc 5.10, bẫy 3).
CMD ["uv", "run", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
