"""
Test cho `POST /internal/v1/vocab/generate` (M9).

Ba nhóm chịu lực, và chúng khác nhóm chịu lực của M8:

NGÂN SÁCH — `test_luot_goi_dat_nhat_van_lot_ngan_sach_mot_phut` render prompt
THẬT ở cấu hình xấu nhất. Nó có HAI khẳng định chứ không phải một: cái thứ nhất
bắt lỗi thảm hoạ (tự 429 mọi request), cái thứ hai bắt lỗi thoái hoá (còn chưa
đủ chỗ cho một lượt /chat). M8 chỉ có cái thứ nhất, và nó vẫn xanh ở 99%.

DANH SÁCH TRÁNH — không có tương đương ở M8. Quan trọng nhất là
`test_danh_sach_tranh_khong_bao_gio_ra_ngoai_allowed_deck_ids`: nó kiểm ranh
giới phạm vi TRÊN PROMPT, tức trước khi model kịp nhìn thấy dữ liệu.

VẮNG MẶT — `test_khong_ap_kiem_bam_van_ban` ghi lại một thứ KHÔNG có, để không
ai "sửa" M9 bằng cách import `sentence_is_from_text` vào.
"""

import asyncio
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
from app.schemas.vocab import VocabGenerateRequest
from app.vocab.generator import (
    AVOID_LIST_SIZE,
    AVOID_WORD_CHARS,
    BASE_TOKENS,
    MAX_CARDS_CAP,
    MAX_TOPIC_CHARS,
    TOKENS_PER_CARD,
    VocabGenerator,
)
from tests.test_llm_client import FakeGroq, ok, rate_limited
from tests.test_security import HEADERS, wait_until_ready

# Trần thiết kế: đặt chỗ ở cấu hình xấu nhất không được vượt tỷ lệ này của ngân
# sách một phút. Đây là con số tròn lớn nhất mà vẫn còn chỗ cho MỘT lượt /chat
# (~1.700 token) chạy song song — xem chú thích `MAX_CARDS_CAP`.
TRAN_THIET_KE = 0.70

CHU_DE = "tôi muốn học từ về du lịch hàng không"


def the(word: str = "itinerary", cau: str | None = None, **ghi_de) -> dict:
    muc = {
        "word": word,
        "phonetic": "/aɪˈtɪnərəri/",
        "part_of_speech": "noun",
        "meaning": "lịch trình chuyến đi",
        "definition_en": "a planned route or journey",
        "example_sentence": cau or f"She printed the {word} before leaving home.",
        "example_meaning": "Cô ấy in lịch trình trước khi rời nhà.",
    }
    muc.update(ghi_de)

    return muc


def tra_loi(*muc: dict, hieu: str = "Du lịch hàng không") -> httpx2.Response:
    payload = {"topic_understood": hieu, "words": list(muc)}

    return ok(jsonlib.dumps(payload, ensure_ascii=False))


@pytest.fixture
def gen_service(synced_service: Service) -> Service:
    """
    Dựng trên `synced_service`, KHÔNG phải `retrieval_service`.

    `retrieval_service` thêm `intent_classifier.warmup()` — một lượt suy luận
    thật cho mỗi test — mà M9 không hề đụng tới `IntentClassifier`.
    """

    synced_service.settings.ai_llm_api_key = "gsk_test"

    return synced_service


def with_groq(service: Service, fake: FakeGroq) -> Service:
    service.llm = LlmClient(
        service.settings, service.budget, service.usage_repo, http_client=fake.as_client()
    )
    service.vocab_generator._llm = service.llm

    return service


async def sinh(service: Service, **ghi_de):
    body = {"topic": CHU_DE, "allowed_deck_ids": [1, 2, 3, 4], "count": 3}
    body.update(ghi_de)

    return await service.vocab_generator.generate(VocabGenerateRequest(**body))


# ---------------------------------------------------------------------
# Tiền xử lý — không được chạm tới Groq, cũng không được chạm tới encoder
# ---------------------------------------------------------------------


async def test_chu_de_qua_dai_thi_400_kem_ca_hai_con_so(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    with pytest.raises(InvalidRequest) as loi:
        await sinh(service, topic="x" * (MAX_TOPIC_CHARS + 1))

    assert str(MAX_TOPIC_CHARS + 1) in loi.value.message
    assert str(MAX_TOPIC_CHARS) in loi.value.message
    assert fake.call_count == 0


async def test_allowed_deck_ids_rong_thi_400_invalid_scope(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    with pytest.raises(InvalidScope):
        await sinh(service, allowed_deck_ids=[])

    assert fake.call_count == 0


async def test_chu_de_khong_co_chu_cai_thi_400(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    with pytest.raises(InvalidRequest):
        await sinh(service, topic="7 42 -- 99")

    assert fake.call_count == 0


async def test_chu_de_tieng_viet_van_chay_binh_thuong(gen_service: Service) -> None:
    """
    ĐẢO NGƯỢC CÓ CHỦ Ý so với M8.

    M8 từ chối một đoạn thuần tiếng Việt (`MIN_ENGLISH_TOKENS`) vì nó cần từ
    tiếng Anh để trích. Ở đây chủ đề tiếng Việt là CA DÙNG CHÍNH, không phải ca
    lỗi. Test này tồn tại để chặn ai đó copy luật kia sang.
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service, topic="hợp đồng thuê nhà và thủ tục giấy tờ")

    assert len(the_ra) == 1
    assert stats.llm_calls == 1


# ---------------------------------------------------------------------
# Ngân sách — nhóm chịu lực
# ---------------------------------------------------------------------


def test_luot_goi_dat_nhat_van_lot_ngan_sach_mot_phut() -> None:
    """
    Render prompt THẬT ở cấu hình xấu nhất mọi đầu vào có thể đạt CÙNG LÚC.

    Đỏ khi: nâng `MAX_CARDS_CAP`, nâng `MAX_TOPIC_CHARS`, nâng `AVOID_LIST_SIZE`
    hay bỏ trần độ dài mỗi từ, nâng `BASE_TOKENS`/`TOKENS_PER_CARD` sau một lần
    đo lại, hạ `ai_global_tokens_per_minute` — và cả khi ai đó chỉ thêm một đoạn
    văn vào file prompt. Cái cuối là lý do test này phải render file `.txt` thật
    chứ không được dùng một chuỗi viết tay.
    """

    settings = Settings(_env_file=None)
    prompts = PromptRegistry()

    system = prompts.render(
        "vocab_generate_system_v1",
        count=str(MAX_CARDS_CAP),
        level="không giới hạn",
    )
    user = prompts.render(
        "vocab_generate_user_v1",
        # `secrets.token_hex(4)` cho 8 ký tự, không phải 6.
        nonce="a3f9c1d2",
        avoid_list=", ".join(["w" * AVOID_WORD_CHARS] * AVOID_LIST_SIZE),
        topic="x" * MAX_TOPIC_CHARS,
    )

    dat_cho = (
        estimate_tokens(system)
        + estimate_tokens(user)
        + VocabGenerator.max_tokens_cho(MAX_CARDS_CAP)
    )
    han_muc = settings.ai_global_tokens_per_minute

    assert dat_cho < han_muc, (
        f"đặt chỗ {dat_cho} vượt ngân sách {han_muc} — endpoint sẽ tự 429 chính "
        f"mình ở MỌI request, kể cả khi hệ thống đang rảnh"
    )
    assert dat_cho <= int(TRAN_THIET_KE * han_muc), (
        f"đặt chỗ {dat_cho}, chỉ còn {han_muc - dat_cho} token trong phút — không "
        f"đủ cho một lượt /chat (~1.700), nên mọi người chat trong lúc endpoint "
        f"này chạy sẽ ăn 429"
    )


def test_max_tokens_du_cho_phan_suy_luan_an() -> None:
    """gpt-oss trừ `reasoning_tokens` ẩn vào chính hạn mức này."""

    assert BASE_TOKENS >= 1200
    assert VocabGenerator.max_tokens_cho(1) >= 1500
    assert VocabGenerator.max_tokens_cho(MAX_CARDS_CAP) >= BASE_TOKENS + TOKENS_PER_CARD


def test_tran_trong_schema_khop_hang_so_trong_generator() -> None:
    """Hai chỗ nói một luật thì không bao giờ được lệch nhau."""

    rang_buoc = VocabGenerateRequest.model_fields["count"].metadata

    assert any(getattr(r, "le", None) == MAX_CARDS_CAP for r in rang_buoc)


async def test_count_khong_bi_kep_theo_do_dai_chu_de(gen_service: Service) -> None:
    """
    NGƯỢC với M8, nơi `wanted` bị kẹp theo số từ tiếng Anh có trong đoạn văn.

    Ở đây không có văn bản nguồn nên không có gì để kẹp: chủ đề ba ký tự vẫn
    đặt chỗ đủ cho `count` thẻ. Test này khoá lại việc "đừng copy phép kẹp kia".
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    await sinh(service, topic="ăn", count=3)

    assert fake.body(0)["max_tokens"] == VocabGenerator.max_tokens_cho(3)


async def test_danh_sach_tranh_bi_kep_kich_thuoc(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    _, _, stats = await sinh(service, exclude_words=[f"word{i}" for i in range(200)])

    assert stats.avoid_list_size <= AVOID_LIST_SIZE


async def test_tu_qua_dai_trong_deck_khong_thoi_phong_prompt(gen_service: Service) -> None:
    """
    Một `word` khổng lồ trong deck của ai đó là một đòn từ chối dịch vụ.

    `estimate_tokens` tính trên prompt ĐÃ RENDER, nên nếu danh sách tránh không
    cắt độ dài từng mục thì chỉ cần một thẻ có `word` dài vài nghìn ký tự là chỗ
    đặt trước vượt 6.400 và TOÀN BỘ service tự 429.
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    _, _, stats = await sinh(service, exclude_words=["a" * 4000, "b" * 4000])

    system = fake.body(0)["messages"][0]["content"]
    user = fake.body(0)["messages"][1]["content"]

    assert "a" * 100 not in user
    assert stats.avoid_list_dropped == 2
    assert estimate_tokens(system) + estimate_tokens(user) < 2000


# ---------------------------------------------------------------------
# Đường thành công
# ---------------------------------------------------------------------


async def test_tra_the_day_du_va_dung_mot_loi_goi(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    hieu, the_ra, stats = await sinh(service)

    assert hieu == "Du lịch hàng không"
    assert the_ra[0].word == "itinerary"
    assert the_ra[0].meaning
    assert the_ra[0].part_of_speech == "noun"
    assert stats.llm_calls == 1
    assert fake.call_count == 1


async def test_bat_json_mode_va_dung_model_vocab(gen_service: Service) -> None:
    """
    `temperature` KHÔNG ghim 0.0 — cố ý khác M8.

    M8 ghim 0.0 vì ràng buộc "câu ví dụ phải lấy từ đoạn văn" cần tái lập được.
    Ở đây 0.0 sẽ khiến nút "sinh thêm" trả về đúng ngần ấy từ cũ mỗi lần bấm.
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    await sinh(service)
    body = fake.body(0)

    assert body["response_format"] == {"type": "json_object"}
    assert body["model"] == service.settings.ai_model_vocab
    assert body["temperature"] == service.settings.ai_temperature


async def test_chu_de_nam_o_luot_user_khong_phai_system(gen_service: Service) -> None:
    """
    Chủ đề là YÊU CẦU của người dùng, không phải tư liệu của tác vụ.

    M8 đặt văn bản người dùng vào system prompt và điều đó bào chữa được. Ở đây
    thì không: nhét chữ người dùng gõ vào vai system là tặng không cho nó một
    nấc thẩm quyền mà model được huấn luyện để tôn trọng hơn.
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    await sinh(service)
    system, user = fake.body(0)["messages"]

    assert "du lịch hàng không" in user["content"]
    assert "du lịch hàng không" not in system["content"]


async def test_level_duoc_dua_vao_prompt(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    await sinh(service, level="B2")

    assert "B2" in fake.body(0)["messages"][0]["content"]


async def test_level_none_khong_ro_ri_chu_None_vao_prompt(gen_service: Service) -> None:
    """
    `render` nhận `**values: str`. Truyền thẳng `None` vào sẽ in ra chữ `"None"`
    giữa prompt — sai âm thầm, không phải một lỗi nào cả.
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    await sinh(service, level=None)
    system = fake.body(0)["messages"][0]["content"]

    assert "None" not in system
    assert "không giới hạn" in system


# ---------------------------------------------------------------------
# Danh sách tránh — không có tương đương ở M8
# ---------------------------------------------------------------------


async def test_tu_da_co_trong_pham_vi_xuat_hien_trong_danh_sach_tranh(
    gen_service: Service,
) -> None:
    """Deck 2 của bộ thẻ mẫu toàn từ công sở, nên chủ đề công việc phải chạm tới."""

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    _, _, stats = await sinh(service, topic="công việc văn phòng và quản lý dự án")
    user = fake.body(0)["messages"][1]["content"]

    assert stats.avoid_list_size > 0
    assert any(
        tu in user for tu in ("deadline", "delegate", "procurement", "stakeholder", "appraisal")
    )


async def test_danh_sach_tranh_khong_bao_gio_ra_ngoai_allowed_deck_ids(
    gen_service: Service,
) -> None:
    """
    Test bảo mật của endpoint này.

    Mạnh hơn phép kiểm tương đương ở M8: nó khẳng định ranh giới phạm vi TRÊN
    PROMPT — tức service không đọc thẻ ngoài phạm vi kể cả chỉ để quyết định
    KHÔNG sinh ra từ gì.
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    await sinh(service, topic="công việc văn phòng và quản lý dự án", allowed_deck_ids=[3])

    # Chỉ soi lượt USER: đó là chỗ duy nhất dữ liệu từ bộ thẻ đi vào. Lượt
    # SYSTEM là prompt do mình viết, và ví dụ JSON trong đó cố tình dùng một từ
    # công sở làm mẫu — soi cả hai thì test tự đỏ vì chính chữ mình viết ra.
    user = fake.body(0)["messages"][1]["content"]

    for ngoai_pham_vi in (
        "deadline",
        "delegate",
        "procurement",
        "stakeholder",
        "appraisal",
        "resilient",
        "meticulous",
        "curriculum",
        "tuition",
        "literacy",
    ):
        assert ngoai_pham_vi not in user, f"{ngoai_pham_vi} lọt ra ngoài allowed_deck_ids"


async def test_chi_muc_rong_thi_danh_sach_tranh_rong_va_van_goi_duoc(
    gen_service: Service,
) -> None:
    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)
    service.index.rebuild([])

    _, the_ra, stats = await sinh(service)

    assert stats.avoid_list_size == 0
    assert stats.dedup_checked is False
    assert len(the_ra) == 1
    assert fake.call_count == 1


async def test_danh_sach_tranh_on_dinh_giua_hai_lan_goi(gen_service: Service) -> None:
    """
    Thứ tự lặp của `set` không ổn định — phải sắp xếp, không thì prompt trôi.

    So sánh DÒNG danh sách tránh chứ không so cả lượt user: nonce cố ý đổi mỗi
    lượt, đó là điểm mấu chốt của nó.
    """

    fake = FakeGroq([tra_loi(the()), tra_loi(the())])
    service = with_groq(gen_service, fake)

    await sinh(service, topic="công việc văn phòng")
    await sinh(service, topic="công việc văn phòng")

    def dong_tranh(i: int) -> str:
        return fake.body(i)["messages"][1]["content"].split("\n")[1]

    assert dong_tranh(0) == dong_tranh(1)
    assert "deadline" in dong_tranh(0)


async def test_exclude_words_duoc_uu_tien_va_vao_prompt(gen_service: Service) -> None:
    """Đây là cơ chế làm nút "thêm từ nữa" mà service không phải lưu trạng thái."""

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    await sinh(service, exclude_words=["layover", "boarding"])
    user = fake.body(0)["messages"][1]["content"]

    assert "layover" in user
    assert "boarding" in user


async def test_exclude_words_khong_bi_cat_mat_khi_danh_sach_day(gen_service: Service) -> None:
    """
    Lỗi mà một lượt chạy THẬT bắt được, không test nào ban đầu thấy.

    Bản đầu gộp `exclude_words` với kết quả tìm ngữ nghĩa, rồi SẮP XẾP theo bảng
    chữ cái TRƯỚC KHI cắt về `AVOID_LIST_SIZE`. Hệ quả: đúng những mục xếp cuối
    bảng chữ cái bị vứt, bất kể chúng là `exclude_words` được cho là ưu tiên —
    và lượt gọi thứ hai trả về lại `scope` với `timeline` dù vừa loại trừ chúng.

    Chọn từ cuối bảng chữ cái làm dữ liệu test là cố ý: đó chính là chỗ lỗi cũ
    biểu hiện.
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    tu_cuoi_bang = ["zucchini", "yardstick", "wavelength", "vineyard"]
    _, _, stats = await sinh(service, exclude_words=tu_cuoi_bang)
    user = fake.body(0)["messages"][1]["content"]

    for tu in tu_cuoi_bang:
        assert tu in user, f"{tu} bị cắt mất dù nằm trong exclude_words"

    assert stats.avoid_list_size == AVOID_LIST_SIZE


async def test_chu_de_duoc_embed_dung_mot_lan_va_khong_tu_noi_prefix(
    gen_service: Service,
) -> None:
    """
    Bẫy prefix của E5: `embed_query` tự nối `"query: "`, nối hai lần hỏng âm
    thầm y hệt quên nối. Và `embed_passages` là prefix SAI cho một câu truy vấn.
    """

    class SpyEncoder:
        def __init__(self, that: Encoder) -> None:
            self._that = that
            self.queries: list[str] = []
            self.passages: list[list[str]] = []

        async def embed_query(self, text: str):
            self.queries.append(text)

            return await self._that.embed_query(text)

        async def embed_passages(self, texts: list[str]):
            self.passages.append(texts)

            return await self._that.embed_passages(texts)

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)
    spy = SpyEncoder(service.encoder)
    service.vocab_generator._encoder = spy

    await sinh(service, topic="du lịch")

    assert spy.queries == ["du lịch"]
    assert spy.passages == []


# ---------------------------------------------------------------------
# Whitelist trường và chốt chặn an toàn
# ---------------------------------------------------------------------


async def test_truong_la_do_llm_them_khong_lot_vao_ket_qua(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the(audio_url="http://evil.test/a.mp3", card_id=999, deck_id=7))])
    service = with_groq(gen_service, fake)

    _, the_ra, _ = await sinh(service)

    assert set(the_ra[0].model_dump()) == {
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


async def test_meaning_chua_the_html_bi_loai(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the(), the(word="layover", meaning="<img src=x onerror=alert(1)>"))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["itinerary"]
    assert stats.dropped_unsafe == 1


async def test_truong_chua_thuoc_tinh_html_bi_loai(gen_service: Service) -> None:
    """
    Lỗ mà denylist của M8 KHÔNG bịt được.

    Chuỗi dưới đây không chứa ký tự nào trong `< > { } [ ] \\`, không có `http`,
    nên `field_is_safe` cho lọt nguyên vẹn — rồi nó thoát ra khỏi bất kỳ thuộc
    tính HTML nào. Allowlist ở `guard.py` chặn vì `=` không được kể tên.
    """

    xau = 'She left" autofocus onfocus=alert(1) x="'
    fake = FakeGroq([tra_loi(the(), the(word="layover", example_sentence=xau))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["itinerary"]
    assert stats.dropped_unsafe == 1


async def test_ten_mien_tran_bi_loai(gen_service: Service) -> None:
    fake = FakeGroq(
        [tra_loi(the(), the(word="layover", definition_en="Read more at evil.test now."))]
    )
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["itinerary"]
    assert stats.dropped_unsafe == 1


async def test_meaning_khong_co_dau_tieng_viet_bi_loai(gen_service: Service) -> None:
    """Model trả lời bằng tiếng Anh vào ô nghĩa tiếng Việt là một lỗi im lặng."""

    fake = FakeGroq([tra_loi(the(), the(word="layover", meaning="a planned route"))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["itinerary"]
    assert stats.dropped_unsafe == 1


async def test_word_khong_dung_dang_tu_bi_loai(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the(), the(word="check-in 24/7 online"))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["itinerary"]
    assert stats.dropped_unsafe == 1


async def test_phien_am_hong_thi_bo_nhan_chu_khong_bo_the(gen_service: Service) -> None:
    """Mất một nhãn còn hơn mất một từ đáng học — cùng cách xử lý với `part_of_speech`."""

    fake = FakeGroq([tra_loi(the(phonetic="ai tin uh rer ee"), the(word="layover"))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert len(the_ra) == 2
    assert the_ra[0].phonetic is None
    assert stats.dropped_unsafe == 0


async def test_part_of_speech_la_gia_tri_la_van_giu_the(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the(part_of_speech="danh từ"))])
    service = with_groq(gen_service, fake)

    _, the_ra, _ = await sinh(service)

    assert len(the_ra) == 1
    assert the_ra[0].part_of_speech is None


# ---------------------------------------------------------------------
# Mạch lạc — và sự VẮNG MẶT của phép kiểm bám văn bản
# ---------------------------------------------------------------------


async def test_cau_vi_du_khong_chua_tu_thi_bo_the(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the(), the(word="layover", cau="The weather was pleasant all week."))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["itinerary"]
    assert stats.dropped_incoherent == 1


async def test_chap_nhan_dang_chia_cua_tu(gen_service: Service) -> None:
    """`delegate` trong câu chia thành `delegated` vẫn là dùng đúng từ đó."""

    fake = FakeGroq(
        [tra_loi(the(word="delegate", cau="She delegated the booking to her assistant."))]
    )
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["delegate"]
    assert stats.dropped_incoherent == 0


async def test_cau_khong_ket_thuc_bang_dau_cau_thi_bo(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the(), the(word="layover", cau="A long layover between two flights"))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["itinerary"]
    assert stats.dropped_incoherent == 1


async def test_khong_ap_kiem_bam_van_ban(gen_service: Service) -> None:
    """
    Ghi lại một thứ KHÔNG có.

    Ở M8, `example_sentence` phải có thật trong đoạn văn người dùng gửi lên. Ở
    đây không có đoạn văn nào, nên phép kiểm ấy vô nghĩa — một câu bình thường
    không liên quan gì tới chủ đề vẫn phải đi qua.

    Test này tồn tại để không ai "sửa" M9 bằng cách import
    `sentence_is_from_text` vào rồi làm hỏng toàn bộ tính năng.
    """

    fake = FakeGroq([tra_loi(the(word="layover", cau="A short layover suits me fine."))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service, topic="nấu ăn")

    assert len(the_ra) == 1
    assert not hasattr(stats, "dropped_not_grounded")


# ---------------------------------------------------------------------
# Khử trùng
# ---------------------------------------------------------------------


async def test_tu_da_co_van_duoc_tra_ve_kem_danh_dau(gen_service: Service) -> None:
    """
    Danh sách tránh là LỜI KHUYÊN, `already_in_deck` mới là LUẬT.

    Model bỏ qua danh sách tránh thì thẻ trùng vẫn quay về, mang cờ, và VẪN nằm
    trong kết quả để giao diện bỏ tick sẵn.
    """

    fake = FakeGroq(
        [tra_loi(the(word="resilient", cau="The crew stayed resilient during the delay."))]
    )
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert the_ra[0].word == "resilient"
    assert the_ra[0].already_in_deck is True
    assert the_ra[0].existing_card_id == 101
    assert stats.already_in_deck_count == 1


async def test_tu_ngoai_allowed_deck_ids_khong_bi_danh_dau(gen_service: Service) -> None:
    fake = FakeGroq(
        [tra_loi(the(word="resilient", cau="The crew stayed resilient during the delay."))]
    )
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service, allowed_deck_ids=[3])

    assert the_ra[0].already_in_deck is False
    assert the_ra[0].existing_card_id is None
    assert stats.already_in_deck_count == 0


async def test_the_trung_nhau_trong_cung_lo_bi_bo(gen_service: Service) -> None:
    """
    Không có tương đương ở M8: hai ứng viên lấy từ một đoạn văn tự nhiên đã khác
    nhau. Với một chủ đề hẹp thì model lặp lại là chuyện thường.
    """

    fake = FakeGroq([tra_loi(the(), the(), the(word="layover"))])
    service = with_groq(gen_service, fake)

    _, the_ra, stats = await sinh(service)

    assert [t.word for t in the_ra] == ["itinerary", "layover"]
    assert stats.dropped_duplicate_in_batch == 1


# ---------------------------------------------------------------------
# Lỗi — endpoint này nói thật, không hạ cấp
# ---------------------------------------------------------------------


async def test_json_hong_thi_503_chu_khong_phai_200_rong(gen_service: Service) -> None:
    fake = FakeGroq([ok("đây không phải JSON")])
    service = with_groq(gen_service, fake)

    with pytest.raises(ProviderUnavailable):
        await sinh(service)


async def test_thieu_khoa_words_thi_503(gen_service: Service) -> None:
    fake = FakeGroq([ok(jsonlib.dumps({"topic_understood": "Du lịch"}))])
    service = with_groq(gen_service, fake)

    with pytest.raises(ProviderUnavailable):
        await sinh(service)


async def test_llm_tra_danh_sach_rong_thi_503_chu_khong_phai_200(gen_service: Service) -> None:
    """
    ĐẢO NGƯỢC CÓ CHỦ Ý so với M8.

    M8 trả 200 cho `{"words": []}` vì "đoạn văn này không có gì đáng học" là một
    sự thật có thể xảy ra. Ở đây "chủ đề của bạn không có từ vựng nào" gần như
    không bao giờ đúng — số 0 nghĩa là model từ chối, chạm bộ lọc an toàn, hoặc
    trả rác. Trả 200 rỗng ở đây là nói dối.
    """

    fake = FakeGroq([tra_loi()])
    service = with_groq(gen_service, fake)

    with pytest.raises(ProviderUnavailable):
        await sinh(service)


async def test_moi_the_bi_loai_thi_503(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the(meaning=""), the(word="1234"))])
    service = with_groq(gen_service, fake)

    with pytest.raises(ProviderUnavailable):
        await sinh(service)


async def test_provider_hong_thi_503_lot_ra_ngoai(gen_service: Service) -> None:
    fake = FakeGroq([httpx2.Response(500, json={"error": {"message": "boom"}})])
    service = with_groq(gen_service, fake)

    with pytest.raises(ProviderUnavailable):
        await sinh(service)


async def test_can_ngan_sach_thi_429_lot_ra_ngoai(gen_service: Service) -> None:
    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)
    service.budget.reserve(service.settings.ai_global_tokens_per_minute)

    with pytest.raises(BudgetExhausted):
        await sinh(service)

    assert fake.call_count == 0


async def test_khong_thu_lai_khi_llm_tra_rac(gen_service: Service) -> None:
    fake = FakeGroq([ok("rác"), ok("rác"), ok("rác")])
    service = with_groq(gen_service, fake)

    with pytest.raises(ProviderUnavailable):
        await sinh(service)

    assert fake.call_count == 1


async def test_gap_429_cua_nha_cung_cap_van_khong_tu_thu_lai_them(gen_service: Service) -> None:
    fake = FakeGroq(lambda _: rate_limited())
    service = with_groq(gen_service, fake)

    with pytest.raises(ProviderUnavailable):
        await sinh(service)

    assert fake.call_count == 6


async def test_semaphore_ban_thi_429_chu_khong_treo(gen_service: Service) -> None:
    """
    Hàng đợi có hạn giờ: hết giờ trả đúng cái 429 mà caller vốn sẽ nhận, nhưng
    đến mà không đốt một lời gọi provider nào — và không để request ngồi treo
    quá cả thời gian chờ của client.
    """

    fake = FakeGroq([tra_loi(the())])
    service = with_groq(gen_service, fake)

    from app.vocab import generator as mod

    cu = mod.SEM_TIMEOUT_SECONDS
    mod.SEM_TIMEOUT_SECONDS = 0.05
    await service.vocab_generator._sem.acquire()

    try:
        with pytest.raises(BudgetExhausted):
            await sinh(service)
    finally:
        mod.SEM_TIMEOUT_SECONDS = cu
        service.vocab_generator._sem.release()

    assert fake.call_count == 0


def test_hai_endpoint_nang_dung_chung_mot_semaphore(service: Service) -> None:
    """
    So sánh DANH TÍNH, không so sánh giá trị.

    Đỏ ngay khi ai đó dựng lại một `Semaphore(1)` riêng cho một trong hai — việc
    rất tự nhiên phải làm khi thêm endpoint đắt tiền thứ ba.
    """

    assert service.vocab._sem is service.vocab_generator._sem
    assert isinstance(service.vocab._sem, asyncio.Semaphore)


# ---------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------


@pytest.fixture
def gen_client(settings: Settings, encoder: Encoder):
    settings.ai_llm_api_key = "gsk_test"

    with TestClient(create_app(settings, encoder=encoder)) as client:
        wait_until_ready(client)
        client.post("/internal/v1/index/sync", headers=HEADERS)

        yield client


def test_generate_can_token(gen_client: TestClient) -> None:
    tra_ve = gen_client.post(
        "/internal/v1/vocab/generate",
        json={"topic": "du lịch", "allowed_deck_ids": [1]},
    )

    assert tra_ve.status_code == 401


def test_chu_de_qua_dai_tra_dang_error_chu_khong_phai_detail(gen_client: TestClient) -> None:
    tra_ve = gen_client.post(
        "/internal/v1/vocab/generate",
        headers=HEADERS,
        json={"topic": "x" * (MAX_TOPIC_CHARS + 1), "allowed_deck_ids": [1]},
    )

    assert tra_ve.status_code == 400
    assert tra_ve.json()["error"]["code"] == "INVALID_REQUEST"


def test_count_ngoai_khoang_thi_422(gen_client: TestClient) -> None:
    tra_ve = gen_client.post(
        "/internal/v1/vocab/generate",
        headers=HEADERS,
        json={"topic": "du lịch", "allowed_deck_ids": [1], "count": 99},
    )

    assert tra_ve.status_code == 422
    assert "detail" in tra_ve.json()


def test_level_khong_hop_le_thi_422(gen_client: TestClient) -> None:
    """Lý do `level` là `Literal` chứ không phải `str`: giá trị lạ là lỗi KIỂU."""

    tra_ve = gen_client.post(
        "/internal/v1/vocab/generate",
        headers=HEADERS,
        json={"topic": "du lịch", "allowed_deck_ids": [1], "level": "Z9"},
    )

    assert tra_ve.status_code == 422


def test_swagger_khai_bao_security_cho_endpoint_moi(gen_client: TestClient) -> None:
    spec = gen_client.get("/openapi.json").json()

    assert spec["paths"]["/internal/v1/vocab/generate"]["post"]["security"]


def test_hai_endpoint_vocab_dung_chung_mot_schema_the(gen_client: TestClient) -> None:
    """
    Phép kiểm máy cho quyết định dùng chung `VocabCandidate`.

    Đỏ khi ai đó tách ra thành hai lớp giống hệt nhau — việc sẽ đẻ thêm một type
    Java, một Jackson binding và một mapper ở phía backend, đổi lại con số không.
    """

    spec = gen_client.get("/openapi.json").json()
    thanh_phan = spec["components"]["schemas"]

    trich = thanh_phan["VocabExtractResponse"]["properties"]["candidates"]["items"]["$ref"]
    sinh_ra = thanh_phan["VocabGenerateResponse"]["properties"]["cards"]["items"]["$ref"]

    assert trich == sinh_ra == "#/components/schemas/VocabCandidate"


# ---------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------


def test_prompt_vocab_generate_render_duoc() -> None:
    prompts = PromptRegistry()

    system = prompts.render("vocab_generate_system_v1", count="6", level="B2")
    user = prompts.render(
        "vocab_generate_user_v1", nonce="a3f9c1d2", avoid_list="deadline", topic="du lịch"
    )

    assert "6" in system
    assert "B2" in system
    # `response_format={"type": "json_object"}` đòi chữ "JSON" có mặt trong
    # prompt, nếu không lại là một lỗi 400 khó hiểu nữa.
    assert "JSON" in system
    assert "du lịch" in user
    assert "a3f9c1d2" in user
    assert "{{" not in system + user


def test_prompt_thieu_bien_thi_nem_keyerror() -> None:
    """Placeholder còn nguyên trong prompt đã ship không làm gãy gì cả — nó chỉ
    làm model trả lời sai theo cách rất khó lần."""

    with pytest.raises(KeyError):
        PromptRegistry().render("vocab_generate_system_v1", count="6")
