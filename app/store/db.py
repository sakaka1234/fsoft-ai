"""
Kết nối SQLite. SPEC muc 5.3 và muc 7.

Không ORM, không migration framework — SQL thuần trong migrations/*.sql.

Về concurrency: đường nóng (retrieval, chat, quiz) đọc hoàn toàn từ vector
index trong RAM, không chạm SQLite. SQLite chỉ bị đụng lúc khởi động và
trong vòng lặp đồng bộ nền — một writer duy nhất. Vì vậy một connection dùng
chung với `check_same_thread=False` cộng một Lock là đủ, không cần aiosqlite.

PRAGMA đặt ở đây chứ không đặt trong file migration: `synchronous` là thiết
lập theo từng connection nên chạy một lần trong migration sẽ không có tác dụng.
"""

import os
import sqlite3
import threading
from pathlib import Path

import anyio

from app.config import PROJECT_ROOT
from app.core.logging import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


class Database:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ---------------------------------------------------------------
    # Vòng đời
    # ---------------------------------------------------------------

    def connect_sync(self) -> None:
        """Mở connection, bật WAL, chạy migration nếu bảng chưa có."""

        self._kiem_ghi_duoc()

        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row

        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")

        self._conn = conn

        self._run_migrations_sync()

        log.info("db_connected", path=str(self._path))

    def _kiem_ghi_duoc(self) -> None:
        """
        Kiểm quyền ghi TRƯỚC khi mở SQLite, để lỗi nói được nó là lỗi gì.

        Không có bước này thì `PermissionError: [Errno 13] Permission denied:
        '/app/data'` nổ ra từ tận trong `pathlib.mkdir`, chôn dưới sáu tầng
        traceback của starlette và anyio, và không nhắc gì tới biến môi trường
        cần sửa. Đây là lỗi ĐÃ XẢY RA THẬT khi deploy lên Render: `AI_DB_PATH`
        được đặt bằng đường dẫn tương đối `./data/fsoft-ai.db`, giải ra thành
        `/app/data`, mà trong image `/app` thuộc root còn service chạy bằng user
        không đặc quyền.
        """

        thu_muc = self._path.parent

        try:
            thu_muc.mkdir(parents=True, exist_ok=True)

        # Bắt cả `OSError` chứ không riêng `PermissionError`: trên Linux lỗi thật
        # là `PermissionError` (Errno 13), nhưng đường dẫn hỏng theo kiểu khác
        # cũng ném `NotADirectoryError` hay `FileExistsError` — cùng một nguyên
        # nhân gốc là AI_DB_PATH sai, nên cùng một lời hướng dẫn.
        except OSError as exc:
            raise PermissionError(
                f"Không tạo được thư mục chứa SQLite: {thu_muc}\n"
                f"AI_DB_PATH đang là {self._path!s}.\n"
                f"Lỗi gốc: {type(exc).__name__}: {exc}\n"
                "\n"
                "Trong container, chỉ /data được cấp quyền ghi cho user chạy service. "
                "Đường dẫn TƯƠNG ĐỐI sẽ giải ra dưới /app và /app thuộc root.\n"
                "Sửa: đặt AI_DB_PATH=/data/fsoft-ai.db (đường dẫn tuyệt đối).\n"
                "Nếu nền tảng hosting không cho gắn volume thì /data vẫn ghi được, "
                "chỉ là mất dữ liệu khi container bị thay — service tự đồng bộ lại."
            ) from exc

        # Thư mục tồn tại chưa đủ: nó có thể thuộc user khác.
        if not os.access(thu_muc, os.W_OK):
            raise PermissionError(
                f"Thư mục {thu_muc} tồn tại nhưng KHÔNG ghi được "
                f"(uid đang chạy: {getattr(os, 'geteuid', lambda: 'n/a')()}).\n"
                f"AI_DB_PATH đang là {self._path!s}.\n"
                "\n"
                "Thường gặp khi bind-mount một thư mục của host vào container: quyền "
                "của host thắng. Sửa bằng cách dùng volume có tên, hoặc chown thư mục "
                "đó trên host, hoặc đặt AI_DB_PATH=/data/fsoft-ai.db."
            )

    def close_sync(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _run_migrations_sync(self) -> None:
        conn = self._require_conn()

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            with self._lock:
                conn.executescript(path.read_text(encoding="utf-8"))
                conn.commit()

            log.info("migration_applied", file=path.name)

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database chưa connect. Gọi connect_sync() trước.")

        return self._conn

    # ---------------------------------------------------------------
    # API đồng bộ — dùng trực tiếp trong threadpool hoặc trong test
    # ---------------------------------------------------------------

    def execute_sync(self, sql: str, params: tuple = ()) -> None:
        conn = self._require_conn()

        with self._lock:
            conn.execute(sql, params)
            conn.commit()

    def execute_many_sync(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return

        conn = self._require_conn()

        with self._lock:
            conn.executemany(sql, rows)
            conn.commit()

    def query_sync(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = self._require_conn()

        with self._lock:
            return conn.execute(sql, params).fetchall()

    # ---------------------------------------------------------------
    # API bất đồng bộ — bọc threadpool để không chặn event loop
    # ---------------------------------------------------------------

    async def execute(self, sql: str, params: tuple = ()) -> None:
        await anyio.to_thread.run_sync(self.execute_sync, sql, params)

    async def execute_many(self, sql: str, rows: list[tuple]) -> None:
        await anyio.to_thread.run_sync(self.execute_many_sync, sql, rows)

    async def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return await anyio.to_thread.run_sync(self.query_sync, sql, params)
