"""
Nạp sẵn model ONNX vào cache lúc build Docker image.

KHÔNG tải lúc runtime — cold start Railway mà phải tải 470 MB là hỏng.

Script này CỐ Ý không import `app`: nó chạy ở một layer Docker riêng, trước khi
`app/` được copy vào image. Nhờ vậy đổi một dòng code trong `app/` không làm
mất layer 470 MB và bắt build tải lại từ đầu.

Cái giá phải trả là danh sách file bị lặp lại giữa đây và
`app/embedding/encoder.py`. `tests/test_embedding.py` có một test ghim hai bên
phải khớp nhau, nên chúng không thể lệch âm thầm.
"""

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("ORT_NUM_THREADS", "1")

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType

MODEL = os.environ.get("AI_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
DIM = int(os.environ.get("AI_EMBEDDING_DIM", "384"))
CACHE_PATH = os.environ.get("FASTEMBED_CACHE_PATH", "/opt/fastembed_cache")

MODEL_FILE = "onnx/model.onnx"

ADDITIONAL_FILES = [
    "onnx/tokenizer.json",
    "onnx/tokenizer_config.json",
    "onnx/special_tokens_map.json",
    "onnx/sentencepiece.bpe.model",
    "onnx/config.json",
]


def main() -> None:
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

    print(f"Đang tải {MODEL} vào {CACHE_PATH} ...")

    model = TextEmbedding(model_name=MODEL, cache_dir=CACHE_PATH)

    vector = next(iter(model.embed(["passage: kiểm tra"])))

    print(f"Xong. Số chiều: {len(vector)}")

    if len(vector) != DIM:
        raise SystemExit(f"Số chiều sai: {len(vector)}, kỳ vọng {DIM}")


if __name__ == "__main__":
    main()
