"""
Kiểm mọi đường dẫn TRƯỚC khi khởi động, báo hết một lượt.

Vì sao cần cả một module cho việc này: khi deploy lên Render, cùng một sai sót —
dán nguyên `.env` của máy dev lên, mang theo những đường dẫn TƯƠNG ĐỐI — đã làm
hỏng deploy HAI LẦN LIÊN TIẾP, mỗi lần lộ ra một biến khác nhau:

    lần 1  AI_DB_PATH=./data/fsoft-ai.db
           -> PermissionError: [Errno 13] Permission denied: '/app/data'

    lần 2  FASTEMBED_CACHE_PATH=./.cache/fastembed
           -> PermissionError: [Errno 13] Permission denied: '/app/.cache'

Cả hai đều nổ ra từ sâu trong thư viện, sau khi service đã báo "live", và cả hai
đều chỉ hỏng ĐÚNG MỘT biến nên vá xong lại phải deploy lại để gặp biến tiếp theo.

Module này đổi cách hỏng: kiểm hết một lượt, liệt kê MỌI đường dẫn có vấn đề
trong một thông báo, và chết ngay lúc khởi động thay vì sống mà không bao giờ
sẵn sàng. Deploy thất bại rõ ràng tốt hơn một service báo "live" rồi trả 503 mãi.
"""

import os
from pathlib import Path

from app.config import Settings, resolve_path
from app.core.logging import get_logger

log = get_logger(__name__)

# Trong container, đường dẫn tương đối giải ra dưới /app — nơi service không có
# quyền ghi. Câu này được nối vào mọi thông báo nên người đọc không phải đi tra
# tài liệu mới biết phải làm gì.
_GOI_Y_CONTAINER = (
    "Trong container chỉ /data (SQLite) và /opt/fastembed_cache (model) được cấp "
    "quyền ghi. Đường dẫn TƯƠNG ĐỐI sẽ giải ra dưới /app, mà /app thuộc root còn "
    "service chạy bằng user không đặc quyền — đừng dán nguyên .env của máy dev."
)


def _dang_trong_container() -> bool:
    """Chỉ dùng để chọn câu gợi ý, không dùng để rẽ nhánh logic."""

    return Path("/.dockerenv").exists() or bool(os.environ.get("RENDER"))


def _ghi_duoc(thu_muc: Path) -> tuple[bool, str]:
    """Thư mục có tồn tại và ghi được không; nếu chưa có thì tạo được không."""

    if thu_muc.is_dir():
        if os.access(thu_muc, os.W_OK):
            return True, ""

        return False, "tồn tại nhưng KHÔNG ghi được"

    # Chưa có thì leo lên tìm tổ tiên gần nhất đang tồn tại: chính nó phải ghi
    # được mới tạo nổi các cấp còn thiếu.
    to_tien = thu_muc

    while not to_tien.exists() and to_tien != to_tien.parent:
        to_tien = to_tien.parent

    if not to_tien.is_dir():
        return False, f"tổ tiên {to_tien} không phải thư mục"

    if not os.access(to_tien, os.W_OK):
        return False, f"chưa tồn tại, và không tạo được vì {to_tien} không ghi được"

    return True, ""


def _co_model_trong_cache(settings: Settings, cache: Path) -> bool:
    """Cache đã chứa ĐÚNG biến thể ONNX đang cấu hình chưa."""

    from app.embedding.encoder import find_local_snapshot

    return (
        find_local_snapshot(cache, settings.ai_embedding_model, settings.ai_embedding_model_file)
        is not None
    )


def kiem_duong_dan(settings: Settings) -> None:
    """
    Ném `RuntimeError` liệt kê mọi đường dẫn có vấn đề.

    Gọi TRƯỚC khi mở SQLite và trước khi nạp model, ở trong `lifespan` — để lỗi
    cấu hình làm deploy thất bại dứt khoát chứ không thành một service sống mà
    `/readyz` không bao giờ lên 200.
    """

    van_de: list[str] = []

    # Giải đường dẫn tương đối theo ĐÚNG cách `build_service` làm — tương đối
    # với gốc repo, không phải với thư mục hiện hành. Kiểm khác chỗ dùng thì
    # preflight sẽ báo xanh cho một đường dẫn mà lúc chạy vẫn hỏng.
    db_path = resolve_path(settings.ai_db_path)
    fixture_path = resolve_path(settings.ai_fixture_path)

    # ---- SQLite: phải ghi được ----
    duoc, ly_do = _ghi_duoc(db_path.parent)

    if not duoc:
        van_de.append(
            f"AI_DB_PATH={settings.ai_db_path}  (giải ra {db_path})\n"
            f"      thư mục {db_path.parent} {ly_do}\n"
            f"      sửa: AI_DB_PATH=/data/fsoft-ai.db"
        )

    # ---- Fixture: chỉ cần khi thật sự dùng ----
    if settings.ai_source_mode == "fixture" and not fixture_path.is_file():
        van_de.append(
            f"AI_FIXTURE_PATH={settings.ai_fixture_path}  (giải ra {fixture_path})\n"
            f"      không tìm thấy tệp\n"
            f"      sửa: dùng AI_SOURCE_MODE=http, hoặc trỏ vào tệp có thật"
        )

    # ---- Cache model ----
    #
    # Yêu cầu quyền phụ thuộc vào việc model CÓ SẴN hay chưa, và đây là chỗ bản
    # trước làm sai: nó đòi quyền GHI vô điều kiện, nên chặn luôn cấu hình đúng.
    #
    # Trong image, /opt/fastembed_cache được tạo lúc build bằng root (bước
    # download_model.py chạy trước `USER fsoft`), nên user chạy service chỉ ĐỌC
    # được. Thế là đủ: model đã nằm sẵn ở đó, không ai cần ghi thêm. fastembed
    # cũng chỉ gọi `mkdir(exist_ok=True)` nên thư mục có sẵn thì không cần ghi.
    #
    # Chỉ khi cache RỖNG thì mới cần ghi — vì lúc đó phải tải model về.
    if settings.fastembed_cache_path is not None:
        cache = resolve_path(settings.fastembed_cache_path)
        co_san_model = _co_model_trong_cache(settings, cache)

        if co_san_model:
            # Cần cả R_OK lẫn X_OK: thiếu quyền "search" trên thư mục thì không
            # mở nổi tệp bên trong dù có quyền đọc chính thư mục.
            if not os.access(cache, os.R_OK | os.X_OK):
                van_de.append(
                    f"FASTEMBED_CACHE_PATH={settings.fastembed_cache_path}  (giải ra {cache})\n"
                    f"      có model nhưng KHÔNG đọc được\n"
                    f"      sửa: cấp quyền đọc cho thư mục này"
                )

        else:
            duoc, ly_do = _ghi_duoc(cache)

            if not duoc:
                van_de.append(
                    f"FASTEMBED_CACHE_PATH={settings.fastembed_cache_path}  (giải ra {cache})\n"
                    f"      KHÔNG có model {settings.ai_embedding_model_file} ở đây, "
                    f"và cũng không tải về được: {ly_do}\n"
                    f"      sửa: trong container dùng FASTEMBED_CACHE_PATH=/opt/fastembed_cache "
                    f"(model đã nạp sẵn lúc build image); ngoài container thì trỏ vào một "
                    f"thư mục ghi được"
                )

    if not van_de:
        return

    thong_bao = "Cấu hình đường dẫn không dùng được, service không khởi động:\n\n" + "\n\n".join(
        f"  - {x}" for x in van_de
    )

    if _dang_trong_container():
        thong_bao += f"\n\n{_GOI_Y_CONTAINER}"

    raise RuntimeError(thong_bao)


# Nơi cgroup công bố giới hạn bộ nhớ. Là hằng số ở cấp module để test thay được
# bằng tệp tạm — hai đường dẫn này không tồn tại trên Windows lẫn macOS.
TEP_GIOI_HAN_RAM = (
    # cgroup v2 — Docker mới, Render, Fly, Cloud Run.
    Path("/sys/fs/cgroup/memory.max"),
    # cgroup v1 — nhân cũ. Không có giới hạn thì ghi một số khổng lồ.
    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
)


def gioi_han_ram_container() -> int | None:
    """
    Giới hạn bộ nhớ mà cgroup áp lên tiến trình, MB. `None` nếu không có.

    Đọc trực tiếp từ cgroup chứ không hỏi tổng RAM của máy: trên Render và mọi
    PaaS khác, máy có hàng chục GB nhưng container chỉ được cấp 512 MB, và cái
    giết tiến trình là con số thứ hai.
    """

    for tep in TEP_GIOI_HAN_RAM:
        try:
            noi_dung = tep.read_text().strip()

        except OSError:
            continue

        if noi_dung == "max":
            return None

        try:
            byte = int(noi_dung)

        except ValueError:
            continue

        # cgroup v1 dùng ~2^63 để nói "không giới hạn". Bất cứ giá trị trên 1 TB
        # đều là cách nói đó, không phải một giới hạn thật.
        if byte <= 0 or byte > 1024**4:
            return None

        return byte // (1024 * 1024)

    return None


# Biến mà từng nền tảng dùng để công bố commit đang chạy. Không nền tảng nào
# thống nhất với nền tảng nào, nên thử lần lượt.
_BIEN_COMMIT = (
    "RENDER_GIT_COMMIT",
    "RAILWAY_GIT_COMMIT_SHA",
    # Heroku và các bản dựng theo chuẩn Cloud Native Buildpacks.
    "SOURCE_VERSION",
    # Tự đặt khi build tay: `docker build --build-arg` rồi `ENV GIT_COMMIT=...`.
    "GIT_COMMIT",
)


def commit_dang_chay() -> str | None:
    """
    Commit của mã đang chạy, nếu nền tảng có công bố.

    Vì sao đáng một hàm riêng: thiếu thông tin này đã dẫn tới một kết luận SAI.
    Trên Render, `model_version` quan sát được là `multilingual-e5-small-q8@t1` —
    và giá trị đó vừa có thể là một biến môi trường cũ còn sót, vừa có thể là
    MẶC ĐỊNH TRONG CODE của một ảnh cũ. Hai nguyên nhân khác nhau hoàn toàn, một
    cái sửa ở bảng điều khiển, một cái sửa bằng deploy lại. Không có commit trong
    log thì không phân biệt được, và tôi đã chọn sai cái.
    """

    for ten in _BIEN_COMMIT:
        gia_tri = os.environ.get(ten, "").strip()

        if gia_tri:
            return gia_tri

    return None


def log_cau_hinh_hieu_luc(settings: Settings) -> None:
    """
    In cấu hình ĐANG CÓ HIỆU LỰC, đánh dấu cái nào bị biến môi trường đè lên.

    Vì sao cần: nền tảng hosting đè biến lên mặc định của code, và một biến cũ
    còn sót trong bảng điều khiển sẽ âm thầm thắng. Cụ thể đã xảy ra:
    `AI_EMBEDDING_MODEL_FILE` còn trỏ vào bản CHƯA tỉa từ vựng, nên ảnh mới có
    sẵn model đã tỉa mà service vẫn nạp bản cũ, đỉnh RAM quay về 538 MB và
    Render free OOM thành vòng lặp chết. Không có gì trong log nói ra điều đó —
    kể cả tôi, khi đi chẩn đoán, cũng không có cách nào biết.

    Gọi TRƯỚC `kiem_duong_dan` để ngay cả một preflight thất bại cũng có dòng
    cấu hình đứng phía trên nó.

    `model_fields_set` của pydantic là chỗ chứa sự thật: nó chỉ gồm những field
    được đặt tường minh (biến môi trường hoặc `.env`), không gồm mặc định.
    """

    from app.embedding.encoder import DINH_RAM_MB_THEO_MODEL

    dat_tu_moi_truong = sorted(settings.model_fields_set)

    log.info(
        "cau_hinh_hieu_luc",
        commit=commit_dang_chay(),
        source_mode=settings.ai_source_mode,
        model_file=settings.ai_embedding_model_file,
        model_version=settings.ai_model_version,
        min_score=settings.ai_min_score,
        embed_batch_size=settings.ai_embed_batch_size,
        onnx_cpu_arena=settings.ai_onnx_cpu_arena,
        ort_intra_op_threads=settings.ai_ort_intra_op_threads,
        db_path=str(resolve_path(settings.ai_db_path)),
        cache_path=(
            str(resolve_path(settings.fastembed_cache_path))
            if settings.fastembed_cache_path is not None
            else None
        ),
        dat_tu_moi_truong=dat_tu_moi_truong,
    )

    # ---- Đỉnh RAM dự kiến so với giới hạn thật ----
    dinh_du_kien = DINH_RAM_MB_THEO_MODEL.get(settings.ai_embedding_model_file)
    gioi_han = gioi_han_ram_container()

    if dinh_du_kien is None or gioi_han is None:
        return

    log.info("ram_container", gioi_han_MB=gioi_han, dinh_du_kien_MB=dinh_du_kien)

    # Ngưỡng 90%: đỉnh đo được dao động tới 38 MB giữa hai lần chạy, nên "vừa
    # khít" trên giấy nghĩa là thỉnh thoảng vượt trong thực tế.
    if dinh_du_kien <= gioi_han * 0.9:
        return

    nhe_nhat = min(DINH_RAM_MB_THEO_MODEL, key=lambda x: DINH_RAM_MB_THEO_MODEL[x])

    # CẢNH BÁO, không phải lỗi: con số đỉnh đo trên máy khác, và chặn deploy dựa
    # trên một phép ước lượng thì tệ hơn để nó chạy rồi xem thật.
    log.warning(
        "ram_co_the_khong_du",
        gioi_han_MB=gioi_han,
        dinh_du_kien_MB=dinh_du_kien,
        model_file=settings.ai_embedding_model_file,
        bi_de_boi_moi_truong="ai_embedding_model_file" in settings.model_fields_set,
        hau_qua=(
            "cgroup giết tiến trình mà KHÔNG sinh traceback: service báo live, "
            "/readyz cho encoder_ready=true index_ready=false một lúc, rồi 502, rồi lặp lại"
        ),
        goi_y=(
            f"bỏ hẳn AI_EMBEDDING_MODEL_FILE khỏi bảng biến của nền tảng để dùng mặc định "
            f"{nhe_nhat} ({DINH_RAM_MB_THEO_MODEL[nhe_nhat]} MB), và bỏ luôn AI_MIN_SCORE, "
            f"AI_MODEL_VERSION, AI_ONNX_CPU_ARENA vì mặc định đã khớp nhau"
        ),
    )


def canh_bao_model_khong_co_san(settings: Settings) -> None:
    """
    Cảnh báo khi cache không chứa biến thể ONNX đang cấu hình.

    Không phải lỗi: encoder sẽ tự tải. Nhưng trong image thì model ĐÃ được nạp
    sẵn lúc build, nên tải lại lúc chạy luôn có nghĩa là cấu hình sai — và nó
    biến một lần khởi động 1,5 giây thành hàng chục giây, đủ để nền tảng hosting
    kết luận là service chết.
    """

    if settings.fastembed_cache_path is None:
        return

    if not _co_model_trong_cache(settings, resolve_path(settings.fastembed_cache_path)):
        log.warning(
            "model_khong_co_san_trong_cache",
            cache_path=str(settings.fastembed_cache_path),
            model_file=settings.ai_embedding_model_file,
            hau_qua="sẽ tải lúc chạy, khởi động chậm hơn nhiều",
            goi_y="trong container, FASTEMBED_CACHE_PATH phải là /opt/fastembed_cache",
        )
