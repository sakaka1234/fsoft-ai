"""
M0 — Kiểm tra embedding.

Chạy:
    uv run python scripts/m0_embedding.py

Mục tiêu:
    1. fastembed có hỗ trợ sẵn multilingual-e5-small không?
    2. fastembed custom model có chạy được không?
    3. Prefix "query:" / "passage:" có được tự thêm không?
    4. Vector có đúng 384 chiều và đã L2-normalize chưa?
    5. Embedding có hiểu ngữ nghĩa tiếng Việt không?
    6. Tốn bao nhiêu RAM, chạy nhanh chậm thế nào?

Model:
    intfloat/multilingual-e5-small

Lưu ý:
    - FastEmbed version hiện tại không có multilingual-e5-small
      trong built-in registry.
    - Vì vậy script đăng ký model dưới dạng CustomTextEmbedding.
    - Model ONNX đã có sẵn trên Hugging Face.
    - FastEmbed KHÔNG tự thêm "query:" / "passage:".
      Encoder của app phải tự thêm prefix.
"""

import os

# ---------------------------------------------------------------------
# BẮT BUỘC đặt trước khi import fastembed.
# ---------------------------------------------------------------------

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("ORT_NUM_THREADS", "1")

import platform
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------
# Windows console UTF-8
# ---------------------------------------------------------------------

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import numpy as np

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType


# =====================================================================
# CONFIG
# =====================================================================

MODEL = "intfloat/multilingual-e5-small"

EXPECTED_DIM = 384
EXPECTED_NORM = 1.0

# Model ONNX mặc định.
MODEL_FILE = "onnx/model.onnx"


# =====================================================================
# UTILS
# =====================================================================


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


def rss_mb() -> float:
    """
    Đo RSS hiện tại của process.

    Windows:
        dùng Windows API.

    Linux/macOS:
        dùng resource.

    Trả về MB.
    """

    system = platform.system()

    # -----------------------------------------------------------------
    # Windows
    # -----------------------------------------------------------------

    if system == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)

            kernel32 = ctypes.windll.kernel32

            # Bắt buộc khai báo restype/argtypes trên x64.
            # Mặc định ctypes coi con trỏ là c_int 32-bit và cắt mất
            # nửa trên của HANDLE -> lời gọi thất bại âm thầm.
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p

            psapi = ctypes.windll.psapi

            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

            result = psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            )

            if result:
                return counters.WorkingSetSize / 1024 / 1024

        except Exception:
            pass

        return 0.0

    # -----------------------------------------------------------------
    # Linux / macOS
    # -----------------------------------------------------------------

    try:
        import resource

        raw = resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss

        if system == "Linux":
            # Linux: KB
            return raw / 1024

        # macOS: bytes
        return raw / 1024 / 1024

    except Exception:
        return 0.0


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Cosine similarity giữa hai vector.
    """

    denominator = np.linalg.norm(a) * np.linalg.norm(b)

    if denominator == 0:
        return 0.0

    return float(np.dot(a, b) / denominator)


def find_hf_snapshot() -> Path | None:
    """
    Tìm snapshot multilingual-e5-small trong Hugging Face cache.

    FastEmbed cache thường nằm ở:

        Windows:
        %TEMP%/fastembed_cache

        Linux/macOS:
        /tmp/fastembed_cache

    Hàm này tìm snapshot mới nhất có chứa:
        onnx/model.onnx
    """

    candidates: list[Path] = []

    # -------------------------------------------------------------
    # FASTEMBED_CACHE_PATH — ưu tiên cao nhất, mọi hệ điều hành
    # -------------------------------------------------------------

    configured = os.environ.get("FASTEMBED_CACHE_PATH", "")

    if configured:
        cache_root = (
            Path(configured)
            / "models--intfloat--multilingual-e5-small"
            / "snapshots"
        )

        if cache_root.exists():
            candidates.extend(
                p
                for p in cache_root.iterdir()
                if p.is_dir()
            )

    # -------------------------------------------------------------
    # Windows
    # -------------------------------------------------------------

    if platform.system() == "Windows":

        temp_dir = Path(os.environ.get("TEMP", ""))

        if temp_dir:
            cache_root = (
                temp_dir
                / "fastembed_cache"
                / "models--intfloat--multilingual-e5-small"
                / "snapshots"
            )

            if cache_root.exists():
                candidates.extend(
                    p
                    for p in cache_root.iterdir()
                    if p.is_dir()
                )

    # -------------------------------------------------------------
    # Linux / macOS
    # -------------------------------------------------------------

    else:

        possible_roots = [
            Path("/tmp/fastembed_cache"),
            Path.home() / ".cache" / "fastembed",
        ]

        for root in possible_roots:

            cache_root = (
                root
                / "models--intfloat--multilingual-e5-small"
                / "snapshots"
            )

            if cache_root.exists():
                candidates.extend(
                    p
                    for p in cache_root.iterdir()
                    if p.is_dir()
                )

    # -------------------------------------------------------------
    # Tìm snapshot có ONNX model
    # -------------------------------------------------------------

    valid = []

    for snapshot in candidates:

        model_path = snapshot / MODEL_FILE

        if model_path.exists():
            valid.append(snapshot)

    if not valid:
        return None

    # Snapshot mới nhất
    valid.sort(
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    return valid[0]


# =====================================================================
# HEADER
# =====================================================================

print("=" * 64)
print("M0 — MULTILINGUAL E5 SMALL EMBEDDING CHECK")
print("=" * 64)

print(f"Hệ điều hành : {platform.system()} {platform.release()}")
print(f"Python       : {sys.version.split()[0]}")
print(f"Model        : {MODEL}")


# =====================================================================
# 1. CHECK BUILT-IN SUPPORT
# =====================================================================

section("1. FASTEMBED BUILT-IN SUPPORT")

supported = [
    m["model"]
    for m in TextEmbedding.list_supported_models()
]

is_builtin = MODEL in supported

print(
    f"{MODEL}: "
    f"{'CÓ' if is_builtin else 'KHÔNG'}"
)

if is_builtin:

    print()
    print(">>> Model đã được FastEmbed hỗ trợ built-in.")

else:

    print()
    print(">>> Model KHÔNG có trong FastEmbed built-in registry.")
    print(">>> Sử dụng CustomTextEmbedding.")


# =====================================================================
# 2. FIND LOCAL SNAPSHOT
# =====================================================================

section("2. TÌM MODEL ONNX LOCAL")

snapshot = find_hf_snapshot()

if snapshot is None:

    print("Không tìm thấy snapshot local.")

    print()
    print("FastEmbed sẽ cần tải model từ Hugging Face.")

    snapshot_path = None

else:

    snapshot_path = snapshot

    print(f"Snapshot : {snapshot}")

    print()
    print("Các file quan trọng:")

    important_files = [
        "onnx/model.onnx",
        "onnx/config.json",
        "onnx/tokenizer.json",
        "onnx/tokenizer_config.json",
        "onnx/special_tokens_map.json",
        "onnx/sentencepiece.bpe.model",
    ]

    for file in important_files:

        path = snapshot / file

        if path.exists():

            size_mb = path.stat().st_size / 1024 / 1024

            print(
                f"  ✓ {file:<40} "
                f"{size_mb:.1f} MB"
            )

        else:

            print(
                f"  ✗ {file:<40} "
                "MISSING"
            )


# =====================================================================
# 3. REGISTER CUSTOM MODEL
# =====================================================================

section("3. ĐĂNG KÝ CUSTOM MODEL")

if not is_builtin:

    TextEmbedding.add_custom_model(
        model=MODEL,

        pooling=PoolingType.MEAN,

        normalization=True,

        sources=ModelSource(
            hf=MODEL,
        ),

        dim=EXPECTED_DIM,

        model_file=MODEL_FILE,

        description=(
            "Multilingual E5 Small "
            "custom ONNX model"
        ),

        license="MIT",

        size_in_gb=0.5,

        additional_files=[
            "onnx/tokenizer.json",
            "onnx/tokenizer_config.json",
            "onnx/special_tokens_map.json",
            "onnx/sentencepiece.bpe.model",
            "onnx/config.json",
        ],
    )

    print(">>> Custom model đã được đăng ký.")

else:

    print(">>> Không cần đăng ký custom.")


# =====================================================================
# 4. LOAD MODEL
# =====================================================================

section("4. LOAD MODEL")

rss_before = rss_mb()

t0 = time.perf_counter()

if snapshot_path is not None:

    print("Đang load từ local snapshot...")

    model = TextEmbedding(
        model_name=MODEL,
        specific_model_path=str(snapshot_path),
    )

else:

    print("Không có local snapshot.")
    print("Đang tải từ Hugging Face...")

    model = TextEmbedding(
        model_name=MODEL,
    )

load_s = time.perf_counter() - t0

rss_after = rss_mb()

print()
print(f"Thời gian load : {load_s:.2f}s")

if rss_before > 0 and rss_after > 0:

    print(f"RSS trước      : {rss_before:.0f} MB")
    print(f"RSS sau        : {rss_after:.0f} MB")
    print(
        f"RSS tăng       : "
        f"{rss_after - rss_before:.0f} MB"
    )

else:

    print("RSS: không đo được")


# =====================================================================
# 5. PREFIX TEST
# =====================================================================

section(
    "5. PREFIX QUERY / PASSAGE"
)

print(
    "FastEmbed CustomTextEmbedding "
    "KHÔNG tự thêm prefix."
)

print()

print("Input query:")
print('    "query: xin chào"')

print("Input passage:")
print('    "passage: xin chào"')

print()

print(
    ">>> KẾT LUẬN:"
)

print(
    ">>> encoder.py PHẢI tự thêm:"
)

print(
    '    query   -> "query: " + text'
)

print(
    '    passage -> "passage: " + text'
)

print()

print(
    ">>> Không dùng query_embed() để "
    "suy luận prefix."
)


# =====================================================================
# 6. DIMENSION + NORMALIZATION
# =====================================================================

section(
    "6. DIMENSION & L2 NORMALIZATION"
)

test_vector = np.array(
    list(
        model.embed(
            ["query: xin chào"]
        )
    )[0]
)

dimension = test_vector.shape[0]

norm = float(
    np.linalg.norm(test_vector)
)

print(
    f"số chiều : {dimension} "
    f"(kỳ vọng {EXPECTED_DIM})"
)

print(
    f"L2 norm  : {norm:.6f} "
    f"(kỳ vọng ~{EXPECTED_NORM})"
)

dimension_ok = (
    dimension == EXPECTED_DIM
)

normalization_ok = (
    abs(norm - EXPECTED_NORM) < 1e-4
)

print()

print(
    f"Dimension test : "
    f"{'ĐẠT' if dimension_ok else 'KHÔNG ĐẠT'}"
)

print(
    f"Normalize test : "
    f"{'ĐẠT' if normalization_ok else 'KHÔNG ĐẠT'}"
)


# =====================================================================
# 7. VIETNAMESE SEMANTIC TEST
# =====================================================================

section(
    "7. KIỂM TRA NGỮ NGHĨA TIẾNG VIỆT"
)

passages = [
    (
        "apprehensive",
        "passage: apprehensive (adj) "
        "— lo lắng, e ngại về điều sắp xảy ra",
    ),

    (
        "anxious",
        "passage: anxious (adj) "
        "— lo âu, bồn chồn, bất an",
    ),

    (
        "diligent",
        "passage: diligent (adj) "
        "— siêng năng, chăm chỉ và bền bỉ",
    ),

    (
        "deforestation",
        "passage: deforestation (n) "
        "— nạn phá rừng",
    ),
]

query_text = (
    "query: từ nào chỉ cảm giác lo lắng"
)

print(f"Query: {query_text}")
print()

passage_texts = [
    text
    for _, text in passages
]

vectors = np.array(
    list(
        model.embed(passage_texts)
    )
)

query_vector = np.array(
    list(
        model.embed([query_text])
    )[0]
)

scores = vectors @ query_vector

order = np.argsort(-scores)

print("Ranking:")

for rank, index in enumerate(
    order,
    start=1,
):

    label, _ = passages[index]

    print(
        f"  {rank}. "
        f"{label:<16} "
        f"{scores[index]:.4f}"
    )


top2 = {
    passages[i][0]
    for i in order[:2]
}

expected_top2 = {
    "apprehensive",
    "anxious",
}

semantic_ok = (
    top2 == expected_top2
)

print()

print(
    ">>> "
    f"{'ĐẠT' if semantic_ok else 'KHÔNG ĐẠT'}"
)

if semantic_ok:

    print(
        ">>> Hai từ liên quan đến "
        "lo lắng đứng top 2."
    )

else:

    print(
        ">>> Expected top 2:"
    )

    print(
        "    apprehensive"
    )

    print(
        "    anxious"
    )

    print(
        ">>> Nếu không đạt, kiểm tra "
        "prefix và model trước."
    )


# =====================================================================
# 8. SPEED TEST — SINGLE QUERY
# =====================================================================

section(
    "8. TỐC ĐỘ — SINGLE QUERY"
)

# Warm-up
list(
    model.embed(
        ["query: warm up"]
    )
)

iterations = 10

t0 = time.perf_counter()

for _ in range(iterations):

    list(
        model.embed(
            ["query: resilient nghĩa là gì"]
        )
    )

elapsed = (
    time.perf_counter() - t0
)

single_ms = (
    elapsed / iterations * 1000
)

print(
    f"{iterations} lần chạy"
)

print(
    f"Trung bình : "
    f"{single_ms:.2f} ms / câu"
)


# =====================================================================
# 9. SPEED TEST — BATCH 64
# =====================================================================

section(
    "9. TỐC ĐỘ — BATCH 64"
)

batch = [
    (
        f"passage: từ vựng số {i} "
        "dùng để kiểm tra tốc độ"
    )
    for i in range(64)
]

# Warm-up
list(model.embed(batch))

t0 = time.perf_counter()

list(
    model.embed(batch)
)

batch_ms = (
    time.perf_counter() - t0
) * 1000

per_item_ms = (
    batch_ms / 64
)

estimated_10k_s = (
    per_item_ms * 10000 / 1000
)

print(
    f"Batch size       : 64"
)

print(
    f"Batch time       : "
    f"{batch_ms:.2f} ms"
)

print(
    f"Per sentence     : "
    f"{per_item_ms:.2f} ms"
)

print(
    f"Ước tính 10k     : "
    f"{estimated_10k_s:.1f} s"
)


# =====================================================================
# 10. FINAL SUMMARY
# =====================================================================

section(
    "TỔNG KẾT — COPY VÀO docs/M0_FINDINGS.md"
)

print(
    f"model              : {MODEL}"
)

print(
    f"FastEmbed built-in  : "
    f"{'CÓ' if is_builtin else 'KHÔNG'}"
)

print(
    f"FastEmbed custom    : "
    f"{'CÓ' if not is_builtin else 'không cần'}"
)

print(
    "prefix query        : "
    "encoder tự thêm"
)

print(
    "prefix passage      : "
    "encoder tự thêm"
)

print(
    f"số chiều            : "
    f"{dimension}"
)

print(
    f"L2 normalize        : "
    f"{'CÓ' if normalization_ok else 'CHƯA'}"
)

if rss_before > 0 and rss_after > 0:

    print(
        f"RSS trước           : "
        f"{rss_before:.0f} MB"
    )

    print(
        f"RSS sau             : "
        f"{rss_after:.0f} MB"
    )

    print(
        f"RSS tăng            : "
        f"{rss_after - rss_before:.0f} MB"
    )

else:

    print(
        "RAM                 : "
        "không đo được"
    )

print(
    f"semantic tiếng Việt : "
    f"{'ĐẠT' if semantic_ok else 'KHÔNG ĐẠT'}"
)

print(
    f"embed 1 câu         : "
    f"{single_ms:.2f} ms"
)

print(
    f"batch 64            : "
    f"{batch_ms:.2f} ms"
)

print(
    f"ước tính 10k        : "
    f"{estimated_10k_s:.1f} s"
)

print()
print("=" * 64)
print("M0 HOÀN TẤT")
print("=" * 64)