"""
Semantic cache trong RAM. SPEC muc 5.7.

Danh sách có giới hạn, quét tuyến tính. 500 entry x 384 chiều dot product mất
dưới 1ms — nhanh hơn một vòng đi Redis. Đuổi theo LRU khi đầy.

Khoá cache gồm HAI phần, và cả hai đều bắt buộc:
  - vector câu hỏi, so bằng cosine (đó là chỗ "semantic")
  - hash phạm vi: tập deck cho phép + tập thẻ đã truy hồi

Thiếu phần thứ hai là lỗ hổng bảo mật: cùng một câu hỏi từ hai người dùng có
quyền khác nhau sẽ nhận cùng một câu trả lời, dựng từ bộ thẻ của người kia.
"""

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np


@dataclass(slots=True)
class CacheEntry:
    vector: np.ndarray
    scope_hash: str
    answer: str
    citations: list[dict]
    expires_at: datetime
    last_used_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def scope_hash(allowed_deck_ids: list[int], card_ids: list[int]) -> str:
    payload = f"decks={sorted(set(allowed_deck_ids))}|cards={sorted(set(card_ids))}"

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SemanticCache:
    def __init__(
        self,
        *,
        threshold: float = 0.97,
        max_size: int = 500,
        ttl_hours: int = 24,
        enabled: bool = True,
    ) -> None:
        self._threshold = threshold
        self._max_size = max_size
        self._ttl = timedelta(hours=ttl_hours)
        self._enabled = enabled

        self._lock = threading.Lock()
        self._entries: list[CacheEntry] = []

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # ---------------------------------------------------------------

    def _drop_expired(self, now: datetime) -> None:
        self._entries = [entry for entry in self._entries if entry.expires_at > now]

    def get(
        self, query_vector: np.ndarray, scope: str, now: datetime | None = None
    ) -> CacheEntry | None:
        if not self._enabled:
            return None

        now = now or datetime.now(UTC)
        vector = np.asarray(query_vector, dtype=np.float32)

        with self._lock:
            self._drop_expired(now)

            candidates = [entry for entry in self._entries if entry.scope_hash == scope]

            if not candidates:
                return None

            matrix = np.asarray([entry.vector for entry in candidates], dtype=np.float32)
            scores = matrix @ vector

            best = int(np.argmax(scores))

            if float(scores[best]) < self._threshold:
                return None

            hit = candidates[best]
            hit.last_used_at = now

            return hit

    def put(
        self,
        query_vector: np.ndarray,
        scope: str,
        answer: str,
        citations: list[dict],
        now: datetime | None = None,
    ) -> None:
        if not self._enabled:
            return

        now = now or datetime.now(UTC)

        with self._lock:
            self._drop_expired(now)

            self._entries.append(
                CacheEntry(
                    vector=np.asarray(query_vector, dtype=np.float32),
                    scope_hash=scope,
                    answer=answer,
                    citations=citations,
                    expires_at=now + self._ttl,
                    last_used_at=now,
                )
            )

            # LRU: đầy thì bỏ entry lâu không dùng nhất.
            if len(self._entries) > self._max_size:
                self._entries.sort(key=lambda entry: entry.last_used_at)
                self._entries = self._entries[-self._max_size :]

    def invalidate_all(self) -> None:
        """
        Gọi sau khi index đổi.

        Câu trả lời đã cache dựng từ nội dung thẻ; thẻ đổi hoặc bị xoá thì câu
        trả lời cũ trở thành sai. Xoá sạch rẻ hơn nhiều so với lần theo từng
        thẻ xem entry nào bị ảnh hưởng.
        """

        self.clear()
