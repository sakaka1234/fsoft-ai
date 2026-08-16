"""
Schema cho POST /internal/v1/quiz/generate. SPEC muc 8.4.

BỐN dạng câu hỏi dùng CHUNG một hình dạng `QuizQuestion`, nên tuỳ dạng mà vài
trường là `null`. Đây là quyết định có chủ ý: backend Java chỉ phải ánh xạ một
lớp DTO thay vì bốn. Cái giá phải trả là người đọc dễ tưởng `correct_index: null`
ở dạng MATCHING là dữ liệu hỏng — không phải, dạng đó chấm bằng `matching`.

BA TRONG BỐN dạng dựng thẳng từ dữ liệu thẻ, không chạm LLM. Chỉ FILL_BLANK cần
Groq, và cũng chỉ khi `use_ai_context=true`.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class QuestionType(StrEnum):
    """
    Dạng câu hỏi. Truyền qua `types`, viết HOA đúng như bảng dưới.

    | Giá trị | Cần Groq? | Đề bài | Chấm điểm bằng |
    |---|---|---|---|
    | `MULTIPLE_CHOICE` | Không | "resilient" nghĩa là gì? | `correct_index` |
    | `LISTENING` | Không | Nghe và chọn từ, kèm `audio_url` | `correct_index` |
    | `MATCHING` | Không | Nối tối đa 5 từ với 5 nghĩa | `matching[]` |
    | `FILL_BLANK` | **Có** | Câu tiếng Anh mới có ô `______` | `correct_index` |

    Vì sao chỉ FILL_BLANK cần LLM: nó đòi một câu tiếng Anh MỚI có ngữ cảnh,
    khác câu ví dụ đã in sẵn trên thẻ. Ba dạng còn lại chỉ ráp lại dữ liệu thẻ
    đã nằm sẵn trong RAM nên không tốn token và không phụ thuộc mạng.
    """

    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    FILL_BLANK = "FILL_BLANK"
    LISTENING = "LISTENING"
    MATCHING = "MATCHING"


class GeneratedBy(StrEnum):
    """
    Câu hỏi này do đâu ra: ráp từ dữ liệu thẻ, hay do LLM soạn.

    Dùng để khoanh vùng khi một câu hỏi nhìn kỳ quặc. `DETERMINISTIC` sai là lỗi
    DỮ LIỆU THẺ (nghĩa thẻ sai, hai thẻ trùng nghĩa) — sửa ở backend Java.
    `LLM` sai là do model bịa — sửa ở prompt.

    LƯU Ý QUAN TRỌNG: một câu FILL_BLANK hỏng (Groq trả JSON sai, đề bài lộ đáp
    án, giải thích không phải tiếng Việt) sẽ được THAY bằng câu trắc nghiệm
    deterministic chứ không báo lỗi. Khi đó `type` thành `MULTIPLE_CHOICE` và
    `generated_by` thành `DETERMINISTIC`. Đối chiếu `stats.llm_count` với số câu
    FILL_BLANK bạn đặt hàng để biết chuyện đó có xảy ra không.
    """

    DETERMINISTIC = "DETERMINISTIC"
    LLM = "LLM"


class MatchingPair(BaseModel):
    """
    Một dòng ở CỘT TRÁI của dạng MATCHING, kèm đáp án đúng của dòng đó.

    Cột phải chính là `options` của câu hỏi — danh sách nghĩa đã xáo trộn sẵn ở
    server. `correct_option_index` là cây cầu nối hai cột.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"card_id": 101, "word": "resilient", "correct_option_index": 1}]
        }
    )

    card_id: int = Field(
        description=(
            "Thẻ ứng với từ ở cột trái. fsoft-ai KHÔNG lưu kết quả làm bài, nên "
            "muốn cập nhật SRS thì backend Java tự đọc `card_id` này rồi ghi lại "
            "người học nối đúng hay sai."
        ),
        examples=[101],
    )
    word: str = Field(
        description="Từ tiếng Anh hiển thị ở cột trái, lấy nguyên văn từ thẻ.",
        examples=["resilient"],
    )
    correct_option_index: int = Field(
        description=(
            "Vị trí 0-based trong `options` là nghĩa đúng của `word`. "
            "Tập các `correct_option_index` của một câu LUÔN là hoán vị đầy đủ của "
            "`0..len(options)-1`: mỗi nghĩa nối đúng một từ, không nghĩa nào thừa, "
            "không nghĩa nào dùng hai lần. Giao diện có thể dựa vào đó để chấm "
            "'đúng hết hoặc sai' mà không sợ lệch."
        ),
        examples=[1],
    )


class QuizQuestion(BaseModel):
    """Một câu hỏi. Bốn dạng dùng chung hình dạng này, khác nhau ở trường nào null."""

    index: int = Field(
        description=(
            "Số thứ tự 1..N để hiển thị. Được đánh lại SAU KHI đã loại các thẻ "
            "không sinh được câu, nên luôn liên tục — đừng dùng khoảng trống của "
            "`index` để phát hiện câu bị bỏ, sẽ không bao giờ thấy. Cũng đừng dùng "
            "làm khoá lưu trữ: gọi lại lần nữa là số này ứng với thẻ khác."
        ),
        examples=[1],
    )
    type: QuestionType = Field(
        description=(
            "Dạng THỰC TẾ của câu này, không phải dạng bạn đặt hàng. Luôn đọc "
            "trường này để chọn cách hiển thị — dạng đặt hàng có thể đã bị lùi về "
            "`MULTIPLE_CHOICE` mà không có cảnh báo nào."
        ),
        examples=["MULTIPLE_CHOICE"],
    )
    card_id: int | None = Field(
        default=None,
        description=(
            "Thẻ nguồn của câu hỏi. `null` ở dạng MATCHING vì một câu MATCHING gộp "
            "tới 5 thẻ — lấy `card_id` trong từng phần tử `matching[]`."
        ),
        examples=[101],
    )
    prompt: str = Field(
        description=(
            "Đề bài, hiển thị nguyên văn. Dạng FILL_BLANK chứa dãy `______` (6 gạch "
            "dưới) đánh dấu chỗ trống — đừng tự chèn ô input ở chỗ khác."
        ),
        examples=['"resilient" nghĩa là gì?'],
    )
    options: list[str] = Field(
        default_factory=list,
        description=(
            "MULTIPLE_CHOICE / LISTENING / FILL_BLANK: ĐÚNG 4 lựa chọn. "
            "MATCHING: đây là CỘT PHẢI, tức danh sách nghĩa, số phần tử bằng số cặp "
            "trong `matching` — cụ thể là `min(5, question_count)`, KHÔNG phải luôn "
            "5. Xin 3 câu thì câu nối chỉ có 3 nghĩa.\n\n"
            "Thứ tự đã được xáo ngẫu nhiên ở server. TUYỆT ĐỐI KHÔNG xáo lại ở "
            "client: `correct_index` và `correct_option_index` trỏ theo vị trí, "
            "xáo lại là chấm sai toàn bộ bài."
        ),
        examples=[
            [
                "tỉ mỉ, cẩn thận đến từng chi tiết",
                "kiên cường, có khả năng phục hồi nhanh",
                "siêng năng, chăm chỉ và bền bỉ",
                "thấu cảm, hiểu và chia sẻ cảm xúc người khác",
            ]
        ],
    )
    correct_index: int | None = Field(
        default=None,
        description=(
            "Vị trí 0-based của đáp án đúng trong `options`. `null` ở dạng MATCHING "
            "— dạng đó chấm bằng `matching[].correct_option_index`.\n\n"
            "Đáp án đúng nằm rải đều nhờ xáo trộn, đừng giả định luôn là 0. Nếu "
            "định giấu trường này khỏi người học thì phải lọc ở backend Java trước "
            "khi trả xuống frontend — fsoft-ai luôn trả kèm."
        ),
        examples=[1],
    )
    explanation: str = Field(
        description=(
            "Câu giải thích TIẾNG VIỆT, hiện SAU khi người học đã chọn đáp án. Câu "
            "deterministic ghép từ nghĩa + định nghĩa tiếng Anh + câu ví dụ của "
            "thẻ, nên nó lộ đáp án — hiện sớm là hỏng bài."
        ),
        examples=[
            (
                "resilient: kiên cường, có khả năng phục hồi nhanh. "
                "Tiếng Anh: able to recover quickly from difficult conditions. "
                "Ví dụ: She remained resilient despite repeated setbacks."
            )
        ],
    )
    generated_by: GeneratedBy = Field(
        description="Ráp từ dữ liệu thẻ (`DETERMINISTIC`) hay do Groq soạn (`LLM`).",
        examples=["DETERMINISTIC"],
    )

    # Chỉ dạng LISTENING dùng.
    audio_url: str | None = Field(
        default=None,
        description=(
            "CHỈ dạng LISTENING mới có, ba dạng còn lại luôn `null`. Lấy nguyên văn "
            "từ trường `audioUrl` của thẻ ở backend Java: fsoft-ai không sinh file "
            "âm thanh, không kiểm tra URL còn sống, không proxy.\n\n"
            "Thẻ không có `audio_url` thì KHÔNG BAO GIỜ ra câu LISTENING — nó lùi "
            "về trắc nghiệm trong im lặng. Xem mô tả endpoint."
        ),
        examples=["https://cdn.example.com/audio/resilient.mp3"],
    )

    # Chỉ dạng MATCHING dùng: cột trái và đáp án nối sang `options`.
    matching: list[MatchingPair] | None = Field(
        default=None,
        description=(
            "CHỈ dạng MATCHING mới có, ba dạng còn lại luôn `null`. Đây là CỘT "
            "TRÁI (từ tiếng Anh); cột phải là `options` (nghĩa tiếng Việt đã xáo). "
            "Người học kéo nối trái sang phải, chấm đúng khi chỉ số nối trùng "
            "`correct_option_index`.\n\n"
            "Số cặp là `min(5, question_count)` chứ không phải luôn 5 — một câu "
            "nối gộp các thẻ ĐÃ ĐƯỢC LẤY RA cho lượt này, nên xin ít câu thì bảng "
            "nối cũng ngắn theo. Dưới 2 cặp thì không dựng được câu nối và nó âm "
            "thầm lùi về `MULTIPLE_CHOICE`."
        ),
    )


class QuizStats(BaseModel):
    """
    Số đo của riêng lượt gọi này. Đây là bằng chứng để trả lời câu hỏi
    'lượt vừa rồi có tốn tiền không', đừng đoán bằng cách nhìn `types`.
    """

    deterministic_count: int = Field(
        description="Số câu ráp từ dữ liệu thẻ, tốn 0 token. Bằng tổng số câu trừ `llm_count`.",
        examples=[5],
    )
    llm_count: int = Field(
        description=(
            "Số câu do Groq soạn và QUA ĐƯỢC bộ kiểm. Nhỏ hơn số câu FILL_BLANK "
            "bạn đặt hàng nghĩa là có câu đã bị thay bằng trắc nghiệm deterministic "
            "— đây là tín hiệu duy nhất cho biết chuyện đó xảy ra."
        ),
        examples=[0],
    )
    prompt_tokens: int = Field(
        description=(
            "Token đầu vào đã tiêu cho lượt này. `use_ai_context=false` LUÔN cho 0 "
            "— dùng đúng con số này để chứng minh chế độ dự phòng không đốt ngân sách."
        ),
        examples=[0],
    )
    completion_tokens: int = Field(
        description="Token đầu ra đã tiêu. Cũng luôn 0 khi `use_ai_context=false`.",
        examples=[0],
    )
    latency_ms: int = Field(
        description=(
            "Thời gian sinh câu hỏi ở phía service, KHÔNG gồm thời gian truyền mạng "
            "nên luôn nhỏ hơn số Swagger hiển thị. Deterministic thuần: 8 câu dưới "
            "2 giây (acceptance SPEC muc 11.6) — đo thật trên dữ liệu mẫu chỉ mất "
            "1-2 ms vì không có lời gọi mạng nào. Có FILL_BLANK: cộng thêm thời "
            "gian chờ Groq cho mỗi lô 5 thẻ, thực đo khoảng 2.5 giây cho một lô."
        ),
        examples=[1],
    )


# Bốn kịch bản thử endpoint, định nghĩa MỘT chỗ và dùng ở HAI nơi:
#   - router truyền vào `Body(openapi_examples=...)` -> chúng rơi vào
#     `requestBody.content["application/json"].examples`, nên Swagger UI hiện
#     MENU THẢ XUỐNG để chọn kịch bản thay vì chỉ điền sẵn đúng một ví dụ;
#   - `QuizRequest.model_config` dùng lại chính các `value` này cho tab Schema.
# Định nghĩa một chỗ để hai nơi không bao giờ lệch nhau.
VI_DU_QUIZ_GENERATE: dict = {
    "che_do_du_phong_0_token": {
        "summary": "Chế độ dự phòng, 0 token — 5 câu trắc nghiệm deck 1",
        "description": (
            "Kịch bản mặc định, cũng là chế độ nên dùng cho ngày demo. Bấm "
            "Execute sẽ thấy 5 câu `MULTIPLE_CHOICE`, câu nào cũng "
            "`generated_by = DETERMINISTIC`, và `stats` cho "
            "`deterministic_count = 5`, `llm_count = 0`, `prompt_tokens = 0`, "
            "`completion_tokens = 0`. Không một lời gọi HTTP nào rời khỏi tiến "
            "trình nên `latency_ms` chỉ 1–2 ms — đây là bằng chứng lượt gọi "
            "không tốn tiền."
        ),
        "value": {
            "deck_id": 1,
            "allowed_deck_ids": [1, 2, 3],
            "question_count": 5,
            "types": ["MULTIPLE_CHOICE"],
            "card_ids": [],
            "use_ai_context": False,
        },
    },
    "tron_ba_dang_khong_can_llm": {
        "summary": "Trộn ba dạng không cần LLM — trắc nghiệm / nghe / nối, vẫn 0 token",
        "description": (
            "Sáu thẻ deck 1 được chia VÒNG TRÒN cho ba dạng, mỗi dạng 2 câu. "
            "Bấm Execute sẽ thấy 6 câu: hai câu `MULTIPLE_CHOICE`, hai câu "
            "`LISTENING` (dạng duy nhất có `audio_url`), hai câu `MATCHING` "
            "với `card_id = null`, `correct_index = null` và 5 cặp trong "
            "`matching[]`. `stats`: `deterministic_count = 6`, `llm_count = 0`, "
            "mọi số token bằng 0."
        ),
        "value": {
            "deck_id": 1,
            "allowed_deck_ids": [1, 2, 3],
            "question_count": 6,
            "types": ["MULTIPLE_CHOICE", "LISTENING", "MATCHING"],
            "card_ids": [],
            "use_ai_context": False,
        },
    },
    "on_dung_the_den_han_theo_srs": {
        "summary": "Ôn đúng 4 thẻ đến hạn do backend Java chọn theo SRS",
        "description": (
            "`card_ids` thu hẹp phạm vi hỏi xuống đúng bốn thẻ 201/203/205/207 "
            "của deck 2. Bấm Execute sẽ thấy 4 câu, xen kẽ `MULTIPLE_CHOICE` và "
            "`LISTENING` theo vòng tròn, và chỉ những thẻ vừa liệt kê xuất hiện "
            "ở `card_id`. Đáp án nhiễu vẫn được lấy từ CẢ deck 2 nên mỗi câu vẫn "
            "đủ 4 lựa chọn. Vẫn 0 token vì `use_ai_context` là `false`."
        ),
        "value": {
            "deck_id": 2,
            "allowed_deck_ids": [1, 2, 3],
            "question_count": 4,
            "types": ["MULTIPLE_CHOICE", "LISTENING"],
            "card_ids": [201, 203, 205, 207],
            "use_ai_context": False,
        },
    },
    "fill_blank_dang_duy_nhat_can_groq": {
        "summary": "Điền vào chỗ trống — dạng duy nhất chạm Groq",
        "description": (
            "Có `AI_LLM_API_KEY` thật: 5 thẻ được gom trong MỘT lời gọi, bấm "
            "Execute sẽ thấy tới 5 câu `FILL_BLANK` với đề bài chứa dãy "
            "`______`, `generated_by = LLM`, `llm_count` lên tới 5, "
            "`prompt_tokens` và `completion_tokens` khác 0, `latency_ms` khoảng "
            "2.5 giây.\n\n"
            "Không có API key thì vẫn nhận 200 chứ không phải lỗi: mọi câu âm "
            "thầm lùi về `MULTIPLE_CHOICE` với `generated_by = DETERMINISTIC` và "
            "`llm_count = 0` — và lượt gọi còn CHẬM hơn vì phải đợi Groq trả lỗi."
        ),
        "value": {
            "deck_id": 2,
            "allowed_deck_ids": [1, 2, 3],
            "question_count": 5,
            "types": ["FILL_BLANK"],
            "card_ids": [],
            "use_ai_context": True,
        },
    },
}


class QuizRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [v["value"] for v in VI_DU_QUIZ_GENERATE.values()]}
    )

    deck_id: int = Field(
        description=(
            "Bộ thẻ được đem ra hỏi. Đúng MỘT deck cho mỗi lượt gọi — muốn ôn trộn "
            "nhiều deck thì gọi nhiều lần rồi tự trộn ở backend Java.\n\n"
            "PHẢI nằm trong `allowed_deck_ids`, nếu không thì 400 `INVALID_SCOPE` "
            "chứ không im lặng bỏ qua. Với dữ liệu mẫu, dùng 1, 2 hoặc 3 — deck 4 "
            "chỉ có 3 thẻ nên luôn trả 400."
        ),
        examples=[1],
    )
    allowed_deck_ids: list[int] = Field(
        description=(
            "TOÀN BỘ ranh giới bảo mật của service nằm ở trường này. fsoft-ai không "
            "biết người dùng là ai: không có `profileId`, không có phiên đăng nhập, "
            "không có bảng phân quyền. Nó chỉ tin danh sách deck mà backend Java "
            "truyền vào.\n\n"
            "Danh sách RỖNG nghĩa là KHÔNG ĐƯỢC PHÉP GÌ CẢ → 400 `INVALID_SCOPE`. "
            "Tuyệt đối không được hiểu thành 'không lọc'. Đừng lấy giá trị này từ "
            "request của frontend — backend Java phải tự tính từ quyền sở hữu deck."
        ),
        examples=[[1, 2, 3]],
    )
    question_count: int = Field(
        default=10,
        ge=1,
        le=50,
        description=(
            "Số THẺ tối đa được lấy ra, cũng là TRẦN số câu hỏi — không phải lời hứa. "
            "Mỗi thẻ sinh nhiều nhất một câu, và thẻ nào không gom đủ đáp án nhiễu "
            "thì bị bỏ, nên `questions.length` có thể nhỏ hơn. Luôn đọc độ dài mảng "
            "thay vì giả định (xem mô tả endpoint).\n\n"
            "Thẻ được lấy theo `card_id` TĂNG DẦN, không ngẫu nhiên: hai lượt gọi "
            "giống nhau luôn hỏi đúng những thẻ đó, chỉ khác thứ tự lựa chọn. Muốn "
            "đổi bộ thẻ thì đổi `card_ids`.\n\n"
            "Ngoài khoảng 1–50 sẽ bị FastAPI chặn ở tầng validate và trả 422 với "
            'hình dạng `{"detail": [...]}`, KHÁC hình dạng `{"error": {...}}` '
            "của các lỗi nghiệp vụ."
        ),
        examples=[5],
    )
    types: list[QuestionType] = Field(
        default_factory=lambda: [QuestionType.MULTIPLE_CHOICE],
        description=(
            "Các dạng muốn có. Chia VÒNG TRÒN cho từng thẻ chứ không ngẫu nhiên: "
            "thẻ thứ `i` nhận `types[i % len(types)]`. Vậy nên tỉ lệ các dạng xấp "
            "xỉ bằng nhau, và LẶP LẠI trong danh sách là cách chỉnh tỉ lệ — "
            '`["MULTIPLE_CHOICE", "MULTIPLE_CHOICE", "LISTENING"]` cho hai '
            "phần ba là trắc nghiệm.\n\n"
            "Có `FILL_BLANK` mà `use_ai_context=false` thì dạng đó bị loại khỏi kế "
            "hoạch trong im lặng; nếu loại xong không còn dạng nào, cả bộ lùi về "
            "`MULTIPLE_CHOICE`."
        ),
        examples=[["MULTIPLE_CHOICE", "LISTENING"]],
    )
    card_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Thu hẹp phạm vi hỏi. ĐỂ RỖNG (mặc định) = LẤY CẢ DECK — rỗng ở đây "
            "KHÔNG có nghĩa là 'không thẻ nào', khác hẳn `allowed_deck_ids`.\n\n"
            "Backend Java truyền vào những thẻ ĐẾN HẠN ÔN theo SRS. fsoft-ai không "
            "biết SRS là gì: không có lịch ôn, không tính khoảng lặp, không lưu kết "
            "quả làm bài. Nó chỉ lọc lại danh sách theo `deck_id` cho an toàn.\n\n"
            "ID không thuộc `deck_id` bị bỏ qua IM LẶNG, không có cảnh báo. Gửi "
            "`[101, 999]` cho deck 1 thì chỉ 101 được dùng.\n\n"
            "Chỉ giới hạn thẻ ĐƯỢC ĐEM RA HỎI. Đáp án nhiễu vẫn lấy từ cả deck — "
            "nhờ vậy 4 thẻ đến hạn vẫn ra được câu hỏi tử tế. Còn dưới 4 thẻ sau "
            "khi lọc thì 400 `INVALID_REQUEST`."
        ),
        examples=[[201, 203, 205, 207]],
    )
    use_ai_context: bool = Field(
        default=False,
        description=(
            "`false` (MẶC ĐỊNH) = KHÔNG chạm Groq một lần nào. Không phải 'gọi ít "
            "đi' mà là không có lời gọi HTTP nào rời khỏi tiến trình: 0 token, "
            "không bao giờ 429, mất mạng vẫn chạy, 8 câu dưới 2 giây. Đây là chế độ "
            "dự phòng cho ngày demo (SPEC muc 14.1) — cứ để `false` trừ khi bạn "
            "thật sự cần dạng `FILL_BLANK`.\n\n"
            "`true` chỉ có tác dụng khi `types` chứa `FILL_BLANK`; ba dạng còn lại "
            "vốn không dùng LLM nên bật cờ này cũng vẫn 0 token."
        ),
        examples=[False],
    )


class QuizResponse(BaseModel):
    # Đây là output CHẠY THẬT của ví dụ request số 2 (deck 1, question_count=6,
    # types MULTIPLE_CHOICE/LISTENING/MATCHING) trên fixture 24 thẻ, chép nguyên
    # văn chứ không bịa. Nhờ vậy nó minh hoạ đúng các quy tắc có thật:
    #   - chia vòng tròn: thẻ 101/102/103/104/105/106 nhận lần lượt
    #     MC / LISTENING / MATCHING / MC / LISTENING / MATCHING;
    #   - câu MATCHING có card_id = null, correct_index = null;
    #   - câu MATCHING gộp 5 thẻ, phần tử đầu là thẻ của vòng đó (103 rồi 106);
    #   - LISTENING mới có audio_url, ba dạng kia luôn null;
    #   - use_ai_context=false -> mọi số token bằng 0.
    # Thứ tự trong `options` là ngẫu nhiên nên gọi lại sẽ khác; các chỉ số
    # correct_index / correct_option_index dưới đây khớp đúng với thứ tự đã in.
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "questions": [
                        {
                            "index": 1,
                            "type": "MULTIPLE_CHOICE",
                            "card_id": 101,
                            "prompt": '"resilient" nghĩa là gì?',
                            "options": [
                                "tỉ mỉ, cẩn thận đến từng chi tiết",
                                "siêng năng, chăm chỉ và bền bỉ",
                                "kiên cường, có khả năng phục hồi nhanh",
                                "lo lắng, e ngại về điều sắp xảy ra",
                            ],
                            "correct_index": 2,
                            "explanation": (
                                "resilient: kiên cường, có khả năng phục hồi nhanh. "
                                "Tiếng Anh: able to recover quickly from difficult "
                                "conditions. Ví dụ: She remained resilient despite "
                                "repeated setbacks."
                            ),
                            "generated_by": "DETERMINISTIC",
                            "audio_url": None,
                            "matching": None,
                        },
                        {
                            "index": 2,
                            "type": "LISTENING",
                            "card_id": 102,
                            "prompt": "Nghe và chọn từ bạn nghe được.",
                            "options": [
                                "indifferent",
                                "impulsive",
                                "diligent",
                                "apprehensive",
                            ],
                            "correct_index": 3,
                            "explanation": (
                                "apprehensive: lo lắng, e ngại về điều sắp xảy ra. "
                                "Tiếng Anh: anxious or fearful that something bad "
                                "will happen. Ví dụ: He felt apprehensive before the "
                                "final presentation."
                            ),
                            "generated_by": "DETERMINISTIC",
                            "audio_url": "https://cdn.example.com/audio/apprehensive.mp3",
                            "matching": None,
                        },
                        {
                            "index": 3,
                            "type": "MATCHING",
                            "card_id": None,
                            "prompt": "Nối mỗi từ với nghĩa đúng của nó.",
                            "options": [
                                "kiên cường, có khả năng phục hồi nhanh",
                                "thờ ơ, hờ hững, không quan tâm",
                                "thấu cảm, hiểu và chia sẻ cảm xúc người khác",
                                "tỉ mỉ, cẩn thận đến từng chi tiết",
                                "lo lắng, e ngại về điều sắp xảy ra",
                            ],
                            "correct_index": None,
                            "explanation": (
                                "Đối chiếu từ ở cột trái với nghĩa tiếng Việt ở cột phải."
                            ),
                            "generated_by": "DETERMINISTIC",
                            "audio_url": None,
                            "matching": [
                                {"card_id": 103, "word": "meticulous", "correct_option_index": 3},
                                {"card_id": 101, "word": "resilient", "correct_option_index": 0},
                                {"card_id": 102, "word": "apprehensive", "correct_option_index": 4},
                                {"card_id": 104, "word": "indifferent", "correct_option_index": 1},
                                {"card_id": 105, "word": "empathetic", "correct_option_index": 2},
                            ],
                        },
                        {
                            "index": 4,
                            "type": "MULTIPLE_CHOICE",
                            "card_id": 104,
                            "prompt": '"indifferent" nghĩa là gì?',
                            "options": [
                                "siêng năng, chăm chỉ và bền bỉ",
                                "bốc đồng, hành động không suy nghĩ trước",
                                "lo lắng, e ngại về điều sắp xảy ra",
                                "thờ ơ, hờ hững, không quan tâm",
                            ],
                            "correct_index": 3,
                            "explanation": (
                                "indifferent: thờ ơ, hờ hững, không quan tâm. "
                                "Tiếng Anh: having no particular interest or sympathy; "
                                "unconcerned. Ví dụ: She seemed indifferent to the "
                                "outcome of the meeting."
                            ),
                            "generated_by": "DETERMINISTIC",
                            "audio_url": None,
                            "matching": None,
                        },
                        {
                            "index": 5,
                            "type": "LISTENING",
                            "card_id": 105,
                            "prompt": "Nghe và chọn từ bạn nghe được.",
                            "options": [
                                "apprehensive",
                                "benign",
                                "empathetic",
                                "diligent",
                            ],
                            "correct_index": 2,
                            "explanation": (
                                "empathetic: thấu cảm, hiểu và chia sẻ cảm xúc người "
                                "khác. Tiếng Anh: showing an ability to understand and "
                                "share the feelings of another. Ví dụ: A good manager "
                                "should be empathetic toward the team."
                            ),
                            "generated_by": "DETERMINISTIC",
                            "audio_url": "https://cdn.example.com/audio/empathetic.mp3",
                            "matching": None,
                        },
                        {
                            "index": 6,
                            "type": "MATCHING",
                            "card_id": None,
                            "prompt": "Nối mỗi từ với nghĩa đúng của nó.",
                            "options": [
                                "kiên cường, có khả năng phục hồi nhanh",
                                "lo lắng, e ngại về điều sắp xảy ra",
                                "thờ ơ, hờ hững, không quan tâm",
                                "tỉ mỉ, cẩn thận đến từng chi tiết",
                                "bốc đồng, hành động không suy nghĩ trước",
                            ],
                            "correct_index": None,
                            "explanation": (
                                "Đối chiếu từ ở cột trái với nghĩa tiếng Việt ở cột phải."
                            ),
                            "generated_by": "DETERMINISTIC",
                            "audio_url": None,
                            "matching": [
                                {"card_id": 106, "word": "impulsive", "correct_option_index": 4},
                                {"card_id": 101, "word": "resilient", "correct_option_index": 0},
                                {"card_id": 102, "word": "apprehensive", "correct_option_index": 1},
                                {"card_id": 103, "word": "meticulous", "correct_option_index": 3},
                                {"card_id": 104, "word": "indifferent", "correct_option_index": 2},
                            ],
                        },
                    ],
                    "stats": {
                        "deterministic_count": 6,
                        "llm_count": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "latency_ms": 1,
                    },
                }
            ]
        }
    )

    questions: list[QuizQuestion] = Field(
        description=(
            "Các câu hỏi, đã đánh số `index` liên tục từ 1. ĐỘ DÀI CÓ THỂ NHỎ HƠN "
            "`question_count` — luôn đọc độ dài mảng.\n\n"
            "Thứ tự: các câu deterministic trước, các câu FILL_BLANK dồn về cuối "
            "(chúng được sinh theo lô sau cùng), KHÔNG theo thứ tự chia vòng tròn "
            "của `types`. Muốn xen kẽ thì tự xáo thứ tự CÂU ở client — nhưng đừng "
            "xáo `options` bên trong câu."
        )
    )
    stats: QuizStats = Field(
        description="Số đo của lượt gọi này: bao nhiêu câu từ đâu ra, tốn bao nhiêu token."
    )
