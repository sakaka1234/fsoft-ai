"""
Nhật ký gọi LLM. M3 mới dùng nhiều — ở M1 chỉ cần chỗ ghi.

Không có profile_id ở đâu cả, đúng nguyên tắc SPEC muc 5.1: service này không
biết gì về người dùng. Backend Java ghi bảng usage riêng có profile_id.
"""

from datetime import UTC, datetime

from app.store.db import Database


class UsageRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(
        self,
        *,
        task: str,
        provider: str,
        model: str,
        latency_ms: int,
        success: bool,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        answer_source: str | None = None,
        intent: str | None = None,
        error_code: str | None = None,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO usage_log (
                task, provider, model, answer_source, intent,
                prompt_tokens, completion_tokens, latency_ms,
                success, error_code, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task,
                provider,
                model,
                answer_source,
                intent,
                prompt_tokens,
                completion_tokens,
                latency_ms,
                1 if success else 0,
                error_code,
                datetime.now(UTC).isoformat(),
            ),
        )
