"""
Cache kết quả tra từ, khoá bằng chuỗi chính xác. SPEC muc 8.5d.

Vì sao KHÔNG dùng lại `SemanticCache` của `/chat`, dù nghe như cùng một việc:

1. Khoá khác hẳn. `SemanticCache` so bằng cosine giữa hai vector câu hỏi — đó
   là chỗ "semantic". Ở đây đầu vào là một TỪ, và "donut" với "doughnut" là hai
   mục khác nhau chứ không phải hai cách hỏi cùng một điều. Khớp chuỗi chính
   xác vừa đúng hơn vừa rẻ hơn (không phải embed).

2. Phạm vi khác hẳn, và đây mới là điểm quan trọng. Chú thích của
   `SemanticCache` nói rõ: thiếu `scope_hash` là LỖ HỔNG BẢO MẬT, vì câu trả
   lời của `/chat` được dựng TỪ THẺ của người dùng. Ở đây thì ngược lại — nghĩa
   của từ "donut" không phụ thuộc bộ thẻ của ai cả. Không có gì để rò rỉ, nên
   cache dùng chung toàn cục là an toàn, và đó chính là chỗ nó có giá trị: một
   người tra rồi thì mọi người sau đều miễn phí.

Ngữ cảnh nằm TRONG khoá vì nó đổi câu trả lời: "bank" trong câu về dòng sông
khác "bank" trong câu về tiền.
"""

import hashlib
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from app.schemas.vocab import VocabCandidate

# 2.000 mục, mỗi mục vài trăm byte — dưới 1 MB, không đáng kể so với đỉnh 317 MB
# của tokenizer ONNX. Tra từ theo phân bố Zipf: vài trăm từ thông dụng chiếm
# phần lớn lưu lượng, nên một cache cỡ này bắt được gần hết phần lặp lại.
CACHE_MAX = 2000

# Một tuần. Nghĩa của từ không đổi theo ngày, khác hẳn cache của `/chat` vốn để
# 24 giờ vì câu trả lời dựng trên bộ thẻ mà bộ thẻ thì thay đổi liên tục.
CACHE_TTL_HOURS = 168


def khoa_cache(word: str, context: str) -> str:
    """
    Khoá gồm cả ngữ cảnh: cùng một từ trong hai câu khác nhau là hai câu trả
    lời khác nhau. Băm ngữ cảnh thay vì giữ nguyên để khoá không phình ra.
    """

    bam = hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]

    return f"{word}\x00{bam}"


class WordCache:
    def __init__(self, *, max_size: int = CACHE_MAX, ttl_hours: int = CACHE_TTL_HOURS) -> None:
        self._max_size = max_size
        self._ttl = timedelta(hours=ttl_hours)

        # `threading.Lock` chứ không phải `asyncio.Lock`: mọi thao tác ở đây đều
        # đồng bộ và cực ngắn, còn `Encoder` thì chạy trên thread pool nên tiến
        # trình này không thuần một luồng.
        self._lock = threading.Lock()
        self._muc: OrderedDict[str, tuple[VocabCandidate, datetime]] = OrderedDict()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._muc)

    def get(self, khoa: str, now: datetime | None = None) -> VocabCandidate | None:
        bay_gio = now or datetime.now(UTC)

        with self._lock:
            muc = self._muc.get(khoa)

            if muc is None:
                return None

            the, het_han = muc

            if het_han <= bay_gio:
                del self._muc[khoa]

                return None

            self._muc.move_to_end(khoa)

            # Trả BẢN SAO. Người gọi sẽ gắn `already_in_deck` và
            # `existing_card_id` lên thẻ — mà hai cờ đó phụ thuộc phạm vi deck
            # của TỪNG người dùng. Trả thẳng đối tượng trong cache thì cờ của
            # người này ghi đè lên bản dùng chung, và người tiếp theo nhận một
            # `existing_card_id` trỏ vào thẻ họ không có quyền thấy.
            return the.model_copy(deep=True)

    def put(self, khoa: str, the: VocabCandidate, now: datetime | None = None) -> None:
        bay_gio = now or datetime.now(UTC)

        with self._lock:
            # Lưu bản sao SẠCH CỜ, vì cùng lý do ở `get`: cache là dữ liệu chung,
            # còn hai cờ khử trùng là của riêng một phạm vi deck.
            sach = the.model_copy(deep=True)
            sach.already_in_deck = False
            sach.existing_card_id = None

            self._muc[khoa] = (sach, bay_gio + self._ttl)
            self._muc.move_to_end(khoa)

            while len(self._muc) > self._max_size:
                self._muc.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._muc.clear()
