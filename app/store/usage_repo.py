"""
Nhật ký mọi lượt gọi. M3 ghi, M7 đọc để dựng `GET /internal/v1/stats`.

Không có profile_id ở đâu cả, đúng nguyên tắc SPEC muc 5.1: service này không
biết gì về người dùng. Backend Java ghi bảng usage riêng có profile_id.

Lưu ý khi đọc bảng này: một lượt chat TỐN token sinh ra dòng do `LlmClient`
ghi, còn lượt chat MIỄN PHÍ sinh ra dòng do `ChatOrchestrator` ghi với
`provider='local'`. Cả hai đều `task='CHAT'`, nên đếm theo `task` là ra đúng
tổng số lượt chat.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.store.db import Database

# Ba nhánh trả lời không tốn token nào. SPEC muc 11.8 đặt mục tiêu tổng tỷ lệ
# của chúng >= 40%: dưới ngưỡng này nghĩa là đang trả tiền cho việc mà dữ liệu
# cục bộ làm được miễn phí.
FREE_SOURCES = ("DIRECT_LOOKUP", "CACHE", "CANNED")


@dataclass(slots=True)
class UsageStats:
    """Số liệu thô. Việc so với ngưỡng do tầng API làm, không làm ở đây."""

    since: datetime
    until: datetime

    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    chat_turns: int = 0
    chat_tokens: int = 0
    free_turns: int = 0
    latency_p95_ms: int = 0

    by_task: dict[str, int] = field(default_factory=dict)
    by_answer_source: dict[str, int] = field(default_factory=dict)

    @property
    def free_ratio(self) -> float:
        return self.free_turns / self.chat_turns if self.chat_turns else 0.0

    @property
    def avg_tokens_per_chat(self) -> float:
        return self.chat_tokens / self.chat_turns if self.chat_turns else 0.0

    @property
    def error_rate(self) -> float:
        return self.failed_calls / self.calls if self.calls else 0.0


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

    # ---------------------------------------------------------------
    # Đọc — SPEC muc 11.8
    # ---------------------------------------------------------------

    async def stats(self, since: datetime, until: datetime) -> UsageStats:
        """
        Tổng hợp cho `GET /internal/v1/stats`.

        Cửa sổ nửa mở `[since, until)` để hai lần gọi liền kề không đếm trùng
        dòng nằm đúng ranh giới.

        `created_at` lưu dạng chuỗi ISO-8601 UTC nên so sánh chuỗi cho kết quả
        y hệt so sánh thời gian — miễn là mọi dòng đều ghi cùng định dạng, và
        `insert()` ở trên là nơi DUY NHẤT ghi bảng này.
        """

        window = (since.astimezone(UTC).isoformat(), until.astimezone(UTC).isoformat())

        rows = await self._db.query(
            """
            SELECT task, answer_source, success,
                   COUNT(*)                AS calls,
                   SUM(prompt_tokens)      AS prompt_tokens,
                   SUM(completion_tokens)  AS completion_tokens
              FROM usage_log
             WHERE created_at >= ? AND created_at < ?
             GROUP BY task, answer_source, success
            """,
            window,
        )

        result = UsageStats(since=since, until=until)

        for row in rows:
            calls = int(row["calls"])
            prompt = int(row["prompt_tokens"] or 0)
            completion = int(row["completion_tokens"] or 0)

            result.calls += calls
            result.prompt_tokens += prompt
            result.completion_tokens += completion

            if not row["success"]:
                result.failed_calls += calls

            result.by_task[row["task"]] = result.by_task.get(row["task"], 0) + calls

            source = row["answer_source"]

            if row["task"] == "CHAT":
                result.chat_turns += calls
                result.chat_tokens += prompt + completion

                if source is not None:
                    result.by_answer_source[source] = result.by_answer_source.get(source, 0) + calls

                if source in FREE_SOURCES:
                    result.free_turns += calls

        # p95 độ trễ tính riêng: không gộp được vào GROUP BY ở trên, và SQLite
        # không có hàm phân vị sẵn.
        result.latency_p95_ms = await self._latency_p95(window)

        return result

    async def _latency_p95(self, window: tuple[str, str]) -> int:
        """
        Phân vị 95 của độ trễ lượt chat.

        Làm bằng OFFSET thay vì kéo hết về Python: bảng này chỉ bị dọn theo
        thời gian nên có thể phình rất to.
        """

        total = await self._db.query(
            "SELECT COUNT(*) AS n FROM usage_log "
            "WHERE task = 'CHAT' AND created_at >= ? AND created_at < ?",
            window,
        )

        count = int(total[0]["n"]) if total else 0

        if count == 0:
            return 0

        # Chỉ số phần tử thứ 95%, đếm từ 0. Với n nhỏ thì rơi về phần tử cuối.
        offset = min(count - 1, int(count * 0.95))

        rows = await self._db.query(
            "SELECT latency_ms FROM usage_log "
            "WHERE task = 'CHAT' AND created_at >= ? AND created_at < ? "
            "ORDER BY latency_ms ASC LIMIT 1 OFFSET ?",
            (*window, offset),
        )

        return int(rows[0]["latency_ms"]) if rows else 0
