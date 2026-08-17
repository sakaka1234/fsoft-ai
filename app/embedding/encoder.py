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

# Bản lượng tử 8 bit ĐÃ TỈA BỚT TỪ VỰNG. Dựng bằng `scripts/tia_vocab.py` từ bản
# `onnx/model_qint8_avx512_vnni.onnx` của Hugging Face — nó KHÔNG tồn tại trên
# HF, phải sinh ra lúc build image.
#
# Đỉnh RSS đo trên bộ 40 case (`scripts/do_bien_the_onnx.py`), hai lần chạy —
# đọc theo DẢI, chênh lệch giữa hai lần tới 38 MB:
#
#   biến thể                      file      đỉnh RSS       Recall@5   NEG    nạp
#   onnx/model.onnx               448 MB    936 / 936 MB    0.9714    1.00   2,5s
#   onnx/model_O4.onnx            224 MB    697 / 652 MB    0.9714    1.00   1,7-2,3s
#   onnx/model_qint8_...onnx      118 MB    538 / 504 MB    0.9714    1.00   1,4s
#   onnx/model_tia113k.onnx        66 MB    317 / 279 MB    0.9714    1.00   0,7s
#
# VÌ SAO TỈA TỪ VỰNG LẠI THẮNG ĐẬM đến vậy, trong khi ba biến thể ONNX kia đều
# mắc quanh 500 MB: kẻ tốn RAM nhất KHÔNG PHẢI model mà là TOKENIZER.
# `Tokenizer.from_file` nạp một model Unigram 250.002 token và ăn 250 MB (~1 KB
# mỗi token), còn `ort.InferenceSession` chỉ ăn 130 MB. Vì 250 MB đó là hằng số
# không phụ thuộc biến thể ONNX, lượng tử hoá kéo được 936 → 538 rồi tắc. Tỉa từ
# vựng đánh vào CẢ HAI: tokenizer 250 → 79 MB, session 134 → 82 MB.
#
# (Giả thuyết ban đầu — ONNX Runtime giải nén bảng embedding về fp32 — đã bị bác
# bỏ: bảng vẫn là `uint8 [250037, 384]` cả trong tệp lẫn trong graph sau tối ưu,
# vì đồ thị là `Gather(bảng_uint8, input_ids)` rồi mới `DequantizeLinear` trên
# kết quả gather nhỏ. Con số 250002 × 384 × 4 ≈ 384 MB chỉ trùng hợp.)
#
# TỈA KHÔNG MẤT CHẤT LƯỢNG vì `scale`/`zero_point` của bảng là VÔ HƯỚNG
# (per-tensor: 0.010546875 / 128), không per-row — nên chọn hàng trên mảng uint8
# là phép toán chính xác, token nào được giữ thì vector giống TỪNG BIT.
_MODEL_FILE_MAC_DINH = "onnx/model_tia113k.onnx"

# Bản gốc trên Hugging Face, dùng làm ĐẦU VÀO cho `scripts/tia_vocab.py`.
# `scripts/download_model.py` tải đúng file này.
MODEL_FILE_GOC = "onnx/model_qint8_avx512_vnni.onnx"

# Ngưỡng lọc liên quan đi kèm từng biến thể. Hiệu chỉnh bằng
# scripts/hieu_chinh_nguong.py trên bộ 40 case.
#
# Bản tỉa giữ đúng 0.8344 của bản gốc — hiệu chỉnh lại trên nó ra cùng con số,
# mất cùng một case (R022), và cả 5 điểm dương thấp nhất lẫn 5 điểm âm trùng tới
# 4 chữ số. Đó là hệ quả trực tiếp của việc tỉa cho vector giống từng bit.
MIN_SCORE_THEO_MODEL = {
    "onnx/model.onnx": 0.83,
    "onnx/model_O4.onnx": 0.83,
    "onnx/model_qint8_avx512_vnni.onnx": 0.8344,
    "onnx/model_tia113k.onnx": 0.8344,
}


def _is_registered(model_name: str) -> bool:
    return any(item.get("model") == model_name for item in TextEmbedding.list_supported_models())


def register_custom_model(
    model_name: str, dim: int, model_file: str = _MODEL_FILE_MAC_DINH
) -> None:
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
        model_file=model_file,
        description="Multilingual E5 Small",
        license="MIT",
        size_in_gb=0.5,
        additional_files=_ADDITIONAL_FILES,
    )

    log.info("custom_model_registered", model=model_name, dim=dim, model_file=model_file)


def find_local_snapshot(
    cache_path: Path | None, model_name: str, model_file: str = _MODEL_FILE_MAC_DINH
) -> Path | None:
    """
    Tìm snapshot đã tải sẵn trong cache.

    Không hard-code commit SHA: HF có thể cập nhật repo và sinh snapshot mới.
    Có nhiều snapshot thì lấy bản mới nhất theo mtime.

    Snapshot chỉ được coi là hợp lệ khi chứa ĐÚNG biến thể đang dùng. Một cache
    có sẵn `model.onnx` nhưng thiếu bản lượng tử phải bị coi là không có, để rơi
    xuống nhánh tải chứ không nạp nhầm biến thể.
    """

    if cache_path is None:
        return None

    snapshots_dir = cache_path / f"models--{model_name.replace('/', '--')}" / "snapshots"

    if not snapshots_dir.is_dir():
        return None

    valid = [p for p in snapshots_dir.iterdir() if p.is_dir() and (p / model_file).exists()]

    if not valid:
        return None

    valid.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    return valid[0]


class Encoder:
    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.ai_embedding_model
        self._model_file = settings.ai_embedding_model_file
        self._cpu_arena = settings.ai_onnx_cpu_arena
        self._intra_op_threads = settings.ai_ort_intra_op_threads
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
        register_custom_model(self._model_name, self._dim, self._model_file)

        # fastembed chuyển tiếp `enable_cpu_mem_arena` xuống `ort.SessionOptions`
        # (EXPOSED_SESSION_OPTIONS trong fastembed/common/onnx_model.py).
        #
        # Bộ cấp phát arena của ONNX Runtime giữ lại vùng nhớ đã xin để lần suy
        # luận sau khỏi xin lại. Với batch lớn thì đáng, nhưng ở đây batch là 32
        # câu ngắn nên nó chỉ giữ chỗ vô ích: đo được thêm khoảng 47 MB RSS mà
        # độ trễ không đổi (10,2ms so với 10,0ms — trong khoảng nhiễu).
        #
        # Truyền tường minh chứ không qua `**dict`: fastembed khai báo vài tham
        # số có kiểu rồi mới tới `**kwargs`, nên mypy đem dict khớp vào tham số
        # đầu tiên còn trống và báo lỗi kiểu sai chỗ.
        #
        # `threads` là ĐƯỜNG DUY NHẤT đặt được `intra_op_num_threads`: fastembed
        # chỉ gọi `so.intra_op_num_threads = threads` khi `threads is not None`
        # (common/onnx_model.py). Trước đây không truyền gì, nên ONNX Runtime tự
        # quyết theo số nhân của MÁY CHỦ — trong container bị bóp CPU thì nó mở
        # thừa luồng rồi tranh nhau. `None` = giữ nguyên hành vi tự quyết.
        threads = self._intra_op_threads or None

        snapshot = find_local_snapshot(self._cache_path, self._model_name, self._model_file)

        if snapshot is not None:
            log.info("encoder_loading", source="local_snapshot", path=str(snapshot))
            self._model = TextEmbedding(
                model_name=self._model_name,
                specific_model_path=str(snapshot),
                enable_cpu_mem_arena=self._cpu_arena,
                threads=threads,
            )

        else:
            # Không có sẵn thì fastembed tự tải từ Hugging Face — chậm (113 MB)
            # nhưng service vẫn chạy được. Trong Docker, model đã nạp sẵn lúc
            # build image nên nhánh này không bao giờ chạy ở production.
            log.warning("encoder_loading", source="huggingface_download")
            self._model = TextEmbedding(
                model_name=self._model_name,
                cache_dir=str(self._cache_path) if self._cache_path else None,
                enable_cpu_mem_arena=self._cpu_arena,
                threads=threads,
            )

        log.info(
            "encoder_loaded",
            model=self._model_name,
            model_file=self._model_file,
            dim=self._dim,
            cpu_arena=self._cpu_arena,
            intra_op_threads=threads,
        )

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

    def embed_queries_sync(self, texts: list[str]) -> list[np.ndarray]:
        """Embed nhiều câu hỏi theo lô — dùng lúc warmup centroid intent."""

        if not texts:
            return []

        model = self._require_model()

        prefixed = [self._query_prefix + text for text in texts]

        return [
            np.asarray(vector, dtype=np.float32)
            for vector in model.embed(prefixed, batch_size=self._batch_size)
        ]

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

    async def embed_queries(self, texts: list[str]) -> list[np.ndarray]:
        return await anyio.to_thread.run_sync(self.embed_queries_sync, texts)

    async def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        return await anyio.to_thread.run_sync(self.embed_passages_sync, texts)
