"""
Canh file cấu hình mẫu.

Vì sao cần test riêng cho một file `.md` và một file `.example`: chúng là thứ
người mới gõ `cp .env.example .env` sẽ chạy đầu tiên, nhưng không có test nào
khác chạm tới chúng. CI cũng không — workflow tự đặt biến môi trường chứ không
copy `.env.example`, nên về mặt cấu trúc CI KHÔNG THỂ bắt được lỗi ở đây.
"""

import re
from pathlib import Path

import pytest
from dotenv import dotenv_values

from app.config import PROJECT_ROOT, Settings
from app.embedding.encoder import MIN_SCORE_THEO_MODEL

ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
SPEC = PROJECT_ROOT / "docs" / "SPEC.md"

# Dòng để trống rồi chú thích ngay sau dấu `=`.
_TRONG_KEM_CHU_THICH = re.compile(r"^([A-Z_][A-Z0-9_]*)=[ \t]*#", re.MULTILINE)


def test_bien_de_trong_khong_bi_bien_chu_thich_thanh_gia_tri() -> None:
    """
    `AI_FOO=   # giải thích` KHÔNG cho ra chuỗi rỗng.

    python-dotenv nuốt khoảng trắng sau dấu `=` rồi thấy ký tự kế tiếp là `#`
    nên đọc cả dòng làm giá trị. Bộ lọc chú thích của nó đòi phải có khoảng
    trắng TRƯỚC dấu `#` *bên trong* giá trị, nên dòng có giá trị thật thì vẫn
    cắt đúng — chỉ dòng để trống mới dính.

    Hậu quả đo được nếu để lọt: `AI_INTERNAL_TOKEN` thành một chuỗi tiếng Việt
    có dấu, `secrets.compare_digest` ném `TypeError: comparing strings with
    non-ASCII characters is not supported`, và MỌI request nội bộ trả 500 —
    kể cả request mang đúng token.
    """

    for path in (ENV_EXAMPLE, SPEC):
        vi_pham = _TRONG_KEM_CHU_THICH.findall(path.read_text(encoding="utf-8"))

        assert not vi_pham, (
            f"{path.name}: {vi_pham} để trống nhưng có chú thích cùng dòng. "
            "Đưa chú thích lên dòng riêng."
        )


def test_khong_gia_tri_nao_trong_env_example_bat_dau_bang_dau_thang() -> None:
    """Chốt chặn thứ hai, đọc bằng chính thư viện mà app dùng."""

    for key, value in dotenv_values(ENV_EXAMPLE).items():
        assert value is None or not value.lstrip().startswith("#"), (
            f"{key} có giá trị là một chuỗi chú thích: {value!r}"
        )


def test_moi_gia_tri_trong_env_example_deu_la_ascii() -> None:
    """
    Giá trị cấu hình đi thẳng vào HTTP header và `secrets.compare_digest`, cả
    hai đều chỉ chịu ASCII. Chú thích tiếng Việt lọt vào là hỏng ngay.
    """

    for key, value in dotenv_values(ENV_EXAMPLE).items():
        if value is None:
            continue

        assert value.isascii(), f"{key} chứa ký tự ngoài ASCII: {value!r}"


def test_env_example_nap_duoc_thanh_settings(tmp_path: Path) -> None:
    """`cp .env.example .env` phải cho ra một cấu hình dùng được ngay."""

    ban_sao = tmp_path / ".env"
    ban_sao.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=ban_sao)  # type: ignore[call-arg]

    assert settings.ai_internal_token
    assert settings.ai_internal_token.isascii()

    # Chưa điền thì phải là RỖNG, để các chốt `if not ...` còn tác dụng.
    assert settings.ai_backend_token == ""
    assert settings.ai_llm_api_key == ""

    assert settings.ai_embedding_dim == 384

    # Không ghim con số ở đây: ngưỡng đi kèm biến thể ONNX, và bảng trong
    # encoder.py là nguồn sự thật duy nhất. Ghim số sẽ biến mỗi lần đổi biến thể
    # thành ba chỗ phải sửa, mà quên một chỗ thì hỏng âm thầm.
    assert settings.ai_min_score == pytest.approx(
        MIN_SCORE_THEO_MODEL[settings.ai_embedding_model_file]
    )
    # Tiền tố E5 phải giữ nguyên dấu cách cuối, mất nó là retrieval kém âm thầm.
    assert settings.ai_query_prefix == "query: "
    assert settings.ai_passage_prefix == "passage: "


def test_env_example_phu_het_moi_truong_cua_settings() -> None:
    """
    Thêm trường vào `Settings` mà quên ghi vào `.env.example` thì người mới
    không biết là nó tồn tại.
    """

    trong_file = set(dotenv_values(ENV_EXAMPLE))
    trong_code = {name.upper() for name in Settings.model_fields}

    thieu = trong_code - trong_file

    assert not thieu, f".env.example thiếu: {sorted(thieu)}"
