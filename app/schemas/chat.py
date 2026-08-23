"""
Hợp đồng cho chat và retrieval. SPEC muc 8.1.

File này cũng là thứ Swagger đọc để dựng trang `/docs`: mọi `description=` và
`examples=` ở đây hiện thẳng vào ô "Schema" của endpoint. Vì vậy viết cho
người chưa từng nghe tới RAG hay embedding vẫn tự bấm "Try it out" được, và
luôn nói rõ HẬU QUẢ khi truyền sai chứ không chỉ nói field đó là gì.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Intent(StrEnum):
    """
    Ý định của câu hỏi. Đoán bằng regex + so vector, KHÔNG gọi LLM (0 token).

    Đây là thứ quyết định lượt chat đi vào nhánh nào, nên đọc nó là cách nhanh
    nhất để hiểu vì sao câu trả lời lại ra như vậy:

    | Giá trị | Có tìm thẻ? | Kết cục |
    |---|---|---|
    | `VOCAB_LOOKUP` | Có | Nhánh DUY NHẤT được trả lời bằng template 0 token |
    | `EXAMPLE_REQUEST` | Có | Luôn phải gọi LLM vì phải đặt câu mới |
    | `QUIZ_REQUEST` | Có | Vẫn chỉ trả lời bằng chữ, KHÔNG sinh quiz |
    | `GRAMMAR_QA` | **Không** | Ngữ pháp không nằm trong thẻ nên bỏ qua tìm kiếm |
    | `TRANSLATE` | **Không** | Như trên: dịch câu không cần thẻ nào |
    | `SMALLTALK` | Không | Trả câu chào cố định, 0 token |
    | `OUT_OF_SCOPE` | Không | Trả câu từ chối cố định, 0 token |

    Hai dòng in đậm là chỗ hay gây ngạc nhiên nhất: hỏi ngữ pháp hay nhờ dịch
    thì `citations` LUÔN rỗng, không phải do tìm không ra thẻ.

    `QUIZ_REQUEST` cũng vậy — "tạo cho tôi bài kiểm tra" chỉ được trả lời bằng
    một đoạn văn xuôi. Muốn câu hỏi trắc nghiệm thật thì gọi
    `POST /internal/v1/quiz/generate`.

    Đoán sai intent chỉ làm câu trả lời kém hay đi, KHÔNG bao giờ làm lộ thẻ
    ngoài phạm vi: `allowed_deck_ids` chặn ở tầng tìm kiếm, mọi nhánh đều qua.
    """

    VOCAB_LOOKUP = "VOCAB_LOOKUP"
    EXAMPLE_REQUEST = "EXAMPLE_REQUEST"
    TRANSLATE = "TRANSLATE"
    GRAMMAR_QA = "GRAMMAR_QA"
    QUIZ_REQUEST = "QUIZ_REQUEST"
    SMALLTALK = "SMALLTALK"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class AnswerSource(StrEnum):
    """
    Câu trả lời này được dựng ra bằng cách nào — tức là lượt chat vừa rồi có
    tốn tiền hay không.

    Mỗi lượt đi lần lượt qua các nhánh rẻ trước, gặp nhánh nào thoả thì dừng
    ngay tại đó:

    | Giá trị | Gọi LLM? | Token | Rơi vào khi nào |
    |---|---|---|---|
    | `CANNED` | Không | **0** | `intent` là `SMALLTALK` hoặc `OUT_OF_SCOPE` |
    | `DIRECT_LOOKUP` | Không | **0** | Tra nghĩa một từ khớp chính xác trong thẻ |
    | `CACHE` | Không | **0** | Câu gần y hệt đã hỏi trong 24 giờ, cùng phạm vi |
    | `RAG` | Có | ~570–800 | Cần diễn giải, và có ít nhất một thẻ làm ngữ cảnh |
    | `LLM_ONLY` | Có | ~500–600 | Có gọi LLM nhưng `citations` rỗng |

    Hai con số cuối đo thật trên dữ liệu mẫu 24 thẻ, `top_k` mặc định là 3
    (trung vị `RAG` ~730). Chúng tăng theo `top_k` và theo độ dài nội dung thẻ.

    `LLM_ONLY` KHÔNG có nghĩa là câu trả lời sai. Nó có nghĩa là câu trả lời
    không dựa trên thẻ nào của người học — hoặc vì câu hỏi thuộc loại bỏ qua
    tìm kiếm (`GRAMMAR_QA`, `TRANSLATE`), hoặc vì không thẻ nào trong
    `allowed_deck_ids` đủ liên quan. Giao diện nên gắn nhãn "kiến thức chung"
    cho những câu này thay vì hiện như câu trả lời có nguồn.

    LƯU Ý cho `/chat/stream`: endpoint đó chỉ chép lại kết luận sớm của bước
    chuẩn bị, không có sẵn thì ghi thẳng `RAG` — nó không xét `citations` rỗng
    hay không như `POST /chat` làm, nên `meta` KHÔNG BAO GIỜ ghi `LLM_ONLY`. Ở
    luồng streaming, hãy nhìn mảng trong sự kiện `citations`: rỗng tức là
    LLM_ONLY.
    """

    DIRECT_LOOKUP = "DIRECT_LOOKUP"
    CACHE = "CACHE"
    RAG = "RAG"
    LLM_ONLY = "LLM_ONLY"
    CANNED = "CANNED"


class MatchType(StrEnum):
    """
    Thẻ này được tìm thấy nhờ tầng nào trong ba tầng tìm kiếm.

    | Giá trị | Nghĩa |
    |---|---|
    | `EXACT` | Câu hỏi chứa đúng từ đó, khớp tuyệt đối. Luôn được ghim hạng 1 |
    | `LEXICAL` | Chỉ BM25 bắt được: trùng chữ, sai chính tả nhẹ, từ hiếm |
    | `SEMANTIC` | Chỉ vector bắt được: hỏi vòng vo, tả nghĩa mà không nêu từ |
    | `HYBRID` | Cả hai tầng cùng bắt được — tín hiệu chắc chắn nhất sau `EXACT` |

    Chỉ `EXACT` mới mở được nhánh trả lời 0 token `DIRECT_LOOKUP`.
    """

    EXACT = "EXACT"
    LEXICAL = "LEXICAL"
    SEMANTIC = "SEMANTIC"
    HYBRID = "HYBRID"


class HistoryMessage(BaseModel):
    """Một lượt trong hội thoại trước đó, đúng như đã hiện cho người dùng."""

    role: Literal["user", "assistant"] = Field(
        description=(
            "Chỉ nhận đúng hai giá trị này. Gửi `system` hay `bot` sẽ bị **422**, "
            "không phải bị bỏ qua im lặng."
        ),
        examples=["user"],
    )
    content: str = Field(
        description=(
            "Nội dung tin nhắn. Cứ gửi nguyên văn, service tự cắt còn 6 tin gần "
            "nhất trước khi đưa vào prompt."
        ),
        examples=["resilient nghĩa là gì"],
    )


class ChatOptions(BaseModel):
    """Tinh chỉnh không bắt buộc. Bỏ trống cả cụm là đủ dùng cho mọi trường hợp."""

    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description=(
            "Số thẻ tối đa lấy làm ngữ cảnh cho LLM. Bỏ trống = `AI_TOP_K` (mặc định 3).\n\n"
            "Tăng lên thì câu trả lời đầy đủ hơn nhưng mỗi thẻ cộng thêm khoảng "
            "150 token vào prompt, nên `top_k=20` dễ chạm hạn mức và ăn **429** hơn "
            "hẳn. Giá trị này KHÔNG nới được ranh giới deck: thẻ ngoài "
            "`allowed_deck_ids` không bao giờ lọt vào, dù đặt bao nhiêu."
        ),
        examples=[3],
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=16,
        le=4096,
        description=(
            "Trần độ dài câu trả lời. Bỏ trống = `AI_MAX_OUTPUT_TOKENS` (mặc định 700).\n\n"
            "**Hiện chỉ `/chat/stream` đọc field này.** `POST /chat` bỏ qua nó và "
            "luôn dùng giá trị cấu hình — đặt ở đây rồi thấy câu trả lời không "
            "ngắn đi thì đó là lý do.\n\n"
            "Đặt quá thấp (ví dụ 16) thì câu trả lời bị cắt ngang, kéo theo mã "
            "`[#id]` ở cuối câu biến mất và `used_in_answer` sẽ toàn `false`."
        ),
        examples=[700],
    )


# Sáu kịch bản chat, dùng chung cho `POST /chat` và `POST /chat/stream`.
#
# Định nghĩa ở đây (chứ không ở router) để đúng một bản giá trị phục vụ cả hai
# chỗ: router truyền dict này vào `Body(openapi_examples=...)` để Swagger hiện
# MENU THẢ XUỐNG chọn ví dụ, còn `ChatRequest.model_config` lấy lại phần
# `value` cho ô "Schema". Sửa giá trị ở đây là cả hai nơi cùng đổi theo.
#
# Xếp ví dụ 0 token lên đầu: đây là ví dụ duy nhất chắc chắn chạy được khi
# CHƯA cấu hình AI_LLM_API_KEY.
VI_DU_CHAT: dict = {
    "tra_tu_0_token": {
        "summary": "Tra nghĩa một từ tiếng Anh — trả lời 0 token, không cần khoá Groq",
        "description": (
            "Bấm **Execute** sẽ thấy `answer_source = DIRECT_LOOKUP` và "
            "`usage.prompt_tokens = 0`: câu trả lời được ghép thẳng từ các trường của "
            "thẻ, không một request nào gửi sang Groq. `citations` có đúng 1 phần tử "
            "(thẻ 101 `resilient`, `score = 1.0`, `used_in_answer = true`).\n\n"
            "Đây là ví dụ duy nhất chắc chắn chạy được khi chưa cấu hình "
            "`AI_LLM_API_KEY`. Đổi câu hỏi thành `resilient khác gì diligent` là tụt "
            "xuống `RAG` và tốn khoảng 1.000 token, dù chỉ khác vài chữ."
        ),
        "value": {
            "query": "resilient nghĩa là gì",
            "allowed_deck_ids": [1, 2, 3, 4],
        },
    },
    "ngoai_chu_de": {
        "summary": "Hỏi chuyện ngoài việc học từ — nhận câu từ chối cố định, 0 token",
        "description": (
            "Bấm **Execute** sẽ thấy `intent = OUT_OF_SCOPE`, "
            "`answer_source = CANNED`, `citations` rỗng và `usage.prompt_tokens = 0`. "
            "`answer` là một đoạn từ chối lịch sự viết sẵn trong code, luôn giống hệt "
            "nhau qua mọi lần gọi.\n\n"
            "Cũng không cần `AI_LLM_API_KEY`: nhánh này thoát ra trước cả bước tìm thẻ."
        ),
        "value": {
            "query": "hôm nay thời tiết thế nào",
            "allowed_deck_ids": [1, 2, 3, 4],
        },
    },
    "can_dien_giai": {
        "summary": "Hỏi cách dùng từ trong ngữ cảnh công sở — buộc phải gọi LLM (RAG)",
        "description": (
            "Chữ *ngữ cảnh* chặn nhánh template 0 token lại, nên lượt này bắt buộc gọi "
            "LLM. Bấm **Execute** sẽ thấy `answer_source = RAG`, "
            "`usage.prompt_tokens` khoảng 480–660, và `citations` tối đa 5 thẻ của "
            "deck 1 (`options.top_k = 5`) xếp theo `rank`.\n\n"
            "Gửi ĐÚNG body này **lần thứ hai** sẽ nhận `answer_source = CACHE` với "
            "`prompt_tokens = 0` — cách nhanh nhất để tự thấy semantic cache hoạt "
            "động.\n\n"
            "Cần `AI_LLM_API_KEY` hợp lệ; thiếu khoá sẽ nhận "
            "**503 `PROVIDER_UNAVAILABLE`** chứ không phải câu trả lời rỗng."
        ),
        "value": {
            "query": "resilient dùng trong ngữ cảnh công sở thế nào",
            "allowed_deck_ids": [1],
            "options": {"top_k": 5},
        },
    },
    "co_lich_su_viet_lai": {
        "summary": "Câu hỏi dựa vào lượt trước — hệ thống tự viết lại câu hỏi",
        "description": (
            "Bấm **Execute** sẽ thấy `rewritten_query` KHÁC `null` — đại ý "
            "*cho tôi ví dụ về một người kiên cường*. Chính câu ĐÃ viết lại mới là "
            "câu được đem đi tìm thẻ.\n\n"
            "Đây là ví dụ DUY NHẤT trong danh sách cho kết quả khác nhau giữa các "
            "lần chạy, vì bước viết lại do LLM thực hiện và mỗi lần nó diễn đạt một "
            "kiểu. Đo 7 lần liên tiếp: `answer_source` ra `RAG` 6 lần và `CACHE` 1 "
            "lần (lần chạy lại trúng cache), `intent` ra `EXAMPLE_REQUEST` 5 lần và "
            "`VOCAB_LOOKUP` 2 lần, `prompt_tokens` dao động 520–660. Nếu bạn cần một "
            "ví dụ ổn định để so sánh thì dùng `can_dien_giai`.\n\n"
            "Bước viết lại tiêu thêm khoảng 150 token của model `openai/gpt-oss-20b` "
            "và số đó **không** nằm trong `usage` — muốn thấy thì xem "
            "`GET /internal/v1/stats`.\n\n"
            "Xoá `history` đi rồi gửi lại: `rewritten_query` thành `null`, không ai "
            'còn hiểu "từ này" là từ nào, và `citations` sẽ lấy nhầm thẻ. '
            "Cần `AI_LLM_API_KEY`."
        ),
        "value": {
            "query": "cho tôi ví dụ với từ này",
            "allowed_deck_ids": [1],
            "history": [
                {"role": "user", "content": "resilient nghĩa là gì"},
                {
                    "role": "assistant",
                    "content": "resilient nghĩa là kiên cường, phục hồi nhanh. [#101]",
                },
            ],
        },
    },
    "khong_co_trong_bo_the": {
        "summary": "Hỏi từ nằm ở deck không được phép đọc — citations rỗng",
        "description": (
            "Thẻ 303 `deforestation` nằm ở deck 3, mà `allowed_deck_ids` chỉ có "
            "`[1, 2]` — với lượt hỏi này thẻ đó coi như không tồn tại. Bấm "
            "**Execute** sẽ thấy `answer_source = LLM_ONLY`, `citations` **rỗng** và "
            "`usage.prompt_tokens` khoảng 600; `answer` nói rõ từ này chưa có trong "
            "bộ thẻ.\n\n"
            "Thêm `3` vào `allowed_deck_ids` rồi gửi lại: `answer_source` nhảy về "
            "`DIRECT_LOOKUP` và về 0 token. Cần `AI_LLM_API_KEY` cho lần chạy đầu."
        ),
        "value": {
            "query": "deforestation nghĩa là gì",
            "allowed_deck_ids": [1, 2],
        },
    },
    "hoi_ngu_phap": {
        "summary": "Hỏi ngữ pháp — cố tình bỏ qua bước tìm thẻ nên không có nguồn",
        "description": (
            "Bấm **Execute** sẽ thấy `intent = GRAMMAR_QA`, "
            "`answer_source = LLM_ONLY`, `citations` rỗng và `usage.prompt_tokens` "
            "khoảng 600.\n\n"
            "`citations` rỗng ở đây KHÔNG phải vì tìm không ra thẻ — mà vì "
            "`GRAMMAR_QA` bỏ hẳn bước tìm thẻ: ngữ pháp là kiến thức chung, không nằm "
            "trên thẻ nào. Giao diện nên gắn nhãn *kiến thức chung* cho nhóm này. "
            "Cần `AI_LLM_API_KEY`."
        ),
        "value": {
            "query": "thì hiện tại hoàn thành dùng khi nào",
            "allowed_deck_ids": [1, 2, 3, 4],
        },
    },
}


class ChatRequest(BaseModel):
    """
    Một lượt hỏi. Bắt buộc đúng hai field: `query` và `allowed_deck_ids`.

    Các ví dụ dưới ô "Schema" chạy được ngay với dữ liệu mẫu
    (`AI_SOURCE_MODE=fixture`, 24 thẻ, deck 1–4).
    """

    model_config = ConfigDict(
        json_schema_extra={"examples": [v["value"] for v in VI_DU_CHAT.values()]}
    )

    query: str = Field(
        description=(
            "Câu hỏi của người học, tiếng Việt hoặc tiếng Anh đều được.\n\n"
            "HẠN CHẾ ĐÃ BIẾT: câu thuần tiếng Việt viết KHÔNG DẤU "
            "(`tu nao chi cam giac lo lang`) gần như luôn không tìm ra thẻ nào, "
            "vì model embedding coi đó là chuỗi khác hẳn. Câu có lẫn từ tiếng "
            "Anh vẫn chạy tốt nhờ tầng khớp chính xác."
        ),
        examples=["resilient nghĩa là gì"],
    )
    allowed_deck_ids: list[int] = Field(
        description=(
            "Toàn bộ deck mà người dùng hiện tại được phép đọc. **Đây là ranh "
            "giới bảo mật duy nhất** — fsoft-ai không biết người dùng là ai, "
            "không có phiên đăng nhập, không tự kiểm tra quyền được.\n\n"
            "- Danh sách rỗng = **không được phép gì cả** → **400 INVALID_SCOPE** "
            "ở `POST /chat`, còn ở `/chat/stream` là HTTP 200 kèm sự kiện "
            '`error`. Tuyệt đối không hiểu thành "không lọc".\n'
            "- Thẻ ngoài danh sách không bao giờ xuất hiện, kể cả trong "
            "`citations`.\n\n"
            "Backend Java phải tự tính danh sách này từ quyền thật; đừng lấy từ "
            "tham số client gửi lên."
        ),
        examples=[[1, 2, 3, 4]],
    )
    scope_deck_id: int | None = Field(
        default=None,
        description=(
            "Thu hẹp lượt hỏi này về đúng MỘT deck, dùng khi người dùng đang mở "
            "một bộ thẻ cụ thể.\n\n"
            "Phải nằm trong `allowed_deck_ids`, nếu không sẽ bị **400 "
            "INVALID_SCOPE** (ở `/chat/stream` là HTTP 200 kèm sự kiện `error`) "
            "— cố tình như vậy để một request truyền nhầm deck bị chặn thẳng "
            "chứ không âm thầm trả về kết quả rộng hơn ý muốn."
        ),
        examples=[1],
    )
    history: list[HistoryMessage] = Field(
        default_factory=list,
        description=(
            "Các lượt trước của đúng cuộc hội thoại này, cũ ở đầu, mới ở cuối.\n\n"
            "Chỉ **6 tin nhắn cuối** được dùng (`AI_HISTORY_MAX_MESSAGES`). Gửi "
            "50 tin không gây lỗi, nhưng 44 tin đầu bị cắt bỏ im lặng — đừng "
            "trông chờ model nhớ thứ đã trôi quá xa.\n\n"
            "Có `history` mới có thể xảy ra bước viết lại câu hỏi: xem "
            "`rewritten_query` ở phản hồi."
        ),
    )
    options: ChatOptions = Field(
        default_factory=ChatOptions,
        description="Tinh chỉnh không bắt buộc. Bỏ hẳn field này là lựa chọn tốt nhất.",
    )


class CitationOut(BaseModel):
    """Một thẻ đã được đưa vào ngữ cảnh cho câu trả lời này."""

    card_id: int = Field(
        description=(
            "ID thẻ, trùng `cardId` bên backend Java. Mã `[#101]` nằm trong "
            "`answer` chính là số này — dò các mã đó để biết câu nào trong câu "
            "trả lời tương ứng với thẻ nào."
        ),
        examples=[101],
    )
    word: str = Field(description="Từ vựng trên thẻ.", examples=["resilient"])
    deck_id: int = Field(
        description=(
            "Deck chứa thẻ. Luôn nằm trong `allowed_deck_ids` đã gửi lên — thấy "
            "một deck lạ ở đây nghĩa là ranh giới bảo mật đã thủng, phải báo ngay."
        ),
        examples=[1],
    )
    deck_title: str | None = Field(
        default=None,
        description="Tên bộ thẻ để hiển thị. `null` nếu lần đồng bộ không kèm tiêu đề.",
        examples=["TOEIC - Cảm xúc & Tính cách"],
    )
    score: float = Field(
        description=(
            "Độ liên quan. **Không phải xác suất**, và chỉ so được giữa các thẻ "
            "trong CÙNG một phản hồi.\n\n"
            "- `1.0` = thẻ khớp chính xác (câu hỏi chứa đúng từ đó), luôn được "
            "ghim hạng 1 bất kể các tầng tìm kiếm khác nói gì.\n"
            "- Còn lại là điểm RRF, thường nằm quanh 0,015–0,035. Thấy `0.0161` "
            'là hoàn toàn bình thường, đừng dịch thành "chỉ 1,6% liên quan" rồi '
            "lọc bỏ."
        ),
        examples=[1.0],
    )
    rank: int = Field(
        description=(
            "Thứ hạng 1, 2, 3… đúng theo thứ tự đã nạp vào ngữ cảnh cho LLM. "
            "Hiển thị danh sách nguồn theo đúng thứ tự này."
        ),
        examples=[1],
    )
    used_in_answer: bool = Field(
        description=(
            "Thẻ này có thật sự được trích dẫn trong `answer` không — tính bằng "
            "cách dò mã `[#card_id]` trong văn bản trả lời.\n\n"
            "Khác biệt cần nắm: cả mảng `citations` là những thẻ đã ĐƯA CHO model "
            "đọc; `used_in_answer=true` là những thẻ model THẬT SỰ dùng. Giao "
            "diện nên tô đậm nhóm `true` và thu gọn nhóm `false` — đừng ẩn hẳn "
            "nhóm `false`, vì khi câu trả lời lạc đề thì chính chúng cho biết "
            "tìm kiếm đã lấy nhầm thẻ nào.\n\n"
            "Ở `/chat/stream` cờ này gần như luôn `false` trong sự kiện "
            "`citations`, vì lúc đó câu trả lời còn chưa tồn tại."
        ),
        examples=[True],
    )


class UsageOut(BaseModel):
    """Chi phí của lượt chat này. Đọc `prompt_tokens` trước tiên."""

    provider: str = Field(
        default="groq",
        description=(
            "Luôn là chuỗi `groq`, kể cả ở ba nhánh 0 token vốn không hề gọi "
            "Groq — đây là giá trị mặc định của field chứ không phải kết quả đo. "
            "Đừng dùng nó để biết lượt có tốn tiền không."
        ),
        examples=["groq"],
    )
    model: str | None = Field(
        default=None,
        description=(
            "Model đã sinh câu trả lời. `null` ở ba nhánh 0 token vì không có "
            "lời gọi nào.\n\n"
            "Có thể là model dự phòng `openai/gpt-oss-20b` thay vì "
            "`openai/gpt-oss-120b` nếu model chính đang bị nhà cung cấp chặn "
            "vì quá tải — câu trả lời khi đó ngắn và đơn giản hơn bình thường."
        ),
        examples=["openai/gpt-oss-120b"],
    )
    prompt_tokens: int = Field(
        description=(
            '**Đây là cờ "lượt này có tốn tiền không".**\n\n'
            "`0` nghĩa là lượt vừa rồi rơi vào một trong ba nhánh miễn phí "
            "(`CANNED`, `DIRECT_LOOKUP`, `CACHE`) — không một request nào được "
            "gửi sang Groq. Mục tiêu vận hành là ≥ 40% số lượt có `prompt_tokens = 0`.\n\n"
            "Ngoại lệ hiếm: nếu `answer_source` là `RAG`/`LLM_ONLY` mà vẫn thấy "
            "`0` thì đó là nhà cung cấp không trả số liệu, không phải lượt miễn "
            "phí. Luôn đối chiếu với `answer_source`.\n\n"
            "Số này KHÔNG bao gồm ~150 token của bước viết lại câu hỏi (xem "
            "`rewritten_query`); muốn thấy chi phí đó thì xem "
            "`GET /internal/v1/stats`."
        ),
        examples=[0],
    )
    completion_tokens: int = Field(
        description="Số token model sinh ra. `0` ở ba nhánh miễn phí.",
        examples=[0],
    )
    latency_ms: int = Field(
        description=(
            "Thời gian của RIÊNG lời gọi LLM, không phải thời gian cả request "
            "HTTP.\n\n"
            "Bằng `0` ở ba nhánh miễn phí vì không có lời gọi nào để đo — đừng "
            'đọc thành "trả lời tức thì" rồi vẽ biểu đồ p95 từ đó.\n\n'
            "Ở `/chat/stream`, trường cùng tên trong sự kiện `done` đo thứ khác "
            "hẳn tuỳ nhánh: nhánh miễn phí đo TRỌN lượt (nên khác `0`), còn "
            "nhánh gọi LLM vẫn chỉ đo riêng lời gọi streaming. Đừng trộn hai "
            "endpoint vào cùng một biểu đồ."
        ),
        examples=[0],
    )


class ChatResponse(BaseModel):
    """Phản hồi của `POST /internal/v1/chat`. Luôn đủ 6 field, không field nào biến mất."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "answer": (
                        "**resilient** /rɪˈzɪliənt/ (adj)\n"
                        "Nghĩa: kiên cường, có khả năng phục hồi nhanh\n"
                        "Định nghĩa: able to recover quickly from difficult conditions\n"
                        "Ví dụ: She remained resilient despite repeated setbacks.\n"
                        "→ Cô ấy vẫn kiên cường dù liên tục gặp thất bại.\n"
                        "[#101]"
                    ),
                    "intent": "VOCAB_LOOKUP",
                    "answer_source": "DIRECT_LOOKUP",
                    "rewritten_query": None,
                    "citations": [
                        {
                            "card_id": 101,
                            "word": "resilient",
                            "deck_id": 1,
                            "deck_title": "TOEIC - Cảm xúc & Tính cách",
                            "score": 1.0,
                            "rank": 1,
                            "used_in_answer": True,
                        }
                    ],
                    "usage": {
                        "provider": "groq",
                        "model": None,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "latency_ms": 0,
                    },
                }
            ]
        }
    )

    answer: str = Field(
        description=(
            "Câu trả lời tiếng Việt, có markdown nhẹ (`**đậm**`, xuống dòng) và "
            "các mã nguồn dạng `[#101]` ở cuối những câu lấy dữ liệu từ thẻ.\n\n"
            "Giao diện nên thay `[#101]` bằng một chip bấm được dẫn tới thẻ 101; "
            "để nguyên thì người học đọc thấy rất khó hiểu."
        ),
    )
    intent: Intent = Field(
        description=(
            "Loại câu hỏi mà bộ phân loại đã đoán (0 token). Xem bảng ở schema "
            "`Intent` để biết mỗi giá trị kéo theo nhánh xử lý nào."
        ),
        examples=["VOCAB_LOOKUP"],
    )
    answer_source: AnswerSource = Field(
        description=(
            "Câu trả lời được dựng bằng cách nào. Ghi log field này — nó là "
            "cách duy nhất để biết hệ thống đang đốt token vào việc mà dữ liệu "
            "cục bộ làm được miễn phí. Xem bảng ở schema `AnswerSource`."
        ),
        examples=["DIRECT_LOOKUP"],
    )
    rewritten_query: str | None = Field(
        default=None,
        description=(
            "Câu hỏi độc lập do model 8b viết lại từ `query` + `history`. Ví dụ "
            "`cho tôi ví dụ với từ này` → `cho tôi ví dụ với từ resilient`.\n\n"
            "Khi khác `null`, ĐÂY mới là câu thật sự được đem đi tìm thẻ và phân "
            'loại `intent` — gỡ lỗi "sao nó tìm sai thẻ" thì nhìn field này '
            "trước tiên.\n\n"
            "`null` có ba nghĩa gộp lại, không phân biệt được từ ngoài: không "
            "gửi `history`, câu hỏi đã tự đủ nghĩa nên hệ thống cố tình không "
            "viết lại để tiết kiệm token, hoặc lần viết lại bị lỗi và hệ thống "
            "lặng lẽ quay về `query` gốc."
        ),
        examples=[None],
    )
    citations: list[CitationOut] = Field(
        description=(
            "Các thẻ đã được nạp vào ngữ cảnh, xếp theo `rank`. Rỗng ở ba trường "
            "hợp: `intent` là `GRAMMAR_QA`/`TRANSLATE` (cố tình bỏ qua tìm "
            "kiếm), `answer_source=CANNED`, hoặc không thẻ nào trong "
            "`allowed_deck_ids` đủ liên quan."
        ),
    )
    usage: UsageOut = Field(
        description="Chi phí lượt này. `usage.prompt_tokens = 0` là lượt miễn phí.",
    )
