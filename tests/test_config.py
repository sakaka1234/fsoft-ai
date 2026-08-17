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


# ---------------------------------------------------------------------
# Log cấu hình hiệu lực
#
# Vì sao có nhóm test này: một biến cũ sót trong bảng điều khiển của nền tảng
# hosting đè lên mặc định của code và KHÔNG có gì trong log nói ra. Cụ thể đã xảy
# ra: `AI_EMBEDDING_MODEL_FILE` còn trỏ vào bản chưa tỉa từ vựng, nên ảnh mới có
# sẵn model đã tỉa mà service vẫn nạp bản cũ, đỉnh RAM quay về 538 MB và Render
# free OOM thành vòng lặp chết. cgroup giết tiến trình không sinh traceback nên
# không có gì để tra.
# ---------------------------------------------------------------------


class _LogGia:
    """Ghi lại lời gọi log. Đơn giản hơn `structlog.testing.capture_logs`, mà
    `capture_logs` lại không đáng tin ở đây vì `configure_logging` bật
    `cache_logger_on_first_use=True`."""

    def __init__(self) -> None:
        self.su_kien: list[tuple[str, str, dict]] = []

    def info(self, ten: str, **truong) -> None:
        self.su_kien.append(("info", ten, truong))

    def warning(self, ten: str, **truong) -> None:
        self.su_kien.append(("warning", ten, truong))

    def truong_cua(self, ten: str) -> dict | None:
        for _, ten_su_kien, truong in self.su_kien:
            if ten_su_kien == ten:
                return truong

        return None

    def co(self, ten: str) -> bool:
        return self.truong_cua(ten) is not None


@pytest.fixture
def log_gia(monkeypatch) -> _LogGia:
    from app.core import preflight

    gia = _LogGia()
    monkeypatch.setattr(preflight, "log", gia)

    return gia


@pytest.fixture
def moi_truong_sach(monkeypatch):
    """
    Dọn mọi biến `AI_*`/`FASTEMBED_*` khỏi môi trường tiến trình.

    Cần vì `Settings` đọc cả `.env` của máy dev lẫn biến môi trường, nên trên máy
    có `.env` thì `model_fields_set` gồm gần như MỌI field — và test "field nào bị
    đè" sẽ xanh hay đỏ tuỳ máy chạy nó. Trong container thì không có `.env`, nên
    danh sách đó đúng bằng bảng biến của nền tảng, tức đúng thứ ta muốn kiểm.
    """

    import os

    for ten in list(os.environ):
        if ten.startswith(("AI_", "FASTEMBED_")):
            monkeypatch.delenv(ten, raising=False)


def test_log_danh_dau_dung_field_bi_moi_truong_de(
    log_gia: _LogGia, moi_truong_sach, tmp_path: Path
) -> None:
    """
    `dat_tu_moi_truong` phải chứa field được đặt tường minh và KHÔNG chứa field
    lấy mặc định. Đây là toàn bộ giá trị của dòng log này — không phân biệt được
    hai loại đó thì nó chỉ là một bản sao của `Settings`.
    """

    from app.core.preflight import log_cau_hinh_hieu_luc

    log_cau_hinh_hieu_luc(
        Settings(
            _env_file=None,
            ai_embedding_model_file="onnx/model_qint8_avx512_vnni.onnx",
            ai_min_score=0.8344,
            ai_db_path=tmp_path / "x.db",
        )
    )

    truong = log_gia.truong_cua("cau_hinh_hieu_luc")

    assert truong is not None
    assert "ai_embedding_model_file" in truong["dat_tu_moi_truong"]
    # Không hề được đặt trong lời gọi trên.
    assert "ai_embed_batch_size" not in truong["dat_tu_moi_truong"]
    assert truong["model_file"] == "onnx/model_qint8_avx512_vnni.onnx"


def test_canh_bao_khi_dinh_ram_vuot_gioi_han_container(
    log_gia: _LogGia, tmp_path: Path, monkeypatch
) -> None:
    """Đúng cấu hình đã gây ra vòng lặp chết trên Render: gói 512 MB, model chưa tỉa."""

    from app.core import preflight

    monkeypatch.setattr(preflight, "gioi_han_ram_container", lambda: 512)

    preflight.log_cau_hinh_hieu_luc(
        Settings(
            ai_embedding_model_file="onnx/model_qint8_avx512_vnni.onnx",
            ai_min_score=0.8344,
            ai_db_path=tmp_path / "x.db",
        )
    )

    truong = log_gia.truong_cua("ram_co_the_khong_du")

    assert truong is not None
    assert truong["gioi_han_MB"] == 512
    assert truong["dinh_du_kien_MB"] == 538
    # Nói rõ đây là do biến môi trường, không phải mặc định của code — vì chỗ
    # phải sửa là bảng điều khiển của nền tảng, không phải repo.
    assert truong["bi_de_boi_moi_truong"] is True
    assert "onnx/model_tia113k.onnx" in truong["goi_y"]


def test_khong_canh_bao_khi_ban_tia_vua_goi_512(
    log_gia: _LogGia, moi_truong_sach, tmp_path: Path, monkeypatch
) -> None:
    """Mặc định hiện tại phải im lặng ở đúng gói mà nó được chọn để vừa."""

    from app.core import preflight

    monkeypatch.setattr(preflight, "gioi_han_ram_container", lambda: 512)

    preflight.log_cau_hinh_hieu_luc(Settings(_env_file=None, ai_db_path=tmp_path / "x.db"))

    assert not log_gia.co("ram_co_the_khong_du")
    assert log_gia.truong_cua("ram_container")["dinh_du_kien_MB"] == 317


def test_khong_co_gioi_han_cgroup_thi_khong_doan_gi(
    log_gia: _LogGia, tmp_path: Path, monkeypatch
) -> None:
    """
    Ngoài container thì không có gì để so, và một cảnh báo RAM trên máy dev 32 GB
    chỉ dạy người đọc bỏ qua cảnh báo.

    Không dựa vào việc `/sys/fs/cgroup` vắng mặt: nó vắng trên Windows nhưng CÓ
    trên runner Linux của CI, nên test sẽ xanh hay đỏ tuỳ nơi chạy.
    """

    from app.core import preflight

    monkeypatch.setattr(preflight, "TEP_GIOI_HAN_RAM", ())

    preflight.log_cau_hinh_hieu_luc(
        Settings(
            ai_embedding_model_file="onnx/model.onnx",
            ai_min_score=0.83,
            ai_db_path=tmp_path / "x.db",
        )
    )

    assert log_gia.co("cau_hinh_hieu_luc")
    assert not log_gia.co("ram_container")
    assert not log_gia.co("ram_co_the_khong_du")


@pytest.mark.parametrize(
    "noi_dung, mong_doi",
    [
        ("536870912\n", 512),
        # cgroup v2 nói "không giới hạn" bằng chữ.
        ("max\n", None),
        # cgroup v1 nói "không giới hạn" bằng ~2^63. Không lọc thì thành 8796093 MB
        # và mọi so sánh sau đó đều vô nghĩa.
        ("9223372036854771712\n", None),
        ("", None),
        ("khong-phai-so\n", None),
    ],
)
def test_doc_gioi_han_ram_tu_cgroup(tmp_path: Path, monkeypatch, noi_dung: str, mong_doi) -> None:
    from app.core import preflight

    tep = tmp_path / "memory.max"
    tep.write_text(noi_dung)

    monkeypatch.setattr(preflight, "TEP_GIOI_HAN_RAM", (tep,))

    assert preflight.gioi_han_ram_container() == mong_doi


def test_moi_bien_the_onnx_deu_co_ca_nguong_va_dinh_ram() -> None:
    """
    Hai bảng phải phủ cùng một tập biến thể. Thiếu ở bảng RAM thì cảnh báo lặng
    lẽ không bao giờ chạy — đúng kiểu hỏng mà cả nhóm test này sinh ra để chặn.
    """

    from app.embedding.encoder import DINH_RAM_MB_THEO_MODEL

    assert set(DINH_RAM_MB_THEO_MODEL) == set(MIN_SCORE_THEO_MODEL)
