"""
Chạy bộ đo retrieval trên fixture. SPEC muc 11.3 mục 5.

    uv run python scripts/run_eval.py
    uv run python scripts/run_eval.py --scores          # in thêm điểm semantic thô
    uv run python scripts/run_eval.py --json out.json   # ghi số liệu cho CI

Không cần backend Java, không gọi LLM. Thoát với mã 1 nếu dưới ngưỡng, để cắm
thẳng vào CI được (SPEC muc 11.8 mục 3).
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import PROJECT_ROOT, Settings
from app.embedding.encoder import Encoder
from app.main import build_service, load_index_from_db
from tests.eval.metrics import CaseOutcome, EvalReport, load_golden

GOLDEN_PATH = PROJECT_ROOT / "tests" / "eval" / "retrieval_golden.json"

RECALL_THRESHOLD = 0.80
MRR_THRESHOLD = 0.60
INTENT_THRESHOLD = 0.90

TOP_K = 5


async def evaluate(show_scores: bool = False) -> EvalReport:
    settings = Settings(ai_source_mode="fixture", ai_sync_enabled=False)

    encoder = Encoder(settings)
    encoder.load_sync()

    service = build_service(settings, encoder=encoder)
    service.db.connect_sync()

    try:
        await service.intent_classifier.warmup()
        await service.syncer.run_incremental()
        await load_index_from_db(service)

        report = EvalReport()

        for case in load_golden(GOLDEN_PATH):
            vector = await service.encoder.embed_query(case["query"])

            intent = service.intent_classifier.classify(case["query"], vector)

            hits = service.retriever.retrieve(
                query=case["query"],
                query_vector=vector,
                allowed_deck_ids=case["allowed_deck_ids"],
                top_k=TOP_K,
            )

            if show_scores:
                raw = service.index.semantic_search(vector, case["allowed_deck_ids"], 3)
                print(f"{case['id']} {case['category']:14s} semantic thô: {raw}")

            report.outcomes.append(
                CaseOutcome(
                    case_id=case["id"],
                    query=case["query"],
                    category=case["category"],
                    expected=case["expected_card_ids"],
                    returned=[hit.card.card_id for hit in hits],
                    expected_intent=case["expected_intent"],
                    actual_intent=intent.intent.value,
                )
            )

        return report

    finally:
        service.db.close_sync()


def _current_commit() -> str:
    """SHA ngắn để đối chiếu số đo với đúng lần commit sinh ra nó."""

    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=PROJECT_ROOT,
        ).stdout.strip()

    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _json_target() -> Path | None:
    if "--json" not in sys.argv:
        return None

    position = sys.argv.index("--json")

    if position + 1 >= len(sys.argv):
        raise SystemExit("--json cần kèm đường dẫn tệp.")

    return Path(sys.argv[position + 1])


def _write_history(target: Path, report: EvalReport) -> None:
    """
    Nối thêm một dòng JSON, không ghi đè.

    Định dạng JSON Lines để lịch sử chỉ có thêm chứ không bao giờ mất: mỗi lần
    chạy là một dòng, đọc bằng `pandas.read_json(lines=True)` hoặc `jq`.
    SPEC muc 11.8 mục 3 cần lịch sử Recall@5 để phát hiện hồi quy, mà một con
    số đơn lẻ thì không nói lên xu hướng gì.
    """

    record = {
        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "commit": _current_commit(),
        "ref": os.environ.get("GITHUB_REF_NAME", ""),
        "recall_at_5": round(report.recall_at_5, 4),
        "mrr": round(report.mrr, 4),
        "intent_accuracy": round(report.intent_accuracy, 4),
        "cases": len(report.outcomes),
    }

    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    so_dong = sum(1 for _ in target.open(encoding="utf-8"))

    print(f"\nĐã ghi số đo vào {target} ({so_dong} lần đo trong lịch sử)")


def main() -> int:
    # Bảng kết quả có tiếng Việt có dấu. Trên Windows, stdout chuyển hướng ra
    # file hoặc pipe dùng bảng mã cp1252 và `print` sẽ ném UnicodeEncodeError —
    # đúng lúc người ta cần lưu lại kết quả nhất.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    report = asyncio.run(evaluate(show_scores="--scores" in sys.argv))

    target = _json_target()

    # Ghi số đo TRƯỚC khi in: một lỗi lúc in không được phép làm mất kết quả
    # vừa mất mấy phút để đo.
    if target is not None:
        _write_history(target, report)

    print(report.format_table())

    failed = (
        report.recall_at_5 < RECALL_THRESHOLD
        or report.mrr < MRR_THRESHOLD
        or report.intent_accuracy < INTENT_THRESHOLD
    )

    if failed:
        print("\nKHÔNG ĐẠT ngưỡng.")
        return 1

    print("\nĐẠT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
