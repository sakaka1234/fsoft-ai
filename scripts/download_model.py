"""
Nạp sẵn model ONNX vào cache lúc build Docker image và trong CI.

KHÔNG tải lúc runtime — cold start Railway mà phải tải 470 MB là hỏng.

Script này CỐ Ý không import `app`: nó chạy ở một layer Docker riêng, trước khi
`app/` được copy vào image. Nhờ vậy đổi một dòng code trong `app/` không làm
mất layer 470 MB và bắt build tải lại từ đầu.

Cái giá phải trả là danh sách file bị lặp lại giữa đây và
`app/embedding/encoder.py`. `tests/test_embedding.py` có một test ghim hai bên
phải khớp nhau, nên chúng không thể lệch âm thầm.

-----------------------------------------------------------------------
VÌ SAO TỰ TẢI BẰNG huggingface_hub THAY VÌ ĐỂ fastembed TỰ TẢI

Gọi thẳng `TextEmbedding(model_name=..., cache_dir=...)` trên một máy CHƯA có
cache sẽ luôn hỏng với model tuỳ chỉnh này:

    ValueError: Could not load model intfloat/multilingual-e5-small from any source.
    (nguyên nhân bên trong: "Files have been corrupted during downloading process")

Không phải mạng hỏng, cũng không phải file hỏng — file tải về đủ cả. Đó là lỗi
trong chính fastembed, ở `common/model_management.py`:

    file_info_map = {f.path: f for f in repo_files}   # khoá là "onnx/config.json"
    repo_file = file_info_map.get(file_path.name)     # tra bằng "config.json"

Map lập theo ĐƯỜNG DẪN ĐẦY ĐỦ nhưng lại tra bằng TÊN TỆP TRẦN. Repo
`intfloat/multilingual-e5-small` có `config.json` ở gốc (655 byte) và
`onnx/config.json` (653 byte). File thứ hai bị gán nhầm kích thước của file thứ
nhất, rồi bước tự kiểm tra ngay sau đó thấy 653 != 655 và kết luận là hỏng.

Lỗi này chỉ hiện ra trên máy SẠCH: khi cache đã có sẵn, fastembed đi nhánh
`local_files_only=True` vốn không chạy bước đối chiếu đó. Vì vậy nó nằm im suốt
M0–M7 trên máy dev và chỉ nổ ở lần chạy CI đầu tiên — và lẽ ra cũng đã làm hỏng
mọi lần `docker build` sạch.

Tự gọi `snapshot_download` cho ra đúng cấu trúc thư mục mà fastembed mong đợi,
nên `find_local_snapshot()` trong encoder tìm thấy bình thường.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("ORT_NUM_THREADS", "1")

if os.name == "nt":
    # Windows chỉ cho tạo symlink khi bật Developer Mode hoặc chạy quyền admin,
    # còn `huggingface_hub` thì mặc định dùng symlink để khỏi lưu trùng. Không
    # tắt đi thì máy dev Windows chết ngay giữa lúc tải:
    #     OSError: [WinError 1314] A required privilege is not held by the client
    # Bật cờ này thì nó sao chép thay vì liên kết — tốn thêm đĩa, đổi lại chạy
    # được mà không cần quyền gì. Linux (Docker, CI) không đi qua nhánh này.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType
from huggingface_hub import snapshot_download

# Script in tiếng Việt có dấu. Trên Windows, stdout chuyển hướng ra file hoặc
# pipe dùng bảng mã cp1252 và `print` ném UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL = os.environ.get("AI_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
DIM = int(os.environ.get("AI_EMBEDDING_DIM", "384"))
# Mặc định là đường dẫn dev. Dockerfile đặt sẵn `ENV FASTEMBED_CACHE_PATH=
# /opt/fastembed_cache` nên image vẫn nạp đúng chỗ. Trước đây mặc định là
# đường dẫn Docker, khiến chạy tay trên máy dev lại tải vào C:\opt\... trong
# khi README bảo là tải vào .cache/fastembed.
CACHE_PATH = os.environ.get("FASTEMBED_CACHE_PATH", "./.cache/fastembed")

# File GỐC trên Hugging Face. CỐ Ý khác `ai_embedding_model_file` trong
# app/config.py: cái đó là `onnx/model_tia113k.onnx`, một file KHÔNG tồn tại trên
# HF vì `scripts/tia_vocab.py` sinh ra nó từ chính file này.
#
# Hai bước tách rời nhau:
#   download_model.py  tải bản gốc 118 MB từ HF
#   tia_vocab.py       tỉa từ vựng, sinh bản 66 MB mà runtime dùng
#
# `tests/test_embedding.py` ghim quan hệ này để không ai vô tình cho chúng bằng
# nhau — bằng nhau nghĩa là service chạy bản chưa tỉa và ăn thêm 220 MB RAM.
MODEL_FILE = os.environ.get("AI_EMBEDDING_MODEL_FILE_GOC", "onnx/model_qint8_avx512_vnni.onnx")

ADDITIONAL_FILES = [
    "onnx/tokenizer.json",
    "onnx/tokenizer_config.json",
    "onnx/special_tokens_map.json",
    "onnx/sentencepiece.bpe.model",
    "onnx/config.json",
]

# Giống hệt danh sách fastembed tự thêm, để cấu trúc cache trùng khớp bất kể
# sau này có quay lại dùng đường tải của nó hay không.
_HF_BASE_FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
]


def tai_snapshot() -> Path:
    """Tải đúng những file cần dùng, trả về thư mục snapshot."""

    duong_dan = snapshot_download(
        repo_id=MODEL,
        allow_patterns=[*_HF_BASE_FILES, MODEL_FILE, *ADDITIONAL_FILES],
        cache_dir=CACHE_PATH,
    )

    return Path(duong_dan)


def main() -> None:
    print(f"Đang tải {MODEL} vào {CACHE_PATH} ...")

    snapshot = tai_snapshot()

    thieu = [f for f in (MODEL_FILE, *ADDITIONAL_FILES) if not (snapshot / f).is_file()]

    if thieu:
        raise SystemExit(f"Tải xong nhưng thiếu file: {thieu}")

    TextEmbedding.add_custom_model(
        model=MODEL,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=MODEL),
        dim=DIM,
        model_file=MODEL_FILE,
        description="Multilingual E5 Small",
        license="MIT",
        size_in_gb=0.5,
        additional_files=ADDITIONAL_FILES,
    )

    # Nạp đúng cách encoder nạp lúc chạy thật: chỉ thẳng vào snapshot, không để
    # fastembed tự đi tìm. Nhờ vậy build mà chạy được thì runtime cũng chạy được.
    model = TextEmbedding(model_name=MODEL, specific_model_path=str(snapshot))

    vector = next(iter(model.embed(["passage: kiểm tra"])))

    if len(vector) != DIM:
        raise SystemExit(f"Số chiều sai: {len(vector)}, kỳ vọng {DIM}")

    print(f"Xong. Snapshot: {snapshot}")
    print(f"Số chiều: {len(vector)}")


if __name__ == "__main__":
    main()
