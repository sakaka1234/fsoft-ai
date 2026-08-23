"""
Đánh dấu thẻ người học đã có. Dùng chung cho `/vocab/extract` và `/vocab/generate`.

Tách ra khỏi `extractor.py` vì đây là một phép kiểm RANH GIỚI PHẠM VI: nó quyết
định `existing_card_id` nào được phép lộ ra ngoài. Nhân bản loại kiểm tra đó
sang một file thứ hai là kiểu trôi dạt nguy hiểm nhất — hai bản sẽ giống nhau
đúng tới lần sửa đầu tiên.
"""

from app.retrieval.search_index import SearchIndex
from app.schemas.vocab import VocabCandidate


def danh_dau_trung(
    index: SearchIndex, ung_vien: list[VocabCandidate], allowed_deck_ids: list[int]
) -> int:
    """
    Đánh dấu, KHÔNG xoá. Trả số ứng viên đã có sẵn trong bộ thẻ.

    Từ người học đã có vẫn nằm trong kết quả để giao diện bỏ tick sẵn thay vì
    giấu đi — giấu thì người dùng mất một từ mà không hiểu vì sao.

    `lookup_word` chỉ tìm trong `allowed_deck_ids`, nên `existing_card_id` không
    bao giờ tiết lộ sự tồn tại của một thẻ ngoài phạm vi.
    """

    da_co = 0

    for the in ung_vien:
        trung = index.lookup_word(the.word, allowed_deck_ids)

        if trung:
            the.already_in_deck = True
            # Nhỏ nhất, để hai lượt gọi giống nhau cho cùng một kết quả.
            the.existing_card_id = min(trung)
            da_co += 1

    return da_co
