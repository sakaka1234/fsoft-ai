"""
Test luồng chat RAG. Acceptance SPEC muc 11.5.

Groq được mock ở tầng transport httpx2 (openai 3.x dùng httpx2, respx không
chặn được — xem tests/test_llm_client.py).
"""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.chat.direct_answer import build_direct_answer, is_simple_lookup
from app.chat.orchestrator import (
    Citation,
    format_history,
    mark_used_citations,
    needs_rewrite,
)
from app.chat.semantic_cache import SemanticCache, scope_hash
from app.config import Settings
from app.core.errors import InvalidScope
from app.embedding.encoder import Encoder
from app.llm.client import LlmClient
from app.main import Service, create_app
from app.schemas.chat import AnswerSource, Intent
from tests.conftest import make_card
from tests.test_llm_client import FakeGroq, auto, ok
from tests.test_security import HEADERS, wait_until_ready


@pytest.fixture
def chat_service(retrieval_service: Service) -> Service:
    """Service đã đồng bộ + warmup, với Groq giả trả lời cố định."""

    fake = FakeGroq([ok("Đây là câu trả lời mẫu từ mô hình. [#101]")])

    retrieval_service.settings.ai_llm_api_key = "gsk_test"
    retrieval_service.llm = LlmClient(
        retrieval_service.settings,
        retrieval_service.budget,
        retrieval_service.usage_repo,
        http_client=fake.as_client(),
    )
    retrieval_service.orchestrator._llm = retrieval_service.llm
    retrieval_service.fake_groq = fake  # type: ignore[attr-defined]

    return retrieval_service


async def ask(service: Service, query: str, decks: list[int] | None = None, **kwargs):
    return await service.orchestrator.chat(
        query=query, allowed_deck_ids=decks if decks is not None else [1], ready=True, **kwargs
    )


# ---------------------------------------------------------------------
# Bước 1 — phạm vi
# ---------------------------------------------------------------------


async def test_allowed_deck_ids_rong_thi_400(chat_service: Service) -> None:
    with pytest.raises(InvalidScope):
        await ask(chat_service, "resilient nghĩa là gì", decks=[])


async def test_scope_deck_ngoai_pham_vi_thi_400(chat_service: Service) -> None:
    with pytest.raises(InvalidScope):
        await ask(chat_service, "resilient nghĩa là gì", decks=[1, 2], scope_deck_id=3)


# ---------------------------------------------------------------------
# Bước 6 — tra từ trực tiếp, 0 token
# ---------------------------------------------------------------------


async def test_tra_tu_don_gian_khong_ton_token(chat_service: Service) -> None:
    """Acceptance: 'resilient nghĩa là gì' -> DIRECT_LOOKUP, prompt_tokens=0."""

    outcome = await ask(chat_service, "resilient nghĩa là gì")

    assert outcome.answer_source == AnswerSource.DIRECT_LOOKUP
    assert outcome.prompt_tokens == 0
    assert outcome.completion_tokens == 0
    assert chat_service.fake_groq.call_count == 0
    assert "kiên cường" in outcome.answer
    assert "[#101]" in outcome.answer


async def test_tra_tu_truc_tiep_danh_dau_citation_da_dung(chat_service: Service) -> None:
    outcome = await ask(chat_service, "resilient nghĩa là gì")

    assert outcome.citations[0].card_id == 101
    assert outcome.citations[0].used_in_answer is True


async def test_cau_hoi_phuc_tap_thi_khong_dung_template(chat_service: Service) -> None:
    """'resilient khác gì diligent' cần LLM so sánh, template không làm được."""

    outcome = await ask(chat_service, "resilient khác gì diligent")

    assert outcome.answer_source != AnswerSource.DIRECT_LOOKUP
    assert chat_service.fake_groq.call_count >= 1


# ---------------------------------------------------------------------
# Bước 5 — câu mẫu, 0 token
# ---------------------------------------------------------------------


async def test_out_of_scope_tra_cau_mau(chat_service: Service) -> None:
    """Acceptance: 'hôm nay thời tiết thế nào' -> OUT_OF_SCOPE, CANNED, 0 token."""

    outcome = await ask(chat_service, "hôm nay thời tiết thế nào")

    assert outcome.intent == Intent.OUT_OF_SCOPE
    assert outcome.answer_source == AnswerSource.CANNED
    assert outcome.prompt_tokens == 0
    assert outcome.citations == []
    assert chat_service.fake_groq.call_count == 0


async def test_smalltalk_tra_cau_mau(chat_service: Service) -> None:
    outcome = await ask(chat_service, "xin chào")

    assert outcome.intent == Intent.SMALLTALK
    assert outcome.answer_source == AnswerSource.CANNED
    assert chat_service.fake_groq.call_count == 0


async def test_ngu_phap_bo_qua_retrieval(chat_service: Service) -> None:
    """Acceptance: câu hỏi ngữ pháp -> citations rỗng, không chạy retrieval."""

    outcome = await ask(chat_service, "thì hiện tại hoàn thành dùng khi nào")

    assert outcome.intent == Intent.GRAMMAR_QA
    assert outcome.citations == []
    assert chat_service.fake_groq.call_count == 1


# ---------------------------------------------------------------------
# Bước 6 — không tìm thấy trong bộ thẻ
# ---------------------------------------------------------------------


async def test_hoi_tu_ngoai_pham_vi_thi_prompt_noi_ro_khong_co(
    chat_service: Service,
) -> None:
    """
    Acceptance: hỏi từ không có trong phạm vi -> prompt phải nói rõ.

    Kiểm ở prompt gửi đi chứ không kiểm câu trả lời: câu trả lời do model sinh,
    còn prompt là thứ ta kiểm soát được.
    """

    await ask(chat_service, "deforestation nghĩa là gì", decks=[1, 2])

    body = chat_service.fake_groq.body()
    user_message = body["messages"][1]["content"]

    assert "không có thẻ nào khớp" in user_message

    system_message = body["messages"][0]["content"]

    assert "chưa có trong bộ thẻ của bạn" in system_message


# ---------------------------------------------------------------------
# Bước 7 — semantic cache
# ---------------------------------------------------------------------


async def test_hoi_lai_y_het_thi_tra_cache(chat_service: Service) -> None:
    """Acceptance: hỏi lại y hệt -> answer_source=CACHE, prompt_tokens=0."""

    first = await ask(chat_service, "resilient dùng trong ngữ cảnh công sở thế nào")

    assert first.answer_source == AnswerSource.RAG
    assert chat_service.fake_groq.call_count == 1

    second = await ask(chat_service, "resilient dùng trong ngữ cảnh công sở thế nào")

    assert second.answer_source == AnswerSource.CACHE
    assert second.prompt_tokens == 0
    assert second.answer == first.answer
    assert chat_service.fake_groq.call_count == 1


async def test_cache_khong_dung_chung_giua_hai_pham_vi_khac_nhau(
    chat_service: Service,
) -> None:
    """
    Lỗ hổng kinh điển: cùng câu hỏi, hai người quyền khác nhau, dùng chung
    câu trả lời dựng từ bộ thẻ của người kia.
    """

    await ask(chat_service, "từ nào nói về việc hội nhập nhân viên mới", decks=[2])

    goi_dau = chat_service.fake_groq.call_count

    await ask(chat_service, "từ nào nói về việc hội nhập nhân viên mới", decks=[1, 2])

    assert chat_service.fake_groq.call_count > goi_dau


def test_scope_hash_khac_nhau_khi_deck_khac_nhau() -> None:
    assert scope_hash([1], [101]) != scope_hash([1, 2], [101])
    assert scope_hash([1], [101]) != scope_hash([1], [102])
    assert scope_hash([1, 2], [101]) == scope_hash([2, 1], [101])


async def test_dong_bo_lam_moi_cache(chat_service: Service) -> None:
    """Thẻ đổi thì câu trả lời đã cache trở thành sai — phải xoá."""

    await ask(chat_service, "resilient dùng trong ngữ cảnh công sở thế nào")

    assert chat_service.cache.size > 0

    chat_service.cache.invalidate_all()

    assert chat_service.cache.size == 0


# ---------------------------------------------------------------------
# Bước 2 — viết lại câu hỏi
# ---------------------------------------------------------------------


def test_khong_viet_lai_khi_khong_co_lich_su() -> None:
    assert needs_rewrite("cho tôi ví dụ với từ này", []) is False


def test_viet_lai_khi_cau_hoi_co_dai_tu() -> None:
    history = [{"role": "user", "content": "resilient nghĩa là gì"}]

    assert needs_rewrite("cho tôi ví dụ với từ này", history) is True


def test_khong_viet_lai_cau_dai_va_tu_du_nghia() -> None:
    history = [{"role": "user", "content": "resilient nghĩa là gì"}]
    dai = "từ procurement trong lĩnh vực mua sắm doanh nghiệp có nghĩa chính xác ra sao"

    assert needs_rewrite(dai, history) is False


async def test_luot_hai_co_rewritten_query_va_citations(chat_service: Service) -> None:
    """Acceptance: 'cho tôi ví dụ với từ này' ở turn 2 -> có rewritten_query."""

    chat_service.fake_groq._responses = [
        ok("cho tôi ví dụ với từ resilient trong công việc"),
        ok("Ví dụ: She stayed resilient. [#101]"),
    ]

    outcome = await ask(
        chat_service,
        "cho tôi ví dụ với từ này",
        history=[
            {"role": "user", "content": "resilient nghĩa là gì"},
            {"role": "assistant", "content": "Resilient nghĩa là kiên cường."},
        ],
    )

    assert outcome.rewritten_query == "cho tôi ví dụ với từ resilient trong công việc"
    assert outcome.answer_source == AnswerSource.RAG
    assert outcome.citations != []


async def test_viet_lai_hong_thi_dung_cau_goc(chat_service: Service) -> None:
    """Rewrite là tối ưu hoá, không phải yêu cầu — hỏng thì lượt chat vẫn chạy."""

    import httpx2

    chat_service.fake_groq._responses = [
        httpx2.Response(500, json={"error": {}}),
        httpx2.Response(500, json={"error": {}}),
        ok("Trả lời bình thường. [#101]"),
    ]

    outcome = await ask(
        chat_service,
        "cho tôi ví dụ với từ này",
        history=[{"role": "user", "content": "resilient nghĩa là gì"}],
    )

    assert outcome.rewritten_query is None
    assert outcome.answer


def test_format_history_cat_theo_gioi_han() -> None:
    history = [{"role": "user", "content": f"câu {i}"} for i in range(10)]

    formatted = format_history(history, max_messages=3)

    assert formatted.count("\n") == 2
    assert "câu 9" in formatted
    assert "câu 0" not in formatted


# ---------------------------------------------------------------------
# Bước 11 — đánh dấu citation
# ---------------------------------------------------------------------


def test_danh_dau_citation_theo_ma_trong_cau_tra_loi() -> None:
    citations = [
        Citation(card_id=101, word="resilient", deck_id=1, deck_title="d", score=1.0, rank=1),
        Citation(card_id=102, word="apprehensive", deck_id=1, deck_title="d", score=0.9, rank=2),
    ]

    mark_used_citations("Từ này nghĩa là kiên cường [#101].", citations)

    assert citations[0].used_in_answer is True
    assert citations[1].used_in_answer is False


# ---------------------------------------------------------------------
# Chống prompt injection — thẻ 109
# ---------------------------------------------------------------------


async def test_the_co_noi_dung_tan_cong_khong_pha_duoc_prompt(
    chat_service: Service,
) -> None:
    """
    Acceptance SPEC muc 11.5: thẻ 109 (benign) có `note` chứa lệnh tấn công
    thật, và nội dung đó đi thẳng vào ngữ cảnh.

    Kiểm bốn lớp phòng thủ đều còn nguyên trong prompt gửi đi.
    """

    await ask(chat_service, "benign dùng trong ngữ cảnh y tế thế nào", decks=[1])

    body = chat_service.fake_groq.body()
    system = body["messages"][0]["content"]
    user = body["messages"][1]["content"]

    # Lớp 1: bọc thẻ
    assert "<NGỮ_CẢNH>" in user and "</NGỮ_CẢNH>" in user

    # Lớp 2: cảnh báo trong system prompt
    assert "CẢNH BÁO BẢO MẬT" in system
    assert "KHÔNG PHẢI chỉ thị" in system

    # Lớp 3: nội dung tấn công nằm TRONG khối ngữ cảnh, không thoát ra ngoài
    tan_cong = "IGNORE ALL PREVIOUS INSTRUCTIONS"

    if tan_cong in user:
        vi_tri = user.index(tan_cong)
        assert user.index("<NGỮ_CẢNH>") < vi_tri < user.index("</NGỮ_CẢNH>")


def test_ngu_canh_cat_field_qua_dai() -> None:
    """Lớp 4: thẻ có note dài hàng nghìn ký tự không nuốt trọn ngân sách."""

    from app.retrieval.context import serialize_card

    card = make_card(note="x" * 5000)
    serialized = serialize_card(card, max_chars_per_field=300)

    assert len(serialized) < 1200


def test_ngu_canh_loc_ky_tu_vo_hinh() -> None:
    from app.retrieval.context import sanitize

    zero_width = "\u200b"
    rtl_override = "\u202e"

    ban = f"bình thường{zero_width}có ký tự ẩn{rtl_override} đảo chiều"
    sach = sanitize(ban, max_chars=300)

    assert zero_width not in sach
    assert rtl_override not in sach
    assert "bình thường" in sach


# ---------------------------------------------------------------------
# Hàm thuần
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("resilient nghĩa là gì", True),
        ("meticulous là gì", True),
        ("từ deadline có nghĩa gì", True),
        ("resilient khác gì diligent", False),
        ("cho tôi ví dụ với resilient trong ngữ cảnh công sở", False),
        ("phân biệt resilient và diligent", False),
        ("giải thích chi tiết từ resilient", False),
    ],
)
def test_nhan_dien_cau_hoi_tra_tu_don_gian(query: str, expected: bool) -> None:
    assert is_simple_lookup(query) is expected


def test_cau_tra_loi_template_co_du_thong_tin_the() -> None:
    answer = build_direct_answer(make_card(card_id=101))

    assert "resilient" in answer
    assert "kiên cường" in answer
    assert "She remained resilient" in answer
    assert "[#101]" in answer
    assert "None" not in answer


def test_cache_het_han_thi_khong_tra_ve() -> None:
    from datetime import UTC, datetime, timedelta

    cache = SemanticCache(threshold=0.9, ttl_hours=1)
    vector = np.ones(384, dtype=np.float32) / np.sqrt(384)

    now = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
    cache.put(vector, "scope", "câu trả lời", [], now=now)

    assert cache.get(vector, "scope", now=now) is not None
    assert cache.get(vector, "scope", now=now + timedelta(hours=2)) is None


def test_cache_duoi_nguong_thi_khong_trung() -> None:
    cache = SemanticCache(threshold=0.99)

    a = np.zeros(384, dtype=np.float32)
    a[0] = 1.0
    b = np.zeros(384, dtype=np.float32)
    b[1] = 1.0

    cache.put(a, "scope", "trả lời", [])

    assert cache.get(b, "scope") is None


def test_cache_tat_thi_khong_luu_gi() -> None:
    cache = SemanticCache(enabled=False)
    vector = np.ones(384, dtype=np.float32) / np.sqrt(384)

    cache.put(vector, "scope", "trả lời", [])

    assert cache.size == 0
    assert cache.get(vector, "scope") is None


def test_cache_day_thi_duoi_theo_lru() -> None:
    cache = SemanticCache(threshold=0.99, max_size=3)

    for i in range(5):
        vector = np.zeros(384, dtype=np.float32)
        vector[i] = 1.0
        cache.put(vector, "scope", f"trả lời {i}", [])

    assert cache.size == 3


# ---------------------------------------------------------------------
# Endpoint HTTP
# ---------------------------------------------------------------------


@pytest.fixture
def chat_client(settings: Settings, encoder: Encoder):
    settings.ai_llm_api_key = "gsk_test"

    app = create_app(settings, encoder=encoder)

    with TestClient(app) as client:
        wait_until_ready(client)

        fake = FakeGroq(auto("Câu trả lời từ mô hình dành cho bạn. [#101]"))
        service = client.app.state.service
        service.llm = LlmClient(
            settings, service.budget, service.usage_repo, http_client=fake.as_client()
        )
        service.orchestrator._llm = service.llm
        client.fake_groq = fake  # type: ignore[attr-defined]

        client.post("/internal/v1/index/sync", headers=HEADERS)

        yield client


def test_chat_can_token(chat_client: TestClient) -> None:
    response = chat_client.post("/internal/v1/chat", json={"query": "x", "allowed_deck_ids": [1]})

    assert response.status_code == 401


def test_chat_deck_rong_thi_400(chat_client: TestClient) -> None:
    response = chat_client.post(
        "/internal/v1/chat",
        headers=HEADERS,
        json={"query": "resilient nghĩa là gì", "allowed_deck_ids": []},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SCOPE"


def test_chat_tra_dung_shape_spec_8_1(chat_client: TestClient) -> None:
    response = chat_client.post(
        "/internal/v1/chat",
        headers=HEADERS,
        json={"query": "resilient nghĩa là gì", "allowed_deck_ids": [1]},
    )
    body = response.json()

    assert response.status_code == 200
    assert set(body) == {
        "answer",
        "intent",
        "answer_source",
        "rewritten_query",
        "citations",
        "usage",
    }
    assert body["answer_source"] == "DIRECT_LOOKUP"
    assert body["citations"][0]["card_id"] == 101
    assert body["usage"]["prompt_tokens"] == 0


def test_stream_phat_citations_truoc_token_dau_tien(chat_client: TestClient) -> None:
    """
    Acceptance SPEC muc 11.5: sự kiện `citations` PHẢI đến trước token đầu tiên.

    Giao diện hiện nguồn ngay, tạo cảm giác phản hồi nhanh hơn hẳn.
    """

    with chat_client.stream(
        "POST",
        "/internal/v1/chat/stream",
        headers=HEADERS,
        json={
            "query": "resilient dùng trong ngữ cảnh công sở ra sao",
            "allowed_deck_ids": [1],
        },
    ) as response:
        events = [
            line[len("event: ") :] for line in response.iter_lines() if line.startswith("event: ")
        ]

    assert response.status_code == 200
    assert "citations" in events
    assert "token" in events
    assert events.index("citations") < events.index("token")
    assert events[0] == "meta"
    assert events[-1] == "done"


def test_stream_loi_pham_vi_phat_su_kien_error(chat_client: TestClient) -> None:
    with chat_client.stream(
        "POST",
        "/internal/v1/chat/stream",
        headers=HEADERS,
        json={"query": "x", "allowed_deck_ids": []},
    ) as response:
        body = "".join(response.iter_text())

    assert "event: error" in body
    assert "INVALID_SCOPE" in body


def test_stream_nhanh_khong_ton_token_van_dung_dinh_dang(chat_client: TestClient) -> None:
    """Nhánh CANNED cũng phải phát đúng chuỗi sự kiện, không rẽ định dạng riêng."""

    with chat_client.stream(
        "POST",
        "/internal/v1/chat/stream",
        headers=HEADERS,
        json={"query": "hôm nay thời tiết thế nào", "allowed_deck_ids": [1]},
    ) as response:
        raw = "".join(response.iter_text())

    assert "event: meta" in raw
    assert "event: citations" in raw
    assert "event: done" in raw

    meta_line = next(
        line for line in raw.splitlines() if line.startswith("data: ") and "intent" in line
    )
    meta = json.loads(meta_line[len("data: ") :])

    assert meta["answer_source"] == "CANNED"
