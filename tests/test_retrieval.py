"""Test retrieval lai và bộ đo. Acceptance SPEC muc 11.3."""

from datetime import UTC, datetime

import numpy as np
import pytest

from app.config import PROJECT_ROOT
from app.main import Service
from app.retrieval.hybrid import extract_english_tokens, reciprocal_rank_fusion
from app.retrieval.search_index import SearchIndex
from app.schemas.card import SourceCard, StoredCard
from app.schemas.chat import MatchType
from tests.eval.metrics import CaseOutcome, EvalReport, load_golden

GOLDEN_PATH = PROJECT_ROOT / "tests" / "eval" / "retrieval_golden.json"

RECALL_THRESHOLD = 0.80
MRR_THRESHOLD = 0.60
INTENT_THRESHOLD = 0.90


async def _retrieve(service: Service, query: str, deck_ids: list[int], top_k: int = 5):
    vector = await service.encoder.embed_query(query)

    return service.retriever.retrieve(
        query=query, query_vector=vector, allowed_deck_ids=deck_ids, top_k=top_k
    )


# ---------------------------------------------------------------------
# Bộ đo — cổng chặn hồi quy của cả M2
# ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden() -> list[dict]:
    return load_golden(GOLDEN_PATH)


def test_bo_do_du_40_case_va_dung_phan_bo(golden: list[dict]) -> None:
    """SPEC muc 11.3: tối thiểu 40 case, 15 EXACT / 15 SEMANTIC / 5 CROSS / 5 NEGATIVE."""

    counts: dict[str, int] = {}

    for case in golden:
        counts[case["category"]] = counts.get(case["category"], 0) + 1

    assert len(golden) >= 40
    assert counts == {"EXACT": 15, "SEMANTIC": 15, "CROSS_LINGUAL": 5, "NEGATIVE": 5}
    assert len({case["id"] for case in golden}) == len(golden)


@pytest.fixture
async def report(retrieval_service: Service, golden: list[dict]) -> EvalReport:
    result = EvalReport()

    for case in golden:
        vector = await retrieval_service.encoder.embed_query(case["query"])
        intent = retrieval_service.intent_classifier.classify(case["query"], vector)

        hits = retrieval_service.retriever.retrieve(
            query=case["query"],
            query_vector=vector,
            allowed_deck_ids=case["allowed_deck_ids"],
            top_k=5,
        )

        result.outcomes.append(
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

    return result


def test_recall_at_5_dat_nguong(report: EvalReport) -> None:
    assert report.recall_at_5 >= RECALL_THRESHOLD, "\n" + report.format_table()


def test_mrr_dat_nguong(report: EvalReport) -> None:
    assert report.mrr >= MRR_THRESHOLD, "\n" + report.format_table()


def test_intent_dat_nguong(report: EvalReport) -> None:
    assert report.intent_accuracy >= INTENT_THRESHOLD, "\n" + report.format_table()


def test_moi_case_negative_deu_tra_rong(report: EvalReport) -> None:
    """Không có câu trả lời trong phạm vi thì phải nói không có, đừng bịa."""

    assert report.negative_pass_rate == 1.0, "\n" + report.format_table()


# ---------------------------------------------------------------------
# Ranh giới phạm vi — phần bảo mật
# ---------------------------------------------------------------------


async def test_khong_thay_the_ngoai_pham_vi(retrieval_service: Service) -> None:
    """Acceptance: 'deforestation' chỉ có ở deck 3, hỏi trong [1,2] phải rỗng."""

    hits = await _retrieve(retrieval_service, "deforestation nghĩa là gì", [1, 2])

    assert hits == []


async def test_thay_the_khi_deck_duoc_chia_se(retrieval_service: Service) -> None:
    """Acceptance: 'curriculum' ở deck 4, hỏi trong [1,2,4] phải tìm thấy 401."""

    hits = await _retrieve(retrieval_service, "curriculum nghĩa là gì", [1, 2, 4])

    assert hits[0].card.card_id == 401


async def test_allowed_deck_ids_rong_tra_ve_rong(retrieval_service: Service) -> None:
    """Fail đóng. Rỗng nghĩa là không được phép gì, không phải không lọc."""

    hits = await _retrieve(retrieval_service, "resilient nghĩa là gì", [])

    assert hits == []


async def test_the_deck_rieng_tu_khong_ro_ri_qua_semantic(
    retrieval_service: Service,
) -> None:
    """
    Deck 3 là bộ riêng của USER_B. Câu hỏi rất gần nội dung deck 3 mà USER_A
    hỏi thì tuyệt đối không được thấy thẻ nào của deck đó.
    """

    hits = await _retrieve(
        retrieval_service, "từ nào nói về năng lượng tái tạo và môi trường", [1, 2], top_k=10
    )

    assert all(hit.card.deck_id in {1, 2} for hit in hits)
    assert all(hit.card.card_id not in {301, 302, 303, 304} for hit in hits)


# ---------------------------------------------------------------------
# Hành vi từng tầng
# ---------------------------------------------------------------------


async def test_khop_chinh_xac_luon_o_rank_1(retrieval_service: Service) -> None:
    """Acceptance: câu hỏi chứa từ khớp chính xác luôn cho thẻ đó ở rank 1."""

    for word, card_id, deck in [
        ("resilient", 101, 1),
        ("deadline", 201, 2),
        ("emission", 302, 3),
        ("tuition", 402, 4),
    ]:
        hits = await _retrieve(retrieval_service, f"{word} nghĩa là gì", [deck])

        assert hits[0].card.card_id == card_id
        assert hits[0].match_type == MatchType.EXACT
        assert hits[0].rank == 1


async def test_khop_chinh_xac_khong_phan_biet_hoa_thuong(
    retrieval_service: Service,
) -> None:
    hits = await _retrieve(retrieval_service, "RESILIENT nghĩa là gì", [1])

    assert hits[0].card.card_id == 101
    assert hits[0].match_type == MatchType.EXACT


async def test_cau_hoi_tieng_viet_thuan_van_truy_duoc(retrieval_service: Service) -> None:
    """Không có từ tiếng Anh nào trong câu hỏi — hoàn toàn dựa vào semantic."""

    hits = await _retrieve(retrieval_service, "từ nào chỉ cảm giác lo lắng", [1])

    found = [hit.card.card_id for hit in hits]

    assert 102 in found or 108 in found
    assert hits[0].match_type != MatchType.EXACT


async def test_top_k_duoc_ton_trong(retrieval_service: Service) -> None:
    hits = await _retrieve(retrieval_service, "từ nào chỉ cảm giác lo lắng", [1], top_k=2)

    assert len(hits) <= 2


# ---------------------------------------------------------------------
# Hàm thuần
# ---------------------------------------------------------------------


def test_rrf_cong_don_theo_thu_hang() -> None:
    fused = reciprocal_rank_fusion([[10, 20], [20, 10]], k=60)

    # Cả hai đều đứng hạng 1 ở một danh sách và hạng 2 ở danh sách kia.
    assert fused[10] == pytest.approx(fused[20])
    assert fused[10] == pytest.approx(1 / 61 + 1 / 62)


def test_rrf_uu_tien_the_xuat_hien_o_ca_hai_danh_sach() -> None:
    fused = reciprocal_rank_fusion([[10, 30], [10, 40]], k=60)

    assert fused[10] > fused[30]
    assert fused[10] > fused[40]


def test_tach_token_tieng_anh_bo_qua_tu_viet_khong_dau() -> None:
    """
    'anh' trong 'tiếng anh' lọt qua bộ dò [a-zA-Z] và có mặt ở MỌI câu hỏi
    kiểu 'X tiếng anh là gì'. Không lọc thì mọi câu tra từ chéo ngôn ngữ đều
    bị coi là hỏi về một từ vựng tên 'anh'.
    """

    assert extract_english_tokens("khí thải tiếng anh là gì") == []
    assert extract_english_tokens("resilient nghĩa là gì") == ["resilient"]
    assert extract_english_tokens("what does redundant mean") == ["redundant"]


def _stored(card_id: int, deck_id: int, word: str, meaning: str, example: str = "") -> StoredCard:
    return StoredCard(
        card=SourceCard(
            card_id=card_id,
            deck_id=deck_id,
            word=word,
            meaning=meaning,
            example_sentence=example or None,
            source_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        vector=np.zeros(4, dtype="float32"),
    )


def test_bm25_khong_de_hu_tu_quyet_dinh_thu_hang() -> None:
    """
    Hồi quy từ dữ liệu thật của backend.

    "từ" trong câu hỏi khớp với nghĩa "từ chức" của thẻ `resign`. Trong corpus
    nhỏ, `resign` là thẻ DUY NHẤT chứa "từ" nên IDF của nó rất cao: câu hỏi về
    gia đình trả về `resign` với 2.85 điểm trong khi mọi thẻ khác đều 0 điểm.
    """

    index = SearchIndex(dim=4)
    index.rebuild(
        [
            _stored(18, 11, "resign", "từ chức", "He decided to resign from his position."),
            _stored(3, 9, "mother", "mẹ", "She is a loving mother."),
            _stored(5, 9, "sibling", "anh chị em ruột", "I have two siblings."),
        ]
    )

    decks = [9, 11]

    assert index.lexical_search("từ nào nói về gia đình", decks, top_k=5) == []

    # Không được lọc tay quá đà: "resign" vẫn phải tra được như thường.
    assert [cid for cid, _ in index.lexical_search("resign", decks, top_k=5)] == [18]

    # Và "từ" vẫn nằm nguyên trong corpus, nên hỏi thẳng "từ chức" vẫn ra.
    assert 18 in [cid for cid, _ in index.lexical_search("từ chức", decks, top_k=5)]


def test_sanitize_nuot_ky_tu_vo_hinh_dung_de_giau_chi_thi() -> None:
    """
    Bốn dải được thêm vào `_CONTROL_RE` sau, mỗi dải là một cách giấu chỉ thị
    trong văn bản mà mắt người không thấy.

    Dải quan trọng nhất là U+2066-U+2069: bản đầu dừng ngay TRƯỚC chúng. Đó đúng
    là bốn ký tự "isolate" mà tấn công Trojan Source dùng — Unicode 6.3 thêm
    chúng SAU nhóm override đã bị chặn, nên chặn nhóm cũ mà quên nhóm mới là bỏ
    lọt đúng bộ đang được dùng thật.
    """

    from app.retrieval.context import sanitize

    ca = [
        ("LRI", "\u2066"),
        ("PDI", "\u2069"),
        ("khối tag", "\U000e0001"),
        ("variation selector", "\ufe0f"),
        ("Hangul filler", "\u3164"),
        ("Mongolian vowel separator", "\u180e"),
    ]

    for ten, ky_tu in ca:
        assert sanitize(f"a{ky_tu}b", 50) == "a b", f"{ten} vẫn lọt qua"

    # Đối chứng: chữ tiếng Việt có dấu KHÔNG được đụng tới.
    assert sanitize("kiên cường", 50) == "kiên cường"
