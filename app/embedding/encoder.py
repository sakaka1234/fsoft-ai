"""
Bọc fastembed. SPEC muc 5.5 và docs/M0_FINDINGS.md muc 2.

Hai kết luận của M0 quyết định toàn bộ file này:

1. `intfloat/multilingual-e5-small` KHÔNG có trong built-in registry của
   fastembed -> phải `add_custom_model` trước khi khởi tạo.
2. fastembed KHÔNG tự thêm prefix "query: " / "passage: " -> encoder tự nối.
   Đây là chỗ DUY NHẤT được nối prefix. Nối hai lần hỏng âm thầm y hệt quên nối.

Về threading: `OMP_NUM_THREADS` / `ORT_NUM_THREADS` đã được đặt trong
`app/__init__.py`, chạy trước mọi import fastembed.
"""

from pathlib import Path

import anyio
import numpy as np
from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType

from app.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

# Năm file phụ bắt buộc, xác nhận ở M0 mục 2.2.
_ADDITIONAL_FILES = [
    "onnx/tokenizer.json",
    "onnx/tokenizer_config.json",
    "onnx/special_tokens_map.json",
    "onnx/sentencepiece.bpe.model",
    "onnx/config.json",
]

_MODEL_FILE = "onnx/model.onnx"


def _is_registered(model_name: str) -> bool:
    return any(item.get("model") == model_name for item in TextEmbedding.list_supported_models())


def register_custom_model(model_name: str, dim: int) -> None:
    """
    Đăng ký model vào registry của fastembed.

    Idempotent: gọi lần thứ hai với cùng tên sẽ raise, mà lifespan lẫn test đều
    có thể gọi nhiều lần trong một tiến trình.
    """

    if _is_registered(model_name):
        return

    TextEmbedding.add_custom_model(
        model=model_name,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=model_name),
        dim=dim,
        model_file=_MODEL_FILE,
        description="Multilingual E5 Small",
        license="MIT",
        size_in_gb=0.5,
        additional_files=_ADDITIONAL_FILES,
    )

    log.info("custom_model_registered", model=model_name, dim=dim)


def find_local_snapshot(cache_path: Path | None, model_name: str) -> Path | None:
    """
    Tìm snapshot đã tải sẵn trong cache.

    Không hard-code commit SHA: HF có thể cập nhật repo và sinh snapshot mới.
    Có nhiều snapshot thì lấy bản mới nhất theo mtime.
    """

    if cache_path is None:
        return None

    snapshots_dir = cache_path / f"models--{model_name.replace('/', '--')}" / "snapshots"

    if not snapshots_dir.is_dir():
        return None

    valid = [p for p in snapshots_dir.iterdir() if p.is_dir() and (p / _MODEL_FILE).exists()]

    if not valid:
        return None

    valid.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    return valid[0]


class Encoder:
    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.ai_embedding_model
        self._dim = settings.ai_embedding_dim
        self._query_prefix = settings.ai_query_prefix
        self._passage_prefix = settings.ai_passage_prefix
        self._batch_size = settings.ai_embed_batch_size
        self._cache_path = (
            settings.fastembed_cache_path.resolve()
            if settings.fastembed_cache_path is not None
            else None
        )
        self._model: TextEmbedding | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def dim(self) -> int:
        return self._dim

    # ---------------------------------------------------------------
    # Nạp model
    # ---------------------------------------------------------------

    def load_sync(self) -> None:
        register_custom_model(self._model_name, self._dim)

        snapshot = find_local_snapshot(self._cache_path, self._model_name)

        if snapshot is not None:
            log.info("encoder_loading", source="local_snapshot", path=str(snapshot))
            self._model = TextEmbedding(
                model_name=self._model_name,
                specific_model_path=str(snapshot),
            )

        else:
            # Không có sẵn thì fastembed tự tải từ Hugging Face — chậm (470 MB)
            # nhưng service vẫn chạy được. Trong Docker, model đã nạp sẵn lúc
            # build image nên nhánh này không bao giờ chạy ở production.
            log.warning("encoder_loading", source="huggingface_download")
            self._model = TextEmbedding(
                model_name=self._model_name,
                cache_dir=str(self._cache_path) if self._cache_path else None,
            )

        log.info("encoder_loaded", model=self._model_name, dim=self._dim)

    async def load(self) -> None:
        await anyio.to_thread.run_sync(self.load_sync)

    def _require_model(self) -> TextEmbedding:
        if self._model is None:
            raise RuntimeError("Encoder chưa nạp model. Gọi load() trước.")

        return self._model

    # ---------------------------------------------------------------
    # Embed
    # ---------------------------------------------------------------

    def embed_query_sync(self, text: str) -> np.ndarray:
        model = self._require_model()

        vector = next(iter(model.embed([self._query_prefix + text])))

        return np.asarray(vector, dtype=np.float32)

    def embed_passages_sync(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []

        model = self._require_model()

        prefixed = [self._passage_prefix + text for text in texts]

        return [
            np.asarray(vector, dtype=np.float32)
            for vector in model.embed(prefixed, batch_size=self._batch_size)
        ]

    async def embed_query(self, text: str) -> np.ndarray:
        """
        ONNX Runtime nhả GIL khi chạy, nhưng tokenize và xử lý numpy thì không.
        Gọi thẳng trong `async def` sẽ chặn event loop (SPEC muc 5.10, bẫy 1).
        """

        return await anyio.to_thread.run_sync(self.embed_query_sync, text)

    async def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        return await anyio.to_thread.run_sync(self.embed_passages_sync, texts)
