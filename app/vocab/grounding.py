"""
Kiểm một ứng viên từ vựng có THẬT SỰ bám vào đoạn văn người dùng gửi không.
SPEC muc 8.5b.

Vì sao tách thành module hàm thuần: đây là phần duy nhất của tính năng trích
xuất có thể test mà không cần dựng `Service`, không cần model, không cần mock
LLM. Cùng lý do `app/quiz/validator.py` tách khỏi `generator.py`.

Vì sao nó tồn tại: `example_sentence` là kênh duy nhất trong phản hồi mà LLM
được phép trả về một câu tiếng Anh dài. Kết quả của endpoint này sẽ được backend
LƯU THÀNH THẺ, thẻ nằm trong deck chia sẻ được, rồi đồng bộ ngược vào chính
corpus RAG. Nghĩa là bất cứ thứ gì lọt qua đây đều thành nội dung lưu trữ mà
người khác đọc. Bắt buộc câu đó phải có sẵn trong văn bản gốc là cách đóng kênh
ấy lại — tất định, không phụ thuộc model có nghe lời hay không.
"""

import re
import unicodedata

# Token tiếng Anh: hai chữ cái trở lên, chỉ a-z.
#
# CỐ Ý KHÔNG dùng lại `extract_english_tokens` của app/retrieval/hybrid.py: hàm
# đó áp `QUERY_STOPWORDS`, vốn được chỉnh cho "vỏ câu hỏi tiếng Việt" (what,
# mean, sentence...) và tài liệu của nó ghi rõ chỉ dùng phía truy vấn, không
# bao giờ phía ngữ liệu. Áp vào đây sẽ loại từ hợp lệ ra khỏi whitelist, rồi
# bộ lọc bên dưới bỏ nhầm những thẻ tốt.
#
# Ở đây whitelist là phép KIỂM TƯ CÁCH THÀNH VIÊN, không phải danh sách ứng
# viên — nên giữ cả `the` lẫn `and` là đúng, không phải thiếu sót.
_TOKEN_TIENG_ANH_RE = re.compile(r"\b[a-zA-Z]{2,}\b")

# Quy các biến thể hình thức về một dạng.
#
# Ở đây dán ký tự THẬT là an toàn và dễ đọc hơn escape, khác với
# app/retrieval/context.py: bảng này toàn dấu câu NHÌN THẤY ĐƯỢC, còn quy tắc
# "phải viết bằng escape" bên kia là để chặn ký tự VÔ HÌNH nằm lẫn trong mã
# nguồn. Hai chuyện khác nhau.
_BANG_QUY = str.maketrans(
    {
        "‘": "'",  # dấu nháy đơn mở
        "’": "'",  # dấu nháy đơn đóng
        "‛": "'",
        "′": "'",  # prime
        "“": '"',  # nháy kép mở
        "”": '"',  # nháy kép đóng
        "„": '"',
        "‟": '"',
        "″": '"',
        "‑": "-",  # gạch nối không ngắt dòng
        "‒": "-",
        "–": "-",  # en dash
        "—": "-",  # em dash
        "−": "-",  # dấu trừ
    }
)

_KHOANG_TRANG_RE = re.compile(r"\s+")

# Ký tự không bao giờ xuất hiện trong một thẻ từ vựng lành mạnh, nhưng là vật
# liệu cơ bản của payload XSS và của việc giả mạo cấu trúc JSON/HTML.
_KY_TU_CAM_RE = re.compile(r"[<>{}\[\]\\]|https?://", re.IGNORECASE)


def english_tokens(text: str) -> set[str]:
    """Tập token tiếng Anh phân biệt, đã hạ chữ thường."""

    return {tu.lower() for tu in _TOKEN_TIENG_ANH_RE.findall(text)}


def normalize_for_grounding(text: str) -> str:
    """
    Chuẩn hoá để so khớp câu, KHÔNG phải để hiển thị.

    Hấp thụ đúng những sai lệch hình thức mà LLM hay tạo ra khi chép lại một
    câu — đổi nháy thẳng thành nháy cong, gộp/tách khoảng trắng, đổi hoa thường
    — mà vẫn giữ nguyên từ ngữ, nên một câu BỊA ra vẫn bị bắt.

    Cố ý KHÔNG bỏ dấu câu như `normalize_meaning` của app/quiz/distractors.py:
    bỏ dấu câu sẽ khiến hai câu khác nhau về ranh giới câu trở nên khớp nhau,
    tức nới lỏng đúng cái đang cần chặt.
    """

    cleaned = unicodedata.normalize("NFC", text)
    cleaned = cleaned.translate(_BANG_QUY)
    cleaned = _KHOANG_TRANG_RE.sub(" ", cleaned)

    return cleaned.strip().casefold()


def sentence_is_from_text(sentence: str, haystack_normalized: str) -> bool:
    """
    Câu ví dụ có nằm nguyên trong đoạn văn gốc không.

    `haystack_normalized` là `normalize_for_grounding(đoạn văn)` đã dựng SẴN
    MỘT LẦN cho cả request — chuẩn hoá lại 4.000 ký tự cho từng ứng viên là
    lãng phí không cần thiết.
    """

    if not sentence or not sentence.strip():
        return False

    return normalize_for_grounding(sentence) in haystack_normalized


def word_is_from_text(word: str, tokens: set[str]) -> bool:
    """
    Từ có xuất phát từ đoạn văn không — kiểm LỎNG HƠN câu, và cố ý như vậy.

    Prompt yêu cầu `word` ở dạng từ điển, nên `running` trong văn bản thành
    `run` trên thẻ là ĐÚNG chứ không phải bịa. Đòi khớp nguyên văn ở đây sẽ
    loại bỏ chính hành vi mình vừa yêu cầu model làm.

    Rủi ro thật cần chặn là từ KHÔNG HỀ có trong văn bản — model nhớ ra từ kho
    huấn luyện. Chung tiền tố đủ dài đã đủ để chặn việc đó.
    """

    tu = word.lower().strip()

    if not tu:
        return False

    if tu in tokens:
        return True

    if len(tu) < 3:
        return False

    return any(_chung_goc(tu, token) for token in tokens)


def field_is_safe(value: str) -> bool:
    """
    Không chứa vật liệu XSS / giả mạo cấu trúc.

    Đây là chốt chặn TẤT ĐỊNH, chạy sau khi LLM đã trả lời, nên nó đứng vững
    kể cả khi prompt injection thành công hoàn toàn. Prompt chỉ là lời khuyên;
    hàm này mới là luật.
    """

    return not _KY_TU_CAM_RE.search(value) and "\n" not in value


def _chung_goc(a: str, b: str, toi_thieu: int = 5) -> bool:
    """Một từ là dạng biến đổi của từ kia."""

    if len(b) < 3:
        return False

    if a.startswith(b) or b.startswith(a):
        return True

    chung = 0

    while chung < len(a) and chung < len(b) and a[chung] == b[chung]:
        chung += 1

    return chung >= toi_thieu
