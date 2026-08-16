"""Test sinh quiz. Acceptance SPEC muc 11.6."""

import json as jsonlib
import random
import re
import time

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import InvalidRequest, InvalidScope
from app.embedding.encoder import Encoder
from app.llm.client import LlmClient
from app.main import Service, create_app
from app.quiz.distractors import normalize_meaning, pick_distractors
from app.quiz.validator import BLANK, validate_question
from app.schemas.quiz import (
    GeneratedBy,
    MatchingPair,
    QuestionType,
    QuizQuestion,
    QuizRequest,
)
from tests.test_llm_client import FakeGroq, ok
from tests.test_security import HEADERS, wait_until_ready

DECK_1_ADJECTIVES = {101, 102, 103, 104, 105, 106, 107, 108, 109}
DECK_3_CARDS = {301, 302, 303, 304}


def make_request(**overrides) -> QuizRequest:
    data = {
        "deck_id": 1,
        "allowed_deck_ids": [1, 2, 4],
        "question_count": 8,
        "types": [QuestionType.MULTIPLE_CHOICE],
        "use_ai_context": False,
    }
    data.update(overrides)

    return QuizRequest(**data)


@pytest.fixture
def quiz_service(retrieval_service: Service) -> Service:
    retrieval_service.settings.ai_llm_api_key = "gsk_test"

    return retrieval_service


def with_groq(service: Service, fake: FakeGroq) -> Service:
    service.llm = LlmClient(
        service.settings, service.budget, service.usage_repo, http_client=fake.as_client()
    )
    service.quiz._llm = service.llm
    service.fake_groq = fake  # type: ignore[attr-defined]

    return service


# ---------------------------------------------------------------------
# Chế độ deterministic — không chạm Groq
# ---------------------------------------------------------------------


async def test_8_cau_deterministic_khong_ton_token(quiz_service: Service) -> None:
    """Acceptance: 8 câu use_ai_context=false -> dưới 2 giây, prompt_tokens=0."""

    fake = FakeGroq([ok()])
    with_groq(quiz_service, fake)

    started = time.perf_counter()
    questions, stats = await quiz_service.quiz.generate(make_request(), rng=random.Random(42))
    elapsed = time.perf_counter() - started

    assert len(questions) == 8
    assert stats.prompt_tokens == 0
    assert stats.completion_tokens == 0
    assert stats.llm_count == 0
    assert stats.deterministic_count == 8
    assert fake.call_count == 0
    assert elapsed < 2.0, f"mất {elapsed:.2f}s"


async def test_moi_trac_nghiem_co_4_lua_chon_khac_nhau(quiz_service: Service) -> None:
    """Acceptance: đúng 4 lựa chọn khác nhau, correct_index hợp lệ."""

    questions, _ = await quiz_service.quiz.generate(make_request(), rng=random.Random(1))

    for question in questions:
        assert len(question.options) == 4
        assert len({normalize_meaning(o) for o in question.options}) == 4
        assert question.correct_index is not None
        assert 0 <= question.correct_index < 4
        assert question.explanation


async def test_nhieu_lay_trong_cung_deck_khong_lan_sang_deck_khac(
    quiz_service: Service,
) -> None:
    """
    Acceptance: hỏi thẻ 101 (resilient) -> nhiễu là tính từ khác trong deck 1,
    KHÔNG phải từ deck 3.
    """

    card = quiz_service.index.get(101)
    distractors = pick_distractors(
        quiz_service.index, card, count=3, max_cosine=0.92, field="meaning"
    )

    assert len(distractors) == 3

    for distractor in distractors:
        assert distractor.deck_id == 1
        assert distractor.card_id in DECK_1_ADJECTIVES
        assert distractor.card_id not in DECK_3_CARDS


async def test_loai_the_qua_giong_de_khong_co_hai_dap_an_dung(
    quiz_service: Service,
) -> None:
    """
    apprehensive (102) và anxious (108) rất gần nhau về nghĩa. Ngưỡng cosine
    tồn tại chính vì cặp này — chọn cả hai làm lựa chọn là ra hai đáp án đúng.
    """

    card = quiz_service.index.get(102)

    long_leo = pick_distractors(quiz_service.index, card, count=3, max_cosine=1.0)
    chat_che = pick_distractors(quiz_service.index, card, count=3, max_cosine=0.80)

    assert len(chat_che) <= len(long_leo)

    for distractor in chat_che:
        score = float(
            quiz_service.index.vectors.vector_of(102)
            @ quiz_service.index.vectors.vector_of(distractor.card_id)
        )
        assert score <= 0.80


async def test_dang_nghe_bo_qua_the_khong_co_audio(quiz_service: Service) -> None:
    """Acceptance: LISTENING bỏ qua thẻ 402 (tuition) vì audio_url là null."""

    from app.quiz.deterministic import build_listening

    khong_audio = quiz_service.index.get(402)
    co_audio = quiz_service.index.get(403)

    assert khong_audio.audio_url is None

    assert (
        build_listening(
            quiz_service.index,
            khong_audio,
            index_number=1,
            max_cosine=0.92,
            rng=random.Random(0),
        )
        is None
    )

    assert co_audio.audio_url is not None


async def test_dang_nghe_tren_deck_du_the(quiz_service: Service) -> None:
    questions, _ = await quiz_service.quiz.generate(
        make_request(types=[QuestionType.LISTENING], question_count=4),
        rng=random.Random(7),
    )

    nghe = [q for q in questions if q.type == QuestionType.LISTENING]

    assert nghe

    for question in nghe:
        assert question.audio_url
        assert len(question.options) == 4


async def test_dang_noi_tu_xao_tron_va_anh_xa_dung(quiz_service: Service) -> None:
    questions, _ = await quiz_service.quiz.generate(
        make_request(types=[QuestionType.MATCHING], question_count=2),
        rng=random.Random(3),
    )

    noi = [q for q in questions if q.type == QuestionType.MATCHING]

    assert noi

    for question in noi:
        assert question.matching
        assert len(question.options) == len(question.matching)

        indexes = sorted(pair.correct_option_index for pair in question.matching)
        assert indexes == list(range(len(question.options)))


# ---------------------------------------------------------------------
# Ranh giới phạm vi và đầu vào
# ---------------------------------------------------------------------


async def test_deck_ngoai_pham_vi_thi_400_invalid_scope(quiz_service: Service) -> None:
    """Acceptance: deck_id=3 với allowed_deck_ids=[1,2] -> 400 INVALID_SCOPE."""

    with pytest.raises(InvalidScope):
        await quiz_service.quiz.generate(make_request(deck_id=3, allowed_deck_ids=[1, 2]))


async def test_allowed_deck_ids_rong_thi_400(quiz_service: Service) -> None:
    with pytest.raises(InvalidScope):
        await quiz_service.quiz.generate(make_request(allowed_deck_ids=[]))


async def test_deck_qua_it_the_thi_400_khong_phai_500(quiz_service: Service) -> None:
    """Acceptance: deck 4 chỉ có 3 thẻ -> 400 INVALID_REQUEST message tiếng Việt."""

    with pytest.raises(InvalidRequest) as excinfo:
        await quiz_service.quiz.generate(make_request(deck_id=4, allowed_deck_ids=[1, 4]))

    assert excinfo.value.http_status == 400
    assert excinfo.value.code == "INVALID_REQUEST"
    assert "thẻ" in excinfo.value.message


async def test_card_ids_thu_hep_pham_vi(quiz_service: Service) -> None:
    """card_ids do backend chọn theo SRS — fsoft-ai chỉ lọc lại cho an toàn."""

    questions, _ = await quiz_service.quiz.generate(
        make_request(card_ids=[101, 102, 103, 104, 105], question_count=3),
        rng=random.Random(5),
    )

    assert all(q.card_id in {101, 102, 103, 104, 105} for q in questions if q.card_id)


# ---------------------------------------------------------------------
# Dạng điền từ — cần LLM
# ---------------------------------------------------------------------


def requested_card_ids(request: httpx2.Request) -> list[int]:
    """Rút card_id mà prompt đang hỏi, để fake trả lời đúng lô được yêu cầu."""

    system = jsonlib.loads(request.content)["messages"][0]["content"]

    return [int(m) for m in re.findall(r"card_id=(\d+)", system)]


def fill_blank_responder(request: httpx2.Request):
    """Groq giả biết soạn đúng những thẻ được hỏi trong lô."""

    payload = {
        "questions": [
            {
                "card_id": card_id,
                "prompt": f"The team stayed {BLANK} after the setback number {card_id}.",
                "options": [f"word{card_id}", "indifferent", "impulsive", "meticulous"],
                "correct_index": 0,
                "explanation": "Câu nói về việc giữ vững tinh thần sau thất bại.",
            }
            for card_id in requested_card_ids(request)
        ]
    }

    return ok(jsonlib.dumps(payload, ensure_ascii=False))


async def test_dien_tu_goi_llm_theo_lo(quiz_service: Service) -> None:
    """
    Acceptance: 8 câu use_ai_context=true -> tối đa 2 lời gọi LLM.

    8 thẻ chia lô 5 -> đúng 2 lô, mỗi lô một lời gọi. Sinh từng câu một sẽ tốn
    8 lời gọi và gấp khoảng bốn lần token vì system prompt lặp lại mỗi lần.
    """

    fake = FakeGroq(fill_blank_responder)
    with_groq(quiz_service, fake)

    questions, stats = await quiz_service.quiz.generate(
        make_request(types=[QuestionType.FILL_BLANK], question_count=8, use_ai_context=True),
        rng=random.Random(11),
    )

    assert fake.call_count <= 2, f"gọi LLM {fake.call_count} lần"
    assert len(questions) == 8
    assert stats.llm_count == 8


async def test_moi_lo_chi_hoi_dung_the_cua_lo_do(quiz_service: Service) -> None:
    fake = FakeGroq(fill_blank_responder)
    with_groq(quiz_service, fake)

    await quiz_service.quiz.generate(
        make_request(types=[QuestionType.FILL_BLANK], question_count=8, use_ai_context=True),
        rng=random.Random(11),
    )

    lo = [requested_card_ids(req) for req in fake.requests]

    assert [len(batch) for batch in lo] == [5, 3]
    assert not set(lo[0]) & set(lo[1])


async def test_use_ai_context_false_bo_han_dang_dien_tu(quiz_service: Service) -> None:
    """Chế độ dự phòng ngày bảo vệ: không chạm Groq dù có yêu cầu FILL_BLANK."""

    fake = FakeGroq([ok()])
    with_groq(quiz_service, fake)

    questions, stats = await quiz_service.quiz.generate(
        make_request(
            types=[QuestionType.FILL_BLANK, QuestionType.MULTIPLE_CHOICE],
            use_ai_context=False,
        ),
        rng=random.Random(13),
    )

    assert fake.call_count == 0
    assert stats.prompt_tokens == 0
    assert all(q.type != QuestionType.FILL_BLANK for q in questions)


async def test_llm_tra_json_hong_thi_thay_bang_cau_deterministic(
    quiz_service: Service,
) -> None:
    """
    Acceptance: mock LLM trả JSON hỏng -> tự thay bằng câu deterministic,
    response vẫn 200.

    Không bao giờ trả lỗi cho người học chỉ vì LLM sinh sai.
    """

    fake = FakeGroq(lambda request: ok("{{{ đây không phải JSON"))
    with_groq(quiz_service, fake)

    questions, stats = await quiz_service.quiz.generate(
        make_request(types=[QuestionType.FILL_BLANK], question_count=4, use_ai_context=True),
        rng=random.Random(17),
    )

    assert len(questions) == 4
    assert stats.llm_count == 0
    assert all(q.generated_by == GeneratedBy.DETERMINISTIC for q in questions)


async def test_llm_lo_dap_an_trong_de_bai_thi_bi_loai(quiz_service: Service) -> None:
    """Acceptance: không FILL_BLANK nào chứa từ đáp án trong đề bài."""

    lo_dap_an = jsonlib.dumps(
        {
            "questions": [
                {
                    "card_id": 101,
                    "prompt": f"She remained resilient despite {BLANK} setbacks.",
                    "options": ["resilient", "diligent", "impulsive", "meticulous"],
                    "correct_index": 0,
                    "explanation": "Giải thích bằng tiếng Việt có dấu.",
                }
            ]
        },
        ensure_ascii=False,
    )

    fake = FakeGroq(lambda request: ok(lo_dap_an))
    with_groq(quiz_service, fake)

    questions, _ = await quiz_service.quiz.generate(
        make_request(types=[QuestionType.FILL_BLANK], question_count=1, use_ai_context=True),
        rng=random.Random(19),
    )

    assert questions[0].generated_by == GeneratedBy.DETERMINISTIC


async def test_llm_hong_hoan_toan_van_tra_du_cau_hoi(quiz_service: Service) -> None:
    fake = FakeGroq([httpx2.Response(500, json={"error": {}})])
    with_groq(quiz_service, fake)

    questions, _ = await quiz_service.quiz.generate(
        make_request(types=[QuestionType.FILL_BLANK], question_count=3, use_ai_context=True),
        rng=random.Random(23),
    )

    assert len(questions) == 3


# ---------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------


def valid_question(**overrides) -> QuizQuestion:
    data = {
        "index": 1,
        "type": QuestionType.FILL_BLANK,
        "card_id": 101,
        "prompt": f"The team stayed {BLANK} after the setback.",
        "options": ["resilient", "indifferent", "impulsive", "meticulous"],
        "correct_index": 0,
        "explanation": "Giải thích bằng tiếng Việt có dấu đầy đủ.",
        "generated_by": GeneratedBy.LLM,
    }
    data.update(overrides)

    return QuizQuestion(**data)


def test_validator_chap_nhan_cau_dung() -> None:
    assert validate_question(valid_question(), answer_word="resilient").ok


@pytest.mark.parametrize(
    ("overrides", "ly_do"),
    [
        ({"options": ["a", "b", "c"]}, "cần đúng 4"),
        ({"options": ["a", "a", "b", "c"]}, "trùng nhau"),
        ({"options": ["a", "A ", "b", "c"]}, "trùng nhau"),
        ({"correct_index": 9}, "ngoài khoảng"),
        ({"correct_index": None}, "ngoài khoảng"),
        ({"explanation": ""}, "thiếu giải thích"),
        ({"explanation": "This is an English explanation"}, "không phải tiếng Việt"),
        ({"prompt": "Không có chỗ trống ở đây."}, "phải chứa"),
    ],
)
def test_validator_bat_loi(overrides: dict, ly_do: str) -> None:
    result = validate_question(valid_question(**overrides), answer_word="resilient")

    assert result.ok is False
    assert ly_do in result.reason


def test_validator_bat_de_bai_lo_dap_an() -> None:
    question = valid_question(prompt=f"She was resilient when {BLANK} happened.")

    result = validate_question(question, answer_word="resilient")

    assert result.ok is False
    assert "lộ đáp án" in result.reason


def test_validator_kiem_dang_noi_tu() -> None:
    hop_le = QuizQuestion(
        index=1,
        type=QuestionType.MATCHING,
        prompt="Nối từ với nghĩa",
        options=["nghĩa A", "nghĩa B"],
        explanation="Giải thích tiếng Việt.",
        generated_by=GeneratedBy.DETERMINISTIC,
        matching=[
            MatchingPair(card_id=1, word="a", correct_option_index=0),
            MatchingPair(card_id=2, word="b", correct_option_index=1),
        ],
    )

    assert validate_question(hop_le).ok

    sai = hop_le.model_copy(
        update={
            "matching": [
                MatchingPair(card_id=1, word="a", correct_option_index=0),
                MatchingPair(card_id=2, word="b", correct_option_index=0),
            ]
        }
    )

    assert validate_question(sai).ok is False


def test_chuan_hoa_nghia_bo_dau_cau_va_hoa_thuong() -> None:
    assert normalize_meaning("Kiên cường, phục hồi!") == normalize_meaning("kiên cường  phục hồi")


# ---------------------------------------------------------------------
# Endpoint HTTP
# ---------------------------------------------------------------------


@pytest.fixture
def quiz_client(settings: Settings, encoder: Encoder):
    settings.ai_llm_api_key = "gsk_test"

    with TestClient(create_app(settings, encoder=encoder)) as client:
        wait_until_ready(client)
        client.post("/internal/v1/index/sync", headers=HEADERS)

        yield client


def test_quiz_can_token(quiz_client: TestClient) -> None:
    response = quiz_client.post(
        "/internal/v1/quiz/generate",
        json={"deck_id": 1, "allowed_deck_ids": [1]},
    )

    assert response.status_code == 401


def test_quiz_tra_dung_shape_spec_8_4(quiz_client: TestClient) -> None:
    response = quiz_client.post(
        "/internal/v1/quiz/generate",
        headers=HEADERS,
        json={
            "deck_id": 1,
            "allowed_deck_ids": [1, 2],
            "question_count": 5,
            "types": ["MULTIPLE_CHOICE"],
            "use_ai_context": False,
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert set(body) == {"questions", "stats"}
    assert len(body["questions"]) == 5
    assert body["stats"]["prompt_tokens"] == 0
    assert body["questions"][0]["generated_by"] == "DETERMINISTIC"
    assert body["questions"][0]["index"] == 1


def test_quiz_deck_ngoai_pham_vi_thi_400(quiz_client: TestClient) -> None:
    response = quiz_client.post(
        "/internal/v1/quiz/generate",
        headers=HEADERS,
        json={"deck_id": 3, "allowed_deck_ids": [1, 2]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_SCOPE"


def test_quiz_deck_qua_it_the_thi_400(quiz_client: TestClient) -> None:
    response = quiz_client.post(
        "/internal/v1/quiz/generate",
        headers=HEADERS,
        json={"deck_id": 4, "allowed_deck_ids": [4]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert "thẻ" in response.json()["error"]["message"]


def test_quiz_so_cau_ngoai_khoang_thi_422(quiz_client: TestClient) -> None:
    response = quiz_client.post(
        "/internal/v1/quiz/generate",
        headers=HEADERS,
        json={"deck_id": 1, "allowed_deck_ids": [1], "question_count": 999},
    )

    assert response.status_code == 422
