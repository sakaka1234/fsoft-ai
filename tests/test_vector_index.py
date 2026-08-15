"""Test vector index. SPEC muc 5.6 và acceptance muc 11.2."""

import time

import numpy as np
import pytest

from app.embedding.vector_index import VectorIndex
from app.schemas.card import IndexRow

DIM = 384


def unit(seed: int, dim: int = DIM) -> np.ndarray:
    """Vector ngẫu nhiên đã L2-normalize — index luôn giả định vector đã chuẩn hoá."""

    rng = np.random.default_rng(seed)
    vector = rng.standard_normal(dim).astype(np.float32)

    return vector / np.linalg.norm(vector)


def row(card_id: int, deck_id: int, word: str = "w", seed: int | None = None) -> IndexRow:
    return IndexRow(
        card_id=card_id,
        deck_id=deck_id,
        word=word,
        vector=unit(card_id if seed is None else seed),
    )


@pytest.fixture
def index() -> VectorIndex:
    idx = VectorIndex(DIM)
    idx.upsert(
        [
            row(101, 1, "resilient"),
            row(102, 1, "apprehensive"),
            row(201, 2, "delegate"),
            row(301, 3, "deforestation"),
        ]
    )

    return idx


# ---------------------------------------------------------------------
# Cơ bản
# ---------------------------------------------------------------------


def test_upsert_va_size(index: VectorIndex) -> None:
    assert index.size == 4
    assert index.stats() == {"index_size": 4, "deck_count": 3, "dim": DIM}


def test_search_tra_ve_chinh_no_o_hang_dau(index: VectorIndex) -> None:
    results = index.search(unit(101), [1], top_k=2)

    assert results[0][0] == 101
    assert results[0][1] == pytest.approx(1.0, abs=1e-5)


def test_search_chi_tra_the_trong_deck_cho_phep(index: VectorIndex) -> None:
    results = index.search(unit(301), [1, 2], top_k=10)

    assert 301 not in [card_id for card_id, _ in results]
    assert {card_id for card_id, _ in results} == {101, 102, 201}


def test_search_diem_giam_dan(index: VectorIndex) -> None:
    results = index.search(unit(101), [1, 2, 3], top_k=4)
    scores = [score for _, score in results]

    assert scores == sorted(scores, reverse=True)


def test_top_k_lon_hon_so_the_khong_loi(index: VectorIndex) -> None:
    assert len(index.search(unit(101), [1], top_k=99)) == 2


# ---------------------------------------------------------------------
# Fail đóng — lỗi kinh điển ở SPEC muc 11.3
# ---------------------------------------------------------------------


def test_allowed_deck_ids_rong_tra_ve_rong(index: VectorIndex) -> None:
    """
    Danh sách rỗng nghĩa là KHÔNG ĐƯỢC PHÉP GÌ, không phải "không lọc gì".

    Hiểu sai chỗ này là lộ bộ thẻ riêng tư của người khác.
    """

    assert index.search(unit(101), [], top_k=5) == []


def test_deck_khong_ton_tai_tra_ve_rong(index: VectorIndex) -> None:
    assert index.search(unit(101), [999], top_k=5) == []


def test_index_rong_tra_ve_rong() -> None:
    assert VectorIndex(DIM).search(unit(1), [1], top_k=5) == []


def test_top_k_khong_duong_tra_ve_rong(index: VectorIndex) -> None:
    assert index.search(unit(101), [1], top_k=0) == []


# ---------------------------------------------------------------------
# Cập nhật và xoá
# ---------------------------------------------------------------------


def test_upsert_de_len_the_da_co_khong_lam_phinh_index(index: VectorIndex) -> None:
    index.upsert([row(101, 1, "resilient", seed=777)])

    assert index.size == 4
    assert index.search(unit(777), [1], top_k=1)[0][0] == 101


def test_doi_deck_id_thi_rows_of_deck_cap_nhat_theo(index: VectorIndex) -> None:
    """Acceptance SPEC muc 11.2: đổi deckId của thẻ 101 -> _rows_of_deck theo kịp."""

    index.upsert([row(101, 2, "resilient")])

    trong_deck_1 = [cid for cid, _ in index.search(unit(101), [1], top_k=5)]
    trong_deck_2 = [cid for cid, _ in index.search(unit(101), [2], top_k=5)]

    assert 101 not in trong_deck_1
    assert 101 in trong_deck_2
    assert index.size == 4  # chuyển deck, không phải thêm thẻ mới


def test_delete_xoa_khoi_index(index: VectorIndex) -> None:
    index.delete({101})

    assert index.size == 3
    assert 101 not in [cid for cid, _ in index.search(unit(101), [1, 2, 3], top_k=10)]


def test_delete_the_khong_ton_tai_khong_loi(index: VectorIndex) -> None:
    index.delete({99999})

    assert index.size == 4


def test_delete_het_thi_index_rong(index: VectorIndex) -> None:
    index.delete({101, 102, 201, 301})

    assert index.size == 0
    assert index.search(unit(101), [1], top_k=5) == []


def test_rebuild_thay_the_toan_bo(index: VectorIndex) -> None:
    index.rebuild([row(500, 5, "new")])

    assert index.size == 1
    assert index.search(unit(500), [5], top_k=1)[0][0] == 500
    assert index.search(unit(101), [1], top_k=1) == []


def test_rebuild_rong_khong_loi(index: VectorIndex) -> None:
    index.rebuild([])

    assert index.size == 0


# ---------------------------------------------------------------------
# Khớp chính xác theo từ
# ---------------------------------------------------------------------


def test_lookup_word_khong_phan_biet_hoa_thuong(index: VectorIndex) -> None:
    assert index.lookup_word("RESILIENT", [1]) == [101]


def test_lookup_word_ton_trong_pham_vi_deck(index: VectorIndex) -> None:
    assert index.lookup_word("deforestation", [1, 2]) == []
    assert index.lookup_word("deforestation", [3]) == [301]


def test_lookup_word_deck_rong_tra_ve_rong(index: VectorIndex) -> None:
    assert index.lookup_word("resilient", []) == []


# ---------------------------------------------------------------------
# Hiệu năng
# ---------------------------------------------------------------------


def test_search_tren_5000_the_duoi_50ms() -> None:
    """Acceptance SPEC muc 11.2: semantic search trên 5.000 thẻ dưới 50ms (p95)."""

    idx = VectorIndex(DIM)
    idx.upsert([row(i, i % 10, f"w{i}") for i in range(5000)])

    query = unit(42)
    durations = []

    for _ in range(20):
        started = time.perf_counter()
        idx.search(query, list(range(10)), top_k=5)
        durations.append((time.perf_counter() - started) * 1000)

    p95 = sorted(durations)[int(len(durations) * 0.95) - 1]

    assert p95 < 50, f"p95 = {p95:.1f}ms"
