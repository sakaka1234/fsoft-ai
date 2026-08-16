"""
Chỉ số cho bộ đo retrieval. SPEC muc 11.3 mục 5.

Định nghĩa theo Phụ lục D của SPEC:
  Recall@5 — tỷ lệ câu hỏi mà kết quả đúng nằm trong 5 kết quả đầu
  MRR      — trung bình của 1/thứ hạng kết quả đúng đầu tiên

Case NEGATIVE (`expected_card_ids` rỗng) không tham gia hai chỉ số trên — không
có kết quả đúng nào để mà xếp hạng. Chúng được chấm riêng: đạt khi hệ thống
KHÔNG trả về thẻ nào, tức không bịa ra một đáp án sai.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class CaseOutcome:
    case_id: str
    query: str
    category: str
    expected: list[int]
    returned: list[int]
    expected_intent: str
    actual_intent: str

    @property
    def is_negative(self) -> bool:
        return not self.expected

    @property
    def first_hit_rank(self) -> int | None:
        for rank, card_id in enumerate(self.returned, start=1):
            if card_id in self.expected:
                return rank

        return None

    @property
    def passed(self) -> bool:
        if self.is_negative:
            return not self.returned

        return self.first_hit_rank is not None

    @property
    def intent_ok(self) -> bool:
        return self.actual_intent == self.expected_intent


@dataclass(slots=True)
class EvalReport:
    outcomes: list[CaseOutcome] = field(default_factory=list)

    @property
    def positives(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if not o.is_negative]

    @property
    def negatives(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.is_negative]

    @property
    def recall_at_5(self) -> float:
        positives = self.positives

        if not positives:
            return 0.0

        return sum(o.first_hit_rank is not None for o in positives) / len(positives)

    @property
    def mrr(self) -> float:
        positives = self.positives

        if not positives:
            return 0.0

        return sum(1.0 / o.first_hit_rank if o.first_hit_rank else 0.0 for o in positives) / len(
            positives
        )

    @property
    def negative_pass_rate(self) -> float:
        negatives = self.negatives

        if not negatives:
            return 1.0

        return sum(o.passed for o in negatives) / len(negatives)

    @property
    def intent_accuracy(self) -> float:
        if not self.outcomes:
            return 0.0

        return sum(o.intent_ok for o in self.outcomes) / len(self.outcomes)

    def by_category(self) -> dict[str, tuple[int, int]]:
        """category -> (số case đạt, tổng số case)."""

        result: dict[str, list[int]] = {}

        for outcome in self.outcomes:
            bucket = result.setdefault(outcome.category, [0, 0])
            bucket[0] += outcome.passed
            bucket[1] += 1

        return {key: (value[0], value[1]) for key, value in result.items()}

    def failures(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if not o.passed]

    def format_table(self) -> str:
        lines = [
            f"{'ID':6s} {'loại':14s} {'câu hỏi':44s} {'kỳ vọng':14s} {'top-5':22s} {'':4s}",
            "-" * 108,
        ]

        for o in self.outcomes:
            expected = ",".join(map(str, o.expected)) or "(rỗng)"
            returned = ",".join(map(str, o.returned[:5])) or "(rỗng)"
            flag = "OK" if o.passed else "SAI"

            lines.append(
                f"{o.case_id:6s} {o.category:14s} {o.query[:42]:44s} "
                f"{expected:14s} {returned:22s} {flag:4s}"
            )

        by_cat = self.by_category()

        lines += [
            "-" * 108,
            f"Recall@5        : {self.recall_at_5:.3f}   (ngưỡng 0.80)",
            f"MRR             : {self.mrr:.3f}   (ngưỡng 0.60)",
            f"NEGATIVE đạt    : {self.negative_pass_rate:.3f}",
            f"Intent đúng     : {self.intent_accuracy:.3f}   (ngưỡng 0.90)",
            "Theo loại       : "
            + "  ".join(f"{key}={hit}/{total}" for key, (hit, total) in sorted(by_cat.items())),
        ]

        return "\n".join(lines)


def load_golden(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))
