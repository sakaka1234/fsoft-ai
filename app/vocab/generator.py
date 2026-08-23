"""
Sinh thẻ từ vựng từ một CHỦ ĐỀ. SPEC muc 8.5c.

    tiền xử lý  ->  embed chủ đề + tìm ngữ nghĩa  ->  MỘT lời gọi json_mode
    (ném 400)       (0 token LLM, ~10ms ONNX)         (semaphore dùng chung)

                ->  lọc tất định  ->  khử trùng trong lô  ->  đánh dấu trùng
                    (bỏ thẻ hỏng)     (chủ đề hẹp hay lặp)    (lookup_word)

Anh em với `app/vocab/extractor.py` nhưng KHÔNG phải bản sao. Bốn chỗ cố ý làm
ngược, mỗi chỗ một lý do:

1. Chủ đề đi vào lượt USER, không phải system. M8 đặt văn bản người dùng vào
   system prompt và điều đó bào chữa được — đoạn văn LÀ tư liệu của tác vụ. Ở
   đây chủ đề là YÊU CẦU của người dùng; nhét nó vào vai system là tặng không
   cho nó một nấc thẩm quyền mà model được huấn luyện để tôn trọng hơn.

2. `temperature` lấy mặc định 0.3 chứ không ghim 0.0. M8 ghim 0.0 vì ràng buộc
   "câu ví dụ phải lấy từ đoạn văn" cần tái lập được. Ở đây không có ràng buộc
   ấy, mà 0.0 sẽ khiến nút "sinh thêm" trả về đúng ngần ấy từ cũ mỗi lần bấm.

3. `returned_by_llm == 0` trả 503, không trả 200 rỗng. Ở M8, "đoạn văn này
   không có gì đáng học" là một sự thật có thể xảy ra. Ở đây "chủ đề của bạn
   không có từ vựng nào" gần như không bao giờ đúng — số 0 nghĩa là model từ
   chối, hoặc chạm bộ lọc an toàn, hoặc trả rác. Trả 200 rỗng là nói dối.

4. Không có phép kiểm bám văn bản, vì không có văn bản. Xem `app/vocab/guard.py`
   để biết cái gì thay thế nó và cái gì KHÔNG thể thay thế được.
"""

import asyncio
import json
import secrets

from app.config import Settings
from app.core.errors import BudgetExhausted, InvalidRequest, InvalidScope, ProviderUnavailable
from app.core.logging import get_logger
from app.embedding.encoder import Encoder
from app.llm.client import LlmClient
from app.llm.registry import PromptRegistry
from app.retrieval.context import sanitize
from app.retrieval.search_index import SearchIndex
from app.schemas.vocab import VocabCandidate, VocabGenerateRequest, VocabGenerateStats
from app.vocab.dedup import danh_dau_trung
from app.vocab.guard import (
    cau_chua_tu,
    cau_dung_dang,
    chuan_hoa,
    field_an_toan,
    phien_am_hop_le,
    tu_hop_le,
)

log = get_logger(__name__)

# ĐO THẬT — xem `scripts/do_sinh_theo_chu_de.py`, bảng kết quả nằm trong
# docstring của chính script đó. Công thức:
#
#     max_tokens = BASE_TOKENS + TOKENS_PER_CARD * wanted
#
# KHÔNG mượn số của M8 được, và số đo đã chứng minh vì sao — hình dạng đường
# cong ở đây KHÁC HẲN.
#
# ĐO THẬT 23/08/2026, gpt-oss-120b, ô xấu nhất (chủ đề trừu tượng / tiếng Việt /
# dài nhất, kèm CEFR A2, danh sách tránh đầy).
#
# VÒNG 1 — prompt bản đầu, 33 mẫu hợp lệ:
#
#     xin N    suy luận       tổng completion   model TRẢ VỀ mấy từ
#        1     1769           2726              5-8   (2/3 mẫu CẮT CỤT ở 3.000)
#        3      847-1389      1531-1915         4-5
#        6     1060-2358      1621-3053         6-8
#       10     1821-2488      2961-3493         10
#
# Cột cuối là chỗ số đo bắt được một lỗi mà đọc code không thấy: model BỎ QUA
# trần số thẻ. Xin 1 trả về 5-8, xin 3 trả về 4-5 — và đó chính là thứ làm hai
# mẫu N=1 cắt cụt. Đã siết quy tắc 1 trong prompt thành "soạn ĐÚNG bằng ấy thẻ",
# và thêm một lát cắt tất định ở `generate()`, vì prompt chỉ là lời khuyên.
#
# VÒNG 2 — sau khi siết, 12 mẫu, KHÔNG mẫu nào cắt cụt, 12/12 trả ĐÚNG số xin:
#
#     xin N    suy luận       tổng completion
#        5      701-1179      1216-1745
#        6     1088-1315      1607-1892
#
# Siết quy tắc số lượng làm SUY LUẬN GIẢM GẦN MỘT NỬA — bỏ câu hỏi "cho bao
# nhiêu từ thì vừa" là bỏ đi cả một nhánh cân nhắc của model. Đây là lý do phải
# đo LẠI sau khi sửa prompt chứ không suy ra từ bảng cũ.
#
# Điều quan trọng nhất mà cả hai vòng đều nói: SUY LUẬN KHÔNG TĂNG THEO N. Nó là
# chi phí cố định theo PROMPT (mười quy tắc, khối bảo mật, khuôn JSON), không
# phải theo số thẻ. Ngược hẳn M8, nơi suy luận tăng gần gấp đôi từ N=3 lên N=10
# vì phải quét lại đoạn văn cho từng lựa chọn. Hệ quả: hệ số chặn phải LỚN và độ
# dốc phải NHỎ. Một công thức kiểu M8 (chặn nhỏ, dốc lớn) sẽ đặt thiếu ở N nhỏ —
# đúng chỗ nguy hiểm nhất, vì ai cũng đinh ninh N nhỏ thì rẻ.
#
# Hai hằng số dưới để chỗ thừa khoảng 65-70% so với mẫu xấu nhất. Nghe như quá
# tay, nhưng phân bố suy luận lệch phải và 6 mẫu mỗi ô chỉ chạm tới khoảng phân
# vị 83 — cái đuôi mới là thứ gây lỗi, và nó không hiện ra trong 6 mẫu.
#
# Đặt thiếu thì hỏng theo kiểu tệ nhất: `400 json_validate_failed` với
# `failed_generation` RỖNG, đi qua model dự phòng, hỏng y hệt, rồi ra
# `503 PROVIDER_UNAVAILABLE` — triệu chứng cuối cùng giống hệt "nhà cung cấp sập".
BASE_TOKENS = 2000
TOKENS_PER_CARD = 200

# Trần số thẻ mỗi lượt, và nó là ràng buộc NGÂN SÁCH chứ không phải sản phẩm.
#
# Luật đặt trần: đặt chỗ ở cấu hình xấu nhất phải <= 70% ngân sách token mỗi
# phút của TOÀN hệ thống. 70% là con số tròn lớn nhất mà vẫn còn chỗ cho ít
# nhất MỘT lượt `/chat` (~1.700 token) chạy song song.
#
# M8 đang ở 87%, nghĩa là mọi lượt chat trong lúc nó chạy đều ăn 429. Đó là
# khiếm khuyết đã ship, ghi nhận là ngoại lệ, không nhân rộng sang đây.
# Số đo chốt con số này: ở cấu hình xấu nhất, N=6 đặt chỗ 4.378 = 68,4% còn
# N=7 đặt chỗ 4.578 = 71,5%. Vậy 6 là số lớn nhất còn lọt trần.
#
# N=8 và N=10 KHÔNG ĐO ĐƯỢC trong lần chạy này (cạn hạn mức theo phút của nhà
# cung cấp giữa chừng). Điều đó không đổi gì: ngay cả nếu đo được, chúng đã vượt
# trần ngân sách rồi. Nhưng nguyên tắc vẫn phải nói ra — KHÔNG ship một con số
# chưa đo được, vì không có cách nào chứng minh nó không cắt cụt trong sản xuất.
MAX_CARDS_CAP = 6

# Trần độ dài chủ đề. Vượt là 400, KHÔNG cắt bớt — cắt im lặng thì người dùng
# gõ một câu dài rồi nhận về thẻ của nửa câu đầu mà không hiểu vì sao.
MAX_TOPIC_CHARS = 120

# Danh sách "tránh": số mục tối đa đưa vào prompt, và trần độ dài mỗi mục.
#
# Trần độ dài mỗi mục KHÔNG phải chuyện vệ sinh — nó là chốt chặn ngân sách.
# `estimate_tokens` tính trên prompt ĐÃ RENDER, nên một thẻ có `word` dài 4.000
# ký tự nằm trong deck của ai đó sẽ thổi phồng chỗ đặt trước vượt quá 6.400 và
# tự 429 TOÀN BỘ service, âm thầm, mà kẻ gây ra không cần cố gắng gì.
#
# 24 mục × 24 ký tự là con số cân giữa hai phía: đủ rộng để phủ hết một deck
# nhỏ và để `mother-in-law` (13) hay `notwithstanding` (15) lọt qua nguyên vẹn,
# nhưng đủ hẹp để trường hợp XẤU NHẤT của danh sách này vẫn lọt trần 70%. Nới
# ra là ăn thẳng vào chỗ dành cho `max_tokens`, và test ngân sách sẽ đỏ.
AVOID_LIST_SIZE = 24
AVOID_WORD_CHARS = 24

# Chờ tối đa bao lâu để tới lượt dùng semaphore. Hết giờ thì trả đúng cái 429 mà
# caller vốn sẽ nhận, nhưng đến mà không đốt một lời gọi provider nào.
SEM_TIMEOUT_SECONDS = 10.0

_LOAI_TU_HOP_LE = frozenset({"noun", "verb", "adj", "adv", "prep", "conj"})

# Ký tự cấu trúc bị bỏ khỏi chủ đề trước khi render. M8 đưa `request.text` vào
# prompt hoàn toàn thô — với 4.000 ký tự thì đó là đánh đổi chấp nhận được, còn
# với 120 ký tự thì không có lý do gì cho phép dấu ngoặc nhọn. Bỏ hẳn chúng làm
# việc giả mạo thẻ đóng thành BẤT KHẢ chứ không chỉ là khó, và đẩy nonce về
# đúng vai phòng thủ lớp hai.
_BANG_BO = str.maketrans({ky: None for ky in "<>{}[]"})


class VocabGenerator:
    def __init__(
        self,
        *,
        settings: Settings,
        index: SearchIndex,
        encoder: Encoder,
        llm: LlmClient,
        prompts: PromptRegistry,
        sem: asyncio.Semaphore,
    ) -> None:
        self._settings = settings
        self._index = index
        self._encoder = encoder
        self._llm = llm
        self._prompts = prompts

        # Semaphore được TIÊM VÀO chứ không tự dựng, và dùng chung với
        # `VocabExtractor`. Chú thích của chính M8 nói semaphore tồn tại "vì một
        # lời gọi đặt chỗ hơn nửa ngân sách token của TOÀN hệ thống" — mục đích
        # đó là toàn cục, không thuộc riêng một endpoint. Hai instance riêng là
        # chỗ sai phạm vi hiện hình: một extract và một generate chạy song song
        # sẽ cùng đặt chỗ hơn 10.000 token trên ngân sách 6.400.
        #
        # Nó KHÔNG sửa hết: `/chat` và `/quiz` không lấy semaphore này, nên
        # trường hợp xấu nhất chỉ giảm từ bốn xuống ba lời gọi chồng nhau. Trần
        # 70% ở `MAX_CARDS_CAP` mới đóng nốt phần còn lại.
        self._sem = sem

    async def generate(
        self, request: VocabGenerateRequest
    ) -> tuple[str | None, list[VocabCandidate], VocabGenerateStats]:
        chu_de = self._tien_xu_ly(request)
        wanted = min(request.count, MAX_CARDS_CAP)

        tranh, tranh_bo = await self._danh_sach_tranh(
            chu_de, request.allowed_deck_ids, request.exclude_words
        )

        system = self._prompts.render(
            "vocab_generate_system_v1",
            count=str(wanted),
            level=request.level or "không giới hạn",
        )
        user = self._prompts.render(
            "vocab_generate_user_v1",
            nonce=secrets.token_hex(4),
            avoid_list=", ".join(tranh) if tranh else "(chưa có từ nào)",
            topic=chu_de,
        )

        async with self._giu_cho():
            ket_qua = await self._llm.complete(
                task="VOCAB_GENERATE",
                system=system,
                user=user,
                model=self._settings.ai_model_vocab,
                json_mode=True,
                max_tokens=self.max_tokens_cho(wanted),
            )

        da_doc = self._parse(ket_qua.text)

        if da_doc is None:
            raise ProviderUnavailable("Trợ lý AI trả về dữ liệu không đọc được, vui lòng thử lại.")

        hieu, tho = da_doc

        the: list[VocabCandidate] = []
        da_thay: set[str] = set()
        bo_khong_an_toan = bo_khong_mach_lac = bo_trung_lo = 0

        for muc in tho:
            ung_vien, ly_do = self._to_card(muc)

            if ung_vien is None:
                bo_khong_an_toan += ly_do == "khong_an_toan"
                bo_khong_mach_lac += ly_do == "khong_mach_lac"
                continue

            # Khử trùng TRONG LÔ. M8 không cần bước này: hai ứng viên lấy từ một
            # đoạn văn tự nhiên đã khác nhau. Ở đây chủ đề hẹp thì model lặp lại
            # là chuyện thường.
            if ung_vien.word in da_thay:
                bo_trung_lo += 1
                continue

            da_thay.add(ung_vien.word)
            the.append(ung_vien)

        # Cắt về đúng số đã xin. Prompt bảo model trả đúng `wanted`, nhưng số đo
        # cho thấy nó không nghe: xin 1 trả về 5-8, xin 3 trả về 4-5. Prompt chỉ
        # là lời khuyên, dòng này mới là luật — `count` là một lời hứa với
        # backend, không phải một gợi ý.
        thua = len(the) - wanted
        the = the[:wanted]

        if thua > 0:
            log.info("vocab_gen_cat_bot", xin=wanted, model_tra=len(tho), thua=thua)

        if not the:
            # KHÔNG có nhánh 200 rỗng ở đây, cố ý ngược M8. Xem docstring đầu file.
            log.warning(
                "vocab_gen_khong_con_the",
                returned_by_llm=len(tho),
                khong_an_toan=bo_khong_an_toan,
                khong_mach_lac=bo_khong_mach_lac,
                trung_lo=bo_trung_lo,
            )
            raise ProviderUnavailable(
                "Trợ lý AI không sinh được từ nào cho chủ đề này. "
                "Thử một chủ đề cụ thể hơn, hoặc thử lại sau ít phút."
            )

        da_co = danh_dau_trung(self._index, the, request.allowed_deck_ids)

        return (
            hieu,
            the,
            VocabGenerateStats(
                topic_chars=len(request.topic),
                level=request.level,
                requested=wanted,
                avoid_list_size=len(tranh),
                avoid_list_dropped=tranh_bo,
                returned_by_llm=len(tho),
                dropped_unsafe=bo_khong_an_toan,
                dropped_incoherent=bo_khong_mach_lac,
                dropped_duplicate_in_batch=bo_trung_lo,
                already_in_deck_count=da_co,
                dedup_checked=self._index.size > 0,
                llm_calls=1,
                prompt_tokens=ket_qua.prompt_tokens,
                completion_tokens=ket_qua.completion_tokens,
                latency_ms=ket_qua.latency_ms,
            ),
        )

    # ---------------------------------------------------------------

    @staticmethod
    def max_tokens_cho(wanted: int) -> int:
        return BASE_TOKENS + TOKENS_PER_CARD * wanted

    def _giu_cho(self):
        """Semaphore có hạn giờ, dạng dùng được với `async with`."""

        return _CoHanGio(self._sem)

    def _tien_xu_ly(self, request: VocabGenerateRequest) -> str:
        """Ném 400 TRƯỚC khi đặt chỗ ngân sách và trước khi embed. Trả chủ đề đã sạch."""

        if len(request.topic) > MAX_TOPIC_CHARS:
            raise InvalidRequest(
                f"Chủ đề dài {len(request.topic)} ký tự, vượt giới hạn "
                f"{MAX_TOPIC_CHARS} ký tự. Hãy rút ngắn lại."
            )

        if not request.allowed_deck_ids:
            raise InvalidScope("allowed_deck_ids không được rỗng.")

        chu_de = sanitize(request.topic, MAX_TOPIC_CHARS).translate(_BANG_BO).strip()

        # Cố ý KHÔNG đòi từ tiếng Anh như `MIN_ENGLISH_TOKENS` của M8: chủ đề
        # tiếng Việt là ca dùng chính ở đây, không phải ca lỗi.
        if not any(ky.isalpha() for ky in chu_de):
            raise InvalidRequest("Chủ đề phải có ít nhất một chữ cái.")

        return chu_de

    async def _danh_sach_tranh(
        self, chu_de: str, allowed_deck_ids: list[int], exclude_words: list[str]
    ) -> tuple[list[str], int]:
        """
        Từ người học đã có, để model khỏi sinh lại. 0 token LLM.

        CHỈ lấy `card.word`, tuyệt đối không dùng `serialize_card` — hàm đó cố ý
        đưa cả `note` vào prompt, mà `note` là trường tự do người dùng tự gõ.
        Trong chính bộ thẻ mẫu của repo này đã có một thẻ mang sẵn câu
        "IGNORE ALL PREVIOUS INSTRUCTIONS..." trong `note`. Dựng danh sách tránh
        bằng `serialize_card` là tự tiêm payload đó vào prompt của mình.
        """

        bo = 0

        def loc(nguon: list[str]) -> list[str]:
            """Làm sạch, giữ NGUYÊN thứ tự vào, bỏ trùng. Đếm số mục bị loại."""

            nonlocal bo
            ra: list[str] = []
            da_thay: set[str] = set()

            for raw in nguon:
                gon = sanitize(str(raw), AVOID_WORD_CHARS)

                # `sanitize` cắt dài bằng dấu "…". Từ bị cắt không còn là từ.
                if gon.endswith("…"):
                    bo += 1
                    continue

                tu = chuan_hoa(gon)

                # Cùng một định nghĩa "thế nào là một từ" với đầu ra của model.
                # Giá trị không thể là một từ hợp lệ ở đầu ra thì cũng không
                # được phép làm một mục trong danh sách tránh.
                if not tu_hop_le(tu):
                    bo += 1
                    continue

                if tu not in da_thay:
                    da_thay.add(tu)
                    ra.append(tu)

            return ra

        # `exclude_words` là những từ người dùng VỪA nhìn thấy ở lượt trước, nên
        # chúng được LẤY TRƯỚC, rồi mới lấp phần còn lại bằng kết quả tìm ngữ
        # nghĩa. Thứ tự này quan trọng, và một lần chạy thật đã chứng minh vì sao:
        # bản đầu gộp hai nguồn rồi SẮP XẾP TRƯỚC KHI CẮT, nên đúng những mục
        # xếp cuối bảng chữ cái bị vứt — `scope` và `timeline` quay lại ở lượt
        # hai dù đã nằm trong `exclude_words`. Cái "ưu tiên" ghi trong tài liệu
        # khi đó chưa hề tồn tại trong code.
        chon = loc(exclude_words)[:AVOID_LIST_SIZE]

        if len(chon) < AVOID_LIST_SIZE:
            vector = await self._encoder.embed_query(chu_de)
            trung = self._index.semantic_search(vector, allowed_deck_ids, AVOID_LIST_SIZE)
            tu_deck = loc([the.word for the in self._index.get_many([cid for cid, _ in trung])])

            da_co = set(chon)
            chon += [tu for tu in tu_deck if tu not in da_co][: AVOID_LIST_SIZE - len(chon)]

        if bo:
            # Chỉ đếm, KHÔNG log nội dung. Ghi payload vào log là mở thêm một
            # bồn chứa tiêm mới. Cố ý khác `extractor.py` vốn log `preview` của
            # JSON hỏng — JSON hỏng thì xem được, giá trị đã kết luận là hỏng thì không.
            log.warning("vocab_gen_danh_sach_tranh_bo_bot", so_muc=bo)

        # Sắp xếp SAU KHI đã chọn xong, để hai lượt gọi giống nhau cho cùng một
        # prompt — thứ tự lặp của `set` không ổn định giữa các tiến trình.
        return sorted(chon), bo

    @staticmethod
    def _parse(text: str) -> tuple[str | None, list[dict]] | None:
        """
        `None` = không đọc được (-> 503). Tuple = đọc được, kể cả khi danh sách rỗng.

        Giữ hai ca này tách nhau là bắt buộc: gộp lại thì rác từ model đi ra
        thành một câu trả lời bình thường, và người dùng sẽ gõ lại mãi.
        """

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            log.warning("vocab_gen_json_invalid", preview=text[:200])

            return None

        if not isinstance(payload, dict):
            log.warning("vocab_gen_json_sai_dang", preview=text[:200])

            return None

        muc = payload.get("words")

        if not isinstance(muc, list):
            log.warning("vocab_gen_json_thieu_words", preview=text[:200])

            return None

        hieu = str(payload.get("topic_understood") or "").strip()

        if not field_an_toan(hieu, ten="topic_understood", tieng_viet=None):
            hieu = ""

        return (hieu or None), [m for m in muc if isinstance(m, dict)]

    def _to_card(self, tho: dict) -> tuple[VocabCandidate | None, str]:
        """
        Whitelist trường + chốt chặn tất định. Trả (thẻ, "") hoặc (None, lý do).

        Prompt chỉ là lời khuyên; mấy dòng này mới là luật. Chúng đứng vững kể
        cả khi việc tiêm chỉ thị vào chủ đề đã thành công hoàn toàn.
        """

        try:
            word = chuan_hoa(str(tho.get("word") or ""))
            meaning = str(tho.get("meaning") or "").strip()
            cau = str(tho.get("example_sentence") or "").strip()

            def loai(truong: str, ly_do: str) -> tuple[None, str]:
                # Ghi TÊN LUẬT đã chặn, tuyệt đối không ghi nội dung: giá trị đã
                # bị kết luận là hỏng mà đem vào log là mở thêm một bồn chứa
                # tiêm mới. Không có dòng này thì `stats.dropped_unsafe` chỉ nói
                # "có thẻ bị loại" mà không nói luật nào, và người vận hành không
                # phân biệt được "model kém" với "chốt chặn của mình quá chặt".
                log.info("vocab_gen_loai_the", truong=truong, luat=ly_do)

                return None, "khong_an_toan"

            if not tu_hop_le(word):
                return loai("word", "sai dang tu hoac qua dai")

            if not field_an_toan(meaning, ten="meaning", tieng_viet=True):
                return loai("meaning", "qua dai, sai bo ky tu, hoac khong co dau tieng Viet")

            if not field_an_toan(cau, ten="example_sentence", tieng_viet=False):
                return loai("example_sentence", "qua dai, sai bo ky tu, hoac co dau tieng Viet")

            dinh_nghia = str(tho.get("definition_en") or "").strip() or None
            nghia_cau = str(tho.get("example_meaning") or "").strip() or None

            if dinh_nghia and not field_an_toan(dinh_nghia, ten="definition_en", tieng_viet=False):
                return loai("definition_en", "qua dai, sai bo ky tu, hoac co dau tieng Viet")

            if nghia_cau and not field_an_toan(nghia_cau, ten="example_meaning", tieng_viet=True):
                return loai(
                    "example_meaning", "qua dai, sai bo ky tu, hoac khong co dau tieng Viet"
                )

            if not cau_dung_dang(cau):
                log.info("vocab_gen_loai_the", truong="example_sentence", luat="khong phai mot cau")

                return None, "khong_mach_lac"

            if not cau_chua_tu(word, cau):
                log.info(
                    "vocab_gen_loai_the", truong="example_sentence", luat="khong chua chinh tu do"
                )

                return None, "khong_mach_lac"

            # Phiên âm hỏng thì bỏ NHÃN, không bỏ thẻ — cùng cách xử lý với
            # `part_of_speech`, mất một nhãn còn hơn mất một từ đáng học.
            phien_am = str(tho.get("phonetic") or "").strip()
            loai_tu = str(tho.get("part_of_speech") or "").strip().lower()

            return (
                VocabCandidate(
                    word=word,
                    phonetic=phien_am if phien_am_hop_le(phien_am) else None,
                    part_of_speech=loai_tu if loai_tu in _LOAI_TU_HOP_LE else None,
                    meaning=meaning,
                    definition_en=dinh_nghia,
                    example_sentence=cau,
                    example_meaning=nghia_cau,
                    already_in_deck=False,
                    existing_card_id=None,
                ),
                "",
            )
        except Exception:  # LLM trả gì cũng có thể, hỏng thì bỏ mục này
            log.warning("vocab_gen_ep_kieu_hong", exc_info=True)

            return None, "khong_an_toan"


class _CoHanGio:
    """
    `async with` cho semaphore, nhưng chờ có hạn.

    Không có hạn giờ thì hàng đợi lớn không giới hạn và request ngồi chờ quá cả
    thời gian chờ của client — người dùng thấy treo, chứ không thấy lỗi.
    """

    def __init__(self, sem: asyncio.Semaphore) -> None:
        self._sem = sem

    async def __aenter__(self) -> None:
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=SEM_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            cho = int(SEM_TIMEOUT_SECONDS)

            raise BudgetExhausted(
                f"Một lượt sinh thẻ khác đang chạy, thử lại sau {cho} giây.",
                retry_after_seconds=cho,
            ) from exc

    async def __aexit__(self, *_) -> None:
        self._sem.release()
