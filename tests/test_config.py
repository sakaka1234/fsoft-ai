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


# ---------------------------------------------------------------------
# Đường dẫn SQLite — lỗi đã xảy ra thật khi deploy lên Render
# ---------------------------------------------------------------------


def test_khong_ghi_duoc_thi_loi_noi_ro_phai_sua_bien_nao(tmp_path: Path, monkeypatch) -> None:
    """
    `AI_DB_PATH` tương đối trong container giải ra `/app/data`, mà `/app` thuộc
    root còn service chạy bằng user không đặc quyền.

    Lỗi gốc là `PermissionError: [Errno 13] Permission denied: '/app/data'` nổ ra
    từ tận trong `pathlib.mkdir`, chôn dưới sáu tầng traceback của starlette và
    anyio, KHÔNG nhắc gì tới biến môi trường cần sửa. Test này khoá lại yêu cầu:
    thông báo phải gọi tên biến và nói rõ giá trị đúng.

    Giả lập bằng monkeypatch chứ không bằng chmod: Windows bỏ qua chmod nên test
    sẽ không chạy được trên máy dev.
    """

    from app.store.db import Database

    def mkdir_bi_tu_choi(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "mkdir", mkdir_bi_tu_choi)

    with pytest.raises(PermissionError) as thong_tin:
        Database(tmp_path / "data" / "fsoft-ai.db").connect_sync()

    loi = str(thong_tin.value)

    assert "AI_DB_PATH" in loi
    assert "/data/fsoft-ai.db" in loi
    assert "tuyệt đối" in loi
    # Phải giữ lại lỗi gốc để còn lần được nguyên nhân thật.
    assert "PermissionError" in loi


def test_thu_muc_ton_tai_nhung_khong_ghi_duoc_cung_bao_ro(tmp_path: Path, monkeypatch) -> None:
    """Bind-mount thư mục của host: mkdir thành công nhưng vẫn không ghi được."""

    from app.store import db as db_module

    monkeypatch.setattr(db_module.os, "access", lambda *_a, **_k: False)

    with pytest.raises(PermissionError, match="KHÔNG ghi được"):
        db_module.Database(tmp_path / "data" / "fsoft-ai.db").connect_sync()


# ---------------------------------------------------------------------
# Preflight — chặn cả LỚP lỗi đường dẫn, không vá từng biến một
# ---------------------------------------------------------------------


def test_preflight_bao_HET_moi_duong_dan_sai_trong_mot_lan(monkeypatch) -> None:
    """
    Deploy lên Render hỏng HAI LẦN LIÊN TIẾP vì cùng một sai sót — dán nguyên
    `.env` của máy dev, mang theo đường dẫn tương đối — nhưng mỗi lần chỉ lộ ra
    đúng một biến:

        lần 1  AI_DB_PATH=./data/...            -> Permission denied '/app/data'
        lần 2  FASTEMBED_CACHE_PATH=./.cache/... -> Permission denied '/app/.cache'

    Vá xong biến thứ nhất lại phải deploy lại mới gặp biến thứ hai. Test này khoá
    yêu cầu: một lần chạy phải liệt kê HẾT, để sửa một lượt là xong.
    """

    from app.core import preflight

    # Không ghi được ở đâu cả, và cache cũng chưa có model — đúng tình huống
    # container với đường dẫn tương đối.
    monkeypatch.setattr(preflight.os, "access", lambda *_a, **_k: False)
    monkeypatch.setattr(preflight, "_co_model_trong_cache", lambda *_a, **_k: False)

    settings = Settings(
        ai_db_path=Path("./data/fsoft-ai.db"),
        fastembed_cache_path=Path("./.cache/fastembed"),
        ai_source_mode="fixture",
    )

    with pytest.raises(RuntimeError) as thong_tin:
        preflight.kiem_duong_dan(settings)

    loi = str(thong_tin.value)

    # Cả hai biến phải cùng có mặt — đây là điểm chính của test.
    assert "AI_DB_PATH" in loi
    assert "FASTEMBED_CACHE_PATH" in loi

    # Và mỗi cái phải kèm giá trị đúng, không chỉ nói "sai".
    assert "/data/fsoft-ai.db" in loi
    assert "/opt/fastembed_cache" in loi


def test_preflight_im_lang_khi_cau_hinh_dung(settings: Settings) -> None:
    """Cấu hình test hợp lệ thì preflight không được cản đường."""

    from app.core.preflight import kiem_duong_dan

    kiem_duong_dan(settings)


def test_preflight_bao_fixture_thieu_chi_khi_dang_dung_fixture(tmp_path: Path) -> None:
    """
    `AI_FIXTURE_PATH` trỏ vào tệp không tồn tại chỉ là lỗi khi
    `AI_SOURCE_MODE=fixture`. Ở chế độ http nó không được đọc, nên bắt lỗi ở đó
    sẽ chặn oan một cấu hình production hoàn toàn hợp lệ.
    """

    from app.core.preflight import kiem_duong_dan

    thieu = tmp_path / "khong-co.json"

    with pytest.raises(RuntimeError, match="AI_FIXTURE_PATH"):
        kiem_duong_dan(
            Settings(ai_source_mode="fixture", ai_fixture_path=thieu, ai_db_path=tmp_path / "a.db")
        )

    # Chế độ http: cùng đường dẫn thiếu đó phải được bỏ qua.
    kiem_duong_dan(
        Settings(ai_source_mode="http", ai_fixture_path=thieu, ai_db_path=tmp_path / "a.db")
    )


def test_cache_model_chi_doc_van_hop_le(tmp_path: Path, monkeypatch) -> None:
    """
    Hồi quy từ deploy Render thật.

    Trong image, `/opt/fastembed_cache` được tạo lúc build bằng `root` (bước tải
    model chạy trước `USER fsoft`), nên user chạy service chỉ ĐỌC được. Thế là
    đủ — model đã nằm sẵn ở đó, không ai cần ghi thêm.

    Bản preflight đầu tiên đòi quyền GHI vô điều kiện nên nó chặn luôn cấu hình
    ĐÚNG, và câu "sửa:" lại bảo đặt đúng cái giá trị đang đặt — một thông báo tự
    mâu thuẫn, tệ hơn cả không có thông báo.
    """

    from app.core import preflight

    cache = tmp_path / "fastembed_cache"
    cache.mkdir()

    # Cache có model. Chặn quyền ghi CHỈ trên thư mục cache, để đường dẫn DB
    # trong test vẫn hợp lệ — chặn tất thì test sẽ đỏ vì một lý do khác.
    monkeypatch.setattr(preflight, "_co_model_trong_cache", lambda *_a, **_k: True)

    access_that = preflight.os.access

    def access_gia(duong_dan, mode):
        if Path(duong_dan) == cache:
            return mode != preflight.os.W_OK

        return access_that(duong_dan, mode)

    monkeypatch.setattr(preflight.os, "access", access_gia)

    preflight.kiem_duong_dan(
        Settings(
            fastembed_cache_path=cache,
            ai_db_path=tmp_path / "data" / "x.db",
            ai_source_mode="http",
        )
    )


def test_cache_rong_va_khong_ghi_duoc_thi_moi_la_loi(tmp_path: Path, monkeypatch) -> None:
    """Không có model VÀ không tải về được — lúc đó mới thật sự chặn được đường."""

    from app.core import preflight

    cache = tmp_path / "fastembed_cache"
    cache.mkdir()

    monkeypatch.setattr(preflight, "_co_model_trong_cache", lambda *_a, **_k: False)
    monkeypatch.setattr(preflight.os, "access", lambda *_a, **_k: False)

    with pytest.raises(RuntimeError) as thong_tin:
        preflight.kiem_duong_dan(
            Settings(
                fastembed_cache_path=cache,
                ai_db_path=tmp_path / "data" / "x.db",
                ai_source_mode="http",
            )
        )

    loi = str(thong_tin.value)

    assert "FASTEMBED_CACHE_PATH" in loi
    assert "KHÔNG có model" in loi
