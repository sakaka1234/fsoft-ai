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
