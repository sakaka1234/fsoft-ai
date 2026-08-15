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

        self._path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row

        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")

        self._conn = conn

        self._run_migrations_sync()

        log.info("db_connected", path=str(self._path))

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
