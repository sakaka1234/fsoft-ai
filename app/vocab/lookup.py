"""
Tra đúng MỘT từ, dựng sẵn thành thẻ. SPEC muc 8.5d.

    tiền xử lý  ->  đã có trong deck?  ->  cache?  ->  MỘT lời gọi json_mode
    (ném 400)       (0 token)              (0 token)   (chỉ khi hai đường trên trượt)

Endpoint này khác hai anh em ở một điểm quyết định mọi thứ còn lại: NÓ ĐƯỢC GỌI
NHIỀU HƠN HẲN. `extract` và `generate` là việc người dùng cố ý làm rồi ngồi
chờ; đây là một cái nút bấm trong lúc soạn thẻ, và bấm nhầm cũng bấm.

Với ngân sách 6.400 token mỗi phút cho TOÀN hệ thống, nếu mỗi lượt bấm đều gọi
LLM thì cả ứng dụng chỉ chịu được khoảng ba lượt bấm một phút. Vì vậy hai đường
0 token ở trên không phải tối ưu hoá cho vui — chúng là thứ làm cái nút này khả
thi. Cộng thêm việc backend chỉ gọi tới đây KHI TỪ ĐIỂN CỦA HỌ TRƯỢT (xem
`docs/BACKEND_INTEGRATION.md` mục 5.8), phần thật sự chạm LLM còn lại rất nhỏ.

Ba chỗ cố ý khác `generate`:

1. KHÔNG lấy semaphore dùng chung của `extract`/`generate`. Xếp một cái nút bấm
   sau hàng đợi của một lượt trích xuất 5 giây là làm nó trông như bị treo.
   Lượt tra rẻ hơn nhiều nên `TokenBudget` một mình là đủ: quá tải thì nó trả
   429 ngay lập tức kèm `retry_after_seconds`, và với thao tác tương tác thì
   một lỗi nhanh tốt hơn một lần chờ dài.

2. `found: false` là câu trả lời 200 hợp lệ. Người dùng gõ sai chính tả là
   chuyện thường xuyên, không phải sự cố. Trả 503 ở đây sẽ nói dối về nguyên nhân.

3. Có cache dùng chung TOÀN CỤC. Nghĩa của một từ không phụ thuộc bộ thẻ của
   ai, nên không có gì để rò rỉ giữa hai người dùng — khác hẳn cache của `/chat`
   vốn bắt buộc phải khoá theo phạm vi deck.

`allowed_deck_ids` ở đây TUỲ CHỌN: tra từ có nghĩa cả khi không gắn với deck nào
nào (ví dụ tra ở màn hình không thuộc bộ thẻ cụ thể). Không khai phạm vi thì
đường `YOUR_DECK` và việc đánh dấu trùng tự tắt — hai cờ giữ giá trị mặc định
an toàn — còn đường cache/AI chạy như thường. Danh sách RỖNG có mặt thì vẫn là
400, đúng luật chung của hệ thống: rỗng nghĩa là KHÔNG ĐƯỢC PHÉP GÌ.
"""

import json
import secrets
import time

from app.config import Settings
from app.core.errors import InvalidRequest, InvalidScope, ProviderUnavailable
from app.core.logging import get_logger
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.retrieval.context import sanitize
from app.retrieval.search_index import SearchIndex
from app.schemas.card import SourceCard
from app.schemas.vocab import VocabCandidate, VocabLookupRequest, VocabLookupStats
from app.vocab.guard import (
    MAX_FIELD_CHARS,
    cau_chua_tu,
    cau_dung_dang,
    chuan_hoa,
    field_an_toan,
    phien_am_hop_le,
    tu_hop_le,
)
from app.vocab.word_cache import WordCache, khoa_cache

log = get_logger(__name__)

# ĐO THẬT 23/08/2026, gpt-oss-120b, 15 mẫu, KHÔNG mẫu nào cắt cụt.
# Xem `scripts/do_tra_tu.py`.
#
#     ca đo                       suy luận     tổng completion
#     từ thông dụng (book)         520-555      636-640
#     từ hiếm (obfuscate)          255-341      367-450
#     cụm từ (take on)             630          733
#     có ngữ cảnh (bank)           294-513      412-611
#     ngữ cảnh dài 290 ký tự       596-629      709-735   <- đắt nhất
#     sai chính tả (recieve)       379-517      422-560
#     không tồn tại (asdfghjk)     132-209      173-250
#     chuỗi tiêm chỉ thị           280-288      321-329
#
# Ba điều số đo nói ra:
#
# 1. RẺ HƠN `generate` khoảng bốn lần (735 so với 1.892 ở cùng ô xấu nhất).
#    Nhưng KHÔNG phải vì nó sinh một thẻ thay vì sáu — số đo của M9 đã chứng
#    minh suy luận không tỉ lệ với số thẻ. Nó rẻ hơn vì PROMPT ngắn hơn: tám
#    quy tắc thay vì mười, không danh sách tránh, không khuôn mảng.
#
# 2. Hai nhánh `found: false` là RẺ NHẤT (173-560). Model quyết định "không có
#    từ này" nhanh hơn nhiều so với soạn một thẻ đầy đủ. Nghĩa là người dùng gõ
#    sai chính tả không hề đắt — một lý do nữa để không sợ nhánh đó.
#
# 3. Chuỗi tiêm chỉ thị ra `found: false` cả hai lần, không phải một thẻ bịa.
#
# 1.500 để dư gấp đôi so với mẫu xấu nhất. Nghe dư, nhưng đặt THIẾU thì hỏng
# theo kiểu tệ nhất — `400 json_validate_failed` với `failed_generation` RỖNG,
# đi qua model dự phòng, hỏng y hệt, rồi ra 503 trông y như "nhà cung cấp sập".
# Còn đặt dư chỉ tốn phần ĐẶT CHỖ tạm thời, mà `settle()` trả lại ngay sau đó.
MAX_TOKENS = 1500

# Ghi lại một chỗ lệch đo được, để người sau khỏi phải đo lại: `estimate_tokens`
# (`len // 3`) ước tính prompt này khoảng 813 token, còn Groq đếm thật 970-1003
# — tức ĐẾM THIẾU khoảng 20%. Chú thích ở `app/llm/budget.py` nói 3 ký tự mỗi
# token là "thận trọng" vì tiếng Việt tách mịn hơn; với một prompt dày đặc tiếng
# Việt có dấu như prompt này thì điều đó không còn đúng. Không nguy hiểm —
# `settle()` thay ước tính bằng số thật ngay khi có phản hồi — nhưng nó có nghĩa
# là cổng ngân sách hơi dễ dãi hơn mình tưởng ở các endpoint prompt tiếng Việt.

# Trần độ dài chuỗi người dùng gõ vào ô tra. Rộng hơn `word` hợp lệ (32) vì
# người dùng gõ thừa khoảng trắng hay dán cả cụm; chuẩn hoá xong mới siết.
MAX_WORD_INPUT_CHARS = 64

# Ngữ cảnh dài hơn thì CẮT, không báo lỗi. Khác `topic` của `generate` vốn vượt
# là 400: ở đó chủ đề là toàn bộ yêu cầu nên cắt đi là đổi ý người dùng, còn ở
# đây ngữ cảnh chỉ để chọn nghĩa, mất phần đuôi vẫn dùng được. Bắt người dùng
# đang bôi đen một câu dài phải xử lý lỗi 400 thì vô lý.
MAX_CONTEXT_CHARS = 300

_LOAI_TU_HOP_LE = frozenset({"noun", "verb", "adj", "adv", "prep", "conj"})

_BANG_BO = str.maketrans({ky: None for ky in "<>{}[]"})


class VocabLookup:
    def __init__(
        self,
        *,
        settings: Settings,
        index: SearchIndex,
        llm: LlmClient,
        prompts: PromptRegistry,
        cache: WordCache,
    ) -> None:
        self._settings = settings
        self._index = index
        self._llm = llm
        self._prompts = prompts
        self._cache = cache

    async def lookup(
        self, request: VocabLookupRequest
    ) -> tuple[str, bool, str | None, VocabCandidate | None, VocabLookupStats]:
        """Trả (source, found, suggestion, card, stats)."""

        tu, ngu_canh = self._tien_xu_ly(request)
        bat_dau = time.perf_counter()
        pham_vi = request.allowed_deck_ids or None

        # --- Đường 1: người dùng đã có từ này. 0 token. ---
        #
        # Vừa miễn phí vừa là thông tin ĐÚNG LÚC NHẤT: họ đang ở màn hình soạn
        # thẻ, nên biết mình sắp tạo thẻ trùng còn giá trị hơn một thẻ mới.
        # Bỏ qua hẳn khi request không khai phạm vi — `lookup_word` với danh
        # sách rỗng vốn trả `[]`, nên nhánh này tự tắt đúng như ý.
        da_co = self._index.lookup_word(tu, pham_vi) if pham_vi else []

        if da_co:
            the_cu = self._index.get(min(da_co))

            if the_cu is not None:
                return (
                    "YOUR_DECK",
                    True,
                    None,
                    self._tu_the_da_co(the_cu),
                    self._stats("YOUR_DECK", request, llm_calls=0, bat_dau=bat_dau),
                )

        # --- Đường 2: đã có người tra từ này. 0 token. ---
        khoa = khoa_cache(tu, ngu_canh)
        trong_cache = self._cache.get(khoa)

        if trong_cache is not None:
            self._danh_dau(trong_cache, pham_vi)

            return (
                "CACHE",
                True,
                None,
                trong_cache,
                self._stats("CACHE", request, llm_calls=0, bat_dau=bat_dau),
            )

        # --- Đường 3: gọi LLM. ---
        ket_qua = await self._llm.complete(
            task="VOCAB_LOOKUP",
            system=self._prompts.render("vocab_lookup_system_v1"),
            user=self._prompts.render(
                "vocab_lookup_user_v1",
                nonce=secrets.token_hex(4),
                word=tu,
                context=ngu_canh or "(không có)",
            ),
            model=self._settings.ai_model_vocab,
            json_mode=True,
            max_tokens=MAX_TOKENS,
        )

        tho = self._parse(ket_qua.text)

        if tho is None:
            raise ProviderUnavailable("Trợ lý AI trả về dữ liệu không đọc được, vui lòng thử lại.")

        stats = self._stats("AI", request, llm_calls=1, bat_dau=bat_dau, ket_qua=ket_qua)

        if not tho.get("found"):
            goi_y = chuan_hoa(str(tho.get("suggestion") or ""))

            return "AI", False, (goi_y if tu_hop_le(goi_y) else None), None, stats

        the, ly_do = self._to_card(tho, tu)

        if ly_do == "tu_khac":
            # Model bảo "tìm thấy" nhưng trả về một TỪ KHÁC. Trong thực tế đây
            # gần như luôn là nó tự sửa chính tả giúp: hỏi `recieve`, nó đáp
            # `receive`. Một lượt chạy thật đã bắt được đúng ca này.
            #
            # Trả 503 ở đây là biến một lần sửa chính tả ĐÚNG thành lỗi hạ tầng,
            # và giấu mất chính thứ người dùng cần nhất lúc đó. Nên nó được quy
            # về đúng cái mà nó thật sự là: "không có từ bạn gõ, ý bạn là X?"
            goi_y = chuan_hoa(str(tho.get("word") or ""))

            return "AI", False, (goi_y if tu_hop_le(goi_y) else None), None, stats

        if the is None:
            # Còn lại là thẻ trượt chốt chặn an toàn. Đây KHÔNG phải
            # `found: false` — nói vậy là đổ cho người dùng gõ sai một lỗi của
            # phía mình. Trả 503 để họ thử lại, và để log ghi đúng nguyên nhân.
            log.warning("vocab_lookup_the_khong_qua_chot", tu_dai=len(tu))

            raise ProviderUnavailable("Trợ lý AI trả về dữ liệu không dùng được, vui lòng thử lại.")

        # Cache TRƯỚC khi đánh dấu trùng: `WordCache.put` tự xoá hai cờ, nhưng
        # gọi đúng thứ tự vẫn rẻ hơn là dựa vào nó.
        self._cache.put(khoa, the)
        self._danh_dau(the, pham_vi)
        stats.cache_size = self._cache.size

        return "AI", True, None, the, stats

    # ---------------------------------------------------------------

    def _tien_xu_ly(self, request: VocabLookupRequest) -> tuple[str, str]:
        """Ném 400 TRƯỚC khi đặt chỗ ngân sách. Trả (từ đã chuẩn hoá, ngữ cảnh đã sạch)."""

        if len(request.word) > MAX_WORD_INPUT_CHARS:
            raise InvalidRequest(
                f"Chuỗi cần tra dài {len(request.word)} ký tự, vượt giới hạn "
                f"{MAX_WORD_INPUT_CHARS} ký tự."
            )

        # `allowed_deck_ids` TUỲ CHỌN ở endpoint này: bỏ hẳn là tra không kiểm
        # trùng, vẫn hợp lệ. Chỉ danh sách RỖNG có mặt mới là phạm vi bằng không
        # — giữ nguyên luật 400 như hai endpoint kia, và dùng `None` để phân biệt
        # hai trạng thái đó thay vì ép mọi lượt gọi đều phải khai deck.
        if request.allowed_deck_ids is not None and not request.allowed_deck_ids:
            raise InvalidScope("allowed_deck_ids không được rỗng.")

        tu = chuan_hoa(sanitize(request.word, MAX_WORD_INPUT_CHARS).translate(_BANG_BO))

        # Cùng định nghĩa "thế nào là một từ" với đầu ra của model. Chặn ở đây là
        # chặn miễn phí: chuỗi có chữ số hay bốn từ trở lên thì không lời gọi LLM
        # nào cứu được, mà mỗi lượt gọi hụt ăn mất một phần ngân sách của cả nhà.
        if not tu_hop_le(tu):
            raise InvalidRequest(
                "Chuỗi cần tra phải là một từ hoặc cụm nhiều nhất ba từ tiếng Anh, "
                "chỉ gồm chữ cái, dấu nối hoặc dấu nháy đơn."
            )

        # Ngữ cảnh thì CẮT chứ không ném lỗi — xem chú thích `MAX_CONTEXT_CHARS`.
        ngu_canh = sanitize(request.context, MAX_CONTEXT_CHARS).translate(_BANG_BO).strip()

        return tu, ngu_canh

    def _stats(
        self,
        source: str,
        request: VocabLookupRequest,
        *,
        llm_calls: int,
        bat_dau: float,
        ket_qua=None,
    ) -> VocabLookupStats:
        return VocabLookupStats(
            source=source,  # type: ignore[arg-type]
            word_chars=len(request.word),
            context_chars=len(request.context),
            llm_calls=llm_calls,
            cache_size=self._cache.size,
            prompt_tokens=ket_qua.prompt_tokens if ket_qua else 0,
            completion_tokens=ket_qua.completion_tokens if ket_qua else 0,
            latency_ms=ket_qua.latency_ms
            if ket_qua
            else int((time.perf_counter() - bat_dau) * 1000),
        )

    def _danh_dau(self, the: VocabCandidate, allowed_deck_ids: list[int] | None) -> None:
        """
        Đánh dấu trùng SAU khi đọc cache.

        Bắt buộc phải làm ở đây chứ không lưu sẵn vào cache: hai cờ này phụ
        thuộc phạm vi deck của TỪNG người dùng, còn cache thì dùng chung.

        `allowed_deck_ids` là `None` khi request không khai phạm vi: không có
        ranh giới thì không thể khẳng định "đã có", nên bỏ qua thay vì trả cờ
        sai. Cache vẫn cho thẻ sạch nên không cần xoá gì.
        """

        if not allowed_deck_ids:
            return

        trung = self._index.lookup_word(the.word, allowed_deck_ids)

        if trung:
            the.already_in_deck = True
            the.existing_card_id = min(trung)

    @staticmethod
    def _tu_the_da_co(card: SourceCard) -> VocabCandidate:
        """
        Dựng phản hồi từ chính thẻ người dùng đang có. Không chữ nào do model sinh.

        Cắt độ dài theo đúng bảng dùng cho đầu ra của model. Thẻ này do người
        dùng tự nhập nên nội dung là của họ, nhưng phản hồi vẫn phải vừa khuôn
        `VocabCandidate` mà backend đã bind — dài hơn thì cắt, không loại thẻ.
        """

        def gon(gia_tri: str | None, ten: str) -> str | None:
            if not gia_tri:
                return None

            return gia_tri.strip()[: MAX_FIELD_CHARS[ten]]

        return VocabCandidate(
            word=card.word.strip().lower()[: MAX_FIELD_CHARS["word"]],
            phonetic=gon(card.phonetic, "phonetic"),
            part_of_speech=(card.part_of_speech or "").strip().lower() or None,
            meaning=(gon(card.meaning, "meaning") or ""),
            definition_en=gon(card.definition_en, "definition_en"),
            example_sentence=(gon(card.example_sentence, "example_sentence") or ""),
            example_meaning=gon(card.example_meaning, "example_meaning"),
            already_in_deck=True,
            existing_card_id=card.card_id,
        )

    @staticmethod
    def _parse(text: str) -> dict | None:
        """`None` = không đọc được (-> 503). Dict = đọc được, kể cả khi `found` là false."""

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            log.warning("vocab_lookup_json_invalid", preview=text[:200])

            return None

        if not isinstance(payload, dict):
            log.warning("vocab_lookup_json_sai_dang", preview=text[:200])

            return None

        return payload

    def _to_card(self, tho: dict, tu_da_hoi: str) -> tuple[VocabCandidate | None, str]:
        """
        Cùng bộ chốt chặn với `generate` — mọi trường ở đây đều do model bịa ra.

        Thêm một luật riêng: `word` model trả về phải LIÊN QUAN tới từ người
        dùng hỏi. Không có luật này thì model đổi chủ đề mà không ai biết, và
        người dùng gõ "donut" lại nhận về thẻ của một từ khác.
        """

        try:
            word = chuan_hoa(str(tho.get("word") or ""))
            meaning = str(tho.get("meaning") or "").strip()
            cau = str(tho.get("example_sentence") or "").strip()

            if not tu_hop_le(word):
                return None, "khong_an_toan"

            # Cho phép lệch dạng chia (`donuts` -> `donut`) nhưng không cho phép
            # đổi hẳn sang từ khác.
            if not cau_chua_tu(word, tu_da_hoi) and not cau_chua_tu(tu_da_hoi, word):
                log.warning("vocab_lookup_tra_ve_tu_khac")

                return None, "tu_khac"

            if not field_an_toan(meaning, ten="meaning", tieng_viet=True):
                return None, "khong_an_toan"

            if not field_an_toan(cau, ten="example_sentence", tieng_viet=False):
                return None, "khong_an_toan"

            dinh_nghia = str(tho.get("definition_en") or "").strip() or None
            nghia_cau = str(tho.get("example_meaning") or "").strip() or None

            if dinh_nghia and not field_an_toan(dinh_nghia, ten="definition_en", tieng_viet=False):
                return None, "khong_an_toan"

            if nghia_cau and not field_an_toan(nghia_cau, ten="example_meaning", tieng_viet=True):
                return None, "khong_an_toan"

            if not cau_dung_dang(cau) or not cau_chua_tu(word, cau):
                return None, "khong_mach_lac"

            phien_am = str(tho.get("phonetic") or "").strip()
            loai_tu = str(tho.get("part_of_speech") or "").strip().lower()

            the = VocabCandidate(
                word=word,
                phonetic=phien_am if phien_am_hop_le(phien_am) else None,
                part_of_speech=loai_tu if loai_tu in _LOAI_TU_HOP_LE else None,
                meaning=meaning,
                definition_en=dinh_nghia,
                example_sentence=cau,
                example_meaning=nghia_cau,
                already_in_deck=False,
                existing_card_id=None,
            )

            return the, ""
        except Exception:  # LLM trả gì cũng có thể, hỏng thì bỏ
            log.warning("vocab_lookup_ep_kieu_hong", exc_info=True)

            return None, "khong_an_toan"
