"""
Test encoder và text_builder. Acceptance SPEC muc 11.2.

Kết luận nền tảng từ M0 (docs/M0_FINDINGS.md muc 2.3): fastembed KHÔNG tự thêm
prefix, encoder phải tự nối. Test ở đây ghim lại điều đó.
"""

import numpy as np
import pytest

from app.config import Settings
from app.embedding.encoder import Encoder, register_custom_model
from app.embedding.text_builder import build_text, build_text_and_hash, content_hash
from tests.conftest import make_card

# ---------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------


def test_embed_query_tra_384_chieu_norm_1(encoder: Encoder) -> None:
    vector = encoder.embed_query_sync("xin chào")

    assert vector.shape == (384,)
    assert vector.dtype == np.float32
    assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)


def test_embed_passages_tra_dung_so_luong_va_da_normalize(encoder: Encoder) -> None:
    vectors = encoder.embed_passages_sync(["một", "hai", "ba"])

    assert len(vectors) == 3

    for vector in vectors:
        assert vector.shape == (384,)
        assert float(np.linalg.norm(vector)) == pytest.approx(1.0, abs=1e-5)


def test_embed_passages_rong_khong_goi_model(encoder: Encoder) -> None:
    assert encoder.embed_passages_sync([]) == []


def test_query_va_passage_dung_prefix_khac_nhau(encoder: Encoder) -> None:
    """
    Nếu encoder quên nối prefix thì hai vector này sẽ giống hệt nhau. Đây là
    lưới bắt cho lỗi im lặng tốn nhiều giờ nhất của E5.
    """

    as_query = encoder.embed_query_sync("resilient")
    as_passage = encoder.embed_passages_sync(["resilient"])[0]

    assert not np.allclose(as_query, as_passage)


def test_prefix_chi_noi_dung_mot_lan(encoder: Encoder) -> None:
    """
    Nối prefix hai lần hỏng âm thầm y hệt quên nối.

    embed_query("x") phải bằng model.embed("query: x") thô, không phải
    model.embed("query: query: x").
    """

    from_encoder = encoder.embed_query_sync("resilient")

    raw = next(iter(encoder._model.embed(["query: resilient"])))
    raw = np.asarray(raw, dtype=np.float32)

    assert np.allclose(from_encoder, raw, atol=1e-6)


def test_dang_ky_custom_model_idempotent() -> None:
    """Lifespan và test đều có thể gọi nhiều lần trong cùng một tiến trình."""

    register_custom_model("intfloat/multilingual-e5-small", 384)
    register_custom_model("intfloat/multilingual-e5-small", 384)


def test_encoder_chua_nap_thi_bao_loi_ro_rang() -> None:
    with pytest.raises(RuntimeError, match="chưa nạp model"):
        Encoder(Settings()).embed_query_sync("x")


def test_download_model_khai_bao_giong_het_encoder() -> None:
    """
    `scripts/download_model.py` cố ý lặp lại khai báo model để chạy được ở một
    layer Docker riêng, trước khi app/ được copy vào image.

    Test này khiến hai bên không thể lệch âm thầm — lệch nghĩa là image nạp một
    model, còn runtime lại đòi model khác.
    """

    import importlib.util

    from app.config import PROJECT_ROOT
    from app.embedding import encoder as encoder_module

    spec = importlib.util.spec_from_file_location(
        "download_model", PROJECT_ROOT / "scripts" / "download_model.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    assert script.ADDITIONAL_FILES == encoder_module._ADDITIONAL_FILES
    assert script.MODEL == Settings().ai_embedding_model
    assert script.DIM == Settings().ai_embedding_dim

    # `download_model.py` tải bản GỐC từ Hugging Face; runtime dùng bản ĐÃ TỈA
    # từ vựng mà `scripts/tia_vocab.py` sinh ra. Hai file CỐ Ý khác nhau.
    assert script.MODEL_FILE == encoder_module.MODEL_FILE_GOC

    # Bằng nhau nghĩa là ai đó đã gỡ bước tỉa và service đang chạy bản chưa tỉa —
    # vẫn đúng kết quả, nhưng ăn thêm 220 MB RAM và quay lại vượt trần 512 MB.
    assert script.MODEL_FILE != Settings().ai_embedding_model_file, (
        "File tải về và file runtime đang trùng nhau: bước tỉa từ vựng đã bị bỏ."
    )


def test_nguong_loc_khop_voi_bien_the_onnx_dang_dung() -> None:
    """
    Ghim cặp (biến thể ONNX, AI_MIN_SCORE) lại với nhau.

    Lượng tử hoá làm DỊCH phân bố cosine. Đo thật trên bộ 40 case: bản int8 dùng
    ngưỡng 0.83 của fp32 thì 2 trong 5 case NEGATIVE hỏng — service vẫn trả 200,
    vẫn không có log lỗi nào, chỉ là câu lẽ ra trả rỗng bắt đầu trả về thẻ bừa.

    Không có test này thì đổi biến thể để tiết kiệm RAM là một cái bẫy.
    """

    from app.embedding.encoder import MIN_SCORE_THEO_MODEL

    settings = Settings()

    assert settings.ai_embedding_model_file in MIN_SCORE_THEO_MODEL, (
        f"Biến thể {settings.ai_embedding_model_file} chưa được hiệu chỉnh ngưỡng. "
        "Chạy scripts/hieu_chinh_nguong.py rồi thêm vào MIN_SCORE_THEO_MODEL."
    )

    assert settings.ai_min_score == MIN_SCORE_THEO_MODEL[settings.ai_embedding_model_file]


# ---------------------------------------------------------------------
# Ngữ nghĩa
# ---------------------------------------------------------------------


def test_ngu_nghia_tieng_viet_theo_chieu_bat_doi_xung(encoder: Encoder) -> None:
    """
    Acceptance SPEC muc 11.2: cos("lo lắng","bồn chồn") > cos("lo lắng","cái bàn").

    Đo theo đúng chiều mà ứng dụng dùng: câu hỏi là `query: `, thẻ là `passage: `.
    E5 là model BẤT ĐỐI XỨNG, chỉ chiều này mới có ý nghĩa
    (docs/M0_FINDINGS.md muc 2.5).
    """

    query = encoder.embed_query_sync("lo lắng")
    gan, xa = encoder.embed_passages_sync(["bồn chồn", "cái bàn"])

    assert float(query @ gan) > float(query @ xa)


def test_cau_hoi_tieng_viet_truy_duoc_the_tieng_anh(encoder: Encoder) -> None:
    """Cross-lingual: đây là năng lực cả sản phẩm phụ thuộc vào."""

    query = encoder.embed_query_sync("từ nào chỉ cảm giác lo lắng")

    apprehensive, meticulous, deforestation = encoder.embed_passages_sync(
        [
            "apprehensive (adj)\nNghĩa: lo lắng, e ngại về điều sắp xảy ra",
            "meticulous (adj)\nNghĩa: tỉ mỉ, cẩn thận đến từng chi tiết",
            "deforestation (n)\nNghĩa: nạn phá rừng",
        ]
    )

    assert float(query @ apprehensive) > float(query @ meticulous)
    assert float(query @ apprehensive) > float(query @ deforestation)


def test_so_query_voi_query_bi_nen_sat_nhau(encoder: Encoder) -> None:
    """
    Ghim lại một hành vi để M2 không bị bất ngờ.

    Hai câu hỏi HOÀN TOÀN không liên quan vẫn đạt cosine rất cao khi cùng mang
    prefix `query: `. Vì vậy ngưỡng tuyệt đối AI_INTENT_THRESHOLD=0.50 trong
    SPEC là vô dụng để phát hiện OUT_OF_SCOPE — M2 phải hiệu chỉnh lại bằng
    dữ liệu thật, đừng tin con số đó.
    """

    a = encoder.embed_query_sync("lo lắng")
    b = encoder.embed_query_sync("cái bàn")

    assert float(a @ b) > 0.80


# ---------------------------------------------------------------------
# text_builder
# ---------------------------------------------------------------------


def test_build_text_day_du_field() -> None:
    text = build_text(make_card(card_id=101))

    assert text.splitlines() == [
        "resilient (adj) /rɪˈzɪliənt/",
        "Nghĩa: kiên cường, có khả năng phục hồi nhanh",
        "Định nghĩa: able to recover quickly from difficult conditions",
        (
            "Ví dụ: She remained resilient despite repeated setbacks. "
            "— Cô ấy vẫn kiên cường dù liên tục gặp thất bại."
        ),
    ]


def test_build_text_bo_han_dong_khi_field_rong() -> None:
    """Không bao giờ được in chuỗi 'None' vào text đem đi embed."""

    text = build_text(
        make_card(
            phonetic=None,
            part_of_speech=None,
            definition_en=None,
            example_sentence=None,
            example_meaning=None,
            note="   ",
        )
    )

    assert "None" not in text
    assert text == "resilient\nNghĩa: kiên cường, có khả năng phục hồi nhanh"


def test_build_text_co_note_thi_them_dong_ghi_chu() -> None:
    text = build_text(make_card(note="dùng nhiều trong văn nói"))

    assert text.endswith("Ghi chú: dùng nhiều trong văn nói")


def test_content_hash_doi_khi_noi_dung_doi() -> None:
    _, hash_goc = build_text_and_hash(make_card())
    _, hash_moi = build_text_and_hash(make_card(meaning="nghĩa đã sửa"))

    assert hash_goc != hash_moi


def test_content_hash_khong_doi_khi_chi_doi_deck() -> None:
    """deck_id không nằm trong text embed nên không được ảnh hưởng hash."""

    _, a = build_text_and_hash(make_card(deck_id=1))
    _, b = build_text_and_hash(make_card(deck_id=9))

    assert a == b


def test_content_hash_tinh_truoc_prefix() -> None:
    """
    Hash phải là của text trần. Nếu tính sau khi nối 'passage: ' thì đổi prefix
    sẽ bắt embed lại toàn bộ index mà chẳng vì lý do gì.
    """

    text, computed = build_text_and_hash(make_card())

    assert computed == content_hash(text)
    assert computed != content_hash("passage: " + text)
