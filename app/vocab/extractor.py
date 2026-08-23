"""
Trích từ vựng đáng học từ một đoạn văn người dùng dán vào. SPEC muc 8.5b.

Luồng, và cả ba nhánh 0 token đều chạy TRƯỚC khi đặt chỗ ngân sách:

    tiền xử lý  ->  MỘT lời gọi json_mode  ->  lọc bám text  ->  đánh dấu trùng
    (ném 400)       (semaphore = 1)            (bỏ thẻ bịa)      (lookup_word)

Ba điểm khác `app/quiz/` một cách CÓ CHỦ Ý, mỗi điểm đều có lý do riêng ghi ở
chỗ tương ứng bên dưới: không vòng lặp thử lại, không nuốt lỗi, không giữ trạng
thái tích luỹ trên instance.
"""

import asyncio
import json
import secrets

from app.config import Settings
from app.core.errors import InvalidRequest, InvalidScope, ProviderUnavailable
from app.core.logging import get_logger
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.retrieval.search_index import SearchIndex
from app.schemas.vocab import VocabCandidate, VocabExtractRequest, VocabExtractStats
from app.vocab.grounding import (
    english_tokens,
    field_is_safe,
    normalize_for_grounding,
    sentence_is_from_text,
    word_is_from_text,
)

log = get_logger(__name__)

# Hạn mức đầu ra: phần cố định cộng phần theo số từ.
#
#     max_tokens = BASE_TOKENS + TOKENS_PER_CANDIDATE * wanted
#
# ĐO THẬT ngày 22/08/2026, prompt này, bài đọc 3.070 ký tự, gpt-oss-120b:
#
#     wanted   suy luận      nội dung    tổng completion
#        3     1088-1362     448- 603    1691-1810
#        6     1183-1787     728- 890    1911-2677
#       10     2643-2900    1100-1357    CHẠM TRẦN 4000, BỊ CẮT CỤT
#
# Hai điều số đo này nói ra mà phỏng đoán không nói được:
#
# 1. SUY LUẬN TĂNG THEO SỐ TỪ, không phải hằng số. Con số 79-296 ghi ở
#    app/config.py đo trên prompt quiz 1.114 ký tự và KHÔNG dùng lại được —
#    ở đây nó gấp 4 tới 10 lần. "Đọc bài dài rồi CHỌN LỌC" tốn suy luận hơn
#    hẳn "viết câu hỏi cho danh sách từ đã cho sẵn".
# 2. Nội dung tốn đều ~150 token mỗi từ (448/3, 890/6, 1357/9 đều ra ~150).
#
# Đặt thiếu thì Groq trả `400 json_validate_failed` với `failed_generation`
# RỖNG, `LlmClient` thử model dự phòng cùng họ, hỏng y hệt, rồi kết thúc bằng
# `503 PROVIDER_UNAVAILABLE` — TRIỆU CHỨNG GIỐNG HỆT "nhà cung cấp sập", và
# gần ngưỡng thì còn không tất định. Vì vậy biên ở đây rộng chứ không sát nút:
# +640 token ở mức 3, +823 ở mức 6 so với lần quan sát tệ nhất.
BASE_TOKENS = 1400
TOKENS_PER_CANDIDATE = 350

# Vì sao trần chỉ 6 chứ không phải con số tròn trịa hơn: đây là RÀNG BUỘC NGÂN
# SÁCH, không phải kỹ thuật. `LlmClient` đặt chỗ trước cả `max_tokens` lẫn độ
# dài prompt, nên với đoạn văn 4.000 ký tự:
#
#     wanted   đặt chỗ   phần của ngân sách 6.400/phút
#        5      5.194              81%
#        6      5.544              87%
#        8      6.244              98%
#       10      6.944             108%  <- tự 429 chính mình, LUÔN LUÔN
#
# Mức 10 mà kế hoạch ban đầu định lấy là bất khả thi: nó vượt trọn ngân sách
# một phút của TOÀN hệ thống trước khi gửi đi một byte nào.
MAX_CANDIDATES_CAP = 6

# Dưới ngưỡng này thì đoạn văn không đủ chất liệu để trích, và gọi LLM chỉ tổ
# đốt vài nghìn token. Cũng là hàng phòng thủ cho việc `estimate_tokens` dùng
# `len // 3` — với văn bản CJK hoặc base64 nó ước lượng THIẾU 2-3 lần.
MIN_ENGLISH_TOKENS = 3

_LOAI_TU_HOP_LE = frozenset({"noun", "verb", "adj", "adv", "prep", "conj"})

_DAI_NHAT = {
    "word": 64,
    "phonetic": 64,
    "meaning": 120,
    "definition_en": 300,
    "example_sentence": 300,
    "example_meaning": 300,
}


class VocabExtractor:
    def __init__(
        self,
        *,
        settings: Settings,
        index: SearchIndex,
        llm: LlmClient,
        prompts: PromptRegistry,
        sem: asyncio.Semaphore | None = None,
    ) -> None:
        self._settings = settings
        self._index = index
        self._llm = llm
        self._prompts = prompts

        # KHÔNG có field tích luỹ kiểu `self.prompt_tokens += ...`.
        #
        # `FillBlankGenerator` có, và nó an toàn vì `QuizGenerator` dựng một
        # instance MỚI cho mỗi request. `VocabExtractor` thì được dựng ĐÚNG MỘT
        # LẦN trong `build_service()` và sống hết đời tiến trình, nên field tích
        # luỹ ở đây sẽ là trạng thái dùng chung giữa các request đồng thời — số
        # token của người này cộng vào hoá đơn của người kia.
        #
        # Semaphore thì ngược lại, cố ý dùng chung: nó tồn tại để giới hạn số
        # lời gọi extract đang bay. Một lời gọi đặt chỗ hơn nửa ngân sách token
        # mỗi phút của TOÀN hệ thống, nên hai lời gọi song song là đủ để mọi
        # người khác nhận 429 khi chat.
        #
        # Từ M9 nó được TIÊM VÀO và dùng chung với `VocabGenerator`. Lý do nằm
        # ngay trong câu trên: mục đích của nó là toàn cục ("ngân sách token của
        # TOÀN hệ thống"), không thuộc riêng endpoint nào. Hai instance riêng
        # cho hai endpoint đắt tiền là chỗ sai phạm vi hiện hình.
        #
        # Mặc định `None` để test và mã dựng trực tiếp cũ không phải đổi.
        self._sem = sem or asyncio.Semaphore(1)

    async def extract(
        self, request: VocabExtractRequest
    ) -> tuple[list[VocabCandidate], VocabExtractStats]:
        tokens = self._tien_xu_ly(request)
        wanted = min(request.max_candidates, len(tokens))

        async with self._sem:
            result = await self._llm.complete(
                task="VOCAB_EXTRACT",
                system=self._prompts.render(
                    "vocab_extract_v1",
                    text=request.text,
                    nonce=secrets.token_hex(4),
                    max_candidates=str(wanted),
                ),
                user="Trích từ vựng đáng học từ đoạn văn trên.",
                model=self._settings.ai_model_vocab,
                json_mode=True,
                # 0.0 chứ không phải mặc định 0.3: ràng buộc "câu ví dụ phải lấy
                # từ đoạn văn" cần tái lập được giữa hai lần gọi giống nhau.
                temperature=0.0,
                max_tokens=self.max_tokens_cho(wanted),
            )

        tho = self._parse(result.text)

        if tho is None:
            raise ProviderUnavailable("Trợ lý AI trả về dữ liệu không đọc được, vui lòng thử lại.")

        # Dựng haystack đã chuẩn hoá MỘT LẦN: chuẩn hoá lại 4.000 ký tự cho từng
        # ứng viên là lãng phí không có lý do.
        haystack = normalize_for_grounding(request.text)

        ung_vien: list[VocabCandidate] = []
        bo_vi_khong_bam = 0
        bo_vi_khong_an_toan = 0

        for muc in tho:
            the, ly_do = self._to_candidate(muc, tokens, haystack)

            if the is not None:
                ung_vien.append(the)
            elif ly_do == "khong_bam":
                bo_vi_khong_bam += 1
            else:
                bo_vi_khong_an_toan += 1

        # LLM có trả lời mà lọc sạch không còn gì -> nó đang bịa toàn bộ. Không
        # có câu trả lời 200 nào trung thực cho ca này: `candidates: []` sẽ bị
        # đọc thành "đoạn văn không có gì đáng học", một lời nói dối.
        if tho and not ung_vien:
            log.warning(
                "vocab_tat_ca_bi_loai",
                returned_by_llm=len(tho),
                khong_bam=bo_vi_khong_bam,
                khong_an_toan=bo_vi_khong_an_toan,
            )

            raise ProviderUnavailable("Trợ lý AI trả về dữ liệu không dùng được, vui lòng thử lại.")

        da_co = self._danh_dau_trung(ung_vien, request.allowed_deck_ids)

        return ung_vien, VocabExtractStats(
            text_chars=len(request.text),
            distinct_english_tokens=len(tokens),
            returned_by_llm=len(tho),
            dropped_not_grounded=bo_vi_khong_bam,
            dropped_unsafe=bo_vi_khong_an_toan,
            already_in_deck_count=da_co,
            dedup_checked=self._index.size > 0,
            llm_calls=1,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            latency_ms=result.latency_ms,
        )

    # ---------------------------------------------------------------

    @staticmethod
    def max_tokens_cho(wanted: int) -> int:
        return BASE_TOKENS + TOKENS_PER_CANDIDATE * wanted

    def _tien_xu_ly(self, request: VocabExtractRequest) -> set[str]:
        """Ném 400 trước khi đặt chỗ ngân sách. Trả whitelist token."""

        gioi_han = self._settings.ai_vocab_max_text_chars

        if len(request.text) > gioi_han:
            raise InvalidRequest(
                f"Đoạn văn dài {len(request.text)} ký tự, vượt giới hạn "
                f"{gioi_han} ký tự. Hãy cắt ngắn rồi gọi nhiều lần."
            )

        if not request.allowed_deck_ids:
            raise InvalidScope("allowed_deck_ids không được rỗng.")

        tokens = english_tokens(request.text)

        if len(tokens) < MIN_ENGLISH_TOKENS:
            raise InvalidRequest(
                f"Đoạn văn chỉ có {len(tokens)} từ tiếng Anh phân biệt, "
                f"cần ít nhất {MIN_ENGLISH_TOKENS} để trích từ vựng."
            )

        return tokens

    @staticmethod
    def _parse(text: str) -> list[dict] | None:
        """
        `None` = KHÔNG đọc được, khác hẳn `[]` = đọc được nhưng rỗng.

        Phân biệt hai cái này là bắt buộc, không phải cầu kỳ: `[]` nghĩa là đoạn
        văn không có gì đáng học và phải trả 200; `None` nghĩa là model trả rác
        và phải trả 503. Gộp chúng lại thành một giá trị là biến một lỗi hạ tầng
        thành câu "đoạn văn của bạn không có từ nào đáng học" — người dùng sẽ
        dán lại mãi mà không hiểu vì sao.
        """

        try:
            payload = json.loads(text)

        except json.JSONDecodeError:
            log.warning("vocab_json_invalid", preview=text[:200])

            return None

        items = payload.get("words") if isinstance(payload, dict) else payload

        if not isinstance(items, list):
            log.warning("vocab_json_sai_dang", preview=text[:200])

            return None

        return [muc for muc in items if isinstance(muc, dict)]

    def _to_candidate(
        self, tho: dict, tokens: set[str], haystack: str
    ) -> tuple[VocabCandidate | None, str]:
        """
        Ép một item thô thành thẻ, hoặc loại nó.

        Whitelist trường là phần quan trọng nhất: chỉ đúng bảy khoá dưới đây đi
        tiếp. Model trả thêm `audio_url`, `card_id` hay `deck_id` thì rơi hết —
        nếu không, một URL do người ngoài điều khiển sẽ thành dữ liệu được lưu.
        """

        try:
            word = str(tho.get("word") or "").strip().lower()
            meaning = str(tho.get("meaning") or "").strip()
            cau = str(tho.get("example_sentence") or "").strip()

            if not word or not meaning or not cau:
                return None, "khong_an_toan"

            phu = {
                "phonetic": str(tho.get("phonetic") or "").strip() or None,
                "definition_en": str(tho.get("definition_en") or "").strip() or None,
                "example_meaning": str(tho.get("example_meaning") or "").strip() or None,
            }

            # Chốt chặn TẤT ĐỊNH, chạy sau khi LLM đã nói xong: nó đứng vững kể
            # cả khi prompt injection thành công hoàn toàn. Prompt chỉ là lời
            # khuyên; mấy dòng này mới là luật.
            for ten, gia_tri in [("word", word), ("meaning", meaning), ("example_sentence", cau)]:
                if len(gia_tri) > _DAI_NHAT[ten] or not field_is_safe(gia_tri):
                    return None, "khong_an_toan"

            for ten, tuy_chon in phu.items():
                if tuy_chon is not None and (
                    len(tuy_chon) > _DAI_NHAT[ten] or not field_is_safe(tuy_chon)
                ):
                    return None, "khong_an_toan"

            if not sentence_is_from_text(cau, haystack) or not word_is_from_text(word, tokens):
                return None, "khong_bam"

            loai_tu = str(tho.get("part_of_speech") or "").strip().lower()

            return VocabCandidate(
                word=word,
                phonetic=phu["phonetic"],
                # Giá trị lạ thành None chứ không giết cả thẻ: mất một nhãn còn
                # hơn mất một từ đáng học.
                part_of_speech=loai_tu if loai_tu in _LOAI_TU_HOP_LE else None,
                meaning=meaning,
                definition_en=phu["definition_en"],
                example_sentence=cau,
                example_meaning=phu["example_meaning"],
                already_in_deck=False,
                existing_card_id=None,
            ), ""

        except Exception:  # LLM trả gì cũng có thể, hỏng thì bỏ mục này
            log.warning("vocab_ep_kieu_hong", exc_info=True)

            return None, "khong_an_toan"

    def _danh_dau_trung(self, ung_vien: list[VocabCandidate], allowed_deck_ids: list[int]) -> int:
        """Đánh dấu, KHÔNG xoá. Trả số ứng viên đã có sẵn trong bộ thẻ."""

        da_co = 0

        for the in ung_vien:
            trung = self._index.lookup_word(the.word, allowed_deck_ids)

            if trung:
                the.already_in_deck = True
                # Nhỏ nhất, để hai lượt gọi giống nhau cho cùng một kết quả.
                the.existing_card_id = min(trung)
                da_co += 1

        return da_co
