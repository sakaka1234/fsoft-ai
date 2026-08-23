"""
Vị từ văn bản dùng chung, không phụ thuộc tầng nào.

Ở đây mới có một thứ, và nó nằm đây thay vì nằm trong `app/quiz/` hay
`app/vocab/` vì cả hai chỗ đều cần đúng phép kiểm ấy với đúng một lý do: khẳng
định model trả lời BẰNG TIẾNG VIỆT chứ không phải tiếng Anh. Nhân bản một biểu
thức chính quy có vai trò kiểm soát là cách chắc chắn nhất để hai bản trôi khỏi
nhau rồi một bên âm thầm lỏng hơn bên kia.
"""

import re

# Chữ cái CHỈ có trong bảng chữ tiếng Việt. Cố ý không liệt kê a-z: một câu
# tiếng Anh cũng toàn a-z, nên chỉ những chữ mang dấu mới phân biệt được.
VIETNAMESE_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)


def co_dau_tieng_viet(text: str) -> bool:
    return bool(VIETNAMESE_RE.search(text))
