"""
Hư từ bị loại khỏi CÂU HỎI trước khi vào tầng khớp chính xác và tầng BM25.

Vì sao cần: BM25 chấm điểm theo độ hiếm. Trong một corpus vài chục thẻ, một
hư từ tiếng Việt như "từ" chỉ tình cờ xuất hiện ở đúng một thẻ (`resign` có
nghĩa "từ chức") thì IDF của nó vọt lên, và câu hỏi "từ nào nói về gia đình"
trả về `resign` với điểm 2.85 trong khi mọi thẻ khác đều 0 điểm. Toàn bộ thứ
hạng lexical bị quyết định bởi một từ không mang thông tin nào.

CHỈ lọc phía câu hỏi, KHÔNG lọc corpus. Corpus phải giữ nguyên để thống kê
IDF và độ dài tài liệu của BM25 không bị méo, và để tầng khớp chính xác vẫn
tra được thẻ dù `word` của nó trùng một hư từ.

Chứa cả bản có dấu lẫn không dấu: người Việt gõ không dấu rất phổ biến, và
bộ dò từ tiếng Anh `[a-zA-Z]{2,}` chỉ nhìn thấy bản không dấu.
"""

# Hư từ tiếng Việt, bản không dấu. Bộ dò từ tiếng Anh chỉ bắt được dạng này.
# "anh" là ca nguy hiểm nhất: nó nằm trong "tiếng anh", xuất hiện ở MỌI câu
# hỏi tra từ kiểu "X tiếng anh là gì".
_VIETNAMESE_ASCII = {
    "anh",
    "ban",
    "cho",
    "co",
    "con",
    "cua",
    "em",
    "gi",
    "hay",
    "khi",
    "la",
    "nao",
    "nghia",
    "ta",
    "toi",
    "trong",
    "tu",
    "va",
    "vao",
    "voi",
}

# Cùng những hư từ ấy nhưng có dấu, cộng thêm các từ chỉ xuất hiện ở dạng có
# dấu. Bộ dò từ tiếng Anh không bao giờ sinh ra chúng nên chúng chỉ có tác
# dụng ở tầng BM25 — đúng chỗ đang hỏng.
_VIETNAMESE_DIACRITIC = {
    "ạ",
    "ai",
    "các",
    "cách",
    "câu",
    "của",
    "cũng",
    "chỉ",
    "cho",
    "có",
    "dùng",
    "dụ",
    "đã",
    "đang",
    "đó",
    "được",
    "gì",
    "hãy",
    "hơn",
    "khi",
    "không",
    "là",
    "làm",
    "mà",
    "mình",
    "một",
    "này",
    "nào",
    "nhé",
    "nghĩa",
    "những",
    "rất",
    "sao",
    "sẽ",
    "thế",
    "thì",
    "tiếng",
    "tôi",
    "từ",
    "và",
    "vào",
    "về",
    "ví",
    "việt",
    "với",
}

# Hư từ tiếng Anh trong câu hỏi, không phải từ vựng cần tra.
_ENGLISH = {
    "a",
    "an",
    "are",
    "at",
    "does",
    "example",
    "give",
    "how",
    "in",
    "is",
    "me",
    "mean",
    "on",
    "please",
    "sentence",
    "the",
    "to",
    "use",
    "what",
    "word",
}

QUERY_STOPWORDS = _VIETNAMESE_ASCII | _VIETNAMESE_DIACRITIC | _ENGLISH


def strip_stopwords(tokens: list[str]) -> list[str]:
    """Bỏ hư từ. Câu hỏi toàn hư từ sẽ trả về danh sách rỗng."""

    return [token for token in tokens if token not in QUERY_STOPWORDS]
