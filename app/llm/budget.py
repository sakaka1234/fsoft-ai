"""
Ngân sách token toàn cục. SPEC muc 5.7 và muc 11.4 mục 3.

Cửa sổ cố định theo phút, giữ trong RAM. Với 1 replica thì Redis là hạ tầng
thừa — dữ liệu này vốn tạm, mất khi restart cũng không sao.

Đây là ngân sách TOÀN CỤC cho cả ứng dụng, không phải quota theo người dùng.
Quota theo user là việc của backend Java (SPEC muc 5.1).
"""

import threading
from datetime import UTC, datetime

from app.core.errors import BudgetExhausted
from app.core.logging import get_logger

log = get_logger(__name__)

# Số ký tự trên mỗi token, dùng để ước lượng TRƯỚC khi gọi.
#
# Tokenizer của llama cắt tiếng Việt vụn hơn tiếng Anh khá nhiều, nên 3 là con
# số thận trọng. Ước lượng chỉ để giữ chỗ; sau khi gọi xong thì `settle()` thay
# bằng số thật từ phản hồi, nên sai số không tích luỹ.
CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


class TokenBudget:
    def __init__(self, limit_per_minute: int) -> None:
        self._limit = limit_per_minute
        self._lock = threading.Lock()
        self._minute_key = ""
        self._used = 0

    # ---------------------------------------------------------------

    @staticmethod
    def _key(now: datetime) -> str:
        return now.strftime("%Y%m%d%H%M")

    @staticmethod
    def _seconds_to_next_minute(now: datetime) -> int:
        return max(1, 60 - now.second)

    def _roll_over_if_needed(self, now: datetime) -> None:
        key = self._key(now)

        if key != self._minute_key:
            self._minute_key = key
            self._used = 0

    # ---------------------------------------------------------------

    def snapshot(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)

        with self._lock:
            self._roll_over_if_needed(now)

            return {
                "minute": self._minute_key,
                "limit": self._limit,
                "used": self._used,
                "remaining": max(0, self._limit - self._used),
            }

    def reserve(self, estimated_tokens: int, now: datetime | None = None) -> None:
        """
        Giữ chỗ trước khi gọi LLM. Cạn thì ném BudgetExhausted -> 429.

        Giữ chỗ TRƯỚC chứ không trừ sau: trừ sau nghĩa là đã tiêu tiền rồi mới
        biết là vượt, và nhà cung cấp sẽ trả 429 thay cho ta — chậm hơn và tính
        vào hạn mức thật.
        """

        now = now or datetime.now(UTC)

        with self._lock:
            self._roll_over_if_needed(now)

            if self._used + estimated_tokens > self._limit:
                retry_after = self._seconds_to_next_minute(now)

                log.warning(
                    "budget_exhausted",
                    used=self._used,
                    limit=self._limit,
                    requested=estimated_tokens,
                    retry_after_seconds=retry_after,
                )

                raise BudgetExhausted(
                    f"Ngân sách token toàn cục đã cạn, thử lại sau {retry_after} giây.",
                    retry_after_seconds=retry_after,
                )

            self._used += estimated_tokens

    def settle(
        self, estimated_tokens: int, actual_tokens: int, now: datetime | None = None
    ) -> None:
        """Thay ước lượng bằng số thật sau khi có phản hồi."""

        now = now or datetime.now(UTC)

        with self._lock:
            self._roll_over_if_needed(now)

            self._used = max(0, self._used - estimated_tokens + actual_tokens)

    def sync_from_provider(self, remaining_tokens: int | None, now: datetime | None = None) -> None:
        """
        Đồng bộ với `x-ratelimit-remaining-tokens` của nhà cung cấp.

        Nhà cung cấp mới là nguồn sự thật. Nếu họ báo còn ít hơn ta tưởng thì
        tin họ — có thể có tiến trình khác dùng chung tổ chức, hoặc ước lượng
        của ta lệch.
        """

        if remaining_tokens is None:
            return

        now = now or datetime.now(UTC)

        with self._lock:
            self._roll_over_if_needed(now)

            implied_used = max(0, self._limit - remaining_tokens)

            if implied_used > self._used:
                log.info(
                    "budget_synced_from_provider",
                    ours=self._used,
                    theirs=implied_used,
                )
                self._used = implied_used
