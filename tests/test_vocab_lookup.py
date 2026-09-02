"""
Test cho `POST /internal/v1/vocab/lookup` (M10).

Nhóm chịu lực ở đây KHÁC hai endpoint kia, vì ràng buộc chi phối cũng khác: đây
là một CÁI NÚT BẤM, không phải một thao tác người dùng ngồi chờ. Thứ phải khoá
lại là hai ĐƯỜNG 0 TOKEN — chúng là lý do cái nút này khả thi trên ngân sách
6.400 token mỗi phút, chứ không phải một tối ưu hoá cho vui.

Và một nhóm không endpoint nào khác có: CACHE DÙNG CHUNG. Nó an toàn vì nghĩa
của một từ không phụ thuộc bộ thẻ của ai — nhưng "an toàn" đó chỉ đúng nếu hai
cờ khử trùng KHÔNG bị lưu vào cache. `test_cache_khong_ro_ri_co_khu_trung_giua_hai_nguoi_dung`
là chỗ khoá điều đó lại.
"""

import json as jsonlib

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import BudgetExhausted, InvalidRequest, InvalidScope, ProviderUnavailable
from app.embedding.encoder import Encoder
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.main import Service, create_app
from app.schemas.vocab import VocabLookupRequest
from app.vocab.lookup import MAX_CONTEXT_CHARS, MAX_TOKENS, MAX_WORD_INPUT_CHARS
from app.vocab.word_cache import WordCache, khoa_cache
from tests.test_llm_client import FakeGroq, ok, rate_limited
from tests.test_security import HEADERS, wait_until_ready


def tra_loi(
    *,
    found: bool = True,
    word: str = "donut",
    cau: str | None = None,
    suggestion: str = "",
    **ghi_de,
) -> httpx2.Response:
    payload = {
        "found": found,
        "suggestion": suggestion,
        "word": word,
        "phonetic": "/ˈdoʊnət/",
        "part_of_speech": "noun",
        "meaning": "bánh vòng",
        "definition_en": "a small ring-shaped fried cake",
        "example_sentence": cau or f"He ate a {word} with his morning coffee.",
        "example_meaning": "Anh ấy ăn một chiếc bánh vòng cùng cà phê sáng.",
    }
    payload.update(ghi_de)

    return ok(jsonlib.dumps(payload, ensure_ascii=False))


@pytest.fixture
def tra_service(synced_service: Service) -> Service:
    synced_service.settings.ai_llm_api_key = "gsk_test"
    # Cache sống hết đời tiến trình nên phải dọn giữa các test, nếu không test
    # này ăn kết quả của test kia và cả nhóm thành vô nghĩa.
    synced_service.vocab_lookup._cache.clear()

    return synced_service


def with_groq(service: Service, fake: FakeGroq) -> Service:
    service.llm = LlmClient(
        service.settings, service.budget, service.usage_repo, http_client=fake.as_client()
    )
    service.vocab_lookup._llm = service.llm

    return service


async def tra(service: Service, **ghi_de):
    body = {"word": "donut", "allowed_deck_ids": [1, 2, 3, 4]}
    body.update(ghi_de)

    return await service.vocab_lookup.lookup(VocabLookupRequest(**body))


# ---------------------------------------------------------------------
# Tiền xử lý — không được chạm tới Groq
# ---------------------------------------------------------------------


async def test_chuoi_khong_the_la_mot_tu_thi_400(tra_service: Service) -> None:
    """Chặn miễn phí: không lời gọi LLM nào cứu được một chuỗi như vậy."""

    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    for xau in ("123", "a.b.c", "one two three four", "", "!!!"):
        with pytest.raises(InvalidRequest):
            await tra(service, word=xau)

    assert fake.call_count == 0


async def test_tu_qua_dai_thi_400_kem_ca_hai_con_so(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    with pytest.raises(InvalidRequest) as loi:
        await tra(service, word="x" * (MAX_WORD_INPUT_CHARS + 1))

    assert str(MAX_WORD_INPUT_CHARS + 1) in loi.value.message
    assert str(MAX_WORD_INPUT_CHARS) in loi.value.message
    assert fake.call_count == 0


async def test_allowed_deck_ids_rong_thi_400_invalid_scope(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    with pytest.raises(InvalidScope):
        await tra(service, allowed_deck_ids=[])

    assert fake.call_count == 0


async def test_sai_chinh_ta_KHONG_bi_chan_o_400(tra_service: Service) -> None:
    """
    `recieve` là chuỗi hợp lệ về hình dạng, chỉ sai chính tả.

    Nó phải đi tiếp tới LLM để nhận `found: false` kèm gợi ý, chứ không bị chặn
    ở 400 — chặn ở đó là nhầm "gõ sai" với "gửi rác", và người dùng mất luôn gợi
    ý chính tả vốn là thứ họ cần nhất lúc ấy.
    """

    fake = FakeGroq([tra_loi(found=False, suggestion="receive")])
    service = with_groq(tra_service, fake)

    source, found, goi_y, the, _ = await tra(service, word="recieve")

    assert (source, found, goi_y, the) == ("AI", False, "receive", None)
    assert fake.call_count == 1


async def test_ngu_canh_qua_dai_bi_CAT_chu_khong_bao_loi(tra_service: Service) -> None:
    """
    Cố ý khác `topic` của `/vocab/generate`, vốn vượt độ dài là 400.

    Ở đó chủ đề LÀ toàn bộ yêu cầu nên cắt bớt là đổi ý người dùng. Ở đây ngữ
    cảnh chỉ để chọn nghĩa, mất phần đuôi vẫn dùng được — bắt người đang bôi đen
    một câu dài phải xử lý lỗi 400 thì vô lý.
    """

    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    source, _, _, _, _ = await tra(service, context="y" * (MAX_CONTEXT_CHARS * 3))
    user = fake.body(0)["messages"][1]["content"]

    assert source == "AI"
    assert len(user) < MAX_CONTEXT_CHARS * 2


# ---------------------------------------------------------------------
# Đường 0 token — nhóm chịu lực
# ---------------------------------------------------------------------


async def test_tu_da_co_trong_deck_thi_0_token(tra_service: Service) -> None:
    """
    `resilient` là thẻ 101 của bộ thẻ mẫu.

    Đây là câu trả lời đáng giá nhất trong ba loại, và không phải vì miễn phí:
    người dùng đang ở màn hình SOẠN THẺ MỚI, nên biết mình sắp tạo thẻ trùng
    hữu ích hơn hẳn một thẻ mới.
    """

    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    source, found, _, the, stats = await tra(service, word="resilient")

    assert source == "YOUR_DECK"
    assert found is True
    assert the is not None
    assert the.already_in_deck is True
    assert the.existing_card_id == 101
    assert the.meaning
    assert stats.llm_calls == 0
    assert fake.call_count == 0


async def test_tu_ngoai_pham_vi_KHONG_di_duong_0_token(tra_service: Service) -> None:
    """Ranh giới phạm vi: `resilient` ở deck 1, xin deck 3 thì phải trả tiền như từ mới."""

    fake = FakeGroq([tra_loi(word="resilient", cau="She stayed resilient all week.")])
    service = with_groq(tra_service, fake)

    source, _, _, the, _ = await tra(service, word="resilient", allowed_deck_ids=[3])

    assert source == "AI"
    assert the is not None
    assert the.already_in_deck is False
    assert the.existing_card_id is None
    assert fake.call_count == 1


async def test_luot_thu_hai_cung_tu_thi_an_cache(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    dau, _, _, _, _ = await tra(service)
    sau, _, _, the, stats = await tra(service)

    assert (dau, sau) == ("AI", "CACHE")
    assert the is not None
    assert the.word == "donut"
    assert stats.llm_calls == 0
    assert fake.call_count == 1


async def test_ngu_canh_khac_nhau_la_hai_muc_cache_khac_nhau(tra_service: Service) -> None:
    """`bank` trong câu về dòng sông khác `bank` trong câu về tiền."""

    fake = FakeGroq(
        [
            tra_loi(word="bank", cau="They sat on the river bank at sunset."),
            tra_loi(word="bank", cau="She opened an account at the bank downtown."),
        ]
    )
    service = with_groq(tra_service, fake)

    await tra(service, word="bank", context="They sat on the river bank.")
    source, _, _, _, _ = await tra(service, word="bank", context="She works at a bank.")

    assert source == "AI"
    assert fake.call_count == 2


async def test_cache_khong_ro_ri_co_khu_trung_giua_hai_nguoi_dung(tra_service: Service) -> None:
    """
    Cache dùng chung TOÀN CỤC, nên nó không được mang theo cờ của riêng ai.

    `already_in_deck` và `existing_card_id` phụ thuộc phạm vi deck của TỪNG
    người dùng. Lưu chúng vào cache thì người thứ hai nhận một `existing_card_id`
    trỏ vào thẻ họ không có quyền thấy — đúng loại lỗi mà `scope_hash` của
    `SemanticCache` sinh ra để chặn.
    """

    fake = FakeGroq([tra_loi(word="deadline", cau="We missed the deadline by two days.")])
    service = with_groq(tra_service, fake)

    # Người thứ nhất có deck 2, nên `deadline` (thẻ 201) sẽ được đánh dấu.
    # Dùng phạm vi [3] cho lượt gọi để không rơi vào đường YOUR_DECK, rồi mới
    # đánh dấu — đây đúng là tình huống thật khi hai người có phạm vi khác nhau.
    _, _, _, the_a, _ = await tra(service, word="deadline", allowed_deck_ids=[3])

    assert the_a is not None
    assert the_a.already_in_deck is False

    # Người thứ hai KHÔNG có deck nào chứa `deadline`.
    source, _, _, the_b, _ = await tra(service, word="deadline", allowed_deck_ids=[4])

    assert source == "CACHE"
    assert the_b is not None
    assert the_b.already_in_deck is False
    assert the_b.existing_card_id is None


async def test_cache_giu_ban_sao_chu_khong_giu_tham_chieu(tra_service: Service) -> None:
    """Sửa thẻ trả về không được làm hỏng mục trong cache của người sau."""

    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    _, _, _, the_a, _ = await tra(service)
    assert the_a is not None
    the_a.meaning = "BỊ SỬA"

    _, _, _, the_b, _ = await tra(service)

    assert the_b is not None
    assert the_b.meaning == "bánh vòng"


# ---------------------------------------------------------------------
# Ngân sách
# ---------------------------------------------------------------------


def test_luot_goi_dat_nhat_van_lot_ngan_sach_mot_phut() -> None:
    """
    Endpoint này là một cái nút bấm, nên nó phải chiếm PHẦN NHỎ ngân sách chứ
    không chỉ là "lọt".

    Ngưỡng 40% chọn để hai lượt tra đồng thời cộng một lượt /chat (~1.700) vẫn
    vừa. Đỏ khi ai đó nâng `MAX_TOKENS`, nới `MAX_CONTEXT_CHARS`, hay chỉ đơn
    giản là thêm một đoạn vào file prompt.
    """

    from app.llm.budget import estimate_tokens

    settings = Settings(_env_file=None)
    prompts = PromptRegistry()

    system = prompts.render("vocab_lookup_system_v1")
    user = prompts.render(
        "vocab_lookup_user_v1",
        nonce="a3f9c1d2",
        word="w" * 32,
        context="x" * MAX_CONTEXT_CHARS,
    )

    dat_cho = estimate_tokens(system) + estimate_tokens(user) + MAX_TOKENS
    han_muc = settings.ai_global_tokens_per_minute

    assert dat_cho <= int(0.40 * han_muc), (
        f"đặt chỗ {dat_cho} trên ngân sách {han_muc} — quá đắt cho một endpoint "
        f"được bấm liên tục; hai lượt tra đồng thời sẽ không còn chỗ cho /chat"
    )


def test_max_tokens_du_cho_phan_suy_luan_an() -> None:
    """Mẫu đắt nhất đo được là 735 token completion. Giữ ít nhất gấp rưỡi."""

    assert MAX_TOKENS >= 1100


# ---------------------------------------------------------------------
# Đường AI
# ---------------------------------------------------------------------


async def test_tra_the_day_du_va_dung_mot_loi_goi(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    source, found, goi_y, the, stats = await tra(service)

    assert (source, found, goi_y) == ("AI", True, None)
    assert the is not None
    assert the.word == "donut"
    assert the.part_of_speech == "noun"
    assert the.phonetic == "/ˈdoʊnət/"
    assert stats.llm_calls == 1


async def test_bat_json_mode_va_dung_model_vocab(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)

    await tra(service)
    body = fake.body(0)

    assert body["response_format"] == {"type": "json_object"}
    assert body["model"] == service.settings.ai_model_vocab
    assert body["max_tokens"] == MAX_TOKENS


async def test_tu_va_ngu_canh_nam_o_luot_user(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi(word="bank", cau="The river bank was muddy after the rain.")])
    service = with_groq(tra_service, fake)

    await tra(service, word="bank", context="They sat on the river bank.")
    system, user = fake.body(0)["messages"]

    assert "bank" in user["content"]
    assert "river bank" in user["content"]
    assert "river bank" not in system["content"]


async def test_dang_chia_duoc_quy_ve_dang_tu_dien(tra_service: Service) -> None:
    """Gõ `Donuts`, nhận về `donut`. Thẻ từ vựng cần dạng gốc."""

    fake = FakeGroq([tra_loi(word="donut")])
    service = with_groq(tra_service, fake)

    _, _, _, the, _ = await tra(service, word="Donuts")

    assert the is not None
    assert the.word == "donut"


async def test_model_tra_ve_TU_KHAC_thi_thanh_goi_y_chinh_ta(tra_service: Service) -> None:
    """
    Model bảo "tìm thấy" nhưng trả về một từ KHÁC. Người dùng không bao giờ được
    nhận thẻ của từ khác — nhưng đây cũng không phải lỗi hạ tầng.

    Một lượt CHẠY THẬT dạy ra điều này: hỏi `recieve`, model tự sửa thành
    `receive` rồi trả `found: true`. Bản đầu quy nó về `503`, tức biến một lần
    sửa chính tả ĐÚNG thành "trợ lý AI hỏng" và giấu mất chính thứ người dùng
    cần nhất lúc đó. Giờ nó được gọi đúng tên: "không có từ bạn gõ, ý bạn là X?"
    """

    fake = FakeGroq([tra_loi(word="receive", cau="I will receive the parcel tomorrow.")])
    service = with_groq(tra_service, fake)

    source, found, goi_y, the, _ = await tra(service, word="recieve")

    assert (source, found, goi_y, the) == ("AI", False, "receive", None)


async def test_tu_khac_khong_bi_dua_vao_cache(tra_service: Service) -> None:
    """Không có thẻ thì không có gì để cache — kể cả khi model bảo là tìm thấy."""

    fake = FakeGroq([tra_loi(word="receive", cau="I will receive the parcel tomorrow.")] * 2)
    service = with_groq(tra_service, fake)

    await tra(service, word="recieve")
    source, _, _, _, _ = await tra(service, word="recieve")

    assert source == "AI"
    assert fake.call_count == 2


async def test_goi_y_chinh_ta_khong_hop_le_thi_bo(tra_service: Service) -> None:
    """`suggestion` cũng là chữ model sinh ra, phải qua đúng chốt chặn như `word`."""

    fake = FakeGroq([tra_loi(found=False, suggestion="<script>alert(1)</script>")])
    service = with_groq(tra_service, fake)

    _, found, goi_y, the, _ = await tra(service, word="recieve")

    assert (found, goi_y, the) == (False, None, None)


async def test_found_false_khong_bi_dua_vao_cache(tra_service: Service) -> None:
    """
    Không có thẻ thì không có gì để cache.

    Và cache một câu "không tìm thấy" là tự khoá mình lại: model lần sau có thể
    trả lời đúng, nhất là sau khi đổi prompt hay đổi model.
    """

    fake = FakeGroq([tra_loi(found=False, suggestion="receive"), tra_loi(found=False)])
    service = with_groq(tra_service, fake)

    await tra(service, word="recieve")
    source, _, _, _, _ = await tra(service, word="recieve")

    assert source == "AI"
    assert fake.call_count == 2


# ---------------------------------------------------------------------
# Chốt chặn an toàn — mọi trường đều do model bịa
# ---------------------------------------------------------------------


async def test_meaning_chua_the_html_thi_503(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi(meaning="<img src=x onerror=alert(1)>")])
    service = with_groq(tra_service, fake)

    with pytest.raises(ProviderUnavailable):
        await tra(service)


async def test_truong_chua_thuoc_tinh_html_thi_503(tra_service: Service) -> None:
    """Chuỗi này lọt qua denylist của M8 — allowlist chặn vì `=` không được kể tên."""

    fake = FakeGroq([tra_loi(cau='He ate a donut" autofocus onfocus=alert(1) x="')])
    service = with_groq(tra_service, fake)

    with pytest.raises(ProviderUnavailable):
        await tra(service)


async def test_meaning_khong_co_dau_tieng_viet_thi_503(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi(meaning="a ring-shaped cake")])
    service = with_groq(tra_service, fake)

    with pytest.raises(ProviderUnavailable):
        await tra(service)


async def test_cau_vi_du_khong_chua_tu_thi_503(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi(cau="The weather was pleasant all week.")])
    service = with_groq(tra_service, fake)

    with pytest.raises(ProviderUnavailable):
        await tra(service)


async def test_truong_la_do_llm_them_khong_lot_vao_ket_qua(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi(audio_url="http://evil.test/a.mp3", card_id=999)])
    service = with_groq(tra_service, fake)

    _, _, _, the, _ = await tra(service)

    assert the is not None
    assert set(the.model_dump()) == {
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


async def test_phien_am_hong_thi_bo_nhan_chu_khong_bo_the(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi(phonetic="doh nut")])
    service = with_groq(tra_service, fake)

    _, _, _, the, _ = await tra(service)

    assert the is not None
    assert the.phonetic is None


# ---------------------------------------------------------------------
# Lỗi
# ---------------------------------------------------------------------


async def test_json_hong_thi_503(tra_service: Service) -> None:
    fake = FakeGroq([ok("đây không phải JSON")])
    service = with_groq(tra_service, fake)

    with pytest.raises(ProviderUnavailable):
        await tra(service)


async def test_provider_hong_thi_503_lot_ra_ngoai(tra_service: Service) -> None:
    fake = FakeGroq([httpx2.Response(500, json={"error": {"message": "boom"}})])
    service = with_groq(tra_service, fake)

    with pytest.raises(ProviderUnavailable):
        await tra(service)


async def test_can_ngan_sach_thi_429_lot_ra_ngoai(tra_service: Service) -> None:
    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)
    service.budget.reserve(service.settings.ai_global_tokens_per_minute)

    with pytest.raises(BudgetExhausted):
        await tra(service)

    assert fake.call_count == 0


async def test_duong_0_token_van_chay_khi_ngan_sach_da_can(tra_service: Service) -> None:
    """
    Ngân sách cạn KHÔNG được làm chết đường 0 token.

    Đây là điều dễ mất nhất khi ai đó "dọn dẹp" bằng cách chuyển phép kiểm ngân
    sách lên đầu hàm cho gọn. Từ người dùng đã có thì không tốn gì cả, nên hết
    ngân sách vẫn phải trả lời được.
    """

    fake = FakeGroq([tra_loi()])
    service = with_groq(tra_service, fake)
    service.budget.reserve(service.settings.ai_global_tokens_per_minute)

    source, found, _, the, _ = await tra(service, word="resilient")

    assert source == "YOUR_DECK"
    assert found is True
    assert the is not None
    assert fake.call_count == 0


async def test_khong_thu_lai_khi_llm_tra_rac(tra_service: Service) -> None:
    fake = FakeGroq([ok("rác"), ok("rác"), ok("rác")])
    service = with_groq(tra_service, fake)

    with pytest.raises(ProviderUnavailable):
        await tra(service)

    assert fake.call_count == 1


async def test_gap_429_cua_nha_cung_cap_van_khong_tu_thu_lai_them(tra_service: Service) -> None:
    fake = FakeGroq(lambda _: rate_limited())
    service = with_groq(tra_service, fake)

    with pytest.raises(ProviderUnavailable):
        await tra(service)

    assert fake.call_count == 6


# ---------------------------------------------------------------------
# WordCache — hàm thuần
# ---------------------------------------------------------------------


def test_cache_duoi_theo_lru_khi_day() -> None:
    from app.schemas.vocab import VocabCandidate

    cache = WordCache(max_size=2)

    def the(w: str) -> VocabCandidate:
        return VocabCandidate(
            word=w, meaning="nghĩa", example_sentence=f"A {w} here.", already_in_deck=False
        )

    cache.put(khoa_cache("a", ""), the("a"))
    cache.put(khoa_cache("b", ""), the("b"))
    cache.get(khoa_cache("a", ""))  # chạm vào "a" -> "b" thành cũ nhất
    cache.put(khoa_cache("c", ""), the("c"))

    assert cache.size == 2
    assert cache.get(khoa_cache("a", "")) is not None
    assert cache.get(khoa_cache("b", "")) is None


def test_khoa_cache_phan_biet_theo_ngu_canh() -> None:
    assert khoa_cache("bank", "river") != khoa_cache("bank", "money")
    assert khoa_cache("bank", "river") == khoa_cache("bank", "river")


# ---------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------


@pytest.fixture
def tra_client(settings: Settings, encoder: Encoder):
    settings.ai_llm_api_key = "gsk_test"

    with TestClient(create_app(settings, encoder=encoder)) as client:
        wait_until_ready(client)
        client.post("/internal/v1/index/sync", headers=HEADERS)

        yield client


def test_lookup_can_token(tra_client: TestClient) -> None:
    tra_ve = tra_client.post(
        "/internal/v1/vocab/lookup", json={"word": "donut", "allowed_deck_ids": [1]}
    )

    assert tra_ve.status_code == 401


def test_tu_da_co_tra_ve_qua_http_khong_ton_token(tra_client: TestClient) -> None:
    tra_ve = tra_client.post(
        "/internal/v1/vocab/lookup",
        headers=HEADERS,
        json={"word": "resilient", "allowed_deck_ids": [1, 2, 3, 4]},
    )

    assert tra_ve.status_code == 200
    body = tra_ve.json()
    assert body["source"] == "YOUR_DECK"
    assert body["found"] is True
    assert body["card"]["existing_card_id"] == 101
    assert body["stats"]["llm_calls"] == 0


def test_chuoi_rac_tra_dang_error_chu_khong_phai_detail(tra_client: TestClient) -> None:
    tra_ve = tra_client.post(
        "/internal/v1/vocab/lookup",
        headers=HEADERS,
        json={"word": "12345", "allowed_deck_ids": [1]},
    )

    assert tra_ve.status_code == 400
    assert tra_ve.json()["error"]["code"] == "INVALID_REQUEST"


def test_swagger_khai_bao_security_cho_endpoint_moi(tra_client: TestClient) -> None:
    spec = tra_client.get("/openapi.json").json()

    assert spec["paths"]["/internal/v1/vocab/lookup"]["post"]["security"]


def test_ba_endpoint_vocab_dung_chung_mot_schema_the(tra_client: TestClient) -> None:
    """Phép kiểm máy cho quyết định dùng chung `VocabCandidate` ở cả BA endpoint."""

    thanh_phan = tra_client.get("/openapi.json").json()["components"]["schemas"]
    ref = "#/components/schemas/VocabCandidate"

    assert thanh_phan["VocabExtractResponse"]["properties"]["candidates"]["items"]["$ref"] == ref
    assert thanh_phan["VocabGenerateResponse"]["properties"]["cards"]["items"]["$ref"] == ref
    assert ref in jsonlib.dumps(thanh_phan["VocabLookupResponse"]["properties"]["card"])


# ---------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------


def test_prompt_vocab_lookup_render_duoc() -> None:
    prompts = PromptRegistry()

    system = prompts.render("vocab_lookup_system_v1")
    user = prompts.render(
        "vocab_lookup_user_v1", nonce="a3f9c1d2", word="donut", context="(không có)"
    )

    # `response_format={"type": "json_object"}` đòi chữ "JSON" trong prompt.
    assert "JSON" in system
    assert "found" in system
    assert "donut" in user
    assert "a3f9c1d2" in user
    assert "{{" not in system + user


def test_lookup_khong_dung_semaphore_nang(service: Service) -> None:
    """
    CỐ Ý không xếp hàng chung với `extract`/`generate`.

    Xếp một cái nút bấm sau hàng đợi của một lượt trích xuất 5 giây là làm nó
    trông như bị treo. Lượt tra rẻ hơn nhiều nên `TokenBudget` một mình là đủ.
    """

    assert not hasattr(service.vocab_lookup, "_sem")
