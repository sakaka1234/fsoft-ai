"""
Chạy bộ đo retrieval trên fixture. SPEC muc 11.3 mục 5.

    uv run python scripts/run_eval.py
    uv run python scripts/run_eval.py --scores   # in thêm điểm semantic thô

Không cần backend Java, không gọi LLM. Thoát với mã 1 nếu dưới ngưỡng, để cắm
thẳng vào CI được (SPEC muc 11.8 mục 3).
"""

import asyncio
import sys
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


def main() -> int:
    report = asyncio.run(evaluate(show_scores="--scores" in sys.argv))

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
