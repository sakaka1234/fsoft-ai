"""
Con trỏ đồng bộ. Bảng key-value đơn giản.

Key dùng: last_sync_ts, last_full_sweep_at, last_sync_error.
"""

from app.store.db import Database

KEY_LAST_SYNC_TS = "last_sync_ts"
KEY_LAST_FULL_SWEEP_AT = "last_full_sweep_at"
KEY_LAST_SYNC_ERROR = "last_sync_error"


class SyncStateRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> str | None:
        rows = await self._db.query("SELECT value FROM sync_state WHERE key = ?", (key,))

        return rows[0]["value"] if rows else None

    async def set(self, key: str, value: str) -> None:
        await self._db.execute(
            """
            INSERT INTO sync_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    async def clear(self, key: str) -> None:
        await self._db.execute("DELETE FROM sync_state WHERE key = ?", (key,))
