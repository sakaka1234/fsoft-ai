"""
Đo RAM, chất lượng và độ trễ của từng biến thể ONNX.

    uv run python scripts/do_bien_the_onnx.py

Vì sao cần: bản fp32 ăn 893 MB RSS, vượt mọi gói hosting free (Railway/Render
512 MB, Fly 256 MB). Script này trả lời câu hỏi "bản nào vừa nhẹ vừa không làm
kém chất lượng" bằng số đo thay vì phỏng đoán.

Mỗi biến thể chạy trong MỘT TIẾN TRÌNH RIÊNG. RSS là số cộng dồn: nạp hai model
trong cùng process thì con số thứ hai vô nghĩa.

Kết quả đo ngày 17/08/2026 trên Xeon E5-2680 (2012, KHÔNG có AVX512), hai lần
chạy — cột RSS là ĐỈNH nên nó dao động theo phân bổ nhất thời:

    biến thể                 file      đỉnh RSS      Recall@5  NEG    nạp
    onnx/model.onnx          448 MB    936 / 936 MB   0.971    1.00   2,5s
    onnx/model_O4.onnx       224 MB    697 / 652 MB   0.971    1.00   1,7-2,3s
    onnx/..._qint8...onnx    118 MB    538 / 504 MB   0.971    1.00   1,4s
    onnx/model_tia113k.onnx   66 MB    317 / 279 MB   0.971    1.00   0,7s

Đọc theo DẢI, đừng ghim một con số: chênh lệch giữa hai lần chạy tới 38 MB. Khi
lập kế hoạch dung lượng thì lấy đầu CAO của dải.

Bản int8 chỉ giữ được NEGATIVE=1.00 SAU KHI hiệu chỉnh lại AI_MIN_SCORE lên
0.8344; dùng ngưỡng 0.83 của fp32 thì nó tụt xuống 0.60. Bản tỉa giữ đúng 0.8344
đó — tỉa cho vector giống từng bit nên phân bố không dịch. Xem
scripts/hieu_chinh_nguong.py.

Bản `tia113k` KHÔNG có trên Hugging Face; dựng bằng:
    uv run python scripts/tia_vocab.py --ngan-sach 120000 --ten tia113k
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Script in tiếng Việt có dấu. Trên Windows, stdout chuyển hướng ra file hoặc
# pipe dùng bảng mã cp1252 và `print` ném UnicodeEncodeError.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

BIEN_THE = [
    ("onnx/model.onnx", 0.83),
    ("onnx/model_O4.onnx", 0.83),
    ("onnx/model_qint8_avx512_vnni.onnx", 0.8344),
    # Bản tỉa vocab. Dựng bằng scripts/tia_vocab.py, nằm ở snapshot riêng nên
    # chỉ đo được sau khi đã chạy script đó.
    ("onnx/model_tia113k.onnx", 0.8344),
]


def rss_mb() -> float:
    """
    RSS ĐỈNH của tiến trình, MB.

    Phải là ĐỈNH chứ không phải hiện tại. Bản trước đọc `WorkingSetSize` (hiện
    tại) trên Windows nhưng `ru_maxrss` (đỉnh) trên Linux — hai đại lượng khác
    nhau, nên số của hai nền tảng không so sánh được với nhau.

    Sai lệch đó không vô hại: nó làm báo cáo ghi 504 MB trong khi đỉnh thật là
    538 MB, tức là kết luận "vừa trần 512 MB" trong khi thực tế vượt 26 MB — và
    OOM thì luôn xảy ra ở ĐỈNH. Đỉnh của service này rơi vào lần đồng bộ đầy đầu
    tiên, lúc SQLite còn trống và syncer phải embed toàn bộ thẻ.
    """

    if os.name == "nt":
        import ctypes
        import ctypes.wintypes as wt

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD),
                ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # argtypes/restype PHẢI khai báo tường minh: mặc định ctypes cắt cụt
        # HANDLE 64-bit và hàm trả về 0 — đúng cái bẫy đã ăn một lần ở M0.
        psapi = ctypes.WinDLL("psapi.dll")
        psapi.GetProcessMemoryInfo.argtypes = [
            wt.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wt.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wt.BOOL

        kernel32 = ctypes.WinDLL("kernel32.dll")
        kernel32.GetCurrentProcess.restype = wt.HANDLE

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        )

        return counters.PeakWorkingSetSize / 1024 / 1024

    import resource

    # ru_maxrss vốn đã là ĐỈNH. Linux trả kilobyte, macOS trả byte.
    thoi = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    return thoi / 1024 if sys.platform == "linux" else thoi / 1024 / 1024


# ---------------------------------------------------------------------
# Chế độ con: đo đúng một biến thể rồi in JSON
# ---------------------------------------------------------------------


def do_mot_bien_the() -> None:
    import asyncio
    import statistics
    import time

    truoc = rss_mb()

    sys.path.insert(0, str(PROJECT_ROOT))

    from app.config import Settings
    from app.embedding.encoder import Encoder
    from app.main import build_service, load_index_from_db
    from tests.eval.metrics import CaseOutcome, EvalReport, load_golden

    async def chay() -> dict:
        settings = Settings()

        bat_dau = time.perf_counter()
        enc = Encoder(settings)
        enc.load_sync()
        giay_nap = time.perf_counter() - bat_dau

        sau_nap = rss_mb()

        service = build_service(settings, encoder=enc)
        service.db.connect_sync()

        try:
            await service.intent_classifier.warmup()
            await service.syncer.run_incremental()
            await load_index_from_db(service)

            day_du = rss_mb()

            report = EvalReport()
            do_tre = []

            for case in load_golden(PROJECT_ROOT / "tests" / "eval" / "retrieval_golden.json"):
                t = time.perf_counter()
                vector = await service.encoder.embed_query(case["query"])
                intent = service.intent_classifier.classify(case["query"], vector)
                hits = service.retriever.retrieve(
                    query=case["query"],
                    query_vector=vector,
                    allowed_deck_ids=case["allowed_deck_ids"],
                    top_k=5,
                )
                do_tre.append((time.perf_counter() - t) * 1000)

                report.outcomes.append(
                    CaseOutcome(
                        case_id=case["id"],
                        query=case["query"],
                        category=case["category"],
                        expected=case["expected_card_ids"],
                        returned=[h.card.card_id for h in hits],
                        expected_intent=case["expected_intent"],
                        actual_intent=intent.intent.value,
                    )
                )

            return {
                "model_file": settings.ai_embedding_model_file,
                "min_score": settings.ai_min_score,
                "rss_MB": round(day_du, 1),
                "model_ton_MB": round(sau_nap - truoc, 1),
                "giay_nap": round(giay_nap, 2),
                "recall_at_5": round(report.recall_at_5, 4),
                "mrr": round(report.mrr, 4),
                "intent": round(report.intent_accuracy, 4),
                "negative": round(report.negative_pass_rate, 4),
                "p50_ms": round(statistics.median(do_tre), 1),
            }

        finally:
            service.db.close_sync()

    print("KETQUA_JSON:" + json.dumps(asyncio.run(chay()), ensure_ascii=False))


# ---------------------------------------------------------------------
# Chế độ điều phối: chạy từng biến thể trong tiến trình riêng
# ---------------------------------------------------------------------


def main() -> int:
    ket_qua = []

    for model_file, min_score in BIEN_THE:
        print(f"Đang đo {model_file} ...", flush=True)

        moi_truong = os.environ | {
            "FSOFT_DO_BIEN_THE_CON": "1",
            "AI_EMBEDDING_MODEL_FILE": model_file,
            "AI_MIN_SCORE": str(min_score),
            "AI_SOURCE_MODE": "fixture",
            "AI_SYNC_ENABLED": "false",
            # Mỗi biến thể một DB riêng: vector của biến thể này không dùng lại
            # được cho biến thể khác, và `AI_MODEL_VERSION` khác nhau sẽ bắt
            # embed lại — cứ tách hẳn cho sạch.
            "AI_MODEL_VERSION": f"do-thu@{model_file.replace('/', '-')}",
            "AI_DB_PATH": str(PROJECT_ROOT / "data" / f"do-{model_file.replace('/', '-')}.db"),
        }

        chay = subprocess.run(
            [sys.executable, __file__],
            env=moi_truong,
            # Tiến trình con hỏng là chuyện cần BÁO CÁO chứ không phải ném lỗi:
            # một biến thể không nạp được vẫn nên để các biến thể khác đo xong.
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        dong = [x for x in chay.stdout.splitlines() if x.startswith("KETQUA_JSON:")]

        if not dong:
            print(f"   HỎNG. stderr cuối:\n{chay.stderr[-800:]}")
            continue

        ket_qua.append(json.loads(dong[0].removeprefix("KETQUA_JSON:")))

    if not ket_qua:
        print("Không đo được biến thể nào.")
        return 1

    print()
    print(
        f"{'biến thể':36s} {'ngưỡng':>7} {'RSS':>9} {'Recall@5':>9} {'NEG':>6} {'p50':>8} {'nạp':>7}"
    )
    print("-" * 88)

    for r in ket_qua:
        print(
            f"{r['model_file']:36s} {r['min_score']:>7} {r['rss_MB']:>7} MB "
            f"{r['recall_at_5']:>9} {r['negative']:>6} {r['p50_ms']:>6} ms {r['giay_nap']:>5} s"
        )

    print("-" * 88)
    nhe_nhat = min(ket_qua, key=lambda r: r["rss_MB"])
    print(f"Nhẹ nhất: {nhe_nhat['model_file']} — {nhe_nhat['rss_MB']} MB")

    return 0


if __name__ == "__main__":
    if os.environ.get("FSOFT_DO_BIEN_THE_CON") == "1":
        do_mot_bien_the()
    else:
        raise SystemExit(main())
