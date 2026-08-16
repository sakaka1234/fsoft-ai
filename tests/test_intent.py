"""
Test phân loại intent. Acceptance SPEC muc 11.3 mục 2.

Điểm mấu chốt: 0 lời gọi LLM. Đây là chỗ tiết kiệm ~230 token mỗi lượt chat.
"""

import sys

import pytest

from app.main import Service
from app.retrieval.intent import classify_by_rule
from app.retrieval.intent_examples import INTENT_EXAMPLES
from app.schemas.chat import Intent

# Câu test CỐ Ý khác với câu mẫu trong intent_examples.py — đo trên chính dữ
# liệu huấn luyện thì con số không có ý nghĩa gì.
CASES: list[tuple[str, Intent]] = [
    ("resilient nghĩa là gì", Intent.VOCAB_LOOKUP),
    ("từ deadline có nghĩa gì vậy", Intent.VOCAB_LOOKUP),
    ("từ nào chỉ cảm giác lo lắng", Intent.VOCAB_LOOKUP),
    ("khí thải carbon tiếng anh là gì", Intent.VOCAB_LOOKUP),
    ("giải thích giúp tôi từ stakeholder", Intent.VOCAB_LOOKUP),
    ("procurement phát âm sao", Intent.VOCAB_LOOKUP),
    ("tôi muốn hiểu rõ hơn về từ appraisal", Intent.VOCAB_LOOKUP),
    ("empathetic là từ loại gì", Intent.VOCAB_LOOKUP),
    ("cho tôi ví dụ với từ này", Intent.EXAMPLE_REQUEST),
    ("đặt câu với resilient", Intent.EXAMPLE_REQUEST),
    ("cho ví dụ trong ngữ cảnh công sở", Intent.EXAMPLE_REQUEST),
    ("dùng streamline trong câu thế nào", Intent.EXAMPLE_REQUEST),
    ("viết một câu có từ diligent đi", Intent.EXAMPLE_REQUEST),
    ("dịch câu này sang tiếng Việt giúp tôi", Intent.TRANSLATE),
    ("dịch giùm mình đoạn dưới", Intent.TRANSLATE),
    ("translate this paragraph for me", Intent.TRANSLATE),
    ("thì hiện tại hoàn thành dùng khi nào", Intent.GRAMMAR_QA),
    ("phân biệt câu điều kiện loại 1 và loại 2", Intent.GRAMMAR_QA),
    ("khi nào dùng giới từ at", Intent.GRAMMAR_QA),
    ("cách chia động từ bất quy tắc", Intent.GRAMMAR_QA),
    ("câu bị động dùng ra sao", Intent.GRAMMAR_QA),
    ("tạo cho tôi bài kiểm tra 10 câu", Intent.QUIZ_REQUEST),
    ("cho tôi làm quiz đi", Intent.QUIZ_REQUEST),
    ("tôi muốn ôn tập bộ thẻ này", Intent.QUIZ_REQUEST),
    ("làm bài trắc nghiệm từ vựng nào", Intent.QUIZ_REQUEST),
    ("xin chào", Intent.SMALLTALK),
    ("cảm ơn bạn nhiều nhé", Intent.SMALLTALK),
    ("bạn là ai thế", Intent.SMALLTALK),
    ("tạm biệt nha", Intent.SMALLTALK),
    ("hôm nay thời tiết thế nào", Intent.OUT_OF_SCOPE),
    ("kết quả bóng đá tối qua", Intent.OUT_OF_SCOPE),
    ("chỉ tôi cách nấu bún bò", Intent.OUT_OF_SCOPE),
    ("giá vàng hôm nay bao nhiêu", Intent.OUT_OF_SCOPE),
    ("viết hộ tôi đoạn code java", Intent.OUT_OF_SCOPE),
    ("nên đầu tư chứng khoán nào", Intent.OUT_OF_SCOPE),
    ("gợi ý cho tôi vài bộ phim hay", Intent.OUT_OF_SCOPE),
    ("cách chữa đau lưng tại nhà", Intent.OUT_OF_SCOPE),
    ("thủ đô nước Pháp là gì", Intent.VOCAB_LOOKUP),
    ("literacy nghĩa là gì", Intent.VOCAB_LOOKUP),
    ("cho vài câu ví dụ nữa đi", Intent.EXAMPLE_REQUEST),
]


async def _classify_all(service: Service) -> list[tuple[str, Intent, Intent]]:
    result = []

    for query, expected in CASES:
        vector = await service.encoder.embed_query(query)
        actual = service.intent_classifier.classify(query, vector)

        result.append((query, expected, actual.intent))

    return result


async def test_intent_dung_tren_90_phan_tram(retrieval_service: Service) -> None:
    outcomes = await _classify_all(retrieval_service)

    wrong = [(q, e, a) for q, e, a in outcomes if e != a]
    accuracy = 1 - len(wrong) / len(outcomes)

    detail = "\n".join(f"  {q!r}: kỳ vọng {e}, nhận {a}" for q, e, a in wrong)

    assert accuracy >= 0.90, f"Chỉ đúng {accuracy:.1%}\n{detail}"


async def test_phan_loai_intent_khong_goi_llm(retrieval_service: Service) -> None:
    """
    Acceptance SPEC muc 11.3: 0 lời gọi LLM, khẳng định bằng mock đếm call == 0.

    Chặn ở tầng thấp nhất có thể: bất kỳ ai khởi tạo client OpenAI trong lúc
    phân loại đều làm test nổ.
    """

    calls: list[str] = []

    class TripWire:
        def __init__(self, *args, **kwargs) -> None:
            calls.append("khởi tạo client LLM")

    openai_module = sys.modules.get("openai")

    if openai_module is not None:
        original_sync = openai_module.OpenAI
        original_async = openai_module.AsyncOpenAI
        openai_module.OpenAI = TripWire
        openai_module.AsyncOpenAI = TripWire

    try:
        await _classify_all(retrieval_service)

    finally:
        if openai_module is not None:
            openai_module.OpenAI = original_sync
            openai_module.AsyncOpenAI = original_async

    assert calls == []


# ---------------------------------------------------------------------
# Tầng rule
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("resilient nghĩa là gì", Intent.VOCAB_LOOKUP),
        ("đặt câu với từ này", Intent.EXAMPLE_REQUEST),
        ("cho tôi ví dụ", Intent.EXAMPLE_REQUEST),
        ("dịch câu này giúp tôi", Intent.TRANSLATE),
        ("thì quá khứ đơn dùng khi nào", Intent.GRAMMAR_QA),
        ("tạo cho tôi một bài kiểm tra", Intent.QUIZ_REQUEST),
        ("xin chào bạn", Intent.SMALLTALK),
    ],
)
def test_rule_bat_dung_intent(query: str, expected: Intent) -> None:
    assert classify_by_rule(query) == expected


def test_rule_uu_tien_vi_du_hon_tra_tu() -> None:
    """
    'cho tôi ví dụ với từ resilient nghĩa là gì' chứa dấu hiệu của cả hai.
    Luật đặc thù hơn phải thắng.
    """

    assert classify_by_rule("cho tôi ví dụ về từ resilient") == Intent.EXAMPLE_REQUEST


def test_rule_khong_nham_hu_tu_thi() -> None:
    """
    'thì' là hư từ cực phổ biến trong tiếng Việt. Chỉ được coi là ngữ pháp khi
    đi kèm tên một thì cụ thể, nếu không thì mọi câu 'nếu... thì...' đều bị
    phân loại nhầm.
    """

    assert classify_by_rule("nếu tôi học chăm thì có giỏi lên không") != Intent.GRAMMAR_QA
    assert classify_by_rule("thì hiện tại hoàn thành là gì") == Intent.GRAMMAR_QA


def test_rule_khong_bat_thi_tra_none() -> None:
    assert classify_by_rule("hôm nay trời đẹp quá") is None


# ---------------------------------------------------------------------
# Tầng centroid
# ---------------------------------------------------------------------


def test_moi_intent_deu_co_cau_mau() -> None:
    """
    OUT_OF_SCOPE phải là một lớp THẬT có câu mẫu riêng, không phải "cái còn
    lại khi dưới ngưỡng" — lý do ở docs/M0_FINDINGS.md muc 2.5.
    """

    assert set(INTENT_EXAMPLES) == set(Intent)

    for intent, examples in INTENT_EXAMPLES.items():
        assert len(examples) >= 10, f"{intent} chỉ có {len(examples)} câu mẫu"


async def test_centroid_bat_duoc_out_of_scope(retrieval_service: Service) -> None:
    """Câu ngoài phạm vi mà không luật nào bắt được thì centroid phải bắt."""

    for query in [
        "hôm nay thời tiết thế nào",
        "kết quả bóng đá tối qua",
        "giá vàng hôm nay bao nhiêu",
    ]:
        assert classify_by_rule(query) is None

        vector = await retrieval_service.encoder.embed_query(query)
        result = retrieval_service.intent_classifier.classify(query, vector)

        assert result.intent == Intent.OUT_OF_SCOPE
        assert result.method == "CENTROID"


async def test_chua_warmup_thi_khong_no(service: Service) -> None:
    """Phân loại trước khi warmup phải suy biến an toàn, không raise."""

    import numpy as np

    result = service.intent_classifier.classify("câu gì đó", np.zeros(384, dtype=np.float32))

    assert result.intent == Intent.VOCAB_LOOKUP
