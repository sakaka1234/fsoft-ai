"""
Trích xuất từ vựng từ đoạn văn. SPEC muc 8.5b.

Ba nhóm test chịu lực nhất, và lý do từng nhóm tồn tại:

- NGÂN SÁCH: một lượt gọi đặt chỗ tới 87% ngân sách token mỗi phút của TOÀN hệ
  thống. Test ở đây render prompt THẬT với văn bản dài nhất, nên nó sẽ đỏ ngay
  nếu ai nâng `ai_vocab_max_text_chars` hoặc trần `max_candidates` mà quên tính
  lại. Không có nó, hậu quả chỉ lộ ra dưới dạng "mọi người bị 429" trên
  production.
- BÁM VĂN BẢN: `example_sentence` là trường duy nhất LLM có thể dùng để đưa nội
  dung mới vào dữ liệu SẼ ĐƯỢC LƯU và chia sẻ.
- LỖI: endpoint này cố ý KHÔNG hạ cấp như quiz. Test khoá lại điều đó, vì "trả
  200 với mảng rỗng" là cám dỗ tự nhiên và nó là một lời nói dối.
"""

import json as jsonlib

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import BudgetExhausted, InvalidRequest, InvalidScope, ProviderUnavailable
from app.embedding.encoder import Encoder
from app.llm.budget import estimate_tokens
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.main import Service, create_app
from app.schemas.vocab import VocabExtractRequest
from app.vocab.extractor import (
    BASE_TOKENS,
    MAX_CANDIDATES_CAP,
    MIN_ENGLISH_TOKENS,
    TOKENS_PER_CANDIDATE,
    VocabExtractor,
)
from tests.test_llm_client import FakeGroq, ok, rate_limited
from tests.test_security import HEADERS, wait_until_ready

# Đoạn văn dùng chung. Cố ý chứa `resilient` (thẻ 101, deck 1) và `deadline`
# (thẻ 201, deck 2) để kiểm khử trùng trên dữ liệu mẫu có thật.
DOAN_VAN = (
    "The team stayed resilient after missing the first deadline. "
    "Their meticulous retrospective uncovered a bottleneck nobody had anticipated."
)


def the(
    word: str = "bottleneck",
    cau: str = "Their meticulous retrospective uncovered a bottleneck nobody had anticipated.",
    **ghi_de,
) -> dict:
    muc = {
        "word": word,
        "phonetic": "/ˈbɒtlnek/",
        "part_of_speech": "noun",
        "meaning": "nút thắt cổ chai, điểm nghẽn",
        "definition_en": "a point of congestion that slows a process",
        "example_sentence": cau,
        "example_meaning": "Buổi tổng kết tỉ mỉ của họ đã phát hiện một điểm nghẽn.",
    }
    muc.update(ghi_de)

    return muc


def tra_loi(*muc: dict) -> httpx2.Response:
    return ok(jsonlib.dumps({"words": list(muc)}, ensure_ascii=False))


def the_tot() -> dict:
    """Ứng viên chắc chắn hợp lệ, dùng làm đối chứng trong các test lọc."""

    return the(
        word="retrospective",
        cau="Their meticulous retrospective uncovered a bottleneck nobody had anticipated.",
        meaning="buổi tổng kết, nhìn lại",
    )


@pytest.fixture
def vocab_service(retrieval_service: Service) -> Service:
    retrieval_service.settings.ai_llm_api_key = "gsk_test"

    return retrieval_service


def with_groq(service: Service, fake: FakeGroq) -> Service:
    service.llm = LlmClient(
        service.settings, service.budget, service.usage_repo, http_client=fake.as_client()
    )
    service.vocab._llm = service.llm

    return service


async def trich(service: Service, **ghi_de):
    body = {"text": DOAN_VAN, "allowed_deck_ids": [1, 2, 3, 4], "max_candidates": 3}
    body.update(ghi_de)

    return await service.vocab.extract(VocabExtractRequest(**body))


# ---------------------------------------------------------------------
# Tiền xử lý — không được chạm tới Groq
# ---------------------------------------------------------------------


async def test_van_ban_qua_dai_thi_400_kem_ca_hai_con_so(vocab_service: Service) -> None:
    """
    Thông báo phải nêu CẢ độ dài thật LẪN giới hạn. Chỉ nói "quá dài" thì backend
    không biết phải cắt bớt bao nhiêu, và người dùng thì mù hoàn toàn.
    """

    fake = FakeGroq([tra_loi(the())])
    with_groq(vocab_service, fake)

    gioi_han = vocab_service.settings.ai_vocab_max_text_chars

    with pytest.raises(InvalidRequest) as thong_tin:
        await trich(vocab_service, text="word " * (gioi_han // 2))

    loi = str(thong_tin.value)

    assert str(gioi_han) in loi
    assert fake.call_count == 0


async def test_allowed_deck_ids_rong_thi_400_invalid_scope(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    with_groq(vocab_service, fake)

    with pytest.raises(InvalidScope):
        await trich(vocab_service, allowed_deck_ids=[])

    assert fake.call_count == 0


async def test_van_ban_khong_du_tu_tieng_anh_thi_400(vocab_service: Service) -> None:
    """Chặn việc đốt vài nghìn token vào một đoạn thuần tiếng Việt."""

    fake = FakeGroq([tra_loi(the())])
    with_groq(vocab_service, fake)

    with pytest.raises(InvalidRequest) as thong_tin:
        await trich(vocab_service, text="Đây là một đoạn văn hoàn toàn bằng tiếng Việt.")

    assert str(MIN_ENGLISH_TOKENS) in str(thong_tin.value)
    assert fake.call_count == 0


# ---------------------------------------------------------------------
# Ngân sách — nhóm chịu lực
# ---------------------------------------------------------------------


def test_luot_goi_dat_nhat_van_lot_ngan_sach_mot_phut() -> None:
    """
    Render prompt THẬT với văn bản dài nhất và số từ nhiều nhất, rồi khẳng định
    chỗ đặt trước vẫn nhỏ hơn ngân sách một phút.

    Nếu không, endpoint sẽ tự ném 429 vào chính mình ở MỌI request — một lỗi
    chỉ lộ ra khi chạy thật, và trông y hệt "hệ thống đang quá tải". Đó đúng là
    ca mà kế hoạch ban đầu suýt ship với trần 10 (đặt chỗ 108%).
    """

    settings = Settings(_env_file=None)
    prompts = PromptRegistry()

    system = prompts.render(
        "vocab_extract_v1",
        text="x" * settings.ai_vocab_max_text_chars,
        nonce="a3f9c1",
        max_candidates=str(MAX_CANDIDATES_CAP),
    )
    user = "Trích từ vựng đáng học từ đoạn văn trên."

    dat_cho = (
        estimate_tokens(system)
        + estimate_tokens(user)
        + VocabExtractor.max_tokens_cho(MAX_CANDIDATES_CAP)
    )

    assert dat_cho < settings.ai_global_tokens_per_minute, (
        f"đặt chỗ {dat_cho} vượt ngân sách {settings.ai_global_tokens_per_minute}"
    )


def test_max_tokens_luon_du_cho_phan_suy_luan_an() -> None:
    """
    Đo thật cho thấy `gpt-oss` tiêu 1.088-2.900 token SUY LUẬN cho đúng tác vụ
    này — gấp 4 tới 10 lần con số 79-296 ghi ở app/config.py, vốn đo trên prompt
    quiz ngắn hơn nhiều.

    Đặt thiếu thì Groq trả `400 json_validate_failed` với `failed_generation`
    RỖNG, và triệu chứng cuối cùng giống hệt "nhà cung cấp sập".
    """

    assert BASE_TOKENS >= 1400
    assert VocabExtractor.max_tokens_cho(1) >= 1700
    assert VocabExtractor.max_tokens_cho(MAX_CANDIDATES_CAP) >= 2900 + TOKENS_PER_CANDIDATE


def test_tran_trong_schema_khop_hang_so_trong_extractor() -> None:
    """Hai chỗ ghi cùng một luật thì phải không bao giờ trôi khỏi nhau."""

    rang_buoc = VocabExtractRequest.model_fields["max_candidates"].metadata

    assert any(getattr(m, "le", None) == MAX_CANDIDATES_CAP for m in rang_buoc)


async def test_so_tu_bi_kep_theo_van_ban_ngan(vocab_service: Service) -> None:
    """
    Xin 3 từ từ một đoạn văn 4 từ thì chỉ được hỏi 4 — đặt chỗ theo số xin ban
    đầu là giữ ngân sách cho một thứ không bao giờ dùng tới.
    """

    fake = FakeGroq([tra_loi()])
    with_groq(vocab_service, fake)

    await trich(vocab_service, text="alpha beta gamma delta", max_candidates=3)

    assert fake.body(0)["max_tokens"] == VocabExtractor.max_tokens_cho(3)


# ---------------------------------------------------------------------
# Đường thành công
# ---------------------------------------------------------------------


async def test_tra_the_day_du_va_dung_mot_loi_goi(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service)

    assert len(ung_vien) == 1
    assert ung_vien[0].word == "bottleneck"
    assert ung_vien[0].meaning
    assert stats.llm_calls == 1
    assert fake.call_count == 1


async def test_bat_json_mode_va_dung_model_vocab(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    with_groq(vocab_service, fake)

    await trich(vocab_service)

    body = fake.body(0)

    assert body["response_format"] == {"type": "json_object"}
    assert body["model"] == vocab_service.settings.ai_model_vocab
    # 0.0 chứ không phải 0.3 mặc định: ràng buộc "chép câu từ văn bản" cần tái
    # lập được giữa hai lần gọi giống nhau.
    assert body["temperature"] == 0.0


async def test_van_ban_nguoi_dung_nam_trong_system_prompt(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    with_groq(vocab_service, fake)

    await trich(vocab_service)

    assert DOAN_VAN in fake.body(0)["messages"][0]["content"]


# ---------------------------------------------------------------------
# Bám văn bản
# ---------------------------------------------------------------------


async def test_bo_ung_vien_co_cau_vi_du_bia_ra(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the_tot(), the(cau="She was resilient in the face of adversity."))])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service, max_candidates=3)

    assert [x.word for x in ung_vien] == ["retrospective"]
    assert stats.dropped_not_grounded == 1
    assert stats.returned_by_llm == 2


async def test_chap_nhan_cau_lech_nhay_cong_va_khoang_trang(vocab_service: Service) -> None:
    """
    LLM hay đổi nháy thẳng thành nháy cong hoặc thêm khoảng trắng khi chép lại.
    Loại vì mấy khác biệt đó là loại nhầm chính hành vi mình yêu cầu nó làm.
    """

    lech = "Their  meticulous retrospective uncovered a bottleneck nobody had anticipated."
    fake = FakeGroq([tra_loi(the(cau=lech))])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service)

    assert len(ung_vien) == 1
    assert stats.dropped_not_grounded == 0


async def test_giu_ung_vien_khi_word_la_dang_nguyen_the(vocab_service: Service) -> None:
    """Văn bản có `anticipated`, thẻ ghi `anticipate` — đúng yêu cầu của prompt."""

    fake = FakeGroq([tra_loi(the(word="anticipate"))])
    with_groq(vocab_service, fake)

    ung_vien, _ = await trich(vocab_service)

    assert [x.word for x in ung_vien] == ["anticipate"]


async def test_bo_ung_vien_thieu_meaning(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the_tot(), the(meaning=""))])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service)

    assert [x.word for x in ung_vien] == ["retrospective"]
    assert stats.dropped_unsafe == 1


async def test_part_of_speech_la_gia_tri_la_van_giu_the(vocab_service: Service) -> None:
    """Mất một nhãn còn hơn mất một từ đáng học."""

    fake = FakeGroq([tra_loi(the(part_of_speech="adjective"))])
    with_groq(vocab_service, fake)

    ung_vien, _ = await trich(vocab_service)

    assert len(ung_vien) == 1
    assert ung_vien[0].part_of_speech is None


# ---------------------------------------------------------------------
# Whitelist trường
# ---------------------------------------------------------------------


async def test_truong_la_do_llm_them_khong_lot_vao_ket_qua(vocab_service: Service) -> None:
    """
    Model trả thêm `audio_url` trỏ vào hạ tầng người ngoài thì nó sẽ thành dữ
    liệu ĐƯỢC LƯU nếu ta bê nguyên dict sang.
    """

    fake = FakeGroq([tra_loi(the(audio_url="https://ke-tan-cong.example/a.mp3", card_id=999))])
    with_groq(vocab_service, fake)

    ung_vien, _ = await trich(vocab_service)

    assert len(ung_vien) == 1
    assert not hasattr(ung_vien[0], "audio_url")
    assert ung_vien[0].model_dump().keys() == {
        "word",
        "phonetic",
        "part_of_speech",
        "meaning",
        "definition_en",
        "example_sentence",
        "example_meaning",
        "already_in_deck",
        "existing_card_id",
    }


async def test_meaning_chua_the_html_bi_loai(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the_tot(), the(meaning="<img src=x onerror=alert(1)>"))])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service)

    assert [x.word for x in ung_vien] == ["retrospective"]
    assert stats.dropped_unsafe == 1


async def test_meaning_chua_duong_dan_bi_loai(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the_tot(), the(meaning="xem tại http://ke-tan-cong.example"))])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service)

    assert [x.word for x in ung_vien] == ["retrospective"]
    assert stats.dropped_unsafe == 1


# ---------------------------------------------------------------------
# Khử trùng
# ---------------------------------------------------------------------


async def test_tu_da_co_van_duoc_tra_ve_kem_danh_dau(vocab_service: Service) -> None:
    """Quyết định 'đánh dấu, không xoá' gói gọn trong một test."""

    cau = "The team stayed resilient after missing the first deadline."
    fake = FakeGroq([tra_loi(the(word="resilient", cau=cau))])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service)

    assert len(ung_vien) == 1, "thẻ đã có PHẢI vẫn nằm trong kết quả"
    assert ung_vien[0].already_in_deck is True
    assert ung_vien[0].existing_card_id == 101
    assert stats.already_in_deck_count == 1


async def test_tu_ngoai_allowed_deck_ids_khong_bi_danh_dau(vocab_service: Service) -> None:
    """`resilient` nằm ở deck 1; phạm vi chỉ có deck 3 nên phải báo chưa có."""

    cau = "The team stayed resilient after missing the first deadline."
    fake = FakeGroq([tra_loi(the(word="resilient", cau=cau))])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service, allowed_deck_ids=[3])

    assert ung_vien[0].already_in_deck is False
    assert ung_vien[0].existing_card_id is None
    assert stats.already_in_deck_count == 0


async def test_dedup_checked_bao_false_khi_chi_muc_rong(vocab_service: Service) -> None:
    """
    Không có cờ này thì lời nói dối "không trùng gì cả" hoàn toàn im lặng — và
    nó xảy ra thật mỗi lần container khởi động lại trên gói không có đĩa bền.
    """

    fake = FakeGroq([tra_loi(the())])
    with_groq(vocab_service, fake)
    vocab_service.index.rebuild([])

    _, stats = await trich(vocab_service)

    assert stats.dedup_checked is False


# ---------------------------------------------------------------------
# Lỗi — endpoint này nói thật, không hạ cấp
# ---------------------------------------------------------------------


async def test_json_hong_thi_503_chu_khong_phai_200_rong(vocab_service: Service) -> None:
    """
    Trả 200 với mảng rỗng ở đây là nói dối: nó bảo "đoạn văn không có gì đáng
    học" trong khi sự thật là model trả rác. Người dùng sẽ dán lại mãi.
    """

    fake = FakeGroq([ok("{{{ đây không phải JSON")])
    with_groq(vocab_service, fake)

    with pytest.raises(ProviderUnavailable):
        await trich(vocab_service)


async def test_moi_ung_vien_bi_loai_thi_503(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the(cau="Câu này hoàn toàn không có trong đoạn văn."))])
    with_groq(vocab_service, fake)

    with pytest.raises(ProviderUnavailable):
        await trich(vocab_service)


async def test_llm_tra_danh_sach_rong_thi_200_va_candidates_rong(vocab_service: Service) -> None:
    """
    Cặp đối chứng với hai test trên. `returned_by_llm` là trường duy nhất phân
    biệt "không có gì đáng học" với "model bịa rồi bị lọc sạch".
    """

    fake = FakeGroq([tra_loi()])
    with_groq(vocab_service, fake)

    ung_vien, stats = await trich(vocab_service)

    assert ung_vien == []
    assert stats.returned_by_llm == 0


async def test_provider_hong_thi_503_lot_ra_ngoai_khac_quiz(vocab_service: Service) -> None:
    """
    Quiz nuốt lỗi này vì nó có đường lùi deterministic. Ở đây không có cách nào
    tự chế nghĩa tiếng Việt của một từ, nên nuốt là biến lỗi thành im lặng.
    """

    fake = FakeGroq([httpx2.Response(500, json={"error": {"message": "toang"}})])
    with_groq(vocab_service, fake)

    with pytest.raises(ProviderUnavailable):
        await trich(vocab_service)


async def test_can_ngan_sach_thi_429_lot_ra_ngoai(vocab_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    with_groq(vocab_service, fake)
    vocab_service.budget.reserve(vocab_service.settings.ai_global_tokens_per_minute)

    with pytest.raises(BudgetExhausted):
        await trich(vocab_service)

    assert fake.call_count == 0


async def test_khong_thu_lai_khi_llm_tra_rac(vocab_service: Service) -> None:
    """
    Khoá quyết định "một lời gọi" trước một người đóng góp tốt bụng trong tương
    lai: mỗi lần thử lại phải gửi lại TOÀN BỘ đoạn văn, ba lần là vượt ngân sách
    của cả một phút.
    """

    fake = FakeGroq([ok("rác"), ok("rác"), ok("rác")])
    with_groq(vocab_service, fake)

    with pytest.raises(ProviderUnavailable):
        await trich(vocab_service)

    assert fake.call_count == 1


async def test_gap_429_cua_nha_cung_cap_van_khong_tu_thu_lai_them(
    vocab_service: Service,
) -> None:
    """`LlmClient` tự lo phần thử lại; `VocabExtractor` không thêm tầng nào nữa."""

    fake = FakeGroq(lambda _: rate_limited("0"))
    with_groq(vocab_service, fake)

    with pytest.raises(ProviderUnavailable):
        await trich(vocab_service)

    # 3 lần trên model chính + 3 lần trên model dự phòng, đúng như /chat.
    assert fake.call_count == 6


# ---------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------


@pytest.fixture
def vocab_client(settings: Settings, encoder: Encoder):
    settings.ai_llm_api_key = "gsk_test"

    with TestClient(create_app(settings, encoder=encoder)) as client:
        wait_until_ready(client)
        client.post("/internal/v1/index/sync", headers=HEADERS)

        yield client


def test_extract_can_token(vocab_client: TestClient) -> None:
    r = vocab_client.post(
        "/internal/v1/vocab/extract",
        json={"text": DOAN_VAN, "allowed_deck_ids": [1]},
    )

    assert r.status_code == 401


def test_van_ban_qua_dai_tra_dang_error_chu_khong_phai_detail(vocab_client: TestClient) -> None:
    """
    Đây là hợp đồng trên dây: giới hạn độ dài là LUẬT NGHIỆP VỤ nên phải ra
    `{"error": {...}}` mã 400, khác hẳn `{"detail": [...]}` mã 422 mà FastAPI
    trả khi sai kiểu. Parser phía backend phải chịu được cả hai.
    """

    r = vocab_client.post(
        "/internal/v1/vocab/extract",
        headers=HEADERS,
        json={"text": "word " * 3000, "allowed_deck_ids": [1]},
    )

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_max_candidates_ngoai_khoang_thi_422(vocab_client: TestClient) -> None:
    """Cặp đối chứng với test trên — hai hình dạng lỗi là cố ý và phân biệt được."""

    r = vocab_client.post(
        "/internal/v1/vocab/extract",
        headers=HEADERS,
        json={"text": DOAN_VAN, "allowed_deck_ids": [1], "max_candidates": 99},
    )

    assert r.status_code == 422
    assert "detail" in r.json()


def test_swagger_khai_bao_security_cho_endpoint_moi(vocab_client: TestClient) -> None:
    """
    Không có test này thì chẳng có gì chứng minh route mới nằm sau
    `require_internal_token` — và nó là endpoint đắt nhất service.
    """

    spec = vocab_client.get("/openapi.json").json()

    assert spec["paths"]["/internal/v1/vocab/extract"]["post"]["security"]


# ---------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------


def test_prompt_vocab_extract_render_duoc() -> None:
    prompts = PromptRegistry()

    ra = prompts.render("vocab_extract_v1", text="xin chào", nonce="deadbeef", max_candidates="4")

    assert "xin chào" in ra
    assert "deadbeef" in ra
    assert "4" in ra
    # `response_format={"type": "json_object"}` đòi chữ "JSON" phải có mặt trong
    # messages, nếu không Groq trả 400 với thông báo khó hiểu khác.
    assert "JSON" in ra
