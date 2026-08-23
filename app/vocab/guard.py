"""
Chốt chặn tất định cho thẻ do model TỰ NGHĨ RA (M9). SPEC muc 8.5c.

Vì sao là một module riêng chứ không dùng lại `app/vocab/grounding.py`:

`grounding.py` kiểm XUẤT XỨ. `sentence_is_from_text` bảo đảm mọi câu tiếng Anh
được lưu xuống đã nằm sẵn trong văn bản chính người dùng dán vào — không một
chữ tiếng Anh mới nào lọt vào hệ thống. Đó là một bảo đảm rất mạnh, và nó dựa
hoàn toàn vào việc CÓ một văn bản gốc để đối chiếu.

M9 không có văn bản gốc. Xuất xứ biến mất và không dựng lại được. Mọi hàm ở
đây chỉ kiểm HÌNH DẠNG: độ dài, bộ ký tự, ngôn ngữ, và tính nhất quán nội tại
giữa hai trường của cùng một thẻ.

    M8: model KHÔNG THỂ đưa văn xuôi tiếng Anh mới vào dữ liệu lưu trữ.
    M9: model CÓ THỂ, nhưng chỉ dạng câu đơn ngắn, thuần chữ cái, không đường
        dẫn — và phải có người tick trước khi bất cứ thứ gì được lưu.

Nói cách khác: ở M9, bước người dùng xác nhận KHÔNG phải chi tiết giao diện.
Nó là lần duy nhất một con người nhìn vào nội dung này trước khi nó thành thẻ
chia sẻ được rồi đồng bộ ngược vào chính corpus RAG. Các hàm ở đây thu hẹp cái
mà con người đó phải soát, chúng không thay được con người đó.

Hàm thuần, không I/O, không phụ thuộc `Service` — test được mà không cần
fixture, cùng lý do `grounding.py` và `app/quiz/validator.py` tồn tại.
"""

import re
import unicodedata

from app.core.text import co_dau_tieng_viet
from app.retrieval.context import _CONTROL_RE
from app.vocab.grounding import _BANG_QUY

# Trần độ dài, CHẶT HƠN HẲN `_DAI_NHAT` của M8 — và cố ý không dùng chung.
#
# Bảng của M8 (`app/vocab/extractor.py`) được cân cho câu CHÉP LẠI: 300 ký tự
# ở đó là 300 ký tự người dùng đã tự đọc và tự dán. Ở đây cũng 300 ký tự thì
# lại là 300 ký tự văn xuôi do model viết ra mà chưa ai đọc, nhân với số thẻ.
#
# Đây là chốt duy nhất CHỨNG MINH ĐƯỢC giới hạn thiệt hại: mọi chốt khác đều
# là "loại bỏ thứ trông xấu", chốt này đặt trần cứng lên khối lượng.
#
# Gộp hai bảng làm một sẽ buộc một trong hai phải sai, nên chúng tách nhau.
MAX_FIELD_CHARS = {
    "word": 32,
    "phonetic": 40,
    "meaning": 100,
    "definition_en": 120,
    "example_sentence": 140,
    "example_meaning": 140,
    "topic_understood": 60,
}

# Nhiều nhất ba từ, chỉ a-z, cho phép dấu nối và dấu nháy đơn Ở GIỮA.
#
# Nhận:  work · take on · get away with · well-known · o'clock · mother-in-law
# Loại:  chữ số · dấu chấm · gạch dưới · dấu nối ở đầu/cuối · cụm bốn từ trở lên
#
# CỐ Ý loại café, naïve, déjà vu. Giữ ASCII để `word` tương thích với mọi thứ
# hạ nguồn vốn đã giả định vậy (`_word_index`, BM25, `lookup_word` đều tra trên
# `word.lower()`), và để luật "trường tiếng Anh không được có dấu tiếng Việt" ở
# dưới không nhập nhằng với chữ Latin có dấu.
WORD_RE = re.compile(r"^[a-z]+(?:['-][a-z]+)*(?: [a-z]+(?:['-][a-z]+)*){0,2}$")

# Dấu câu được phép trong các trường văn xuôi. Đây là ALLOWLIST chứ không phải
# denylist, và đó là điểm khác quan trọng nhất so với `field_is_safe` của M8.
#
# `field_is_safe` chặn `< > { } [ ] \` và `http://`. Nhưng chuỗi
#     " autofocus onfocus=alert(1) x="
# không chứa ký tự nào trong danh sách đó, không có `http`, và lọt qua nguyên
# vẹn — rồi thoát ra khỏi bất kỳ thuộc tính HTML nào nó được nhét vào. Không
# cần một dấu `<` nào cả. `evil.com`, `javascript:`, `&#60;`, `\r` cũng lọt.
#
# Allowlist thì ngược lại: `=`, `&`, `@`, `$`, `` ` ``, `|`, `~`, `^`, `_`, `*`
# và mọi thứ khác bị loại vì KHÔNG được kể tên, chứ không phải vì có ai đó nhớ
# ra mà cấm. Nhờ vậy các luật chặn `&#\d` và `&[a-z]+;` thành thừa.
_DAU_CAU = frozenset(".,'\"?!;:()-")

# Tên miền trần: `evil.com`, `bit.ly`. Đòi ít nhất HAI chữ cái sau dấu chấm nên
# không đụng `e.g.`, `i.e.`, `U.S.` — những chỗ đó chỉ có một chữ trước dấu kế.
_TEN_MIEN_RE = re.compile(r"[A-Za-z0-9]\.[A-Za-z]{2,}")

# IPA: bọc hai dấu gạch chéo, bên trong không được có khoảng trắng hay ký tự
# cấu trúc. Cố ý lỏng về bộ chữ — IPA dùng rất nhiều ký hiệu Unicode, và siết
# chặt ở đây chỉ tổ vứt phiên âm đúng.
_PHIEN_AM_RE = re.compile(r"^/[^\s/<>{}\[\]\\]{1,38}/$")

_TOKEN_RE = re.compile(r"[a-z']+")
_KHOANG_TRANG_RE = re.compile(r"\s+")


def chuan_hoa(text: str) -> str:
    """
    NFC -> quy dấu nháy/gạch -> gộp khoảng trắng -> chữ thường.

    Thứ tự quan trọng, và đổi `’` thành `'` TRƯỚC khi so khớp không phải chuyện
    thẩm mỹ: `lookup_word` tra dict trên `word.lower()`
    (`app/embedding/vector_index.py`), nên `don’t` và `don't` sẽ thành hai thẻ
    vĩnh viễn không bao giờ khử trùng được với nhau.
    """

    sach = unicodedata.normalize("NFC", text).translate(_BANG_QUY)

    return _KHOANG_TRANG_RE.sub(" ", sach).strip().casefold()


def tu_hop_le(word: str) -> bool:
    """`word` là trường mất neo: ở M8 nó được văn bản người dùng giữ, ở đây không."""

    return bool(word) and len(word) <= MAX_FIELD_CHARS["word"] and bool(WORD_RE.match(word))


def phien_am_hop_le(phonetic: str) -> bool:
    return len(phonetic) <= MAX_FIELD_CHARS["phonetic"] and bool(_PHIEN_AM_RE.match(phonetic))


def field_an_toan(value: str, *, ten: str, tieng_viet: bool | None) -> bool:
    """
    Chốt chặn cho một trường văn xuôi.

    `tieng_viet=True`  : BẮT BUỘC có dấu tiếng Việt (nếu không, model đang trả
                         lời bằng tiếng Anh vào ô nghĩa tiếng Việt).
    `tieng_viet=False` : CẤM dấu tiếng Việt (giữ trường tiếng Anh sạch, và chặn
                         luôn việc dùng nó làm kênh văn xuôi tự do).
    `tieng_viet=None`  : không xét ngôn ngữ.
    """

    if not value or len(value) > MAX_FIELD_CHARS[ten]:
        return False

    # Ký tự vô hình: LOẠI BỎ có đếm, không chà sạch âm thầm. Sửa lại nội dung
    # mà người dùng sắp phải đồng ý chính là vô hiệu hoá sự đồng ý đó.
    if _CONTROL_RE.search(value):
        return False

    # Khoảng trắng phải đã chuẩn: một luật, giết luôn tab, \r, NBSP và đệm đầu
    # cuối, đồng thời làm dữ liệu lưu xuống có dạng duy nhất.
    if value != _KHOANG_TRANG_RE.sub(" ", value).strip():
        return False

    if any(not (ky.isalpha() or ky.isdigit() or ky == " " or ky in _DAU_CAU) for ky in value):
        return False

    if _TEN_MIEN_RE.search(value):
        return False

    co_dau = co_dau_tieng_viet(value)

    if tieng_viet is True and not co_dau:
        return False

    return not (tieng_viet is False and co_dau)


def cau_dung_dang(sentence: str) -> bool:
    """Đúng một câu: kết bằng dấu câu, không quá dài, không phải một chuỗi liệt kê."""

    return sentence.endswith((".", "!", "?")) and len(sentence.split()) <= 25


def cau_chua_tu(word: str, sentence: str) -> bool:
    """
    Câu ví dụ có thật sự dùng chính từ đó không.

    ĐÂY KHÔNG PHẢI CHỐT AN TOÀN, và phải nói thẳng ra như vậy. 140 ký tự bị ràng
    buộc bởi một token là ~130 ký tự tự do; model viết cả `word` lẫn
    `example_sentence` trong cùng một hơi nên thoả mãn ràng buộc này là mặc
    định chứ không phải trở ngại. Nó bắt được model CẨU THẢ — câu không liên
    quan, một câu dùng chung cho sáu từ — chứ không bắt được kẻ tấn công. Ai
    định xoá một chốt khác vì tưởng chốt này đã phủ là đang nhầm.

    Cố ý KHÔNG dùng lại `word_is_from_text` của `grounding.py`: ở đó tập token
    do NGƯỜI DÙNG viết nên khớp lỏng vẫn có biên, còn ở đây cả hai vế đều do
    model viết, phép kiểm sẽ suy biến thành "model có làm hai chuỗi của chính
    nó vần với nhau không". Và `_chung_goc(toi_thieu=5)` vừa quá lỏng
    (`man` khớp `management`) vừa quá chặt đúng chỗ đau (`take on` trượt `takes`).
    """

    tu = chuan_hoa(word)
    cau = chuan_hoa(sentence)

    if not tu or not cau:
        return False

    # Đường thường gặp: model dùng đúng dạng từ điển. Chặn ở ranh giới chữ cái
    # để `run` không khớp trong `running` theo kiểu tình cờ ở nhánh này —
    # nhánh biến cách bên dưới mới là chỗ xử việc đó, và nó có ràng buộc.
    if re.search(rf"(?<![a-z]){re.escape(tu)}(?![a-z])", cau):
        return True

    tokens = _TOKEN_RE.findall(cau)
    vi_tri = 0

    for phan in tu.split():
        while vi_tri < len(tokens) and not _khop_bien_cach(phan, tokens[vi_tri]):
            vi_tri += 1

        if vi_tri == len(tokens):
            return False

        vi_tri += 1

    return True


def _khop_bien_cach(a: str, b: str) -> bool:
    """
    Khớp lỏng cho dạng chia: work/working, delegate/delegated, run/running.

    KHÔNG bắt được động từ bất quy tắc (give/gave, begin/began) — chấp nhận và
    ghi lại. Hậu quả là thỉnh thoảng mất một thẻ đúng, đếm ở
    `stats.dropped_incoherent`, chứ không phải lọt một thẻ sai.
    """

    if a == b:
        return True

    if abs(len(a) - len(b)) > 4:
        return False

    if b.startswith(a) or a.startswith(b):
        return True

    chung = 0

    for x, y in zip(a, b, strict=False):
        if x != y:
            break

        chung += 1

    return chung >= max(4, min(len(a), len(b)) - 2)
